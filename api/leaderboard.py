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
API_TIMEOUT = float(os.environ.get("SHUFFLE_STATS_TIMEOUT", "8"))
SESSION = requests.Session()

app = Flask(__name__)
app.config["JSONIFY_PRETTYPRINT_REGULAR"] = False

# Leaderboard end time (set via API)
_leaderboard_end_time: Optional[datetime] = None
_end_time_lock = threading.Lock()

# Baseline wagers storage (to calculate new wagers only)
_baseline_wagers: Dict[str, float] = {}  # username -> wagerAmount
_baseline_lock = threading.Lock()
_baseline_set = False


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
    Returns the raw data from the API.
    """
    # Build URL with time parameters to reset wagers
    # startTime and endTime reset wagers to $0, only counting wagers from Dec 1-30, 2025
    url = API_URL
    params = {}
    if start_time:
        params["startTime"] = start_time  # Timestamp in milliseconds
    if end_time:
        params["endTime"] = end_time  # Timestamp in milliseconds
    
    try:
        app.logger.info(f"Fetching from Shuffle API: {url} with params: {params}")
        response = SESSION.get(url, params=params, timeout=API_TIMEOUT)
        
        # Handle rate limit error
        if response.status_code == 400:
            error_data = response.json() if response.headers.get('content-type', '').startswith('application/json') else {}
            if error_data.get('message') == 'TOO_MANY_REQUEST':
                app.logger.warning("Rate limit exceeded: TOO_MANY_REQUEST - will retry")
                # Return empty array instead of raising to allow retry
                return []
        
        response.raise_for_status()
        payload = response.json()
        
        if not isinstance(payload, list):
            app.logger.error(f"Unexpected payload format from upstream API: {type(payload)}")
            return []
        
        app.logger.info(f"Successfully fetched {len(payload)} entries from Shuffle API")
        return payload
        
    except requests.RequestException as exc:
        app.logger.error(f"Failed to fetch upstream leaderboard: {exc}", exc_info=True)
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
    Fetch leaderboard data directly from Shuffle API.
    JUGAD: Fetches current total wagers, stores baseline, and shows only NEW wagers.
    """
    global _leaderboard_end_time, _baseline_wagers, _baseline_set
    
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
    
    # JUGAD: Fetch CURRENT total wagers (without time params to get all data)
    # Retry logic: try up to 2 times if first fetch fails or returns empty
    current_data = []
    max_retries = 2
    for attempt in range(max_retries):
        try:
            current_data = fetch_leaderboard_data(start_time=None, end_time=None)
            # Ensure data is always a list
            if not isinstance(current_data, list):
                app.logger.error(f"Data is not a list: {type(current_data)}")
                current_data = []
            
            # If we got data, break out of retry loop
            if current_data and len(current_data) > 0:
                break
            elif attempt < max_retries - 1:
                app.logger.warning(f"Empty data received, retrying... (attempt {attempt + 1}/{max_retries})")
                import time
                time.sleep(0.5)  # Small delay before retry
        except Exception as e:
            app.logger.error(f"Error fetching leaderboard data (attempt {attempt + 1}): {e}", exc_info=True)
            if attempt < max_retries - 1:
                import time
                time.sleep(0.5)  # Small delay before retry
            else:
                current_data = []
    
    # Set baseline on first fetch when startTime is provided (leaderboard start)
    # This captures the wager amounts at the start of the leaderboard period
    if start_time:
        with _baseline_lock:
            # Always update baseline when startTime is provided and we have data
            if current_data and len(current_data) > 0:
                baseline_updated = False
                for entry in current_data:
                    if isinstance(entry, dict):
                        username = entry.get("username", "")
                        wager_amount = float(entry.get("wagerAmount", 0) or 0)
                        # Set/update baseline for all users
                        if username not in _baseline_wagers or not _baseline_set:
                            _baseline_wagers[username] = wager_amount
                            baseline_updated = True
                if baseline_updated or not _baseline_set:
                    _baseline_set = True
                    app.logger.info(f"Baseline wagers set/updated: {len(_baseline_wagers)} users")
            elif not _baseline_set:
                # If no data but baseline not set, mark as set to avoid repeated attempts
                _baseline_set = True
                app.logger.warning("No data received, baseline will be set on next successful fetch")
    
    # Calculate NEW wagers only (current - baseline)
    # Always return data even if empty to ensure frontend receives consistent response
    simplified = []
    with _baseline_lock:
        if current_data and len(current_data) > 0:
            for entry in current_data:
                if isinstance(entry, dict):
                    username = entry.get("username", "")
                    current_wager = float(entry.get("wagerAmount", 0) or 0)
                    baseline_wager = _baseline_wagers.get(username, 0.0)
                    new_wager = max(0, current_wager - baseline_wager)  # Only show positive new wagers
                    
                    simplified.append({
                        "username": mask_username(username),
                        "wagerAmount": new_wager,
                    })
        else:
            # If no current data but we have baseline, return baseline users with 0 wagers
            if _baseline_set and len(_baseline_wagers) > 0:
                for username in _baseline_wagers.keys():
                    simplified.append({
                        "username": mask_username(username),
                        "wagerAmount": 0.0,
                    })
                app.logger.info("No current data, returning baseline users with 0 wagers")
    
    app.logger.info(f"Returning {len(simplified)} leaderboard entries (new wagers only)")
    
    # Always return data array, even if empty
    response_data = {
        "data": simplified,
        "ended": is_leaderboard_ended()
    }
    
    return jsonify(response_data)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")

