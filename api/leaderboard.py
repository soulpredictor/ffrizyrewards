import json
import os
import requests


API_URL = os.environ.get(
    "SHUFFLE_STATS_URL",
    "https://affiliate.shuffle.com/stats/96cc7e48-64b2-4120-b07d-779f3a9fd870",
)
API_TIMEOUT = float(os.environ.get("SHUFFLE_STATS_TIMEOUT", "8"))


def handler(request):
    try:
        upstream = requests.get(API_URL, timeout=API_TIMEOUT)
        upstream.raise_for_status()
        payload = upstream.json()
    except requests.RequestException:
        return (
            json.dumps({"error": "Unable to reach upstream leaderboard API"}),
            502,
            {"Content-Type": "application/json"},
        )
    except ValueError:
        return (
            json.dumps({"error": "Invalid response from upstream leaderboard API"}),
            502,
            {"Content-Type": "application/json"},
        )

    if not isinstance(payload, list):
        return (
            json.dumps({"error": "Unexpected payload format from upstream API"}),
            502,
            {"Content-Type": "application/json"},
        )

    simplified = [
        {
            "username": entry.get("username", ""),
            "wagerAmount": float(entry.get("wagerAmount", 0) or 0),
        }
        for entry in payload
    ]

    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "public, max-age=60",
    }
    return json.dumps(simplified), 200, headers

