import os
import threading
import hashlib
import json
import heapq
import time
from datetime import datetime
from typing import Optional, List, Any, Dict, Tuple

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
LUXDROP_API_BASE = (os.environ.get("LUXDROP_API_BASE") or "https://api.luxdrop.com").rstrip("/")
LUXDROP_API_KEY = os.environ.get("LUXDROP_API_KEY") or "6b0b6994369fd4092fee9e7ea9dc9c05d3caa7aecb8342bd8f710132ccbca5a7"
LUXDROP_AFFILIATE_CODE = os.environ.get("LUXDROP_AFFILIATE_CODE") or "ffrizy"
API_TIMEOUT = float(os.environ.get("SHUFFLE_STATS_TIMEOUT", "5"))
SESSION = requests.Session()

app = Flask(__name__)
app.config["JSONIFY_PRETTYPRINT_REGULAR"] = False

_leaderboard_end_time: Optional[datetime] = None
_end_time_lock = threading.Lock()

_cache_lock = threading.Lock()
_cache: Dict[str, Tuple[float, Dict[str, Any], str]] = {}
try:
    CACHE_TTL_SECONDS = max(1.0, float(os.environ.get("LEADERBOARD_CACHE_TTL", "12")))
except ValueError:
    CACHE_TTL_SECONDS = 12.0
try:
    MAX_LEADERBOARD_PLAYERS = max(10, int(os.environ.get("MAX_LEADERBOARD_PLAYERS", "100")))
except ValueError:
    MAX_LEADERBOARD_PLAYERS = 100


def mask_username(username: str) -> str:
    if not username or len(username) <= 4:
        if len(username) <= 1:
            return username
        return username[0] + "*" * (len(username) - 1)
    return username[:3] + "*" * (len(username) - 4) + username[-1]


def _coerce_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _top_shuffle_players(raw: Any, limit: int) -> List[Dict[str, Any]]:
    heap: List[Tuple[float, int, str]] = []
    seq = 0
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        username = str(entry.get("username") or "")
        amount = _coerce_float(entry.get("weightedWagerAmount", entry.get("wagerAmount", 0)))
        seq += 1
        item = (amount, seq, username)
        if len(heap) < limit:
            heapq.heappush(heap, item)
            continue
        if heap and amount > heap[0][0]:
            heapq.heapreplace(heap, item)

    heap.sort(key=lambda x: (-x[0], x[1]))
    return [{"username": username, "wagerAmount": amount} for amount, _, username in heap]


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


def _luxdrop_headers() -> Dict[str, str]:
    """Authorization header for LUXDROP API (x-api-key as documented)."""
    if LUXDROP_API_KEY:
        return {"x-api-key": LUXDROP_API_KEY}
    return {}


