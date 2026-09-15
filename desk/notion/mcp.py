"""A minimal MCP client over Streamable HTTP, standard library only.

Speaks JSON-RPC 2.0 to a hosted MCP server such as https://mcp.notion.com/mcp:
initialize, the initialized notification, then tools/call. Handles both plain
JSON responses and text/event-stream responses, and carries the session id the
server hands back.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from .. import config as cfg

PROTOCOL_VERSION = "2025-06-18"


class MCPError(Exception):
    def __init__(self, message: str, status: int | None = None, data: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.data = data


class MCPAuthError(MCPError):
    """401 or 403 from the server: the credential is missing, expired or lacks access."""


@dataclass
class ToolResult:
    text: str
    is_error: bool = False
    structured: Any = None
    raw: dict[str, Any] = field(default_factory=dict)

    def json(self) -> Any:
        return json.loads(self.text)


def parse_sse(body: str) -> list[dict[str, Any]]:
    """Every JSON payload carried by a text/event-stream body."""
    messages = []
    data_lines: list[str] = []
    for line in body.splitlines() + [""]:
        if line == "":
            if data_lines:
                try:
                    messages.append(json.loads("\n".join(data_lines)))
                except json.JSONDecodeError:
                    pass
                data_lines = []
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    return messages


class MCPClient:
    def __init__(self, url: str, token_provider: Callable[[], str | None] | None = None, timeout: float = 90.0,
                 client_name: str = "desk-harness", client_version: str = "0.1.0", opener: Any = None) -> None:
        self.url = url
        self.token_provider = token_provider or (lambda: None)
        self.timeout = timeout
        self.client_name = client_name
        self.client_version = client_version
        self.session_id: str | None = None
        self.server_info: dict[str, Any] = {}
        self._next_id = 1
        self._opener = opener or urllib.request.build_opener()

    # ---- transport --------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            "User-Agent": cfg.USER_AGENT,
        }
        token = self.token_provider()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _post(self, message: dict[str, Any]) -> tuple[int, dict[str, str], str]:
        request = urllib.request.Request(self.url, data=json.dumps(message).encode("utf-8"), headers=self._headers(), method="POST")
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                headers = {k.lower(): v for k, v in response.headers.items()}
                return response.status, headers, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace") if exc.fp else ""
            headers = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
            if exc.code in (401, 403):
                raise MCPAuthError(f"Notion MCP answered {exc.code}: {body[:300] or exc.reason}. Run `python3 -m desk auth` to connect the desk, or to reconnect it if its token has lapsed.",
                                   status=exc.code, data=headers.get("www-authenticate")) from exc
            raise MCPError(f"Notion MCP answered {exc.code}: {body[:300] or exc.reason}", status=exc.code) from exc
        except urllib.error.URLError as exc:
            raise MCPError(f"Notion MCP unreachable at {self.url}: {exc.reason}") from exc

    def _request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        message_id = self._next_id
        self._next_id += 1
        status, headers, body = self._post({"jsonrpc": "2.0", "id": message_id, "method": method, "params": params or {}})
        if headers.get("mcp-session-id"):
            self.session_id = headers["mcp-session-id"]
        content_type = headers.get("content-type", "")
        messages = parse_sse(body) if "text/event-stream" in content_type else ([json.loads(body)] if body.strip() else [])
        for message in messages:
            if message.get("id") == message_id:
                if "error" in message:
                    err = message["error"]
                    raise MCPError(f"{method} failed: {err.get('message')}", data=err.get("data"))
                return message.get("result")
        raise MCPError(f"{method}: no response with id {message_id} (status {status})")

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._post({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # ---- protocol ---------------------------------------------------------
    def initialize(self) -> dict[str, Any]:
        result = self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": self.client_name, "version": self.client_version},
        })
        self.server_info = result or {}
        try:
            self._notify("notifications/initialized")
        except MCPError:
            pass
        return self.server_info

    def ensure_session(self) -> None:
        if not self.session_id and not self.server_info:
            self.initialize()

    def list_tools(self) -> list[dict[str, Any]]:
        self.ensure_session()
        return (self._request("tools/list") or {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        self.ensure_session()
        result = self._request("tools/call", {"name": name, "arguments": arguments}) or {}
        texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        tool_result = ToolResult(text="\n".join(texts), is_error=bool(result.get("isError")), structured=result.get("structuredContent"), raw=result)
        if tool_result.is_error:
            raise MCPError(f"{name} returned an error: {tool_result.text[:500]}", data=result)
        return tool_result
