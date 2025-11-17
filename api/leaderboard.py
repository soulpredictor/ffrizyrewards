import json
import os
import urllib.request
from http import HTTPStatus


API_URL = os.environ.get(
    "SHUFFLE_STATS_URL",
    "https://affiliate.shuffle.com/stats/96cc7e48-64b2-4120-b07d-779f3a9fd870",
)
API_TIMEOUT = float(os.environ.get("SHUFFLE_STATS_TIMEOUT", "8"))


def _json_response(body: dict, status: int):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Cache-Control": "public, max-age=60" if status == 200 else "no-store",
        },
        "body": json.dumps(body),
    }


def handler(request):
    try:
        with urllib.request.urlopen(API_URL, timeout=API_TIMEOUT) as upstream:
            payload = json.loads(upstream.read().decode("utf-8"))
    except Exception as exc:  # pragma: no cover
        return _json_response(
            {"error": "Unable to reach upstream leaderboard API"}, HTTPStatus.BAD_GATEWAY
        )

    if not isinstance(payload, list):
        return _json_response(
            {"error": "Unexpected payload format from upstream API"},
            HTTPStatus.BAD_GATEWAY,
        )

    simplified = [
        {
            "username": entry.get("username", ""),
            "wagerAmount": float(entry.get("wagerAmount", 0) or 0),
        }
        for entry in payload
    ]

    return _json_response(simplified, HTTPStatus.OK)

