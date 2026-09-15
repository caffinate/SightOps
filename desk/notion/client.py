"""Notion clients with one interface: the live MCP one and the offline fixture one.

    fetch_schema(ds_id) -> (title, raw schema)
    query_rows(ds_id) -> list of SQL-shaped rows
    list_users() -> list of {id, name, type, email}
    update_properties(page_id, properties) -> tool text
    page_properties(page_id) -> SQL-shaped property map, for read-back
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any

from .. import config as cfg
from ..values import dashed, page_id_from_url
from .mcp import MCPClient, MCPError

_PROPERTIES = re.compile(r"<properties>\s*(\{.*?\})\s*</properties>", re.DOTALL)
_STATE = re.compile(r"<data-source-state>\s*(\{.*?\})\s*</data-source-state>", re.DOTALL)


def _unwrap(text: str) -> str:
    """Tool output is sometimes a JSON envelope with the markup under 'text'."""
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            loaded = json.loads(stripped)
        except json.JSONDecodeError:
            return text
        if isinstance(loaded, dict) and isinstance(loaded.get("text"), str):
            return loaded["text"]
    return text


def parse_fetch_schema(text: str) -> tuple[str | None, dict[str, Any]]:
    inner = _unwrap(text)
    match = _STATE.search(inner)
    if not match:
        raise MCPError("fetch returned no <data-source-state> block")
    state = json.loads(match.group(1))
    return state.get("name"), state.get("schema") or {}


def parse_fetch_properties(text: str) -> dict[str, Any]:
    inner = _unwrap(text)
    match = _PROPERTIES.search(inner)
    if not match:
        raise MCPError("fetch returned no <properties> block")
    return json.loads(match.group(1))


def parse_results(text: str) -> list[dict[str, Any]]:
    loaded = json.loads(_unwrap(text)) if not text.strip().startswith("[") else json.loads(text)
    if isinstance(loaded, dict):
        return list(loaded.get("results") or [])
    return list(loaded)


class MCPNotionClient:
    """The live client. Only the tools the desk needs, and only through MCP."""

    def __init__(self, mcp: MCPClient) -> None:
        self.mcp = mcp

    def fetch_schema(self, ds_id: str) -> tuple[str | None, dict[str, Any]]:
        result = self.mcp.call_tool("notion-fetch", {"id": f"collection://{dashed(ds_id.replace('-', ''))}"})
        return parse_fetch_schema(result.text)

    def query_rows(self, ds_id: str) -> list[dict[str, Any]]:
        table = f"collection://{dashed(ds_id.replace('-', ''))}"
        result = self.mcp.call_tool("notion-query-data-sources", {"data": {
            "data_source_urls": [table],
            "query": f'SELECT * FROM "{table}" ORDER BY createdTime',
        }})
        return parse_results(result.text)

    def list_users(self) -> list[dict[str, Any]]:
        users: list[dict[str, Any]] = []
        cursor = None
        while True:
            args: dict[str, Any] = {"page_size": 100}
            if cursor:
                args["start_cursor"] = cursor
            loaded = json.loads(self.mcp.call_tool("notion-get-users", args).text)
            users.extend(loaded.get("results") or [])
            cursor = loaded.get("next_cursor") if loaded.get("has_more") else None
            if not cursor:
                break
        return users

    def update_properties(self, page_id: str, properties: dict[str, Any]) -> str:
        result = self.mcp.call_tool("notion-update-page", {
            "page_id": dashed(page_id.replace("-", "")),
            "command": "update_properties",
            "properties": properties,
            "allow_async": False,
        })
        return result.text

    def page_properties(self, page_id: str) -> dict[str, Any]:
        result = self.mcp.call_tool("notion-fetch", {"id": dashed(page_id.replace("-", ""))})
        return parse_fetch_properties(result.text)

    def create_page(self, ds_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        result = self.mcp.call_tool("notion-create-pages", {
            "parent": {"type": "data_source_id", "data_source_id": dashed(ds_id.replace("-", ""))},
            "pages": [{"properties": properties}],
            "allow_async": False,
        })
        text = result.text
        page_id = None
        try:
            loaded = json.loads(_unwrap(text))
            pages = loaded.get("pages") or loaded.get("results") or []
            if pages:
                page_id = page_id_from_url(pages[0].get("url") or pages[0].get("id") or "")
        except (json.JSONDecodeError, AttributeError):
            found = re.findall(r"[0-9a-f]{32}", text.replace("-", ""))
            page_id = found[0] if found else None
        return {"page_id": page_id, "text": text}


class FixtureNotionClient:
    """An offline client over a snapshot. Writes land in the snapshot so a read-back works.

    Also the test double: every update is recorded in `calls`.
    """

    def __init__(self, snapshot: dict[str, Any], fail_on: set[str] | None = None) -> None:
        self.snapshot = snapshot
        self.calls: list[dict[str, Any]] = []
        self.fail_on = fail_on or set()

    def _source(self, ds_id: str) -> dict[str, Any]:
        key = ds_id.lower()
        if key == cfg.ROOM_REGISTER_ID:
            return self.snapshot["register"]
        if key == cfg.CLIPBOARD_ID:
            return self.snapshot["clipboard"]
        source = self.snapshot["sources"].get(key)
        if not source:
            raise MCPError(f"no data source {ds_id} in the snapshot")
        return source

    def fetch_schema(self, ds_id: str) -> tuple[str | None, dict[str, Any]]:
        source = self._source(ds_id)
        return source.get("title"), copy.deepcopy(source.get("schema") or {})

    def query_rows(self, ds_id: str) -> list[dict[str, Any]]:
        return copy.deepcopy(self._source(ds_id).get("rows") or [])

    def list_users(self) -> list[dict[str, Any]]:
        return [dict(u, type=u.get("type", "person")) for u in self.snapshot.get("users") or []]

    def _find(self, page_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        pid = page_id.replace("-", "").lower()
        for source in list(self.snapshot["sources"].values()) + [self.snapshot["register"]]:
            for row in source.get("rows") or []:
                if page_id_from_url(row.get("url")) == pid:
                    return source, row
        raise MCPError(f"page {page_id} not found")

    def update_properties(self, page_id: str, properties: dict[str, Any]) -> str:
        self.calls.append({"page_id": page_id, "properties": copy.deepcopy(properties)})
        if page_id.replace("-", "").lower() in self.fail_on:
            raise MCPError("simulated failure")
        source, row = self._find(page_id)
        schema = source.get("schema") or {}
        for key, value in properties.items():
            name = key[len("userDefined:"):] if key.startswith("userDefined:") else key
            if name.startswith("date:"):
                row[name] = value
                continue
            prop_type = (schema.get(name) or {}).get("type")
            if prop_type in ("person", "people"):
                row[name] = json.dumps([f"user://{dashed(v.replace('user://', '').replace('-', ''))}" for v in value]) if value else None
            elif prop_type == "relation":
                row[name] = json.dumps([f"https://app.notion.com/{page_id_from_url(v) or v}" for v in value]) if value else None
            elif prop_type == "multi_select":
                row[name] = json.dumps(list(value)) if value else None
            elif prop_type == "status" and value is not None and value not in (schema[name].get("options") or []):
                raise MCPError(f"'{value}' is not an option of {name}")
            elif prop_type == "select" and value is not None and schema[name].get("options") and value not in schema[name]["options"]:
                raise MCPError(f"'{value}' is not an option of {name}")
            else:
                row[name] = value
        return json.dumps({"ok": True, "page_id": page_id})

    def page_properties(self, page_id: str) -> dict[str, Any]:
        _, row = self._find(page_id)
        return copy.deepcopy(row)

    def create_page(self, ds_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        import uuid

        self.calls.append({"data_source_id": ds_id, "properties": copy.deepcopy(properties)})
        source = self._source(ds_id)
        page_id = uuid.uuid4().hex
        row: dict[str, Any] = {"url": f"https://app.notion.com/{page_id}", "createdTime": "2026-09-14 21:00:00Z"}
        source.setdefault("rows", []).append(row)
        self.update_properties(page_id, properties)
        return {"page_id": page_id, "text": json.dumps({"pages": [{"url": row["url"]}]})}
