"""Assemble the desk from a snapshot: rooms by area, tasks, findings, people."""
from __future__ import annotations

from datetime import date
from typing import Any

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
        rows = (self.snapshot.get("clipboard") or {}).get("rows") or []
        items = []
        for row in rows:
            room_ids = [page_id_from_url(u) for u in _list(row.get("Room"))]
            applies = [page_id_from_url(u) for u in _list(row.get("Applies to"))]
            items.append({
                "id": page_id_from_url(row.get("url")),
                "item": row.get("Item"),
                "type": row.get("Type"),
                "state": row.get("State"),
                "room_ids": room_ids,
                "room_names": [self.room_by_id[r].name for r in room_ids if r in self.room_by_id],
                "applies_to": [self.room_by_id[r].name for r in applies if r in self.room_by_id],
                "applied_by": row.get("Applied by"),
                "raised": row.get("date:Raised:start"),
                "ruled": row.get("date:Ruled:start"),
                "task_id": None,
            })
        return {
            "open": sum(1 for i in items if i["state"] == "Open"),
            "adjudicated": sum(1 for i in items if i["state"] == "Adjudicated"),
            "items": items,
            "note": "No Clipboard row points at a task yet. The Clipboard to Task relation is build order 5.",
        }

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
            "tasks": {t.id: t.as_dict() for t in self.tasks.values()},
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
