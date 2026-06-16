import os
import threading
import hashlib
import json
from datetime import datetime
from typing import Optional, List, Any, Dict

import requests
from flask import Flask, jsonify, request

from wager_store import (
    get_period_bounds,
    query_snapshots,
    record_snapshot,
)

API_URL = os.environ.get(
    "SHUFFLE_STATS_URL",
    "https://affiliate.shuffle.com/wager/96cc7e48-64b2-4120-b07d-779f3a9fd870",
)
PACKY_API_BASE = (os.environ.get("PACKY_API_BASE") or "https://packy.gg").rstrip("/")
PACKY_API_KEY = os.environ.get("PACKY_API_KEY") or "64913ffff71d5c9c03a50d365dfe1e483b8e34e7b3f067f22f6e5d3bbe91a1d6"
PACKY_CUSTOM_KEY = os.environ.get("PACKY_CUSTOM_KEY") or ""
API_TIMEOUT = float(os.environ.get("SHUFFLE_STATS_TIMEOUT", "5"))
SESSION = requests.Session()

app = Flask(__name__)
app.config["JSONIFY_PRETTYPRINT_REGULAR"] = False

_leaderboard_end_time: Optional[datetime] = None
_end_time_lock = threading.Lock()


def mask_username(username: str) -> str:
    if not username or len(username) <= 4:
        if len(username) <= 1:
            return username
        return username[0] + "*" * (len(username) - 1)
    return username[:3] + "*" * (len(username) - 4) + username[-1]


def _latest_snapshot_players(site: str, period_key: str) -> List[Dict[str, Any]]:
    snapshots = query_snapshots(site=site, period_key=period_key, limit=25)
    for snap in snapshots:
        if not isinstance(snap, dict):
            continue
        players = snap.get("players")
        if isinstance(players, list) and players:
            return players

    snapshots = query_snapshots(site=site, limit=25)
    for snap in snapshots:
        if not isinstance(snap, dict):
            continue
        players = snap.get("players")
        if isinstance(players, list) and players:
            return players
    return []


def _hash_response(period_key: str, ended: bool, players: List[Dict[str, Any]]) -> str:
    normalized = []
    for p in players or []:
        if not isinstance(p, dict):
            continue
        username = (p.get("username") or "User")
        try:
            amt = round(float(p.get("wagerAmount", 0) or 0), 2)
        except (TypeError, ValueError):
            amt = 0.0
        normalized.append({"username": str(username), "wagerAmount": amt})
    normalized.sort(key=lambda x: (-x["wagerAmount"], x["username"]))
    payload = {"periodKey": period_key, "ended": bool(ended), "data": normalized}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _respond(payload: Dict[str, Any], etag: str):
    inm = (request.headers.get("If-None-Match") or "").strip()
    if inm and inm == etag:
        return "", 304, {"ETag": etag, "Cache-Control": "no-store"}
    resp = jsonify(payload)
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = "no-store"
    return resp


def fetch_leaderboard_data(
    start_time: Optional[str] = None, end_time: Optional[str] = None
) -> List[Dict[str, Any]]:
    url = API_URL
    params = {}
    if start_time:
        start_val = int(start_time)
        start_seconds = start_val // 1000 if start_val > 9999999999 else start_val
        params["startTime"] = str(start_seconds)
    if end_time:
        end_val = int(end_time)
        end_seconds = end_val // 1000 if end_val > 9999999999 else end_val
        params["endTime"] = str(end_seconds)

    try:
        response = SESSION.get(url, params=params, timeout=API_TIMEOUT)
        if response.status_code == 400:
            error_data = (
                response.json()
                if response.headers.get("content-type", "").startswith("application/json")
                else {}
            )
            if error_data.get("message") == "TOO_MANY_REQUEST":
                return []
            if error_data.get("message") == "REFEREES_NOT_FOUND":
                return []
            return []
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            return []
        return payload
    except requests.RequestException as exc:
        app.logger.error(f"Failed to fetch upstream leaderboard: {exc}", exc_info=True)
        return []


def is_leaderboard_ended() -> bool:
    with _end_time_lock:
        if _leaderboard_end_time is None:
            return False
        return datetime.utcnow() >= _leaderboard_end_time


def _packy_headers() -> Dict[str, str]:
    headers: Dict[str, str] = {}
    if PACKY_API_KEY:
        headers["X-API-Key"] = PACKY_API_KEY
    if PACKY_CUSTOM_KEY:
        headers["X-Custom-Key"] = PACKY_CUSTOM_KEY
    return headers


