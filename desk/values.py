"""Codecs for the value shapes Notion MCP uses.

SQL rows (query_data_sources) carry people, relations and multi-selects as
JSON-encoded strings, dates as three expanded columns, and checkboxes as
__YES__ / __NO__. update_page takes lists for people, relations and
multi-selects, the same three date keys, and null to clear a value.
"""
from __future__ import annotations

import json
import re
from typing import Any

_HEX32_TAIL = re.compile(r"([0-9a-fA-F]{32})$")
_HEX32_ANY = re.compile(r"[0-9a-fA-F]{32}")

TEXTUAL = {"title", "text", "rich_text", "url", "email", "phone_number", "select", "status"}
LISTY = {"person", "people", "relation", "multi_select"}


def page_id_from_url(url: str | None) -> str | None:
    """Return the 32-hex page id carried by a Notion URL, or None."""
    if not url:
        return None
    tail = url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1].replace("-", "")
    match = _HEX32_TAIL.search(tail)
    if match:
        return match.group(1).lower()
    compact = url.replace("-", "")
    found = _HEX32_ANY.findall(compact)
    return found[-1].lower() if found else None


def normalise_id(value: str | None) -> str | None:
    """A page or user id as 32 lowercase hex characters, whatever the input."""
    if not value:
        return None
    raw = value.strip()
    if raw.startswith("user://"):
        raw = raw[len("user://"):]
    if raw.startswith("http"):
        return page_id_from_url(raw)
    compact = raw.replace("-", "").lower()
    return compact if re.fullmatch(r"[0-9a-f]{32}", compact) else raw.lower()


def dashed(id32: str | None) -> str | None:
    if not id32 or len(id32) != 32:
        return id32
    return f"{id32[:8]}-{id32[8:12]}-{id32[12:16]}-{id32[16:20]}-{id32[20:]}"


def parse_list(value: Any) -> list:
    """A JSON-encoded list from a SQL row, a real list, or a scalar, as a list."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str) and value.lstrip().startswith("["):
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        return list(loaded) if isinstance(loaded, list) else [loaded]
    return [value]


def person_ids(value: Any) -> list[str]:
    return [normalise_id(v) for v in parse_list(value) if isinstance(v, str)]


def relation_page_ids(value: Any) -> list[str]:
    ids = []
    for item in parse_list(value):
        if isinstance(item, str):
            pid = page_id_from_url(item) if item.startswith("http") else normalise_id(item)
            if pid:
                ids.append(pid)
    return ids


def date_of(row: dict, prop: str) -> dict | None:
    start = row.get(f"date:{prop}:start")
    if not start:
        return None
    return {
        "start": start,
        "end": row.get(f"date:{prop}:end"),
        "is_datetime": bool(row.get(f"date:{prop}:is_datetime")),
    }


def decode(prop_type: str, row: dict, name: str) -> Any:
    """The editable, comparable form of one property on a SQL row."""
    if prop_type == "date":
        return date_of(row, name)
    raw = row.get(name)
    if prop_type in ("person", "people"):
        return person_ids(raw)
    if prop_type == "relation":
        return relation_page_ids(raw)
    if prop_type == "multi_select":
        return [str(v) for v in parse_list(raw)]
    if prop_type == "checkbox":
        return raw == "__YES__" or raw is True
    if prop_type == "number":
        return raw
    return None if raw in (None, "") else str(raw)


def to_row_value(prop_type: str, value: Any) -> dict[str, Any] | Any:
    """The SQL-row shape of a decoded value, so a local snapshot can be patched.

    Dates return a dict of the three expanded columns; everything else returns
    the single cell value.
    """
    if prop_type == "date":
        return _date_columns("", value, prefix=False)
    if prop_type in ("person", "people"):
        ids = [normalise_id(v) for v in parse_list(value)]
        return json.dumps([f"user://{dashed(i)}" for i in ids if i]) if ids else None
    if prop_type == "relation":
        ids = [normalise_id(v) for v in parse_list(value)]
        return json.dumps([f"https://app.notion.com/{i}" for i in ids if i]) if ids else None
    if prop_type == "multi_select":
        names = [str(v) for v in parse_list(value)]
        return json.dumps(names) if names else None
    if prop_type == "checkbox":
        return "__YES__" if value in (True, "__YES__", "true", "yes") else "__NO__"
    if value in (None, ""):
        return None
    return value


def _date_columns(name: str, value: Any, prefix: bool = True) -> dict[str, Any]:
    keys = (f"date:{name}:start", f"date:{name}:end", f"date:{name}:is_datetime") if prefix else ("start", "end", "is_datetime")
    if value in (None, ""):
        return {keys[0]: None, keys[1]: None, keys[2]: None}
    if isinstance(value, str):
        is_dt = 1 if ("T" in value or " " in value.strip()) and len(value.strip()) > 10 else 0
        return {keys[0]: value, keys[1]: None, keys[2]: is_dt}
    start = value.get("start")
    end = value.get("end") or None
    is_dt = value.get("is_datetime")
    if is_dt is None:
        is_dt = 1 if start and ("T" in start or " " in start.strip()) and len(start.strip()) > 10 else 0
    return {keys[0]: start or None, keys[1]: end, keys[2]: int(bool(is_dt))}


def encode_property(name: str, prop_type: str, value: Any) -> dict[str, Any]:
    """The fragment of an update_page `properties` map that sets one property.

    Uses the raw value as given: a select or status goes through as its own
    option name, never a normalised one.
    """
    key = f"userDefined:{name}" if name.lower() in ("id", "url") else name
    if prop_type == "date":
        return _date_columns(name, value)
    if prop_type in ("person", "people"):
        ids = [dashed(normalise_id(v)) for v in parse_list(value)]
        return {key: [i for i in ids if i] or None}
    if prop_type == "relation":
        ids = [normalise_id(v) for v in parse_list(value)]
        return {key: [f"https://www.notion.so/{i}" for i in ids if i] or None}
    if prop_type == "multi_select":
        names = [str(v) for v in parse_list(value)]
        return {key: names or None}
    if prop_type == "checkbox":
        return {key: "__YES__" if value in (True, "__YES__", "true", "yes") else "__NO__"}
    if prop_type == "number":
        if value in (None, ""):
            return {key: None}
        return {key: float(value) if "." in str(value) else int(value)}
    if value in (None, ""):
        return {key: None}
    return {key: str(value)}


def values_equal(prop_type: str, a: Any, b: Any) -> bool:
    """Compare two decoded values the way a read-back should."""
    if prop_type in LISTY:
        return sorted(normalise_id(x) or str(x) for x in parse_list(a)) == sorted(normalise_id(x) or str(x) for x in parse_list(b))
    if prop_type == "date":
        da = _date_columns("", a, prefix=False)
        db = _date_columns("", b, prefix=False)
        return (da["start"], da["end"]) == (db["start"], db["end"])
    if prop_type == "checkbox":
        return (a in (True, "__YES__")) == (b in (True, "__YES__"))
    return (None if a in (None, "") else str(a)) == (None if b in (None, "") else str(b))
