import hashlib
import hmac
import os
import time
from functools import wraps

from flask import Flask, jsonify, request

from wager_store import list_period_keys, query_snapshots

app = Flask(__name__)

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "ffrizy-admin-change-me")
TOKEN_TTL_SECONDS = int(os.environ.get("ADMIN_TOKEN_TTL", "86400"))


def _token_secret() -> bytes:
    return (os.environ.get("ADMIN_TOKEN_SECRET") or ADMIN_PASSWORD).encode("utf-8")


def _make_token() -> str:
    issued = str(int(time.time()))
    sig = hmac.new(_token_secret(), issued.encode(), hashlib.sha256).hexdigest()
    return f"{issued}.{sig}"


def _verify_token(token: str) -> bool:
    if not token or "." not in token:
        return False
    issued, sig = token.rsplit(".", 1)
    try:
        issued_ts = int(issued)
    except ValueError:
        return False
    if time.time() - issued_ts > TOKEN_TTL_SECONDS:
        return False
    expected = hmac.new(_token_secret(), issued.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def require_admin(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        token = auth[7:].strip() if auth.startswith("Bearer ") else request.headers.get("X-Admin-Token", "")
        if not _verify_token(token):
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)

    return wrapper


@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    body = request.get_json(silent=True) or {}
    password = body.get("password") or request.form.get("password") or ""
    if not ADMIN_PASSWORD or password != ADMIN_PASSWORD:
        return jsonify({"error": "invalid_credentials"}), 401
    return jsonify({"token": _make_token(), "expiresIn": TOKEN_TTL_SECONDS})


@app.route("/api/admin/wagers", methods=["GET"])
@require_admin
def admin_wagers():
    site = (request.args.get("site") or "").strip().lower() or None
    username = request.args.get("user") or request.args.get("username")
    date_from = request.args.get("from") or request.args.get("dateFrom")
    date_to = request.args.get("to") or request.args.get("dateTo")
    period_key = request.args.get("periodKey")
    try:
        limit = min(int(request.args.get("limit", "200")), 500)
    except ValueError:
        limit = 200

    snapshots = query_snapshots(
        site=site,
        username=username,
        date_from=date_from,
        date_to=date_to,
        period_key=period_key,
        limit=limit,
    )
    return jsonify({"data": snapshots, "count": len(snapshots)})


@app.route("/api/admin/periods", methods=["GET"])
@require_admin
def admin_periods():
    site = (request.args.get("site") or "").strip().lower() or None
    return jsonify({"periods": list_period_keys(site=site)})


if __name__ == "__main__":
    port_raw = os.environ.get("PORT") or os.environ.get("SERVER_PORT") or os.environ.get("PTERODACTYL_PORT") or "4636"
    try:
        port = int(port_raw)
    except ValueError:
        port = 4636
    app.run(host="0.0.0.0", port=port)
