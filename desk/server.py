"""Serve the desk on localhost. The surface talks to this; this talks to Notion MCP."""
from __future__ import annotations

import json
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import config as cfg
from .service import DeskService

STATIC = {"/desk.html": ("desk.html", "text/html; charset=utf-8")}


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class DeskHandler(SimpleHTTPRequestHandler):
    service: DeskService

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), fmt % args))

    # ---- helpers ----------------------------------------------------------
    def _send(self, status: int, payload: Any, head_only: bool = False) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 2_000_000:
            raise ValueError("request too large")
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _static(self, path: str, head_only: bool = False) -> None:
        name, content_type = STATIC[path]
        file_path = cfg.APP_DIR / name
        if not file_path.exists():
            self.send_error(404, "Not found")
            return
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    # ---- routing ----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        self._get(head_only=False)

    def do_HEAD(self) -> None:  # noqa: N802
        self._get(head_only=True)

    def _get(self, head_only: bool) -> None:
        path = urlsplit(self.path).path
        if path in ("/", "/index.html", "/desk"):
            self.send_response(302)
            self.send_header("Location", "/desk.html")
            self.end_headers()
            return
        if path in STATIC:
            self._static(path, head_only)
            return
        try:
            if path == "/api/desk":
                self._send(200, self.service.view(), head_only)
            elif path == "/api/payload":
                self._send(200, {"calls": self.service.payload(), "credential": self.service.has_credential()}, head_only)
            elif path == "/api/findings":
                self._send(200, {"findings": self.service.desk.findings}, head_only)
            elif path == "/api/queue":
                self._send(200, {"summary": self.service.queue.summary(), "changes": [c.as_dict() for c in self.service.queue.changes]}, head_only)
            else:
                self._send(404, {"error": "Not found"}, head_only)
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"error": str(exc)}, head_only)

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        try:
            body = self._body()
            if path == "/api/changes":
                change = self.service.change(str(body.get("task_id") or ""), str(body.get("property") or ""), body.get("to"),
                                             body.get("by") or cfg.AUTHOR_PERSON, str(body.get("note") or ""))
                self._send(201, {"change": change.as_dict(), "task": self.service.desk.tasks[change.task_id].as_dict() if change.task_id in self.service.desk.tasks else None})
            elif path == "/api/tasks":
                change = self.service.create_task(str(body.get("room_id") or ""), str(body.get("title") or ""), body.get("by") or cfg.AUTHOR_PERSON)
                self._send(201, {"change": change.as_dict()})
            elif path.startswith("/api/changes/"):
                parts = path.split("/")
                change_id, verb = parts[3], (parts[4] if len(parts) > 4 else "")
                file = bool(body.get("file", True))
                if verb == "accept":
                    change = self.service.accept(change_id, body.get("to"), file=file)
                elif verb == "reject":
                    change = self.service.reject(change_id, str(body.get("reason") or ""), file=file)
                elif verb == "respond":
                    change = self.service.respond(change_id, str(body.get("text") or ""), file=file)
                elif verb == "ignore":
                    change = self.service.ignore(change_id)
                elif verb == "discard":
                    change = self.service.discard(change_id)
                else:
                    self._send(404, {"error": "Not found"})
                    return
                self._send(200, {"change": change.as_dict()})
            elif path.startswith("/api/clipboard/"):
                parts = path.split("/")
                item_id, verb = parts[3], (parts[4] if len(parts) > 4 else "")
                if verb != "rule":
                    self._send(404, {"error": "Not found"})
                    return
                changes = self.service.rule(item_id, str(body.get("call") or ""), str(body.get("state") or "Adjudicated"), body.get("by") or cfg.AUTHOR_PERSON)
                item = self.service.desk.clipboard_by_id.get(item_id)
                self._send(201, {"changes": [c.as_dict() for c in changes], "item": {k: v for k, v in item.items() if k != "props"} if item else None})
            elif path == "/api/send":
                self._send(200, self.service.send(verify=body.get("verify", True)))
            elif path == "/api/refresh":
                self._send(200, self.service.refresh())
            else:
                self._send(404, {"error": "Not found"})
        except KeyError as exc:
            self._send(404, {"error": f"unknown id {exc}"})
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"error": str(exc)})

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        try:
            if path.startswith("/api/changes/"):
                change = self.service.discard(path.split("/")[3])
                self._send(200, {"change": change.as_dict()})
            else:
                self._send(404, {"error": "Not found"})
        except KeyError as exc:
            self._send(404, {"error": f"unknown id {exc}"})
        except ValueError as exc:
            self._send(400, {"error": str(exc)})


def make_server(service: DeskService, port: int = cfg.DEFAULT_PORT, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    handler = type("BoundDeskHandler", (DeskHandler,), {"service": service})
    return ThreadingHTTPServer((host, port), handler)


def serve(port: int = cfg.DEFAULT_PORT, service: DeskService | None = None) -> None:
    service = service or DeskService()
    server = make_server(service, port)
    kind = service.snapshot.get("source_kind")
    print(f"Desk on http://127.0.0.1:{port}/desk.html  ({kind} snapshot from {service.snapshot.get('captured_at')}; "
          f"{'credential on file' if service.has_credential() else 'no Notion credential'})")
    server.serve_forever()


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else cfg.DEFAULT_PORT)
