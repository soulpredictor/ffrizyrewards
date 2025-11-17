import os
from datetime import timedelta

import requests
from flask import Flask, jsonify, abort


API_URL = os.environ.get(
    "SHUFFLE_STATS_URL",
    "https://affiliate.shuffle.com/stats/96cc7e48-64b2-4120-b07d-779f3a9fd870",
)
API_TIMEOUT = float(os.environ.get("SHUFFLE_STATS_TIMEOUT", "8"))
SESSION = requests.Session()

app = Flask(__name__)
app.config["JSONIFY_PRETTYPRINT_REGULAR"] = False


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
    Proxy the Shuffle affiliate stats endpoint and return only the necessary data.
    """
    try:
        upstream = SESSION.get(API_URL, timeout=API_TIMEOUT)
        upstream.raise_for_status()
        payload = upstream.json()
    except requests.RequestException as exc:
        app.logger.error("Failed to fetch upstream leaderboard: %s", exc, exc_info=True)
        abort(502, description="Unable to reach upstream leaderboard API")
    except ValueError:
        abort(502, description="Invalid response from upstream leaderboard API")

    if not isinstance(payload, list):
        abort(502, description="Unexpected payload format from upstream API")

    # Only forward the properties we actually use on the front end.
    simplified = [
        {
            "username": entry.get("username", ""),
            "wagerAmount": float(entry.get("wagerAmount", 0) or 0),
        }
        for entry in payload
    ]
    return jsonify(simplified)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")

