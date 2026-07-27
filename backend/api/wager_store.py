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
_init_lock = threading.Lock()

DEFAULT_STORE = {"version": 1, "snapshots": [], "baselines": {}}
SITES = ("luxdrop", "packy", "shuffle")
_initialized = False


def _project_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    if os.path.isfile(os.path.join(here, "admin.html")):
        return here
    return os.path.dirname(here)


def _data_dir() -> str:
    return os.path.join(_project_root(), "data")


def _legacy_store_path() -> str:
    return os.path.join(_data_dir(), "wagers.json")


def _site_store_path(site: str) -> str:
    return os.path.join(_data_dir(), f"wagers_{site}.json")


def _store_is_empty(data: Dict[str, Any]) -> bool:
    return not (data.get("snapshots") or []) and not (data.get("baselines") or {})


def _canonical_site_str(site: Any) -> str:
    s = (str(site) if site is not None else "").strip().lower()
    if s in ("winovo", "packy", "luxdrop"):
        return "luxdrop"
    return s


def _load_from_path(path: str) -> Dict[str, Any]:
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


def _save_to_path(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def _ensure_initialized() -> None:
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        os.makedirs(_data_dir(), exist_ok=True)

        env_legacy = os.environ.get("WAGER_STORE_PATH")
        env_any_site = any(os.environ.get(f"WAGER_STORE_PATH_{s.upper()}") for s in SITES)
        if not env_legacy or env_any_site:
            legacy_path = _legacy_store_path()
            legacy = _load_from_path(legacy_path) if os.path.isfile(legacy_path) else None

            for site in SITES:
                site_path = _site_store_path(site)
                if not os.path.isfile(site_path):
                    _save_to_path(site_path, deepcopy(DEFAULT_STORE))

                if not legacy:
                    continue

                current = _load_from_path(site_path)
                if not _store_is_empty(current):
                    continue

                legacy_snapshots = legacy.get("snapshots") or []
                migrated_snapshots = [
                    {**s, "site": site}
                    for s in legacy_snapshots
                    if isinstance(s, dict) and _canonical_site_str(s.get("site")) == site
                ]
                legacy_baselines = legacy.get("baselines") or {}
                if site in ("packy", "luxdrop"):
                    migrated_baselines = {
                        ("luxdrop:" + k.split(":", 1)[1] if k.startswith("winovo:") or k.startswith("packy:") else k): v
                        for k, v in legacy_baselines.items()
                        if isinstance(k, str) and _canonical_site_str(k.split(":", 1)[0]) == "luxdrop"
                    }
                else:
                    migrated_baselines = {
                        k: v
                        for k, v in legacy_baselines.items()
                        if isinstance(k, str) and k.startswith(f"{site}:")
                    }

                migrated = {
                    "version": int(legacy.get("version", 1) or 1),
                    "snapshots": migrated_snapshots,
                    "baselines": migrated_baselines,
                }
                if not _store_is_empty(migrated):
                    _save_to_path(site_path, migrated)

        _initialized = True


def _store_path(site: Optional[str] = None) -> str:
    _ensure_initialized()
    site_norm = _canonical_site_str(site)

    env_site = os.environ.get(f"WAGER_STORE_PATH_{site_norm.upper()}") if site_norm else None
    if env_site:
        return env_site

    env_path = os.environ.get("WAGER_STORE_PATH")
    if env_path and site_norm not in SITES:
        return env_path
    if env_path and site_norm in SITES and not os.environ.get(f"WAGER_STORE_PATH_{site_norm.upper()}"):
        return env_path

    if site_norm in SITES:
        return _site_store_path(site_norm)
    return _legacy_store_path()


def _load(site: Optional[str] = None) -> Dict[str, Any]:
    path = _store_path(site)
    return _load_from_path(path)


def _save(site: Optional[str], data: Dict[str, Any]) -> None:
    path = _store_path(site)
    _save_to_path(path, data)


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
    key = f"{iso_year}-W{iso_week:02d}-luxdrop"
    return monday, sunday_start, key


def get_luxdrop_period_bounds(now: Optional[datetime] = None) -> Tuple[datetime, datetime, str]:
    """Auto-rolling monthly LUXDROP campaign: 27th 00:00:00 ET to 26th 23:59:59 ET of next month."""
    now = now or _now_et()
    if now.day >= 27:
        start = now.replace(day=27, hour=0, minute=0, second=0, microsecond=0)
        if now.month == 12:
            end = start.replace(year=now.year + 1, month=1, day=26, hour=23, minute=59, second=59)
        else:
            end = start.replace(month=now.month + 1, day=26, hour=23, minute=59, second=59)
    else:
        end = now.replace(day=26, hour=23, minute=59, second=59, microsecond=0)
        if now.month == 1:
            start = end.replace(year=now.year - 1, month=12, day=27, hour=0, minute=0, second=0)
        else:
            start = end.replace(month=now.month - 1, day=27, hour=0, minute=0, second=0)
    
    key = f"{start.year}-{start.month:02d}-27-to-{end.year}-{end.month:02d}-26-luxdrop"
    return start, end, key


def get_period_bounds(site: str, now: Optional[datetime] = None) -> Tuple[datetime, datetime, str]:
    s = _canonical_site_str(site)
    if s == "luxdrop":
        return get_luxdrop_period_bounds(now)
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
        data = _load(site)
        if baseline_key not in data["baselines"]:
            data["baselines"][baseline_key] = {
                "capturedAt": datetime.utcnow().isoformat() + "Z",
                "players": _players_map(players),
            }
            _save(site, data)


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
        data = _load(site)
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
        data = _load(site)
        data["snapshots"].append(entry)
        if len(data["snapshots"]) > 5000:
            data["snapshots"] = data["snapshots"][-4000:]
        _save(site, data)


def query_snapshots(
    site: Optional[str] = None,
    username: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    period_key: Optional[str] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    with _store_lock:
        site_norm = _canonical_site_str(site) or None
        if site_norm:
            snapshots = list(_load(site_norm)["snapshots"])
            if not snapshots:
                legacy = _load(None)
                snapshots = [
                    s
                    for s in (legacy.get("snapshots") or [])
                    if isinstance(s, dict) and _canonical_site_str(s.get("site")) == site_norm
                ]
        else:
            snapshots = []
            total = 0
            for s in SITES:
                part = _load(s)
                snaps = list(part.get("snapshots") or [])
                snapshots.extend(snaps)
                total += len(snaps)
            if total == 0:
                snapshots = list(_load(None).get("snapshots") or [])

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
        if site_norm and _canonical_site_str(snap.get("site")) != site_norm:
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
        site_norm = _canonical_site_str(site) or None
        keys = set()

        if site_norm:
            data = _load(site_norm)
            for snap in data.get("snapshots") or []:
                if snap.get("periodKey"):
                    keys.add(snap["periodKey"])
            for bk in data.get("baselines", {}):
                _, pk = bk.split(":", 1) if ":" in bk else ("", bk)
                keys.add(pk)
            if not keys:
                data = _load(None)
                for snap in data.get("snapshots") or []:
                    if isinstance(snap, dict) and _canonical_site_str(snap.get("site")) == site_norm:
                        if snap.get("periodKey"):
                            keys.add(snap["periodKey"])
                for bk in data.get("baselines", {}):
                    if not isinstance(bk, str) or ":" not in bk:
                        continue
                    s, pk = bk.split(":", 1)
                    if _canonical_site_str(s) == site_norm:
                        keys.add(pk)
        else:
            for s in SITES:
                data = _load(s)
                for snap in data.get("snapshots") or []:
                    if snap.get("periodKey"):
                        keys.add(snap["periodKey"])
                for bk in data.get("baselines", {}):
                    _, pk = bk.split(":", 1) if ":" in bk else ("", bk)
                    keys.add(pk)

            if not keys:
                data = _load(None)
                for snap in data.get("snapshots") or []:
                    if snap.get("periodKey"):
                        keys.add(snap["periodKey"])
                for bk in data.get("baselines", {}):
                    s, pk = bk.split(":", 1) if ":" in bk else ("", bk)
                    if s:
                        keys.add(pk)
    return sorted(keys, reverse=True)


_ensure_initialized()
