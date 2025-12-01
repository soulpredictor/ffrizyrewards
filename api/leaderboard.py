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
    Returns the raw data from the API.
    """
    # Build URL with optional time parameters
    url = API_URL
    params = {}
    if start_time:
        params["startTime"] = start_time
    if end_time:
        params["endTime"] = end_time
    
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
    simplified = [
        {
            "username": mask_username(entry.get("username", "")),
            "wagerAmount": float(entry.get("wagerAmount", 0) or 0),
        }
        for entry in data if isinstance(entry, dict)
    ]
    
    app.logger.info(f"Returning {len(simplified)} leaderboard entries")
    
    # Add metadata about whether leaderboard has ended
    response_data = {
        "data": simplified,
        "ended": is_leaderboard_ended()
    }
    
    return jsonify(response_data)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
