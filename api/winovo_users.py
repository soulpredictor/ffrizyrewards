import os

import requests
from flask import Flask, jsonify


WINOVO_API_BASE = os.environ.get("WINOVO_API_BASE", "https://winovo.io")
WINOVO_CREATOR_API_KEY = os.environ.get("WINOVO_CREATOR_API_KEY")
API_TIMEOUT = float(os.environ.get("WINOVO_TIMEOUT", "8"))

SESSION = requests.Session()
app = Flask(__name__)
app.config["JSONIFY_PRETTYPRINT_REGULAR"] = False


def _fetch_winovo_users():
    if not WINOVO_CREATOR_API_KEY:
        return {"status": "error", "error": "missing_winovo_creator_api_key"}, 500

    url = f"{WINOVO_API_BASE}/api/creator/users"
    try:
        response = SESSION.get(
            url,
            timeout=API_TIMEOUT,
            headers={"x-creator-auth": WINOVO_CREATOR_API_KEY},
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException:
        return {"status": "error", "error": "winovo_upstream_error"}, 502
    except ValueError:
        return {"status": "error", "error": "winovo_invalid_json"}, 502

    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return {"status": "error", "error": "winovo_unexpected_payload"}, 502

    return payload, 200


@app.route("/", methods=["GET"])
def handler():
    payload, status_code = _fetch_winovo_users()
    return jsonify(payload), status_code

