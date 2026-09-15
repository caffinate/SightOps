"""Known dirt, surfaced and never cleaned.

Each rule returns findings. A finding is a signal for Nathan to rule on, not a
repair the harness makes. Nothing in this module writes anything.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from . import schema as sch
from .normalise import is_open, is_terminal
from .people import People
from .registry import Room, RoomConfig
from .tasks import Task


def _finding(rule: str, room: Room, note: str, items: list[dict[str, Any]] | None = None, level: str = "room") -> dict[str, Any]:
    items = items or []
    return {
        "rule": rule,
        "level": level,
        "room_id": room.id,
        "room": room.name,
        "count": len(items) if items else 1,
        "items": items,
        "note": note,
    }


def _item(task: Task, detail: str = "") -> dict[str, Any]:
    return {"task_id": task.id, "title": task.title or "(untitled row)", "detail": detail}


def parents_done_children_open(room: Room, tasks: dict[str, Task]) -> list[dict[str, Any]]:
    items = []
    for task in tasks.values():
        if not is_terminal(task.state) or not task.child_ids:
            continue
        open_children = [tasks[c] for c in task.child_ids if c in tasks and not is_terminal(tasks[c].state)]
        if open_children:
            summary = ", ".join(f"{c.title or '(untitled)'} ({c.status_raw or 'no status'})" for c in open_children)
            items.append(_item(task, f"{task.status_raw} with {len(open_children)} of {len(task.child_ids)} sub-items open: {summary}"))
    if not items:
        return []
    return [_finding("parent-closed-children-open", room, "Parents are being closed without their children. Each one is a real signal about how this database is used.", items)]


def unresolved_owners(room: Room, tasks: dict[str, Task], people: People, config: RoomConfig) -> list[dict[str, Any]]:
    by_uid: dict[str, list[Task]] = {}
    for task in tasks.values():
        for uid in task.owner_ids:
            if not people.is_resolved(uid):
                by_uid.setdefault(uid, []).append(task)
    findings = []
    for uid, owned in by_uid.items():
        items = [_item(t, f"{config.owner_field} is user {uid[:8]}") for t in owned]
        findings.append(_finding("unresolved-owner", room,
                                 f"User id {uid[:8]} owns {len(owned)} row(s) here and resolves to no workspace person. Shown as 'unresolved user {uid[:8]}', never as a name it might be.",
                                 items))
    return findings


def empty_titles(room: Room, tasks: dict[str, Task]) -> list[dict[str, Any]]:
    items = [_item(t, "row has no title") for t in tasks.values() if not (t.title or "").strip()]
    if not items:
        return []
    return [_finding("empty-title", room, "Rows with no title. They are real and they are in the database.", items)]


def no_status(room: Room, tasks: dict[str, Task]) -> list[dict[str, Any]]:
    items = [_item(t, "no Status value") for t in tasks.values() if t.state == "unset"]
    if not items:
        return []
    return [_finding("no-status", room, "Rows with no Status at all. They are neither open nor done until someone says which.", items)]


def ungrouped_rows(room: Room, tasks: dict[str, Task], config: RoomConfig, schema: dict[str, dict]) -> list[dict[str, Any]]:
    if not config.grouping_field:
        return []
    others = [n for n, s in schema.items() if s.get("type") in ("select", "multi_select") and n not in (config.grouping_field, "Priority", "Status")]
    items = []
    for task in tasks.values():
        if task.group:
            continue
        carried = [f"{n}={task.props.get(n)}" for n in others if task.props.get(n) not in (None, "", [])]
        detail = f"no {config.grouping_field}" + ("; carries " + ", ".join(carried) if carried else "")
        items.append(_item(task, detail))
    if not items:
        return []
    return [_finding("outside-grouping", room, f"Rows with no {config.grouping_field}. Some carry an older shape of this database instead.", items)]


def mentions_other_room(room: Room, tasks: dict[str, Task], rooms: list[Room], config: RoomConfig) -> list[dict[str, Any]]:
    """A row that sits outside this room's grouping and names another wired room by title.

    Both conditions are required. A grouped row that mentions Beacon is a
    Beacon-related task in this room; an ungrouped row titled with another
    room's name is a row that may live in the wrong database.
    """
    names = [(r.name, r) for r in rooms if r.wired and r.id != room.id]
    items = []
    for task in tasks.values():
        if config.grouping_field and task.group:
            continue
        title = (task.title or "").lower()
        for name, other in names:
            if name.lower() in title and name.lower() not in room.name.lower():
                items.append(_item(task, f"no {config.grouping_field or 'group'}, and the title names {other.name}, which has its own database"))
                break
    if not items:
        return []
    return [_finding("names-another-room", room, "Rows outside this room's grouping whose title names another room with its own Tasks database. They may belong there instead.", items)]


def register_status_vs_data(room: Room, tasks: dict[str, Task]) -> list[dict[str, Any]]:
    open_rows = [t for t in tasks.values() if is_open(t.state)]
    if room.status == "Live" and tasks and not open_rows:
        return [_finding("live-but-all-terminal", room, f"Register says Live; every one of {len(tasks)} rows is terminal. Live on the register does not mean live in the data.")]
    if room.status not in ("Live", None) and open_rows:
        return [_finding("open-work-not-live", room, f"Register says {room.status}; {len(open_rows)} rows are open.")]
    return []


def same_due_date_everywhere(room: Room, tasks: dict[str, Task]) -> list[dict[str, Any]]:
    dated = [t.due["start"][:10] for t in tasks.values() if t.due and t.due.get("start")]
    if len(dated) >= 5 and len(set(dated)) == 1 and len(dated) == len(tasks):
        return [_finding("one-due-date-for-all", room, f"All {len(dated)} rows carry the same due date, {dated[0]}. This is a finished or abandoned list, not a working one.")]
    return []


def overdue_open(room: Room, tasks: dict[str, Task], today: date) -> list[dict[str, Any]]:
    items = []
    for task in tasks.values():
        if not is_open(task.state) or not task.due or not task.due.get("start"):
            continue
        try:
            due = date.fromisoformat(task.due["start"][:10])
        except ValueError:
            continue
        if due < today:
            items.append(_item(task, f"{task.status_raw}, due {due.isoformat()}, {(today - due).days} days ago"))
    if not items:
        return []
    return [_finding("open-and-overdue", room, f"Open rows past their due date as of {today.isoformat()}.", items)]


def multiple_person_fields(room: Room, schema: dict[str, dict], config: RoomConfig) -> list[dict[str, Any]]:
    fields = sch.person_properties(schema)
    if len(fields) > 1:
        return [_finding("several-person-fields", room, f"This database has {len(fields)} person properties ({', '.join(fields)}); the harness treats '{config.owner_field}' as the owner and shows the rest as ordinary properties.", level="schema")]
    return []


def config_warnings(room: Room, config: RoomConfig) -> list[dict[str, Any]]:
    return [_finding("config", room, w, level="config") for w in config.warnings]


def relation_grouping(room: Room, config: RoomConfig, schema: dict[str, dict]) -> list[dict[str, Any]]:
    field = config.grouping_field
    if field and schema.get(field, {}).get("type") == "relation":
        return [_finding("grouping-by-relation", room, f"'{field}' is a relation to another database; groups show the linked page id because that database is not loaded.", level="config")]
    return []


def findings_for_room(room: Room, tasks: dict[str, Task], schema: dict[str, dict], config: RoomConfig,
                      people: People, rooms: list[Room], today: date) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    out += config_warnings(room, config)
    out += relation_grouping(room, config, schema)
    out += multiple_person_fields(room, schema, config)
    out += parents_done_children_open(room, tasks)
    out += unresolved_owners(room, tasks, people, config)
    out += empty_titles(room, tasks)
    out += no_status(room, tasks)
    out += ungrouped_rows(room, tasks, config, schema)
    out += mentions_other_room(room, tasks, rooms, config)
    out += register_status_vs_data(room, tasks)
    out += same_due_date_everywhere(room, tasks)
    out += overdue_open(room, tasks, today)
    return out