def fetch_packy_leaderboards() -> Dict[str, Any]:
    if not PACKY_API_KEY:
        return {"status": "error", "error": "missing_packy_api_key"}

    url = f"{PACKY_API_BASE}/v1/affiliate/leaderboard/external"
    try:
        response = SESSION.get(
            url,
            timeout=API_TIMEOUT,
            headers=_packy_headers(),
        )
        payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        if not response.ok:
            status_code = response.status_code
            if isinstance(payload, dict) and payload:
                return {"status": "error", "error": "packy_upstream_error", "statusCode": status_code, "upstream": payload}
            return {"status": "error", "error": "packy_upstream_error", "statusCode": status_code}
    except requests.RequestException as exc:
        app.logger.error(f"Failed to fetch Packy leaderboard: {exc}", exc_info=True)
        return {"status": "error", "error": "packy_upstream_error"}
    except ValueError:
        return {"status": "error", "error": "packy_invalid_json"}

    leaderboards = (
        payload.get("data", {}).get("leaderboards")
        if isinstance(payload, dict)
        else None
    )
    if payload.get("success") is not True or not isinstance(leaderboards, list):
        return {"status": "error", "error": "packy_unexpected_payload"}

    return {"status": "ok", "data": leaderboards}


def _pick_packy_leaderboard(leaderboards: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not leaderboards:
        return None
    for desired in ("active", "upcoming", "ended"):
        for lb in leaderboards:
            if not isinstance(lb, dict):
                continue
            if (lb.get("time_status") or "").strip().lower() == desired:
                return lb
    for lb in leaderboards:
        if isinstance(lb, dict):
            return lb
    return None


def capture_shuffle_snapshot() -> None:
    period_start_dt, period_end_dt, period_key = get_period_bounds("shuffle")
    start_ms = int(period_start_dt.timestamp() * 1000)
    end_ms = int(period_end_dt.timestamp() * 1000)

    data = fetch_leaderboard_data(start_time=str(start_ms), end_time=str(end_ms))
    if not isinstance(data, list):
        data = []

    raw_for_store = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        username = entry.get("username", "")
        weighted = float(entry.get("weightedWagerAmount", entry.get("wagerAmount", 0)) or 0)
        raw_for_store.append({"username": username, "wagerAmount": weighted})

    record_snapshot("shuffle", raw_for_store, period_start_dt, period_end_dt, period_key)


def capture_packy_snapshot() -> None:
    period_start_dt, period_end_dt, period_key = get_period_bounds("packy")
    payload = fetch_packy_leaderboards()
    if payload.get("status") != "ok":
        return

    picked = _pick_packy_leaderboard(payload.get("data", []))
    entries = picked.get("entries", []) if isinstance(picked, dict) else []
    simplified: List[Dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw_amount = entry.get("total_wagered_usd", entry.get("totalWageredUsd"))
        try:
            amount = float(raw_amount or 0)
        except (TypeError, ValueError):
            amount = 0.0
        simplified.append({"username": entry.get("username") or "User", "wagerAmount": amount})

    record_snapshot("packy", simplified, period_start_dt, period_end_dt, period_key)


@app.route("/api/leaderboard", methods=["GET"])
def leaderboard():
    global _leaderboard_end_time

    site = (request.args.get("site") or "").strip().lower()
    period_start_dt, period_end_dt, period_key = get_period_bounds(
        "packy" if site == "packy" else "shuffle"
    )

    if site == "packy":
        stale = False
        payload = fetch_packy_leaderboards()
        leaderboards = payload.get("data", []) if payload.get("status") == "ok" else []
        picked = _pick_packy_leaderboard(leaderboards) if isinstance(leaderboards, list) else None
        entries = picked.get("entries", []) if isinstance(picked, dict) else []
        simplified: List[Dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            raw_amount = entry.get("total_wagered_usd", entry.get("totalWageredUsd"))
            try:
                amount = float(raw_amount or 0)
            except (TypeError, ValueError):
                amount = 0.0
            simplified.append({"username": entry.get("username") or "User", "wagerAmount": amount})

        if not simplified:
            fallback = _latest_snapshot_players("packy", period_key)
            if fallback:
                stale = True
                simplified = [
                    {
                        "username": (p.get("username") or "User") if isinstance(p, dict) else "User",
                        "wagerAmount": float(p.get("wagerAmount", 0) or 0) if isinstance(p, dict) else 0.0,
                    }
                    for p in fallback
                ]

        if simplified:
            record_snapshot("packy", simplified, period_start_dt, period_end_dt, period_key)
        elif payload.get("status") != "ok":
            status_code = 500 if payload.get("error") == "missing_packy_api_key" else int(payload.get("statusCode") or 502)
            return jsonify(payload), status_code

        end_ms = int(period_end_dt.timestamp() * 1000)
        start_ms = int(period_start_dt.timestamp() * 1000)
        ended = False
        if isinstance(picked, dict):
            ended = (picked.get("time_status") or "").strip().lower() == "ended"
        ended = ended or (datetime.utcnow().timestamp() * 1000 >= end_ms)
        data_hash = _hash_response(period_key, ended, simplified)
        etag = f'W/"{data_hash}"'
        out = {
            "status": "ok",
            "data": simplified,
            "period": {
                "type": "weekly",
                "periodKey": period_key,
                "startTime": start_ms,
                "endTime": end_ms,
            },
            "ended": ended,
            "stale": stale,
            "data_hash": data_hash,
        }
        return _respond(out, etag)

    start_time = request.args.get("startTime")
    end_time = request.args.get("endTime")

    if end_time:
        try:
            end_timestamp = int(end_time) / 1000
            end_datetime = datetime.utcfromtimestamp(end_timestamp)
            with _end_time_lock:
                if _leaderboard_end_time is None or end_datetime < _leaderboard_end_time:
                    _leaderboard_end_time = end_datetime
        except (ValueError, OSError):
            pass

    data = fetch_leaderboard_data(start_time=start_time, end_time=end_time)
    if not isinstance(data, list):
        data = []

    simplified = []
    raw_for_store = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        username = entry.get("username", "")
        weighted = float(entry.get("weightedWagerAmount", entry.get("wagerAmount", 0)) or 0)
        simplified.append(
            {
                "username": mask_username(username),
                "wagerAmount": weighted,
                "weightedWagerAmount": weighted,
            }
        )
        raw_for_store.append({"username": username, "wagerAmount": weighted})

    record_snapshot("shuffle", raw_for_store, period_start_dt, period_end_dt, period_key)

    stale = False
    if not simplified:
        fallback = _latest_snapshot_players("shuffle", period_key)
        if fallback:
            stale = True
            simplified = []
            for p in fallback:
                if not isinstance(p, dict):
                    continue
                username = p.get("username") or "User"
                try:
                    amt = float(p.get("wagerAmount", 0) or 0)
                except (TypeError, ValueError):
                    amt = 0.0
                simplified.append({"username": mask_username(str(username)), "wagerAmount": amt, "weightedWagerAmount": amt})

    ended = is_leaderboard_ended()
    data_hash = _hash_response(period_key, ended, simplified)
    etag = f'W/"{data_hash}"'
    out = {
        "data": simplified,
        "ended": ended,
        "period": {
            "type": "monthly",
            "periodKey": period_key,
            "startTime": int(period_start_dt.timestamp() * 1000),
            "endTime": int(period_end_dt.timestamp() * 1000),
        },
        "stale": stale,
        "data_hash": data_hash,
    }
    return _respond(out, etag)


@app.route("/api/packy/stats", methods=["GET"])
def packy_stats():
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    if not start_date or not end_date:
        return jsonify({"statusCode": 422, "error": "VALIDATION_ERROR", "message": "start_date and end_date are required"}), 422
    if not PACKY_API_KEY:
        return jsonify({"statusCode": 500, "error": "CONFIG_ERROR", "message": "Missing PACKY_API_KEY"}), 500

    url = f"{PACKY_API_BASE}/v1/affiliate/stats"
    try:
        response = SESSION.get(
            url,
            timeout=API_TIMEOUT,
            headers=_packy_headers(),
            params={"start_date": start_date, "end_date": end_date},
        )
        payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        return jsonify(payload), response.status_code if not response.ok else 200
    except requests.RequestException as exc:
        app.logger.error(f"Failed to fetch Packy stats: {exc}", exc_info=True)
        return jsonify({"statusCode": 502, "error": "UPSTREAM_ERROR", "message": "Packy upstream error"}), 502
    except ValueError:
        return jsonify({"statusCode": 502, "error": "UPSTREAM_INVALID_JSON", "message": "Invalid JSON from Packy"}), 502


if __name__ == "__main__":
    port_raw = os.environ.get("PORT") or os.environ.get("SERVER_PORT") or os.environ.get("PTERODACTYL_PORT") or "4636"
    try:
        port = int(port_raw)
    except ValueError:
        port = 4636
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
