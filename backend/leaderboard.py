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
LUXDROP_API_KEY = os.environ.get("LUXDROP_API_KEY") or "64913ffff71d5c9c03a50d365dfe1e483b8e34e7b3f067f22f6e5d3bbe91a1d6"
LUXDROP_CUSTOM_KEY = os.environ.get("LUXDROP_CUSTOM_KEY") or "f15bb7c2-30c6-4e0e-9593-174602ca9fd5"
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
    headers: Dict[str, str] = {}
    if LUXDROP_API_KEY:
        headers["X-API-Key"] = LUXDROP_API_KEY
    if LUXDROP_CUSTOM_KEY:
        headers["X-Custom-Key"] = LUXDROP_CUSTOM_KEY
    return headers


def fetch_luxdrop_leaderboards() -> Dict[str, Any]:
    if not LUXDROP_API_KEY:
        return {"status": "error", "error": "missing_luxdrop_api_key"}

    url = f"{LUXDROP_API_BASE}/external/affiliates"
    try:
        response = SESSION.get(
            url,
            timeout=API_TIMEOUT,
            headers=_luxdrop_headers(),
            params={"codes": "ffrizy"},
        )
        payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        if not response.ok:
            status_code = response.status_code
            if isinstance(payload, dict) and payload:
                return {"status": "error", "error": "luxdrop_upstream_error", "statusCode": status_code, "upstream": payload}
            return {"status": "error", "error": "luxdrop_upstream_error", "statusCode": status_code}
    except requests.RequestException as exc:
        app.logger.error(f"Failed to fetch LUXDROP leaderboard: {exc}", exc_info=True)
        return {"status": "error", "error": "luxdrop_upstream_error"}
    except ValueError:
        return {"status": "error", "error": "luxdrop_invalid_json"}

    leaderboards = (
        payload.get("data", {}).get("leaderboards")
        if isinstance(payload, dict)
        else None
    )
    if payload.get("success") is not True or not isinstance(leaderboards, list):
        return {"status": "error", "error": "luxdrop_unexpected_payload"}

    return {"status": "ok", "data": leaderboards}


def _pick_luxdrop_leaderboard(leaderboards: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
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


def _extract_luxdrop_entries(lb: Any) -> List[Dict[str, Any]]:
    if not isinstance(lb, dict):
        return []
    candidates = [
        lb.get("entries"),
        lb.get("data", {}).get("entries") if isinstance(lb.get("data"), dict) else None,
        lb.get("leaderboard", {}).get("entries") if isinstance(lb.get("leaderboard"), dict) else None,
        lb.get("result", {}).get("entries") if isinstance(lb.get("result"), dict) else None,
    ]
    for c in candidates:
        if isinstance(c, list):
            return [e for e in c if isinstance(e, dict)]
    return []


def _pick_luxdrop_entries(leaderboards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not leaderboards:
        return []
    for desired in ("active", "upcoming", "ended"):
        for lb in leaderboards:
            if not isinstance(lb, dict):
                continue
            if (lb.get("time_status") or "").strip().lower() != desired:
                continue
            entries = _extract_luxdrop_entries(lb)
            if entries:
                return entries
    for lb in leaderboards:
        entries = _extract_luxdrop_entries(lb)
        if entries:
            return entries
    picked = _pick_luxdrop_leaderboard(leaderboards)
    return _extract_luxdrop_entries(picked)


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
    payload = fetch_luxdrop_leaderboards()
    if payload.get("status") != "ok":
        return

    entries = _pick_luxdrop_entries(payload.get("data", []))
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
        payload = fetch_luxdrop_leaderboards()
        leaderboards = payload.get("data", []) if payload.get("status") == "ok" else []
        entries = _pick_luxdrop_entries(leaderboards) if isinstance(leaderboards, list) else []
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
        ended = False
        for lb in leaderboards if isinstance(leaderboards, list) else []:
            if not isinstance(lb, dict):
                continue
            if _extract_luxdrop_entries(lb):
                ended = (lb.get("time_status") or "").strip().lower() == "ended"
                break
        ended = ended or (datetime.utcnow().timestamp() * 1000 >= end_ms)
        data_hash = _hash_response(period_key, ended, simplified)
        etag = f'W/"{data_hash}"'
        out = {
            "status": "ok",
            "site": "luxdrop",
            "data": (simplified[:MAX_LEADERBOARD_PLAYERS] if len(simplified) > MAX_LEADERBOARD_PLAYERS else simplified),
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
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    if not start_date or not end_date:
        return jsonify({"statusCode": 422, "error": "VALIDATION_ERROR", "message": "start_date and end_date are required"}), 422
    if not LUXDROP_API_KEY:
        return jsonify({"statusCode": 500, "error": "CONFIG_ERROR", "message": "Missing LUXDROP_API_KEY"}), 500

    url = f"{LUXDROP_API_BASE}/external/affiliates"
    try:
        response = SESSION.get(
            url,
            timeout=API_TIMEOUT,
            headers=_luxdrop_headers(),
            params={"start_date": start_date, "end_date": end_date},
        )
        payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        return jsonify(payload), response.status_code if not response.ok else 200
    except requests.RequestException as exc:
        app.logger.error(f"Failed to fetch LUXDROP stats: {exc}", exc_info=True)
        return jsonify({"statusCode": 502, "error": "UPSTREAM_ERROR", "message": "LUXDROP upstream error"}), 502
    except ValueError:
        return jsonify({"statusCode": 502, "error": "UPSTREAM_INVALID_JSON", "message": "Invalid JSON from LUXDROP"}), 502


if __name__ == "__main__":
    port_raw = os.environ.get("PORT") or os.environ.get("SERVER_PORT") or os.environ.get("PTERODACTYL_PORT") or "4636"
    try:
        port = int(port_raw)
    except ValueError:
        port = 4636
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
