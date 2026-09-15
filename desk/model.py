"""Assemble the desk from a snapshot: rooms by area, tasks, findings, people."""
from __future__ import annotations

from datetime import date
from typing import Any

from . import clipboard as cb
from . import config as cfg
from . import dirt
from . import schema as sch
from .people import People
from .registry import Room, RoomConfig, load_rooms, room_config, rooms_by_area
from .tasks import Task, build_tasks, counts, group_tasks, top_level
from .values import page_id_from_url


class Desk:
    """Everything the surface needs, built from one snapshot."""

    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.snapshot = snapshot
        self.people = People(snapshot.get("users") or [])
        self.rooms: list[Room] = load_rooms(snapshot["register"]["rows"])
        self.room_by_id: dict[str, Room] = {r.id: r for r in self.rooms}
        self.configs: dict[str, RoomConfig] = {}
        self.tasks: dict[str, Task] = {}
        self.tasks_by_room: dict[str, dict[str, Task]] = {}
        self.findings: list[dict[str, Any]] = []
        self.today = _capture_date(snapshot.get("captured_at"))
        self._assemble()
        # The Clipboard join: every row, decoded, and the task each one points at.
        self.clipboard_schema, self.clipboard_schema_source = cb.clipboard_schema(snapshot)
        self.clipboard_items = cb.build_items(snapshot, self.clipboard_schema, self.tasks, self.room_by_id)
        self.clipboard_by_id: dict[str, dict[str, Any]] = {i["id"]: i for i in self.clipboard_items}
        self.decisions_by_task: dict[str, list[str]] = {}
        for item in self.clipboard_items:
            if item["task_known"]:
                self.decisions_by_task.setdefault(item["task_id"], []).append(item["id"])

    # ---- assembly ---------------------------------------------------------
    def _assemble(self) -> None:
        sources = self.snapshot.get("sources") or {}
        for room in self.rooms:
            if not room.wired:
                continue
            source = sources.get(room.tasks_source)
            if not source:
                self.findings.append({"rule": "source-missing", "level": "room", "room_id": room.id, "room": room.name, "count": 1, "items": [],
                                      "note": f"Register names Tasks source {room.tasks_source} but the snapshot holds no such database."})
                continue
            config = room_config(room, source["schema"])
            self.configs[room.id] = config
            room_tasks = build_tasks(room, source, config, self.people)
            self.tasks_by_room[room.id] = room_tasks
            self.tasks.update(room_tasks)
        for room in self.rooms:
            if room.id not in self.tasks_by_room:
                continue
            source = sources[room.tasks_source]
            self.findings += dirt.findings_for_room(room, self.tasks_by_room[room.id], source["schema"], self.configs[room.id], self.people, self.rooms, self.today)

    # ---- lookups ----------------------------------------------------------
    def room_for_task(self, task_id: str) -> Room | None:
        task = self.tasks.get(task_id)
        return self.room_by_id.get(task.room_id) if task else None

    def schema_for_room(self, room_id: str) -> dict[str, dict]:
        """A room's database schema. The Clipboard is addressed by its own id, like a room."""
        if room_id == cfg.CLIPBOARD_ID:
            return self.clipboard_schema
        room = self.room_by_id[room_id]
        return self.snapshot["sources"][room.tasks_source]["schema"]

    def property_type(self, room_id: str, prop: str) -> str | None:
        return self.schema_for_room(room_id).get(prop, {}).get("type")

    # ---- views ------------------------------------------------------------
    def room_view(self, room: Room) -> dict[str, Any]:
        view: dict[str, Any] = {
            "id": room.id,
            "url": room.url,
            "name": room.name,
            "area": room.area,
            "register_status": room.status,
            "contract": room.contract,
            "notes": room.notes,
            "notion_home": room.notion_home,
            "clipboard": room.clipboard,
            "clipboard_standard": room.clipboard_standard,
            "tasks_url": room.tasks_url,
            "tasks_source": room.tasks_source,
            "wired": room.wired,
            "clipboard_ids": [i["id"] for i in self.clipboard_items if room.id in i["room_ids"] or i["task_room_id"] == room.id],
        }
        if room.id in self.tasks_by_room:
            source = self.snapshot["sources"][room.tasks_source]
            schema = source["schema"]
            config = self.configs[room.id]
            room_tasks = self.tasks_by_room[room.id]
            tops = sorted(top_level(room_tasks), key=lambda t: (t.created or ""))
            parent_prop, children_prop = sch.self_relations(schema, room.tasks_source)
            view.update({
                "database_title": source.get("title"),
                "config": config.as_dict(),
                "register_config": {
                    "grouping_field": room.grouping_field,
                    "status_vocabulary": room.status_vocabulary,
                    "owner_field": room.owner_field,
                },
                "schema": schema,
                "title_property": sch.title_property(schema),
                "status_property": sch.status_property(schema),
                "parent_property": parent_prop,
                "children_property": children_prop,
                "counts": counts(room_tasks),
                "groups": group_tasks(tops, config, schema),
                "top_task_ids": [t.id for t in tops],
                "task_ids": list(room_tasks.keys()),
                "findings": [f for f in self.findings if f["room_id"] == room.id],
            })
        return view

    def clipboard_view(self) -> dict[str, Any]:
        items = [{k: v for k, v in item.items() if k != "props"} for item in self.clipboard_items]
        attached = [i for i in items if i["task_known"]]
        dangling = [i["id"] for i in items if i["task_url"] and not i["task_known"]]
        if not items:
            note = "The Clipboard is empty."
        elif not attached:
            note = "No Clipboard row points at a task yet. A decision at the gate is filed as one, and a room files one by putting the task's address in the Task property."
        else:
            note = f"{len(attached)} of {len(items)} rows point at a task the desk holds."
        if dangling:
            note += f" {len(dangling)} point at a row the desk does not hold."
        if self.clipboard_schema_source == "known":
            note += " The Clipboard's schema here is the desk's own record of 15 September; refresh the snapshot to read it live."
        return {
            "title": (self.snapshot.get("clipboard") or {}).get("title") or cb.TITLE,
            "data_source_id": cfg.CLIPBOARD_ID,
            "open": sum(1 for i in items if i["state"] == cb.OPEN),
            "adjudicated": sum(1 for i in items if i["state"] == cb.ADJUDICATED),
            "returned": sum(1 for i in items if i["state"] == cb.RETURNED),
            "dropped": sum(1 for i in items if i["state"] == cb.DROPPED),
            "attached": len(attached),
            "open_attached": sum(1 for i in attached if i["state"] == cb.OPEN),
            "dangling": dangling,
            "by_task": {k: list(v) for k, v in self.decisions_by_task.items()},
            "items": items,
            "schema": self.clipboard_schema,
            "schema_source": self.clipboard_schema_source,
            "note": note,
        }

    def _task_dicts(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for task in self.tasks.values():
            data = task.as_dict()
            data["decision_ids"] = list(self.decisions_by_task.get(task.id, []))
            out[task.id] = data
        return out

    def as_dict(self) -> dict[str, Any]:
        area_order = list((self.snapshot["register"]["schema"].get("Area") or {}).get("options") or [])
        areas = []
        for area, rooms in rooms_by_area(self.rooms, area_order):
            areas.append({"name": area, "room_ids": [r.id for r in rooms]})
        return {
            "source": {
                "kind": self.snapshot.get("source_kind"),
                "captured_at": self.snapshot.get("captured_at"),
                "captured_via": self.snapshot.get("captured_via"),
                "today": self.today.isoformat(),
            },
            "areas": areas,
            "rooms": {r.id: self.room_view(r) for r in self.rooms},
            "tasks": self._task_dicts(),
            "people": self.people.options(),
            "unresolved_people": sorted(self.people.unresolved.keys()),
            "findings": self.findings,
            "clipboard": self.clipboard_view(),
        }


def _capture_date(value: str | None) -> date:
    if value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            pass
    return date.today()


def _list(value: Any) -> list:
    from .values import parse_list

    return parse_list(value)
