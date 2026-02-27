import os
import threading
from datetime import datetime
from typing import Optional, List, Any, Dict

import requests
from flask import Flask, jsonify, request


API_URL = os.environ.get(
    "SHUFFLE_STATS_URL",
    "https://affiliate.shuffle.com/stats/96cc7e48-64b2-4120-b07d-779f3a9fd870",
)
API_TIMEOUT = float(os.environ.get("SHUFFLE_STATS_TIMEOUT", "5"))  # 5 second timeout
SESSION = requests.Session()

app = Flask(__name__)
app.config["JSONIFY_PRETTYPRINT_REGULAR"] = False

# Leaderboard end time (set via API)
_leaderboard_end_time: Optional[datetime] = None
_end_time_lock = threading.Lock()


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
    Fetch leaderboard data directly from Shuffle API.
    Uses startTime and endTime to reset wager count - only counts wagers within this period.
    IMPORTANT: Shuffle API expects Unix timestamps in SECONDS, not milliseconds!
    Returns the raw data from the API.
    Tries multiple approaches if first attempt returns empty data.
    """
    # Build URL with time parameters to reset wagers
    # Shuffle API requires Unix timestamps in SECONDS (not milliseconds)
    # Frontend sends milliseconds, so we convert to seconds
    url = API_URL
    params = {}
    if start_time:
        # Convert milliseconds to seconds (Shuffle API expects seconds)
        # Timestamps > 10 digits are milliseconds, <= 10 digits are seconds
        start_val = int(start_time)
        start_seconds = start_val // 1000 if start_val > 9999999999 else start_val
        params["startTime"] = str(start_seconds)
        app.logger.debug(f"Converted startTime: {start_time}ms -> {start_seconds}s")
    if end_time:
        # Convert milliseconds to seconds (Shuffle API expects seconds)
        end_val = int(end_time)
        end_seconds = end_val // 1000 if end_val > 9999999999 else end_val
        params["endTime"] = str(end_seconds)
        app.logger.debug(f"Converted endTime: {end_time}ms -> {end_seconds}s")
    
    # Try fetching with provided parameters first
    try:
        app.logger.info(f"Fetching from Shuffle API: {url} with params: {params} (timestamps in SECONDS)")
        response = SESSION.get(url, params=params, timeout=API_TIMEOUT)
        
        # Handle API errors
        if response.status_code == 400:
            error_data = response.json() if response.headers.get('content-type', '').startswith('application/json') else {}
            error_message = error_data.get('message', '')
            
            if error_message == 'TOO_MANY_REQUEST':
                app.logger.warning("Rate limit exceeded: TOO_MANY_REQUEST - API allows 1 request every 10 seconds")
                return []  # Return empty - frontend will retry after delay
            elif error_message == 'REFEREES_NOT_FOUND':
                app.logger.info("No referees found - returning empty array")
                return []  # This is normal if user has no referees
            else:
                app.logger.warning(f"API returned 400 error: {error_data}")
                return []  # Return empty - don't use fallback
        
        response.raise_for_status()
        payload = response.json()
        
        if not isinstance(payload, list):
            app.logger.error(f"Unexpected payload format from upstream API: {type(payload)}")
            return []
        
        # Return data even if empty (empty array is valid - means no wagers in that period)
        app.logger.info(f"Successfully fetched {len(payload)} entries from Shuffle API with time params")
        return payload
        
    except requests.RequestException as exc:
        app.logger.error(f"Failed to fetch upstream leaderboard: {exc}", exc_info=True)
        # Don't use fallback - user wants only exact time data
        return []


def is_leaderboard_ended() -> bool:
    """
    Check if the leaderboard has ended based on the stored end time.
    """
    with _end_time_lock:
        if _leaderboard_end_time is None:
            return False
        return datetime.utcnow() >= _leaderboard_end_time




@app.route("/api/leaderboard", methods=["GET"])
def leaderboard():
    """
    Fetch leaderboard data directly from Shuffle API on every request.
    Supports startTime and endTime query parameters.
    Usernames are masked for privacy.
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
    
    # ALWAYS fetch directly from Shuffle API
    try:
        data = fetch_leaderboard_data(start_time=start_time, end_time=end_time)
    except Exception as e:
        app.logger.error(f"Error fetching leaderboard data: {e}", exc_info=True)
        data = []
    
    # Ensure data is always a list
    if not isinstance(data, list):
        app.logger.error(f"Data is not a list: {type(data)}")
        data = []
    
    # Process and mask usernames
    # Include ALL entries even if wagerAmount is 0
    simplified = []
    for entry in data:
        if isinstance(entry, dict):
            username = entry.get("username", "")
            wager_amount = float(entry.get("wagerAmount", 0) or 0)
            # Include entry even if wagerAmount is 0
            simplified.append({
                "username": mask_username(username),
                "wagerAmount": wager_amount,
            })
    
    app.logger.info(f"Returning {len(simplified)} leaderboard entries (including {sum(1 for e in simplified if e['wagerAmount'] == 0)} with $0)")
    
    # Add metadata about whether leaderboard has ended
    response_data = {
        "data": simplified,
        "ended": is_leaderboard_ended()
    }
    
    return jsonify(response_data)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")

