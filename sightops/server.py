#!/usr/bin/env python3
"""Serve the SightOps Kanban PWA. Notion writes go through Notion MCP only."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT_DIR = Path(__file__).resolve().parents[1]
APP_DIR = ROOT_DIR / "app"
HOME = Path.home()
HERMES_HOME = Path(os.environ.get("HERMES_HOME", HOME / ".hermes/profiles/max-ea"))
SCRIPTS_DIR = HERMES_HOME / "scripts"
PROFILE_HOME = HERMES_HOME
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))
def _listen_port(argv: list[str]) -> int:
    if len(argv) > 1 and argv[1].isdigit():
        return int(argv[1])
    return 3000


PORT = _listen_port(sys.argv)
JSON_PATH = PROFILE_HOME / "notion_tasks_latest.json"
USAGE_JSON_PATH = PROFILE_HOME / "ai_usage_latest.json"
USAGE_HTML = APP_DIR / "ai-usage.html"
USAGE_SCRIPT = SCRIPTS_DIR / "ai_usage_monitor.py"
KANBAN_SRC = APP_DIR / "ops-kanban.html"
PYTHON = os.environ.get(
    "HERMES_PYTHON",
    str(Path.home() / ".hermes/hermes-agent/venv/bin/python3.11"),
)
DASHBOARD_SCRIPT = SCRIPTS_DIR / "notion_task_dashboard.py"

from sightops.board import (  # noqa: E402
    build_board,
    column_for_status,
    find_snapshot_task,
    native_status_for_move,
    observed_statuses,
    all_snapshot_tasks,
    patch_snapshot_status,
)

ALLOWED_FILES = {
    "/ops-kanban.html",
    "/notion-dashboard.html",
    "/ai-usage.html",
    "/dashboard.webmanifest",
    "/dashboard-sw.js",
    "/dashboard-icon-192.png",
    "/dashboard-icon-512.png",
    "/favicon.ico",
}

API_GET_PATHS = {"/api/board", "/api/ai-usage"}
API_PATCH_PATHS = {"/api/tasks"}
API_POST_PATHS = {"/api/refresh", "/api/ai-usage/refresh"}


def canonical_request_path(raw_path: str) -> str:
    """Normalize a request path, including mangled markdown links from chat."""
    path = urlsplit(raw_path).path or "/"
    if "%5D(" in path or "](" in path:
        for prefix in ("/ops-kanban.html", "/notion-dashboard.html", "/ai-usage.html"):
            if path.startswith(prefix):
                return prefix
    if path == "/favicon.ico":
        return "/dashboard-icon-192.png"
    return path

_lock = threading.Lock()
_refreshing = False


def _publish_kanban() -> None:
    if KANBAN_SRC.exists():
        shutil.copy2(KANBAN_SRC, HOME / "ops-kanban.html")


def _load_snapshot() -> dict:
    if not JSON_PATH.exists():
        return {
            "generated_at": "",
            "today": "",
            "overdue": [],
            "due_now": [],
            "due_soon": [],
            "no_due": [],
            "jobs": [],
            "total": 0,
        }
    with JSON_PATH.open() as handle:
        return json.load(handle)


def _save_snapshot(data: dict) -> None:
    JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = JSON_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(JSON_PATH)


def _json_response(
    handler: SimpleHTTPRequestHandler,
    payload: dict,
    status: int = 200,
    head_only: bool = False,
) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()
    if not head_only:
        handler.wfile.write(body)


def _read_json_body(handler: SimpleHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    if length > 1_000_000:
        raise ValueError("Request too large")
    raw = handler.rfile.read(length) if length else b"{}"
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _move_task(page_id: str, column_id: str) -> dict:
    with _lock:
        snapshot = _load_snapshot()
        task = find_snapshot_task(snapshot, page_id)
        if not task:
            raise ValueError("Task is not on the local board")
        observed = observed_statuses(all_snapshot_tasks(snapshot))
        native = native_status_for_move(task, column_id, observed)
        if column_for_status(task.get("status")) == column_id and (task.get("status") or "") == native:
            board = build_board(snapshot)
            board["moved"] = {
                "page_id": page_id,
                "status": native,
                "column": column_id,
                "unchanged": True,
            }
            return board
        from notion_mcp_client import profile_client  # Notion MCP, not REST

        client = profile_client()
        client.update_properties(page_id, {"Status": native})
        patch_snapshot_status(snapshot, page_id, native)
        _save_snapshot(snapshot)
        board = build_board(snapshot)
        board["moved"] = {
            "page_id": page_id,
            "status": native,
            "column": column_id,
            "unchanged": False,
        }
        return board


def _refresh_snapshot() -> dict:
    global _refreshing
    with _lock:
        if _refreshing:
            raise RuntimeError("A refresh is already running")
        _refreshing = True
    env = os.environ.copy()
    env.setdefault("HERMES_HOME", str(PROFILE_HOME))
    try:
        result = subprocess.run(
            [PYTHON, str(DASHBOARD_SCRIPT), "--json"],
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
            cwd=str(SCRIPTS_DIR),
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Refresh failed")
        with _lock:
            board = build_board(_load_snapshot())
        board["refresh_log"] = (result.stdout or "")[-1200:]
        return board
    finally:
        _refreshing = False


def _load_usage() -> dict:
    if USAGE_JSON_PATH.exists():
        with USAGE_JSON_PATH.open() as handle:
            return json.load(handle)
    return {"generated_at": "", "headline": "offline", "providers": [], "error": "No snapshot yet"}


def _refresh_usage() -> dict:
    env = os.environ.copy()
    env.setdefault("HERMES_HOME", str(PROFILE_HOME))
    result = subprocess.run(
        [PYTHON, str(USAGE_SCRIPT), "--json"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        cwd=str(SCRIPTS_DIR),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Usage refresh failed")
    if USAGE_JSON_PATH.exists():
        return _load_usage()
    return json.loads(result.stdout)


def _publish_usage() -> None:
    if USAGE_HTML.exists():
        shutil.copy2(USAGE_HTML, HOME / "ai-usage.html")


class DashboardHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), fmt % args))

    def _serve_file(self, head_only: bool = False) -> None:
        path = canonical_request_path(self.path)
        if path != urlsplit(self.path).path and path in ("/ops-kanban.html", "/notion-dashboard.html", "/ai-usage.html"):
            self.send_response(302)
            self.send_header("Location", path)
            self.end_headers()
            return
        if path in ("/", "/index.html", "/kanban"):
            self.send_response(302)
            self.send_header("Location", "/ops-kanban.html")
            self.end_headers()
            return
        if path.startswith("/api/"):
            _json_response(self, {"error": "Not found"}, 404, head_only=head_only)
            return
        if path not in ALLOWED_FILES:
            self.send_error(404, "Not found")
            return
        if path == "/ops-kanban.html":
            _publish_kanban()
        if path == "/ai-usage.html":
            _publish_usage()
        original = self.path
        if path == "/dashboard-icon-192.png" and urlsplit(self.path).path == "/favicon.ico":
            self.path = "/dashboard-icon-192.png"
        try:
            if head_only:
                super().do_HEAD()
            else:
                super().do_GET()
        finally:
            self.path = original

    def _serve_board(self, head_only: bool = False) -> None:
        try:
            with _lock:
                board = build_board(_load_snapshot())
            _json_response(self, board, head_only=head_only)
        except Exception as exc:
            _json_response(self, {"error": str(exc)}, 500, head_only=head_only)

    def _serve_usage(self, head_only: bool = False) -> None:
        try:
            _json_response(self, _load_usage(), head_only=head_only)
        except Exception as exc:
            _json_response(self, {"error": str(exc)}, 500, head_only=head_only)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        path = canonical_request_path(self.path)
        if path == "/api/board":
            self._serve_board()
            return
        if path == "/api/ai-usage":
            self._serve_usage()
            return
        self._serve_file()

    def do_HEAD(self) -> None:
        path = canonical_request_path(self.path)
        if path == "/api/board":
            self._serve_board(head_only=True)
            return
        if path == "/api/ai-usage":
            self._serve_usage(head_only=True)
            return
        self._serve_file(head_only=True)

    def do_PATCH(self) -> None:
        path = canonical_request_path(self.path)
        if path not in API_PATCH_PATHS:
            _json_response(self, {"error": "Not found"}, 404)
            return
        try:
            body = _read_json_body(self)
            page_id = str(body.get("page_id") or "").strip()
            column_id = str(body.get("column") or "").strip()
            if not page_id or not column_id:
                raise ValueError("page_id and column are required")
            board = _move_task(page_id, column_id)
            _json_response(self, board)
        except ValueError as exc:
            _json_response(self, {"error": str(exc)}, 400)
        except Exception as exc:
            _json_response(self, {"error": str(exc)}, 500)

    def do_POST(self) -> None:
        path = canonical_request_path(self.path)
        if path not in API_POST_PATHS:
            _json_response(self, {"error": "Not found"}, 404)
            return
        try:
            if path == "/api/ai-usage/refresh":
                payload = _refresh_usage()
            else:
                payload = _refresh_snapshot()
            _json_response(self, payload)
        except Exception as exc:
            _json_response(self, {"error": str(exc)}, 500)


def main() -> None:
    _publish_kanban()
    _publish_usage()
    os.chdir(HOME)
    ThreadingHTTPServer(("127.0.0.1", PORT), DashboardHandler).serve_forever()


if __name__ == "__main__":
    main()
