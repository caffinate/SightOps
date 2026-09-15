"""The desk as a service: snapshot, model, queue and the Notion client, wired together.

The HTTP server and the CLI both drive this object. Every mutation goes
through here so the local copy, the queue and the payload never disagree.
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config as cfg
from .changes import Change, PendingQueue, QUEUED, SENDABLE
from .model import Desk
from .notion.client import FixtureNotionClient, MCPNotionClient
from .notion.mcp import MCPClient
from .schema import status_property, title_property
from .snapshot import add_row, build_snapshot, load_fixture, load_snapshot, patch_row, save_snapshot
from .values import normalise_id, values_equal


class DeskService:
    def __init__(self, snapshot: dict[str, Any] | None = None, queue_path: Path | str | None = cfg.QUEUE_PATH,
                 snapshot_path: Path | str | None = cfg.SNAPSHOT_PATH, notion_client: Any = None) -> None:
        self.lock = threading.RLock()
        self.snapshot_path = Path(snapshot_path) if snapshot_path else None
        self.snapshot = snapshot or self._initial_snapshot()
        self.queue = PendingQueue(queue_path)
        self._notion = notion_client
        self.desk = Desk(self.snapshot)
        self._replay_local()

    # ---- setup ------------------------------------------------------------
    def _initial_snapshot(self) -> dict[str, Any]:
        if self.snapshot_path:
            live = load_snapshot(self.snapshot_path)
            if live:
                return live
        return load_fixture()

    def notion(self) -> Any:
        """The write client: the live MCP connection when a credential exists, else the fixture."""
        if self._notion is not None:
            return self._notion
        token = os.environ.get(cfg.NOTION_MCP_TOKEN_ENV)
        provider = None
        if token:
            provider = lambda: token  # noqa: E731
        else:
            try:
                from .notion.oauth import NotionOAuth, TokenStore

                store = TokenStore(cfg.NOTION_AUTH_PATH)
                if store.access_token:
                    provider = NotionOAuth(cfg.NOTION_MCP_URL, store=store).token
            except OSError:
                provider = None
        if provider is None:
            return None
        self._notion = MCPNotionClient(MCPClient(cfg.NOTION_MCP_URL, token_provider=provider))
        return self._notion

    def has_credential(self) -> bool:
        return self.notion() is not None

    def _replay_local(self) -> None:
        """Re-apply queued changes to a freshly loaded snapshot so the local copy shows them."""
        for change in self.queue.changes:
            if change.state in SENDABLE:
                self._apply_locally(change)
        self.rebuild()

    def rebuild(self) -> None:
        self.desk = Desk(self.snapshot)

    # ---- reads ------------------------------------------------------------
    def view(self) -> dict[str, Any]:
        with self.lock:
            data = self.desk.as_dict()
            pending_by_task: dict[str, list[dict[str, Any]]] = {}
            for change in self.queue.pending():
                pending_by_task.setdefault(change.task_id, []).append(change.as_dict())
            for task_id, task in data["tasks"].items():
                task["pending"] = pending_by_task.get(task_id, [])
            data["queue"] = {
                "summary": self.queue.summary(),
                "changes": [c.as_dict() for c in self.queue.changes if c.state in SENDABLE or c.state == "proposed"],
                "history": [c.as_dict() for c in self.queue.changes if c.state not in SENDABLE and c.state != "proposed"][-50:],
            }
            data["write_path"] = {
                "credential": self.has_credential(),
                "mcp_url": cfg.NOTION_MCP_URL,
                "note": ("A Notion MCP credential is on file; Send delivers the payload through notion-update-page and reads every write back."
                         if self.has_credential() else
                         "No Notion MCP credential is on file. The payload is exact and can be sent by any session that holds one; run `python3 -m desk auth` to give the desk its own."),
            }
            return data

    def payload(self) -> list[dict[str, Any]]:
        with self.lock:
            return self.queue.payload(self.desk.schema_for_room, lambda rid: self.desk.room_by_id[rid].name,
                                      lambda rid: self.desk.room_by_id[rid].tasks_source)

    # ---- the one write path -----------------------------------------------
    def change(self, task_id: str, prop: str, to_value: Any, by: str, note: str = "") -> Change:
        with self.lock:
            task_id = normalise_id(task_id) or task_id
            task = self.desk.tasks.get(task_id)
            if task is None:
                raise KeyError(f"no task {task_id} on the desk")
            room = self.desk.room_by_id[task.room_id]
            schema = self.desk.schema_for_room(room.id)
            if prop not in schema:
                raise ValueError(f"'{prop}' is not a property of {room.name}'s database")
            spec = schema[prop]
            kind = spec.get("type")
            if kind in ("select", "status") and to_value not in (None, "") and spec.get("options") and to_value not in spec["options"]:
                raise ValueError(f"'{to_value}' is not an option of {prop} in {room.name}; the options are {', '.join(spec['options'])}")
            if kind == "multi_select":
                bad = [v for v in (to_value or []) if spec.get("options") and v not in spec["options"]]
                if bad:
                    raise ValueError(f"{', '.join(bad)} are not options of {prop} in {room.name}")
            if kind in ("person", "people"):
                to_value = [normalise_id(v) for v in (to_value or []) if v]
            if by not in (cfg.AUTHOR_PERSON, cfg.AUTHOR_AGENT):
                raise ValueError(f"author must be {cfg.AUTHOR_PERSON} or {cfg.AUTHOR_AGENT}")
            from_value = task.props.get(prop)
            if values_equal(kind or "text", from_value, to_value):
                raise ValueError(f"{prop} already is {to_value!r}; nothing to change")
            change = Change.new(task_id, room.id, prop, from_value, to_value, by, title=task.title or "(untitled row)", note=note)
            self.queue.add(change)
            if change.state == QUEUED:
                self._apply_locally(change)
                self.rebuild()
            return change

    def create_task(self, room_id: str, title: str, by: str) -> Change:
        """Queue a new row. Creation is a change like any other, attributed and gated."""
        with self.lock:
            room = self.desk.room_by_id[room_id]
            if not room.wired:
                raise ValueError(f"{room.name} has no Tasks database")
            schema = self.desk.schema_for_room(room_id)
            import uuid

            page_id = uuid.uuid4().hex
            url = f"local://{page_id}"
            title_prop = title_property(schema) or "Task"
            change = Change.new(page_id, room_id, title_prop, None, title, by, title=title, kind="create")
            self.queue.add(change)
            if change.state == QUEUED:
                add_row(self.snapshot, room.tasks_source, page_id, url, title_prop, title)
                self.rebuild()
            return change

    def _apply_locally(self, change: Change) -> None:
        room = self.desk.room_by_id.get(change.room_id)
        if room is None or not room.wired:
            return
        if change.kind == "create":
            if self.desk.tasks.get(change.task_id) is None:
                schema = self.desk.schema_for_room(room.id)
                add_row(self.snapshot, room.tasks_source, change.task_id, f"local://{change.task_id}", title_property(schema) or "Task", change.to_value)
            return
        prop_type = self.desk.property_type(room.id, change.property) or "text"
        patch_row(self.snapshot, room.tasks_source, change.task_id, change.property, prop_type, change.to_value)

    def _revert_locally(self, change: Change) -> None:
        room = self.desk.room_by_id.get(change.room_id)
        if room is None or not room.wired or change.kind == "create":
            return
        prop_type = self.desk.property_type(room.id, change.property) or "text"
        patch_row(self.snapshot, room.tasks_source, change.task_id, change.property, prop_type, change.from_value)

    # ---- the gate ---------------------------------------------------------
    def accept(self, change_id: str, edited_to: Any = None) -> Change:
        with self.lock:
            change = self.queue.accept(change_id, edited_to)
            self._apply_locally(change)
            self.rebuild()
            return change

    def reject(self, change_id: str, reason: str = "") -> Change:
        with self.lock:
            return self.queue.reject(change_id, reason)

    def respond(self, change_id: str, text: str) -> Change:
        with self.lock:
            return self.queue.respond(change_id, text)

    def ignore(self, change_id: str) -> Change:
        with self.lock:
            return self.queue.ignore(change_id)

    def discard(self, change_id: str) -> Change:
        with self.lock:
            change = self.queue.get(change_id)
            if change is None:
                raise KeyError(change_id)
            was_local = change.state in SENDABLE
            change = self.queue.discard(change_id)
            if was_local:
                self._revert_locally(change)
                self.rebuild()
            return change

    # ---- sending and refreshing -------------------------------------------
    def send(self, client: Any = None, verify: bool = True) -> dict[str, Any]:
        with self.lock:
            client = client or self.notion()
            if client is None:
                return {"sent": False, "reason": "no credential", "payload": self.payload()}
            results = self.queue.send(client, self.desk.schema_for_room, lambda rid: self.desk.room_by_id[rid].name,
                                      lambda rid: self.desk.room_by_id[rid].tasks_source, verify=verify)
            return {"sent": True, "results": results, "summary": self.queue.summary()}

    def refresh(self, client: Any = None) -> dict[str, Any]:
        """Rebuild the snapshot from Notion. Falls back to the fixture when offline."""
        with self.lock:
            client = client or self.notion()
            if client is None:
                self.snapshot = load_fixture()
                kind = "fixture"
            else:
                self.snapshot = build_snapshot(client)
                kind = "live"
                if self.snapshot_path:
                    save_snapshot(self.snapshot, self.snapshot_path)
            self.desk = Desk(self.snapshot)
            self._replay_local()
            return {"kind": kind, "captured_at": self.snapshot.get("captured_at"), "rooms": len(self.desk.tasks_by_room), "tasks": len(self.desk.tasks)}

    def now(self) -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fixture_service(tmp_dir: Path | str | None = None) -> DeskService:
    """A service on the fixture with an in-memory or temp-dir queue. Used by tests and demos."""
    snapshot = load_fixture()
    queue_path = Path(tmp_dir) / "queue.json" if tmp_dir else None
    return DeskService(snapshot=snapshot, queue_path=queue_path, snapshot_path=None, notion_client=FixtureNotionClient(snapshot))
