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

import uuid

from . import clipboard as cb
from . import config as cfg
from .changes import Change, PendingQueue, QUEUED, SENDABLE
from .model import Desk
from .notion.client import FixtureNotionClient, MCPNotionClient
from .notion.mcp import MCPClient
from .schema import status_property, title_property
from .snapshot import add_row, build_snapshot, find_row, load_fixture, load_snapshot, patch_row, save_snapshot
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
            for item in data["clipboard"]["items"]:
                item["pending"] = pending_by_task.get(item["id"], [])
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
            return self.queue.payload(self.desk.schema_for_room, self._room_name, self._data_source)

    def _room_name(self, room_id: str) -> str:
        return cb.TITLE if room_id == cfg.CLIPBOARD_ID else self.desk.room_by_id[room_id].name

    def _data_source(self, room_id: str) -> str | None:
        return cfg.CLIPBOARD_ID if room_id == cfg.CLIPBOARD_ID else self.desk.room_by_id[room_id].tasks_source

    def _target(self, row_id: str) -> tuple[str, str, dict[str, dict], dict[str, Any], str]:
        """Where a row lives: room id, room name, schema, current values, title. A Clipboard row is addressed like a task."""
        task = self.desk.tasks.get(row_id)
        if task is not None:
            room = self.desk.room_by_id[task.room_id]
            return room.id, room.name, self.desk.schema_for_room(room.id), task.props, task.title or "(untitled row)"
        item = self.desk.clipboard_by_id.get(row_id)
        if item is not None:
            return cfg.CLIPBOARD_ID, cb.TITLE, self.desk.clipboard_schema, item["props"], item["item"] or "(untitled row)"
        raise KeyError(f"no task or Clipboard row {row_id} on the desk")

    # ---- the one write path -----------------------------------------------
    def change(self, task_id: str, prop: str, to_value: Any, by: str, note: str = "") -> Change:
        with self.lock:
            task_id = normalise_id(task_id) or task_id
            room_id, room_name, schema, current, title = self._target(task_id)
            if prop not in schema:
                raise ValueError(f"'{prop}' is not a property of {room_name}'s database")
            spec = schema[prop]
            kind = spec.get("type")
            if kind in ("select", "status") and to_value not in (None, "") and spec.get("options") and to_value not in spec["options"]:
                raise ValueError(f"'{to_value}' is not an option of {prop} in {room_name}; the options are {', '.join(spec['options'])}")
            if kind == "multi_select":
                bad = [v for v in (to_value or []) if spec.get("options") and v not in spec["options"]]
                if bad:
                    raise ValueError(f"{', '.join(bad)} are not options of {prop} in {room_name}")
            if kind in ("person", "people"):
                to_value = [normalise_id(v) for v in (to_value or []) if v]
            if by not in (cfg.AUTHOR_PERSON, cfg.AUTHOR_AGENT):
                raise ValueError(f"author must be {cfg.AUTHOR_PERSON} or {cfg.AUTHOR_AGENT}")
            from_value = current.get(prop)
            if values_equal(kind or "text", from_value, to_value):
                raise ValueError(f"{prop} already is {to_value!r}; nothing to change")
            change = Change.new(task_id, room_id, prop, from_value, to_value, by, title=title, note=note)
            self.queue.add(change)
            if change.state == QUEUED:
                self._apply_locally(change)
                self.rebuild()
            return change

    def rule(self, item_id: str, call: str, state: str = cb.ADJUDICATED, by: str = cfg.AUTHOR_PERSON) -> list[Change]:
        """Rule on a Clipboard row from the desk: the call, the state and the date, queued like any other change."""
        with self.lock:
            item_id = normalise_id(item_id) or item_id
            item = self.desk.clipboard_by_id.get(item_id)
            if item is None:
                raise KeyError(f"no Clipboard row {item_id} on the desk")
            options = (self.desk.clipboard_schema.get(cb.STATE) or {}).get("options") or []
            if options and state not in options:
                raise ValueError(f"'{state}' is not a State of the Clipboard; the options are {', '.join(options)}")
            call = (call or "").strip()
            if state == cb.ADJUDICATED and not call:
                raise ValueError("a ruling needs a call; say what was decided")
            props = item["props"]
            changes: list[Change] = []
            if call and not values_equal("text", props.get(cb.CALL), call):
                changes.append(self.change(item_id, cb.CALL, call, by))
            if props.get(cb.STATE) != state:
                changes.append(self.change(item_id, cb.STATE, state, by))
            if not (props.get(cb.RULED) or {}).get("start"):
                changes.append(self.change(item_id, cb.RULED, self.today(), by))
            return changes

    def file_decision(self, change: Change, ruling: str) -> Change | None:
        """Record a gate decision as a Clipboard row that points at the task.

        The row is queued as a create through the same write path: attributed
        to the person who ruled, shown locally at once, sent with everything
        else, and read back. Returns the create change, or None when the desk
        no longer holds the task the decision was about.
        """
        with self.lock:
            task = self.desk.tasks.get(change.task_id)
            if task is None:
                return None
            room = self.desk.room_by_id[task.room_id]
            decided = change.decided_at or self.now()
            props = cb.decision_properties(task, room, change, ruling, decided)
            schema = self.desk.clipboard_schema
            page_id = uuid.uuid4().hex
            item_text = props[cb.ITEM]
            note = f"decision on {change.id}"
            created = Change.new(page_id, cfg.CLIPBOARD_ID, cb.ITEM, None, item_text, cfg.AUTHOR_PERSON, title=item_text, note=note, kind="create")
            self.queue.add(created)
            self._apply_locally(created)
            for prop, value in props.items():
                if prop == cb.ITEM or prop not in schema or value in (None, "", []):
                    continue
                extra = Change.new(page_id, cfg.CLIPBOARD_ID, prop, None, value, cfg.AUTHOR_PERSON, title=item_text, note=note)
                self.queue.add(extra)
                self._apply_locally(extra)
            change.history.append({"at": self.now(), "event": "filed", "clipboard_row": page_id})
            self.queue.save()
            self.rebuild()
            return created

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

    def _store(self, room_id: str) -> tuple[str | None, dict[str, dict]]:
        """The data source and schema a change's row lives in, or (None, {}) when the desk cannot hold it."""
        if room_id == cfg.CLIPBOARD_ID:
            return cfg.CLIPBOARD_ID, self.desk.clipboard_schema
        room = self.desk.room_by_id.get(room_id)
        if room is None or not room.wired:
            return None, {}
        return room.tasks_source, self.desk.schema_for_room(room.id)

    def _apply_locally(self, change: Change) -> None:
        ds_id, schema = self._store(change.room_id)
        if ds_id is None:
            return
        if change.kind == "create":
            if find_row(self.snapshot, ds_id, change.task_id) is None:
                add_row(self.snapshot, ds_id, change.task_id, f"local://{change.task_id}", title_property(schema) or "Task", change.to_value)
            return
        prop_type = (schema.get(change.property) or {}).get("type") or "text"
        patch_row(self.snapshot, ds_id, change.task_id, change.property, prop_type, change.to_value)

    def _revert_locally(self, change: Change) -> None:
        ds_id, schema = self._store(change.room_id)
        if ds_id is None or change.kind == "create":
            return
        prop_type = (schema.get(change.property) or {}).get("type") or "text"
        patch_row(self.snapshot, ds_id, change.task_id, change.property, prop_type, change.from_value)

    # ---- the gate ---------------------------------------------------------
    def accept(self, change_id: str, edited_to: Any = None, file: bool = True) -> Change:
        with self.lock:
            change = self.queue.accept(change_id, edited_to)
            self._apply_locally(change)
            self.rebuild()
            if file:
                self.file_decision(change, f"Accepted as edited: {cb.brief(change.to_value)}." if change.edited else "Accepted.")
            return change

    def reject(self, change_id: str, reason: str = "", file: bool = True) -> Change:
        with self.lock:
            change = self.queue.reject(change_id, reason)
            if file:
                self.file_decision(change, f"Rejected. {reason.strip()}" if reason.strip() else "Rejected.")
            return change

    def respond(self, change_id: str, text: str, file: bool = True) -> Change:
        with self.lock:
            change = self.queue.respond(change_id, text)
            if file:
                self.file_decision(change, f"Returned to the agent: {text.strip()}")
            return change

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
            results = self.queue.send(client, self.desk.schema_for_room, self._room_name, self._data_source, verify=verify)
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
            result = {"kind": kind, "captured_at": self.snapshot.get("captured_at"), "rooms": len(self.desk.tasks_by_room),
                      "tasks": len(self.desk.tasks), "credential": client is not None}
            if client is None:
                result["note"] = "No Notion MCP credential on file, so this is the fixture, not a live snapshot. Run `python3 -m desk auth` first."
            elif self.snapshot_path:
                result["saved_to"] = str(self.snapshot_path)
            return result

    def now(self) -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def today(self) -> str:
        return datetime.now(timezone.utc).date().isoformat()


def fixture_service(tmp_dir: Path | str | None = None) -> DeskService:
    """A service on the fixture with an in-memory or temp-dir queue. Used by tests and demos."""
    snapshot = load_fixture()
    queue_path = Path(tmp_dir) / "queue.json" if tmp_dir else None
    return DeskService(snapshot=snapshot, queue_path=queue_path, snapshot_path=None, notion_client=FixtureNotionClient(snapshot))
