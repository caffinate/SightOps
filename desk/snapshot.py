"""Snapshots: what the desk read from Notion, live or from the fixture.

A snapshot is a plain dict:
  captured_at, source_kind, users, register {data_source_id, schema, rows},
  clipboard {data_source_id, title, schema, rows}, sources {data_source_id: {title, schema, rows}}
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config as cfg
from .registry import load_rooms, wired_rooms
from .schema import reduce_schema


def load_fixture(directory: Path | str = cfg.FIXTURE_DIR) -> dict[str, Any]:
    base = Path(directory)
    meta = json.loads((base / "meta.json").read_text(encoding="utf-8"))
    users = json.loads((base / "users.json").read_text(encoding="utf-8"))
    register = json.loads((base / "register.json").read_text(encoding="utf-8"))
    clipboard = json.loads((base / "clipboard.json").read_text(encoding="utf-8"))
    sources: dict[str, Any] = {}
    for path in sorted((base / "sources").glob("*.json")):
        source = json.loads(path.read_text(encoding="utf-8"))
        source["schema"] = reduce_schema(source["schema"])
        sources[source["data_source_id"].lower()] = source
    register["schema"] = reduce_schema(register.get("schema") or {})
    clipboard["schema"] = reduce_schema(clipboard.get("schema") or {})
    return {
        "captured_at": meta.get("captured_at"),
        "captured_via": meta.get("captured_via"),
        "source_kind": "fixture",
        "fixture_dir": str(base),
        "users": users.get("people") or [],
        "register": register,
        "clipboard": clipboard,
        "sources": sources,
    }


def load_snapshot(path: Path | str = cfg.SNAPSHOT_PATH) -> dict[str, Any] | None:
    path = Path(path)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    for source in data.get("sources", {}).values():
        source["schema"] = reduce_schema(source.get("schema") or {})
    if data.get("clipboard"):
        data["clipboard"]["schema"] = reduce_schema(data["clipboard"].get("schema") or {})
    data.setdefault("source_kind", "live")
    return data


def save_snapshot(data: dict[str, Any], path: Path | str = cfg.SNAPSHOT_PATH) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return path


def build_snapshot(client: Any, register_id: str = cfg.ROOM_REGISTER_ID, clipboard_id: str = cfg.CLIPBOARD_ID) -> dict[str, Any]:
    """Read the register, the clipboard, the people and every wired database.

    `client` is any object with fetch_schema(ds_id) -> (title, schema),
    query_rows(ds_id) -> list[dict] and list_users() -> list[dict].
    """
    reg_title, reg_schema = client.fetch_schema(register_id)
    register = {"data_source_id": register_id, "title": reg_title, "schema": reduce_schema(reg_schema), "rows": client.query_rows(register_id)}
    cb_title, cb_schema = client.fetch_schema(clipboard_id)
    clipboard = {"data_source_id": clipboard_id, "title": cb_title, "schema": reduce_schema(cb_schema), "rows": client.query_rows(clipboard_id)}
    users = [u for u in client.list_users() if u.get("type", "person") == "person"]
    sources: dict[str, Any] = {}
    for room in wired_rooms(load_rooms(register["rows"])):
        ds_id = room.tasks_source
        title, schema = client.fetch_schema(ds_id)
        sources[ds_id] = {"data_source_id": ds_id, "title": title, "schema": reduce_schema(schema), "rows": client.query_rows(ds_id)}
    return {
        "captured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "captured_via": "Notion MCP through the desk's own connection",
        "source_kind": "live",
        "users": users,
        "register": register,
        "clipboard": clipboard,
        "sources": sources,
    }


def table(snapshot: dict[str, Any], ds_id: str) -> dict[str, Any] | None:
    """The part of a snapshot that holds a data source's rows: a room's database, the Clipboard or the register."""
    key = ds_id.lower()
    if key == cfg.CLIPBOARD_ID:
        return snapshot.get("clipboard")
    if key == cfg.ROOM_REGISTER_ID:
        return snapshot.get("register")
    return snapshot.get("sources", {}).get(key)


def find_row(snapshot: dict[str, Any], ds_id: str, page_id: str) -> dict[str, Any] | None:
    from .values import page_id_from_url

    source = table(snapshot, ds_id)
    if not source:
        return None
    for row in source.get("rows") or []:
        if page_id_from_url(row.get("url")) == page_id:
            return row
    return None


def patch_row(snapshot: dict[str, Any], ds_id: str, page_id: str, prop: str, prop_type: str, value: Any) -> bool:
    """Apply a decoded value to the local copy of a row, in the SQL row shape."""
    from .values import to_row_value

    row = find_row(snapshot, ds_id, page_id)
    if row is None:
        return False
    encoded = to_row_value(prop_type, value)
    if prop_type == "date":
        for key, val in encoded.items():
            row[f"date:{prop}:{key}"] = val
    else:
        row[prop] = encoded
    return True


def add_row(snapshot: dict[str, Any], ds_id: str, page_id: str, url: str, title_prop: str, title: str | None) -> dict[str, Any] | None:
    source = table(snapshot, ds_id)
    if not source:
        return None
    row = {"url": url, "createdTime": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"), title_prop: title}
    source.setdefault("rows", []).append(row)
    return row
