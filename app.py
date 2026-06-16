import os

from server import app, resolve_port, start_snapshot_scheduler


if __name__ == "__main__":
    port = resolve_port()
    start_snapshot_scheduler()
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
