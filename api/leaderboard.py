import os
import time
import threading
from datetime import timedelta, datetime
from collections import deque
from typing import Optional, Dict, List, Any

import requests
from flask import Flask, jsonify, abort, request


API_URL = os.environ.get(
    "SHUFFLE_STATS_URL",
    "https://affiliate.shuffle.com/stats/96cc7e48-64b2-4120-b07d-779f3a9fd870",
)
API_TIMEOUT = float(os.environ.get("SHUFFLE_STATS_TIMEOUT", "8"))
SESSION = requests.Session()

# Rate limiting: 1 call every 30 seconds
RATE_LIMIT_SECONDS = 30
# Polling interval: every 20 seconds as per documentation
POLLING_INTERVAL_SECONDS = 20

app = Flask(__name__)
app.config["JSONIFY_PRETTYPRINT_REGULAR"] = False

# Data storage for polled data
# Structure: { "lifetime": [...], "weekly": { "startTime_endTime": [...] } }
_data_store: Dict[str, Any] = {
    "lifetime": [],
    "weekly": {}
}
_data_lock = threading.Lock()

# Leaderboard end time (set via environment variable or API)
_leaderboard_end_time: Optional[datetime] = None
_end_time_lock = threading.Lock()

# Rate limiting tracking
_last_api_call_time: Optional[float] = None
_rate_limit_lock = threading.Lock()


def mask_username(username: str) -> str:
    """
    Mask username for privacy: UsernameA -> Use***A
    Shows first 3 characters and last character, masks the rest.
    """
    if not username or len(username) <= 4:
        # If username is too short, just show first char and mask rest
        if len(username) <= 1:
            return username
        return username[0] + "*" * (len(username) - 1)
    
    # Show first 3 chars, mask middle, show last char
    return username[:3] + "*" * (len(username) - 4) + username[-1]


