"""The Clipboard join: a decision is a Clipboard row that points at a task.

The Clipboard is Nathan's inbox in Notion. A room puts an item outside the
door, he rules on it, and the room reads the ruling back. Build order 5 joins
it to the desk: a row's Task property carries the address of a task page, so
a ruling shows on its task and a decision at the gate can be filed as a row,
through the same queue and payload as every other write.

The pointer is a URL, not a relation, by ruling on 15 September 2026: a
relation targets one database, and the tasks live in nine.
"""
from __future__ import annotations

from typing import Any

from . import config as cfg
from .values import decode, page_id_from_url

ITEM = "Item"
TYPE = "Type"
STATE = "State"
ROOM = "Room"
APPLIES_TO = "Applies to"
APPLIED_BY = "Applied by"
RAISED = "Raised"
RULED = "Ruled"
SOURCE = "Source"
CHOICES = "Choices"
OPTIONS_GIVEN = "Options given"
CALL = "Nathan call"
TASK = "Task"

OPEN = "Open"
ADJUDICATED = "Adjudicated"
RETURNED = "Returned"
DROPPED = "Dropped"
DECISION = "Decision"

TITLE = "The Clipboard"

# The Clipboard's schema as read through Notion MCP on 15 September 2026,
# after the Task column was added. Used only when a snapshot carries no schema
# for the Clipboard (a live snapshot taken before the desk read it); the
# schema in the snapshot always wins.
KNOWN_SCHEMA: dict[str, dict[str, Any]] = {
    ITEM: {"type": "title"},
    TYPE: {"type": "select", "options": [DECISION, "Open question", "Finding", "Deliverable"]},
    STATE: {"type": "select", "options": [OPEN, ADJUDICATED, RETURNED, DROPPED]},
    ROOM: {"type": "relation", "target": cfg.ROOM_REGISTER_ID},
    APPLIES_TO: {"type": "relation", "target": cfg.ROOM_REGISTER_ID},
    APPLIED_BY: {"type": "text"},
    RAISED: {"type": "date"},
    RULED: {"type": "date"},
    SOURCE: {"type": "url"},
    CHOICES: {"type": "text"},
    OPTIONS_GIVEN: {"type": "text"},
    CALL: {"type": "text"},
    TASK: {"type": "url"},
}


def clipboard_schema(snapshot: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], str]:
    """The Clipboard's reduced schema and where it came from: 'snapshot' or 'known'."""
    schema = (snapshot.get("clipboard") or {}).get("schema") or {}
    if schema:
        return schema, "snapshot"
    return {name: dict(spec) for name, spec in KNOWN_SCHEMA.items()}, "known"


def build_items(snapshot: dict[str, Any], schema: dict[str, dict[str, Any]], tasks: dict[str, Any], room_by_id: dict[str, Any]) -> list[dict[str, Any]]:
    """Every Clipboard row, decoded, joined to the task it points at when the desk holds that task."""
    items: list[dict[str, Any]] = []
    for row in (snapshot.get("clipboard") or {}).get("rows") or []:
        item_id = page_id_from_url(row.get("url"))
        if not item_id:
            continue
        props = {name: decode(spec.get("type"), row, name) for name, spec in schema.items()}
        room_ids = list(props.get(ROOM) or [])
        applies = list(props.get(APPLIES_TO) or [])
        task_url = props.get(TASK)
        task_id = page_id_from_url(task_url) if task_url else None
        task = tasks.get(task_id) if task_id else None
        items.append({
            "id": item_id,
            "url": row.get("url"),
            "item": props.get(ITEM),
            "type": props.get(TYPE),
            "state": props.get(STATE),
            "room_ids": room_ids,
            "room_names": [room_by_id[r].name for r in room_ids if r in room_by_id],
            "applies_to": [room_by_id[r].name for r in applies if r in room_by_id],
            "applied_by": props.get(APPLIED_BY),
            "raised": (props.get(RAISED) or {}).get("start"),
            "ruled": (props.get(RULED) or {}).get("start"),
            "choices": props.get(CHOICES),
            "options_given": props.get(OPTIONS_GIVEN),
            "call": props.get(CALL),
            "source": props.get(SOURCE),
            "task_url": task_url,
            "task_id": task_id,
            "task_known": task is not None,
            "task_title": task.title if task else None,
            "task_room_id": task.room_id if task else None,
            "props": props,
        })
    return items


def brief(value: Any) -> str:
    """One value in one line, the way the Item field wants it."""
    if value in (None, "", []):
        return "(empty)"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        start = value.get("start") or ""
        return f"{start} to {value['end']}" if value.get("end") else start
    return str(value)


def decision_properties(task: Any, room: Any, change: Any, ruling: str, decided_at: str) -> dict[str, Any]:
    """The Clipboard row that records a gate decision: decoded values keyed by property name.

    Item states the decision in one line. Options given and Choices carry what
    was put, in the Clipboard's own format, with the agent's proposal flagged
    as its recommendation. Nathan call carries the ruling. Task points at the
    row the decision was about.
    """
    to_text = brief(change.to_value)
    from_text = brief(change.from_value)
    title = task.title or "(untitled row)"
    why = f", because {change.note}" if change.note else ""
    return {
        ITEM: f"{title}: {change.property} → {to_text}",
        TYPE: DECISION,
        STATE: ADJUDICATED,
        ROOM: [room.id],
        TASK: task.url if task.url and not task.url.startswith("local://") else None,
        RAISED: (change.created_at or decided_at)[:10],
        RULED: decided_at[:10],
        OPTIONS_GIVEN: f"The agent proposed {change.property}: {from_text} → {to_text}{why}.",
        CHOICES: "\n".join([
            f"A | accept: set {change.property} to {to_text} | REC",
            f"B | reject: keep {from_text}",
            "C | respond: send the proposal back with a note",
        ]),
        CALL: ruling,
    }
