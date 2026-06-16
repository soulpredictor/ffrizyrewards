"""
Local development server — leaderboard + admin APIs with JSON wager storage.

  set ADMIN_PASSWORD=your-secret
  python server.py

Open index.html via a static server, or: npx serve . and visit /admin.html
"""
import os
import sys
import threading
import time
from pathlib import Path

from flask import Flask, make_response, request

ROOT = Path(__file__).resolve().parent
API_DIR = ROOT / "api"
sys.path.insert(0, str(API_DIR))

from leaderboard import (  # noqa: E402
    app as leaderboard_app,
    capture_shuffle_snapshot,
    capture_packy_snapshot,
)
from admin import app as admin_app  # noqa: E402

app = Flask(__name__)

def resolve_port(default: int = 4636) -> int:
    for key in ("PORT", "SERVER_PORT", "PTERODACTYL_PORT"):
        val = os.environ.get(key)
        if val:
            try:
                return int(val)
            except ValueError:
                continue
    return int(default)


def _add_cors_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
    requested_headers = request.headers.get("Access-Control-Request-Headers")
    resp.headers["Access-Control-Allow-Headers"] = requested_headers or "Authorization, Content-Type, X-Requested-With, Cache-Control, Pragma"
    resp.headers["Access-Control-Max-Age"] = "86400"
    resp.headers["Vary"] = "Origin, Access-Control-Request-Headers"
    return resp


@app.before_request
def _cors_preflight():
    if request.method != "OPTIONS":
        return None
    resp = make_response("", 204)
    return _add_cors_headers(resp)


@app.after_request
def _cors_after(resp):
    return _add_cors_headers(resp)


def _mount(source_app, prefix=""):
    for rule in source_app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        view = source_app.view_functions[rule.endpoint]
        app.add_url_rule(
            rule.rule,
            endpoint=f"{prefix}{rule.endpoint}",
            view_func=view,
            methods=rule.methods,
        )


_mount(leaderboard_app, "lb_")
_mount(admin_app, "adm_")

_snapshot_thread = None
_snapshot_lock = threading.Lock()


def start_snapshot_scheduler() -> None:
    global _snapshot_thread

    enabled = (os.environ.get("SNAPSHOT_SCHEDULER", "1") or "1").strip().lower()
    if enabled in ("0", "false", "no", "off"):
        return

    try:
        interval_seconds = float(os.environ.get("SNAPSHOT_INTERVAL_SECONDS", "60"))
    except ValueError:
        interval_seconds = 60.0
    interval_seconds = max(5.0, interval_seconds)

    with _snapshot_lock:
        if _snapshot_thread and _snapshot_thread.is_alive():
            return

        def _loop():
            time.sleep(1.0)
            while True:
                try:
                    capture_shuffle_snapshot()
                except Exception:
                    app.logger.exception("Shuffle snapshot capture failed")
                try:
                    capture_packy_snapshot()
                except Exception:
                    app.logger.exception("Packy snapshot capture failed")
                time.sleep(interval_seconds)

        _snapshot_thread = threading.Thread(target=_loop, daemon=True, name="snapshot-scheduler")
        _snapshot_thread.start()


if __name__ == "__main__":
    port = resolve_port()
    start_snapshot_scheduler()
    print(f"API: http://127.0.0.1:{port}/api/leaderboard")
    print(f"Packy stats: http://127.0.0.1:{port}/api/packy/stats?start_date=...&end_date=...")
    print(f"Admin API: http://127.0.0.1:{port}/api/admin/login")
    print(f"Admin UI: open admin.html (use any static server for assets)")
    print("Set ADMIN_PASSWORD in the environment (default: ffrizy-admin-change-me)")
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
