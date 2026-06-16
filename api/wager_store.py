"""
JSON-backed wager snapshot storage for admin history and Packy weekly snapshots.
"""
import json
import os
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore

ET = ZoneInfo("America/New_York")
_store_lock = threading.Lock()

DEFAULT_STORE = {"version": 1, "snapshots": [], "baselines": {}}


def _store_path() -> str:
    env_path = os.environ.get("WAGER_STORE_PATH")
    if env_path:
        return env_path
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    preferred = os.path.join(base, "data", "wagers.json")
    data_dir = os.path.join(base, "data")
    try:
        os.makedirs(data_dir, exist_ok=True)
        return preferred
    except OSError:
        return os.path.join("/tmp", "wagers.json")


def _load() -> Dict[str, Any]:
    path = _store_path()
    if not os.path.isfile(path):
        return deepcopy(DEFAULT_STORE)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return deepcopy(DEFAULT_STORE)
        data.setdefault("version", 1)
        data.setdefault("snapshots", [])
        data.setdefault("baselines", {})
        return data
    except (json.JSONDecodeError, OSError):
        return deepcopy(DEFAULT_STORE)


def _save(data: Dict[str, Any]) -> None:
    path = _store_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _now_et() -> datetime:
    return datetime.now(ET)


def get_month_bounds_et(now: Optional[datetime] = None) -> Tuple[datetime, datetime, str]:
    now = now or _now_et()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if now.month == 12:
        next_month = start.replace(year=now.year + 1, month=1)
    else:
        next_month = start.replace(month=now.month + 1)
    end = next_month - timedelta(seconds=1)
    key = f"{start.year}-{start.month:02d}-shuffle"
    return start, end, key


def get_week_bounds_et(now: Optional[datetime] = None) -> Tuple[datetime, datetime, str]:
    """Monday 00:00 ET through Sunday 00:00 ET (exclusive)."""
    now = now or _now_et()
    monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    sunday_start = monday + timedelta(days=6)
    iso_year, iso_week, _ = monday.isocalendar()
    key = f"{iso_year}-W{iso_week:02d}-packy"
    return monday, sunday_start, key


def get_period_bounds(site: str, now: Optional[datetime] = None) -> Tuple[datetime, datetime, str]:
    if site == "packy":
        return get_week_bounds_et(now)
    return get_month_bounds_et(now)


def _players_map(players: List[Dict[str, Any]]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for p in players:
        name = (p.get("username") or p.get("name") or "").strip()
        if not name:
            continue
        out[name.lower()] = float(p.get("wagerAmount", p.get("wagered", 0)) or 0)
    return out


def _players_list_from_map(wagers: Dict[str, float], display_names: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    display_names = display_names or {}
    rows = []
    for key, amount in wagers.items():
        username = display_names.get(key, key)
        rows.append({"username": username, "wagerAmount": round(amount, 2)})
    rows.sort(key=lambda x: x["wagerAmount"], reverse=True)
    return rows


def ensure_baseline(site: str, period_key: str, players: List[Dict[str, Any]]) -> None:
    baseline_key = f"{site}:{period_key}"
    with _store_lock:
        data = _load()
        if baseline_key not in data["baselines"]:
            data["baselines"][baseline_key] = {
                "capturedAt": datetime.utcnow().isoformat() + "Z",
                "players": _players_map(players),
            }
            _save(data)


def weekly_from_baseline(
    site: str, period_key: str, players: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    baseline_key = f"{site}:{period_key}"
    current = _players_map(players)
    display: Dict[str, str] = {}
    for p in players:
        name = (p.get("username") or p.get("name") or "").strip()
        if name:
            display[name.lower()] = name

    with _store_lock:
        data = _load()
        baseline = data["baselines"].get(baseline_key, {}).get("players", {})

    if not baseline:
        return _players_list_from_map(current, display)

    weekly: Dict[str, float] = {}
    all_keys = set(current.keys()) | set(baseline.keys())
    for key in all_keys:
        weekly[key] = max(0.0, current.get(key, 0.0) - baseline.get(key, 0.0))

    return _players_list_from_map(weekly, display)


def record_snapshot(
    site: str,
    players: List[Dict[str, Any]],
    period_start: datetime,
    period_end: datetime,
    period_key: str,
) -> None:
    entry = {
        "id": str(uuid.uuid4()),
        "site": site,
        "periodKey": period_key,
        "periodStart": period_start.astimezone(ET).isoformat(),
        "periodEnd": period_end.astimezone(ET).isoformat(),
        "capturedAt": datetime.utcnow().isoformat() + "Z",
        "players": [
            {
                "username": p.get("username") or p.get("name") or "User",
                "wagerAmount": float(p.get("wagerAmount", p.get("wagered", 0)) or 0),
            }
            for p in players
        ],
    }
    with _store_lock:
        data = _load()
        data["snapshots"].append(entry)
        if len(data["snapshots"]) > 5000:
            data["snapshots"] = data["snapshots"][-4000:]
        _save(data)


def query_snapshots(
    site: Optional[str] = None,
    username: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    period_key: Optional[str] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    with _store_lock:
        data = _load()
        snapshots = list(data["snapshots"])

    def parse_dt(s: str) -> Optional[datetime]:
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None

    df = parse_dt(date_from) if date_from else None
    dt = parse_dt(date_to) if date_to else None
    user_q = (username or "").strip().lower()
    results: List[Dict[str, Any]] = []

    for snap in reversed(snapshots):
        if site and snap.get("site") != site:
            continue
        if period_key and snap.get("periodKey") != period_key:
            continue
        captured = parse_dt(snap.get("capturedAt", ""))
        if df and captured and captured < df:
            continue
        if dt and captured and captured > dt:
            continue

        players = snap.get("players") or []
        if user_q:
            players = [
                p
                for p in players
                if user_q in (p.get("username") or "").lower()
            ]
            if not players:
                continue
            snap = {**snap, "players": players}

        results.append(snap)
        if len(results) >= limit:
            break

    return results


def list_period_keys(site: Optional[str] = None) -> List[str]:
    with _store_lock:
        data = _load()
        keys = set()
        for snap in data["snapshots"]:
            if site and snap.get("site") != site:
                continue
            if snap.get("periodKey"):
                keys.add(snap["periodKey"])
        for bk in data.get("baselines", {}):
            s, pk = bk.split(":", 1) if ":" in bk else ("", bk)
            if site and s != site:
                continue
            keys.add(pk)
    return sorted(keys, reverse=True)