def fetch_leaderboard_data(start_time: Optional[str] = None, end_time: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Fetch leaderboard data from Shuffle API with rate limiting.
    Returns the raw data from the API.
    """
    global _last_api_call_time
    
    # Rate limiting check
    with _rate_limit_lock:
        current_time = time.time()
        if _last_api_call_time is not None:
            time_since_last_call = current_time - _last_api_call_time
            if time_since_last_call < RATE_LIMIT_SECONDS:
                wait_time = RATE_LIMIT_SECONDS - time_since_last_call
                app.logger.warning(f"Rate limit: waiting {wait_time:.1f} seconds before next API call")
                time.sleep(wait_time)
        
        # Build URL with optional time parameters
        url = API_URL
        params = {}
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        
        try:
            response = SESSION.get(url, params=params, timeout=API_TIMEOUT)
            
            # Handle rate limit error
            if response.status_code == 400:
                error_data = response.json() if response.headers.get('content-type', '').startswith('application/json') else {}
                if error_data.get('message') == 'TOO_MANY_REQUEST':
                    app.logger.error("Rate limit exceeded: TOO_MANY_REQUEST")
                    raise requests.RequestException("Rate limit exceeded: TOO_MANY_REQUEST")
            
            response.raise_for_status()
            payload = response.json()
            _last_api_call_time = time.time()
            
            if not isinstance(payload, list):
                app.logger.error("Unexpected payload format from upstream API")
                return []
            
            return payload
            
        except requests.RequestException as exc:
            app.logger.error("Failed to fetch upstream leaderboard: %s", exc, exc_info=True)
            raise


def is_leaderboard_ended() -> bool:
    """
    Check if the leaderboard has ended based on the stored end time.
    """
    with _end_time_lock:
        if _leaderboard_end_time is None:
            return False
        return datetime.utcnow() >= _leaderboard_end_time


def poll_leaderboard_background():
    """
    Background thread that polls the API every 20 seconds and stores the data.
    Stops polling after the leaderboard end time.
    """
    app.logger.info("Starting background polling thread")
    
    while True:
        try:
            # Check if leaderboard has ended
            if is_leaderboard_ended():
                app.logger.info("Leaderboard has ended. Stopping background polling.")
                break
            
            # Poll lifetime data (only if endTime is not set, otherwise use endTime)
            try:
                with _end_time_lock:
                    end_time_str = None
                    if _leaderboard_end_time:
                        # Convert to ISO format timestamp (milliseconds)
                        end_time_str = str(int(_leaderboard_end_time.timestamp() * 1000))
                
                lifetime_data = fetch_leaderboard_data(end_time=end_time_str)
                with _data_lock:
                    _data_store["lifetime"] = lifetime_data
                app.logger.info(f"Updated lifetime leaderboard data: {len(lifetime_data)} entries")
            except Exception as e:
                app.logger.error(f"Error polling lifetime data: {e}")
            
            # Wait 20 seconds before next poll
            time.sleep(POLLING_INTERVAL_SECONDS)
            
        except Exception as e:
            app.logger.error(f"Error in background polling thread: {e}")
            time.sleep(POLLING_INTERVAL_SECONDS)


@app.after_request
def add_cache_headers(response):
    """
    Allow short-lived caching on the API to avoid hammering Shuffle's endpoint.
    """
    if response.direct_passthrough or response.status_code != 200:
        return response
    response.cache_control.max_age = int(timedelta(minutes=1).total_seconds())
    response.cache_control.public = True
    return response


@app.route("/api/leaderboard", methods=["GET"])
def leaderboard():
    """
    Return leaderboard data from stored cache (polled every 20 seconds).
    Supports startTime and endTime query parameters for weekly leaderboards.
    Usernames are masked for privacy.
    
    If endTime is provided, it will be stored and used to stop counting wagers after that time.
    """
    global _leaderboard_end_time
    
    start_time = request.args.get("startTime")
    end_time = request.args.get("endTime")
    
    # Store the end time if provided (for leaderboard cutoff)
    if end_time:
        try:
            # Parse endTime (expecting milliseconds timestamp)
            end_timestamp = int(end_time) / 1000  # Convert from milliseconds to seconds
            end_datetime = datetime.utcfromtimestamp(end_timestamp)
            with _end_time_lock:
                # Only update if not already set or if new time is earlier
                if _leaderboard_end_time is None or end_datetime < _leaderboard_end_time:
                    _leaderboard_end_time = end_datetime
                    app.logger.info(f"Leaderboard end time set to: {_leaderboard_end_time}")
        except (ValueError, OSError) as e:
            app.logger.warning(f"Invalid endTime format: {end_time}, error: {e}")
    
    # Determine which data to return
    with _data_lock:
        if start_time and end_time:
            # Weekly leaderboard - use time range as key
            cache_key = f"{start_time}_{end_time}"
            if cache_key not in _data_store["weekly"]:
                # Fetch and cache this time range
                try:
                    weekly_data = fetch_leaderboard_data(start_time, end_time)
                    _data_store["weekly"][cache_key] = weekly_data
                    data = weekly_data
                except Exception as e:
                    app.logger.error(f"Error fetching weekly data: {e}")
                    abort(502, description="Unable to fetch weekly leaderboard data")
            else:
                data = _data_store["weekly"][cache_key]
        else:
            # Lifetime data (from background polling)
            # If leaderboard has ended, use the endTime when fetching
            if is_leaderboard_ended():
                # Leaderboard has ended, fetch final data with endTime
                try:
                    with _end_time_lock:
                        end_time_str = None
                        if _leaderboard_end_time:
                            end_time_str = str(int(_leaderboard_end_time.timestamp() * 1000))
                    
                    if end_time_str:
                        final_data = fetch_leaderboard_data(end_time=end_time_str)
                        with _data_lock:
                            _data_store["lifetime"] = final_data
                        data = final_data
                    else:
                        data = _data_store["lifetime"]
                except Exception as e:
                    app.logger.error(f"Error fetching final leaderboard data: {e}")
                    data = _data_store["lifetime"]  # Fallback to cached data
            else:
                data = _data_store["lifetime"]
    
    if not isinstance(data, list):
        abort(502, description="Unexpected data format")
    
    # Process and mask usernames
    simplified = [
        {
            "username": mask_username(entry.get("username", "")),
            "wagerAmount": float(entry.get("wagerAmount", 0) or 0),
        }
        for entry in data
    ]
    
    # Add metadata about whether leaderboard has ended
    response_data = {
        "data": simplified,
        "ended": is_leaderboard_ended()
    }
    
    return jsonify(response_data)


# Start background polling thread when app starts
def start_background_polling():
    """Start the background polling thread."""
    thread = threading.Thread(target=poll_leaderboard_background, daemon=True)
    thread.start()
    app.logger.info("Background polling thread started")


if __name__ == "__main__":
    # Start background polling
    start_background_polling()
    
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
else:
    # For production deployments (e.g., gunicorn), start polling when module is imported
    start_background_polling()