def fetch_luxdrop_leaderboards(start_date: Optional[str] = None, end_date: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetch affiliate leaderboard data from the LUXDROP API.

    GET https://api.luxdrop.com/external/affiliates
    Required param : codes  (comma-separated affiliate codes)
    Optional params: startDate, endDate  (YYYY-MM-DD)
    Header         : x-api-key: <key>

    The response is a JSON object (or list) containing per-user wager data
    for the requested affiliate code(s).  We normalise it into the internal
    {username, wagerAmount} list used by the rest of the system.
    """
    if not LUXDROP_API_KEY:
        return {"status": "error", "error": "missing_luxdrop_api_key"}

    url = f"{LUXDROP_API_BASE}/external/affiliates"
    params: Dict[str, str] = {"codes": LUXDROP_AFFILIATE_CODE}
    if start_date:
        params["startDate"] = start_date
    if end_date:
        params["endDate"] = end_date

    try:
        response = SESSION.get(
            url,
            timeout=API_TIMEOUT,
            headers=_luxdrop_headers(),
            params=params,
        )
        ct = response.headers.get("content-type", "")
        try:
            payload = response.json() if "json" in ct or response.text.strip().startswith(("{", "[")) else None
        except ValueError:
            payload = None

        if not response.ok:
            status_code = response.status_code
            if isinstance(payload, (dict, list)) and payload:
                return {"status": "error", "error": "luxdrop_upstream_error", "statusCode": status_code, "upstream": payload}
            return {"status": "error", "error": "luxdrop_upstream_error", "statusCode": status_code}

    except requests.RequestException as exc:
        app.logger.error(f"Failed to fetch LUXDROP leaderboard: {exc}", exc_info=True)
        return {"status": "error", "error": "luxdrop_upstream_error"}

    if payload is None:
        return {"status": "error", "error": "luxdrop_invalid_json"}

    # -------------------------------------------------------------------------
    # Normalise the LUXDROP response into a flat list of {username, wagerAmount}
    # The API returns data for the requested affiliate code.
    # Possible shapes:
    #   Shape A: list of user objects directly
    #      [{"username": "...", "wager_amount": 123.45, ...}, ...]
    #   Shape B: dict with a top-level list key  ("data", "users", "affiliates", etc.)
    #      {"data": [{"username": "...", "wager_amount": 123.45}, ...]}
    #   Shape C: dict keyed by affiliate code, each value holding user list
    #      {"ffrizy": {"users": [{"username": ..., "wager_amount": ...}]}}
    # We try each shape in order.
    # -------------------------------------------------------------------------
    entries = _extract_luxdrop_users(payload)
    if entries is None:
        app.logger.warning(f"LUXDROP unexpected payload shape: {str(payload)[:300]}")
        return {"status": "error", "error": "luxdrop_unexpected_payload"}

    return {"status": "ok", "data": entries}


def _extract_luxdrop_users(payload: Any) -> Optional[List[Dict[str, Any]]]:
    """
    Convert whatever LUXDROP sends into a flat list of raw user dicts.
    Returns None if the payload shape is unrecognised.
    """
    # Shape A — payload is already a list
    if isinstance(payload, list):
        return [e for e in payload if isinstance(e, dict)]

    if not isinstance(payload, dict):
        return None

    # Shape B — look for a list under common keys
    for key in ("data", "users", "affiliates", "leaderboard", "entries", "results"):
        val = payload.get(key)
        if isinstance(val, list):
            flat = []
            for item in val:
                if isinstance(item, dict):
                    # item might itself have a nested users list
                    inner = item.get("users") or item.get("entries")
                    if isinstance(inner, list):
                        flat.extend([e for e in inner if isinstance(e, dict)])
                    else:
                        flat.append(item)
            return flat

    # Shape C — dict keyed by affiliate code
    for code_key, code_val in payload.items():
        if isinstance(code_val, dict):
            for sub in ("users", "entries", "leaderboard", "data"):
                inner = code_val.get(sub)
                if isinstance(inner, list):
                    return [e for e in inner if isinstance(e, dict)]
        if isinstance(code_val, list):
            return [e for e in code_val if isinstance(e, dict)]

    # Fallback — treat the whole dict as one user record
    if any(k in payload for k in ("username", "user", "wager_amount", "wagered")):
        return [payload]

    return None


def _normalise_luxdrop_entry(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Turn a raw LUXDROP user dict into {username, wagerAmount}.
    Handles multiple field-name conventions.
    """
    username = (
        entry.get("username")
        or entry.get("user")
        or entry.get("name")
        or entry.get("affiliate_code")
        or ""
    )
    if not username:
        return None

    # wager amount field names used by various affiliate APIs
    raw_amount = (
        entry.get("wager_amount")
        or entry.get("wagerAmount")
        or entry.get("wagered")
        or entry.get("total_wagered")
        or entry.get("total_wagered_usd")
        or entry.get("totalWageredUsd")
        or 0
    )
    try:
        amount = float(raw_amount or 0)
    except (TypeError, ValueError):
        amount = 0.0

    return {"username": str(username), "wagerAmount": amount}


def capture_shuffle_snapshot() -> None:
    period_start_dt, period_end_dt, period_key = get_period_bounds("shuffle")
    start_ms = int(period_start_dt.timestamp() * 1000)
    end_ms = int(period_end_dt.timestamp() * 1000)

    data = fetch_leaderboard_data(start_time=str(start_ms), end_time=str(end_ms))
    if not isinstance(data, list):
        data = []

    raw_for_store = _top_shuffle_players(data, MAX_LEADERBOARD_PLAYERS)

    if raw_for_store:
        record_snapshot("shuffle", raw_for_store, period_start_dt, period_end_dt, period_key)


def capture_luxdrop_snapshot() -> None:
    period_start_dt, period_end_dt, period_key = get_period_bounds("luxdrop")
    # Pass the current week's date range so LUXDROP returns only this week's data
    start_date = period_start_dt.strftime("%Y-%m-%d")
    end_date = period_end_dt.strftime("%Y-%m-%d")

    payload = fetch_luxdrop_leaderboards(start_date=start_date, end_date=end_date)
    if payload.get("status") != "ok":
        app.logger.warning(f"LUXDROP snapshot skipped: {payload.get('error')}")
        return

    raw_entries = payload.get("data") or []
    simplified: List[Dict[str, Any]] = []
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        normalised = _normalise_luxdrop_entry(entry)
        if normalised:
            simplified.append(normalised)

    simplified.sort(key=lambda x: x["wagerAmount"], reverse=True)
    if simplified:
        record_snapshot("luxdrop", simplified, period_start_dt, period_end_dt, period_key)


@app.route("/api/leaderboard", methods=["GET"])
def leaderboard():
    global _leaderboard_end_time

    site = (request.args.get("site") or "").strip().lower()
    period_start_dt, period_end_dt, period_key = get_period_bounds(
        "luxdrop" if site == "packy" else "shuffle"
    )

    if site == "packy":
        now_ts = time.time()
        cache_key = f"luxdrop:{period_key}"
        with _cache_lock:
            cached = _cache.get(cache_key)
            if cached and cached[0] > now_ts:
                return _respond(cached[1], cached[2])

        stale = False
        # Pass the week's date window to the LUXDROP API
        start_date = period_start_dt.strftime("%Y-%m-%d")
        end_date = period_end_dt.strftime("%Y-%m-%d")
        payload = fetch_luxdrop_leaderboards(start_date=start_date, end_date=end_date)
        raw_entries = payload.get("data") or [] if payload.get("status") == "ok" else []
        simplified: List[Dict[str, Any]] = []
        for entry in raw_entries if isinstance(raw_entries, list) else []:
            if not isinstance(entry, dict):
                continue
            normalised = _normalise_luxdrop_entry(entry)
            if normalised:
                simplified.append(normalised)
        simplified.sort(key=lambda x: x["wagerAmount"], reverse=True)

        if not simplified:
            fallback = _latest_snapshot_players("luxdrop", period_key)
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
            record_snapshot("luxdrop", simplified, period_start_dt, period_end_dt, period_key)
        elif payload.get("status") != "ok":
            status_code = 500 if payload.get("error") == "missing_luxdrop_api_key" else int(payload.get("statusCode") or 502)
            return jsonify(payload), status_code
        else:
            stale = True

        end_ms = int(period_end_dt.timestamp() * 1000)
        start_ms = int(period_start_dt.timestamp() * 1000)
        ended = datetime.utcnow().timestamp() * 1000 >= end_ms
        data_hash = _hash_response(period_key, ended, simplified)
        etag = f'W/"{data_hash}"'
        out = {
            "status": "ok",
            "site": "luxdrop",
            "data": (simplified[:MAX_LEADERBOARD_PLAYERS] if len(simplified) > MAX_LEADERBOARD_PLAYERS else simplified),
            "period": {
                "type": "monthly",
                "periodKey": period_key,
                "startTime": start_ms,
                "endTime": end_ms,
            },
            "ended": ended,
            "stale": stale,
            "data_hash": data_hash,
        }
        with _cache_lock:
            _cache[cache_key] = (now_ts + CACHE_TTL_SECONDS, out, etag)
        return _respond(out, etag)

    start_time = request.args.get("startTime")
    end_time = request.args.get("endTime")
    now_ts = time.time()
    cache_key = f"shuffle:{start_time or ''}:{end_time or ''}"
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and cached[0] > now_ts:
            return _respond(cached[1], cached[2])

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

    raw_for_store = _top_shuffle_players(data, MAX_LEADERBOARD_PLAYERS)
    simplified = [
        {
            "username": mask_username(p.get("username") or ""),
            "wagerAmount": _coerce_float(p.get("wagerAmount", 0)),
            "weightedWagerAmount": _coerce_float(p.get("wagerAmount", 0)),
        }
        for p in raw_for_store
        if isinstance(p, dict)
    ]

    if raw_for_store:
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
        "site": "shuffle",
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
    with _cache_lock:
        _cache[cache_key] = (now_ts + CACHE_TTL_SECONDS, out, etag)
    return _respond(out, etag)


@app.route("/api/luxdrop/stats", methods=["GET"])
def luxdrop_stats():
    """
    Proxy endpoint — forward a date-ranged stats request to the LUXDROP API.
    Accepts: startDate (YYYY-MM-DD), endDate (YYYY-MM-DD), codes (optional).
    """
    start_date = request.args.get("startDate") or request.args.get("start_date")
    end_date = request.args.get("endDate") or request.args.get("end_date")
    codes = request.args.get("codes") or LUXDROP_AFFILIATE_CODE

    if not LUXDROP_API_KEY:
        return jsonify({"statusCode": 500, "error": "CONFIG_ERROR", "message": "Missing LUXDROP_API_KEY"}), 500

    url = f"{LUXDROP_API_BASE}/external/affiliates"
    params: Dict[str, str] = {"codes": codes}
    if start_date:
        params["startDate"] = start_date
    if end_date:
        params["endDate"] = end_date

    try:
        response = SESSION.get(
            url,
            timeout=API_TIMEOUT,
            headers=_luxdrop_headers(),
            params=params,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        return jsonify(payload), response.status_code if not response.ok else 200
    except requests.RequestException as exc:
        app.logger.error(f"Failed to fetch LUXDROP stats: {exc}", exc_info=True)
        return jsonify({"statusCode": 502, "error": "UPSTREAM_ERROR", "message": "LUXDROP upstream error"}), 502


if __name__ == "__main__":
    port_raw = os.environ.get("PORT") or os.environ.get("SERVER_PORT") or os.environ.get("PTERODACTYL_PORT") or "4636"
    try:
        port = int(port_raw)
    except ValueError:
        port = 4636
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
