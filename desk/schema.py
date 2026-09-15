"""Per-database schema handling.

Nine databases, nine schemas. The harness never assumes a column exists; it
reads the schema and lets the Room Register say which properties matter.
"""
from __future__ import annotations

from typing import Any

STATUS_GROUP_ORDER = ("to_do", "in_progress", "current", "future", "complete")

# Fallbacks only. The Room Register carries the real per-room configuration.
GROUPING_PREFERENCE = ("Category", "Phase", "Wave", "Build Area", "Workstream", "Project", "Time Box", "Stream", "Section/Column")
OWNER_PREFERENCE = ("Owner", "Assignee", "Owners", "Person")
GROUPABLE_TYPES = {"select", "multi_select", "relation", "status"}


def reduce_schema(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Reduce a Notion data-source-state schema to what the harness needs.

    Accepts either the live fetch shape (options as objects with name and
    colour, status options nested in groups, relations with a dataSourceUrl)
    or an already reduced schema, and returns the reduced shape either way.
    """
    reduced: dict[str, dict[str, Any]] = {}
    for name, prop in (raw or {}).items():
        kind = prop.get("type")
        spec: dict[str, Any] = {"type": kind}
        if kind == "status":
            groups_raw = prop.get("groups") or {}
            groups = {g: [_option_name(o) for o in opts] for g, opts in groups_raw.items()}
            ordered: list[str] = []
            for g in STATUS_GROUP_ORDER:
                ordered.extend(n for n in groups.get(g, []) if n not in ordered)
            for g, names in groups.items():
                ordered.extend(n for n in names if n not in ordered)
            if not ordered and prop.get("options"):
                ordered = [_option_name(o) for o in prop["options"]]
            spec["options"] = ordered
            spec["groups"] = {g: names for g, names in groups.items() if names}
        elif kind in ("select", "multi_select"):
            spec["options"] = [_option_name(o) for o in prop.get("options") or []]
        elif kind == "relation":
            target = prop.get("target") or (prop.get("dataSourceUrl") or "").replace("collection://", "")
            spec["target"] = target or None
            if prop.get("limit"):
                spec["limit"] = prop["limit"]
        reduced[name] = spec
    return reduced


def _option_name(option: Any) -> str:
    return option["name"] if isinstance(option, dict) else str(option)


def title_property(schema: dict[str, dict]) -> str | None:
    for name, spec in schema.items():
        if spec.get("type") == "title":
            return name
    return None


def status_property(schema: dict[str, dict]) -> str | None:
    """The status-like property: a status type first, else a select called Status."""
    for name, spec in schema.items():
        if spec.get("type") == "status":
            return name
    if schema.get("Status", {}).get("type") == "select":
        return "Status"
    return None


def status_vocabulary(schema: dict[str, dict]) -> list[str]:
    prop = status_property(schema)
    return list(schema[prop].get("options") or []) if prop else []


def status_groups(schema: dict[str, dict]) -> dict[str, list[str]]:
    prop = status_property(schema)
    return dict(schema[prop].get("groups") or {}) if prop else {}


def person_properties(schema: dict[str, dict]) -> list[str]:
    return [name for name, spec in schema.items() if spec.get("type") in ("person", "people")]


def self_relations(schema: dict[str, dict], data_source_id: str) -> tuple[str | None, str | None]:
    """(parent property, children property) for a database with its own hierarchy."""
    own = [(name, spec) for name, spec in schema.items()
           if spec.get("type") == "relation" and (spec.get("target") or "") == data_source_id]
    if not own:
        return None, None
    parent = next((n for n, s in own if s.get("limit") == 1), None)
    if parent is None:
        parent = next((n for n, _ in own if n.lower().startswith("parent")), None)
    children = next((n for n, _ in own if n != parent), None)
    return parent, children


def guess_grouping_field(schema: dict[str, dict]) -> str | None:
    """A fallback only: the first preferred groupable property that actually divides the rows.

    A select with a single option groups nothing, so it loses to a later
    candidate with real choices (Vertical Vision's Project has one option;
    its Time Box has seven).
    """
    fallback = None
    for name in GROUPING_PREFERENCE:
        spec = schema.get(name)
        if not spec or spec.get("type") not in GROUPABLE_TYPES:
            continue
        options = spec.get("options")
        if spec.get("type") == "relation" or options is None or len(options) > 1:
            return name
        fallback = fallback or name
    return fallback


def guess_owner_field(schema: dict[str, dict]) -> str | None:
    for name in OWNER_PREFERENCE:
        if name in schema and schema[name].get("type") in ("person", "people", "select"):
            return name
    return None


def option_order(schema: dict[str, dict], prop: str | None) -> list[str]:
    if not prop or prop not in schema:
        return []
    return list(schema[prop].get("options") or [])
