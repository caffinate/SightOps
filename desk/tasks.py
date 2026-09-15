"""Tasks read from one room's database: raw values kept, display state derived."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import schema as sch
from .normalise import is_open, normalise
from .people import People
from .registry import Room, RoomConfig
from .values import decode, page_id_from_url


@dataclass
class Task:
    id: str
    url: str | None
    room_id: str
    title: str | None
    status_raw: str | None
    state: str
    group: list[str]
    owner_ids: list[str]
    owner_names: list[str]
    due: dict | None
    priority: str | None
    notes: str | None
    parent_id: str | None
    child_ids: list[str]
    created: str | None
    props: dict[str, Any] = field(default_factory=dict)

    @property
    def is_open(self) -> bool:
        return is_open(self.state)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "room_id": self.room_id,
            "title": self.title,
            "status_raw": self.status_raw,
            "state": self.state,
            "group": list(self.group),
            "owner_ids": list(self.owner_ids),
            "owner_names": list(self.owner_names),
            "due": self.due,
            "priority": self.priority,
            "notes": self.notes,
            "parent_id": self.parent_id,
            "child_ids": list(self.child_ids),
            "created": self.created,
            "props": self.props,
        }


def build_tasks(room: Room, source: dict[str, Any], config: RoomConfig, people: People) -> dict[str, Task]:
    """Every row of a room's database as a Task, keyed by page id."""
    schema = source["schema"]
    ds_id = source["data_source_id"]
    title_prop = sch.title_property(schema) or "Task"
    status_prop = sch.status_property(schema)
    groups = sch.status_groups(schema)
    parent_prop, children_prop = sch.self_relations(schema, ds_id)
    grouping = config.grouping_field
    owner_prop = config.owner_field
    tasks: dict[str, Task] = {}
    for row in source.get("rows") or []:
        page_id = page_id_from_url(row.get("url"))
        if not page_id:
            continue
        props = {name: decode(spec.get("type"), row, name) for name, spec in schema.items()}
        raw_status = props.get(status_prop) if status_prop else None
        group_value = props.get(grouping) if grouping else None
        if isinstance(group_value, list):
            group = [str(g) for g in group_value]
        elif group_value in (None, ""):
            group = []
        else:
            group = [str(group_value)]
        owner_value = props.get(owner_prop) if owner_prop else None
        if owner_prop and schema[owner_prop].get("type") in ("person", "people"):
            owner_ids = list(owner_value or [])
            owner_names = [people.name(uid) or uid for uid in owner_ids]
        elif owner_value not in (None, "", []):
            owner_ids = []
            owner_names = [str(owner_value)]
        else:
            owner_ids, owner_names = [], []
        parent_ids = props.get(parent_prop) if parent_prop else None
        child_ids = props.get(children_prop) if children_prop else None
        tasks[page_id] = Task(
            id=page_id,
            url=row.get("url"),
            room_id=room.id,
            title=props.get(title_prop),
            status_raw=raw_status,
            state=normalise(raw_status, groups),
            group=group,
            owner_ids=owner_ids,
            owner_names=owner_names,
            due=props.get("Due Date") if schema.get("Due Date", {}).get("type") == "date" else None,
            priority=props.get("Priority") if isinstance(props.get("Priority"), str) else None,
            notes=props.get("Notes") if isinstance(props.get("Notes"), str) else None,
            parent_id=(parent_ids[0] if parent_ids else None),
            child_ids=list(child_ids or []),
            created=row.get("createdTime"),
            props=props,
        )
    # A child listed by a parent that never names it back still belongs to it.
    for task in tasks.values():
        for child in task.child_ids:
            if child in tasks and tasks[child].parent_id is None:
                tasks[child].parent_id = task.id
    return tasks


def top_level(tasks: dict[str, Task]) -> list[Task]:
    return [t for t in tasks.values() if not t.parent_id or t.parent_id not in tasks]


def group_tasks(tasks: list[Task], config: RoomConfig, schema: dict[str, dict]) -> list[dict[str, Any]]:
    """Tasks bucketed by the room's grouping field, in that field's option order."""
    field_name = config.grouping_field
    if not field_name:
        return [{"value": None, "label": "All tasks", "task_ids": [t.id for t in tasks]}]
    order = sch.option_order(schema, field_name)
    buckets: dict[str | None, list[str]] = {}
    for task in tasks:
        keys = task.group or [None]
        for key in keys:
            buckets.setdefault(key, []).append(task.id)
    ordered_keys = [k for k in order if k in buckets] + [k for k in buckets if k is not None and k not in order]
    result = [{"value": k, "label": k, "task_ids": buckets[k]} for k in ordered_keys]
    if None in buckets:
        result.append({"value": None, "label": f"No {field_name}", "task_ids": buckets[None]})
    return result


def counts(tasks: dict[str, Task]) -> dict[str, int]:
    out = {"rows": len(tasks), "open": 0, "unset": 0, "done": 0, "cancelled": 0, "unknown": 0}
    for task in tasks.values():
        if task.state in ("unset", "done", "cancelled", "unknown"):
            out[task.state] += 1
        else:
            out["open"] += 1
    return out
