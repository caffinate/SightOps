"""One write path, two authors.

Every change is the same object: task, property, from, to, by. A change a
person makes applies to the local copy immediately and queues. A change the
agent proposes waits at the gate and, once accepted, queues the same way. One
pending list, one payload, attributed.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config as cfg
from .values import encode_property, values_equal

PROPOSED = "proposed"      # agent-authored, waiting on the gate
QUEUED = "queued"          # applied locally, waiting to be sent
SENT = "sent"              # update_page returned without error
VERIFIED = "verified"      # read back and matched
FAILED = "failed"          # update_page failed or read-back mismatched
REJECTED = "rejected"      # gate said no
IGNORED = "ignored"        # gate never answered; explicitly distinct from no
RETURNED = "returned"      # gate sent free text back to the agent
DISCARDED = "discarded"    # a person withdrew their own change

SENDABLE = {QUEUED, FAILED}
OPEN_GATE = {PROPOSED}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class Change:
    id: str
    task_id: str
    room_id: str
    property: str
    from_value: Any
    to_value: Any
    by: str
    state: str
    created_at: str
    title: str = ""
    note: str = ""
    decided_at: str | None = None
    sent_at: str | None = None
    result: dict[str, Any] | None = None
    kind: str = "update"
    response: str | None = None
    edited: bool = False
    history: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def new(cls, task_id: str, room_id: str, prop: str, from_value: Any, to_value: Any, by: str, title: str = "", note: str = "", kind: str = "update") -> "Change":
        state = PROPOSED if by == cfg.AUTHOR_AGENT else QUEUED
        return cls(id=uuid.uuid4().hex[:12], task_id=task_id, room_id=room_id, property=prop, from_value=from_value,
                   to_value=to_value, by=by, state=state, created_at=_now(), title=title, note=note, kind=kind)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Change":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


class PendingQueue:
    """The one pending list, persisted as JSON so nothing is lost on restart."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else None
        self.changes: list[Change] = []
        if self.path and self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.changes = [Change.from_dict(c) for c in data.get("changes", [])]

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"changes": [c.as_dict() for c in self.changes]}, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    # ---- basic access -----------------------------------------------------
    def get(self, change_id: str) -> Change | None:
        return next((c for c in self.changes if c.id == change_id), None)

    def add(self, change: Change) -> Change:
        self.changes.append(change)
        self.save()
        return change

    def for_task(self, task_id: str, states: set[str] | None = None) -> list[Change]:
        return [c for c in self.changes if c.task_id == task_id and (states is None or c.state in states)]

    def sendable(self) -> list[Change]:
        return [c for c in self.changes if c.state in SENDABLE]

    def proposals(self) -> list[Change]:
        return [c for c in self.changes if c.state in OPEN_GATE]

    def pending(self) -> list[Change]:
        return [c for c in self.changes if c.state in SENDABLE | OPEN_GATE]

    # ---- the gate: accept, edit, respond, ignore --------------------------
    def accept(self, change_id: str, edited_to: Any = None) -> Change:
        change = self._gated(change_id)
        if edited_to is not None and edited_to != change.to_value:
            change.history.append({"at": _now(), "event": "edited", "from": change.to_value, "to": edited_to})
            change.to_value = edited_to
            change.edited = True
        change.state = QUEUED
        change.decided_at = _now()
        change.history.append({"at": change.decided_at, "event": "accepted"})
        self.save()
        return change

    def reject(self, change_id: str, reason: str = "") -> Change:
        change = self._gated(change_id)
        change.state = REJECTED
        change.decided_at = _now()
        change.response = reason or None
        change.history.append({"at": change.decided_at, "event": "rejected", "reason": reason})
        self.save()
        return change

    def respond(self, change_id: str, text: str) -> Change:
        change = self._gated(change_id)
        change.state = RETURNED
        change.decided_at = _now()
        change.response = text
        change.history.append({"at": change.decided_at, "event": "returned", "text": text})
        self.save()
        return change

    def ignore(self, change_id: str) -> Change:
        change = self._gated(change_id)
        change.state = IGNORED
        change.decided_at = _now()
        change.history.append({"at": change.decided_at, "event": "ignored"})
        self.save()
        return change

    def discard(self, change_id: str) -> Change:
        change = self.get(change_id)
        if change is None:
            raise KeyError(change_id)
        if change.state not in SENDABLE | OPEN_GATE:
            raise ValueError(f"change {change_id} is {change.state}; only pending changes can be discarded")
        change.state = DISCARDED
        change.decided_at = _now()
        change.history.append({"at": change.decided_at, "event": "discarded"})
        self.save()
        return change

    def _gated(self, change_id: str) -> Change:
        change = self.get(change_id)
        if change is None:
            raise KeyError(change_id)
        if change.state not in OPEN_GATE:
            raise ValueError(f"change {change_id} is {change.state}; only a proposal can be decided")
        return change

    # ---- the payload ------------------------------------------------------
    def payload(self, schema_for_room, room_name_for=None, data_source_for=None) -> list[dict[str, Any]]:
        """The exact notion-update-page calls that would be sent, one per task.

        The latest queued change per property wins. Values are encoded from
        each database's own vocabulary; nothing normalised ever goes in.
        """
        calls: list[dict[str, Any]] = []
        by_task: dict[str, list[Change]] = {}
        for change in self.sendable():
            by_task.setdefault(change.task_id, []).append(change)
        for task_id, changes in by_task.items():
            schema = schema_for_room(changes[0].room_id)
            latest: dict[str, Change] = {}
            for change in changes:
                latest[change.property] = change
            properties: dict[str, Any] = {}
            for prop, change in latest.items():
                prop_type = (schema.get(prop) or {}).get("type") or "text"
                properties.update(encode_property(prop, prop_type, change.to_value))
            creating = any(c.kind == "create" for c in changes)
            call = {
                "task_id": task_id,
                "room_id": changes[0].room_id,
                "room": room_name_for(changes[0].room_id) if room_name_for else None,
                "title": changes[0].title,
                "change_ids": [c.id for c in latest.values()],
                "authors": sorted({c.by for c in latest.values()}),
            }
            if creating:
                data_source_id = data_source_for(changes[0].room_id) if data_source_for else None
                call.update({"tool": "notion-create-pages",
                             "arguments": {"parent": {"type": "data_source_id", "data_source_id": data_source_id}, "pages": [{"properties": properties}]}})
            else:
                call.update({"tool": "notion-update-page",
                             "arguments": {"page_id": task_id, "command": "update_properties", "properties": properties}})
            calls.append(call)
        return calls

    # ---- sending ----------------------------------------------------------
    def send(self, client, schema_for_room, room_name_for=None, data_source_for=None, verify: bool = True) -> list[dict[str, Any]]:
        """Send the payload through a Notion client and read every write back."""
        results = []
        for call in self.payload(schema_for_room, room_name_for, data_source_for):
            args = call["arguments"]
            changes = [self.get(cid) for cid in call["change_ids"]]
            outcome: dict[str, Any] = {"task_id": call["task_id"], "title": call["title"], "change_ids": call["change_ids"]}
            page_id = args.get("page_id")
            try:
                if call["tool"] == "notion-create-pages":
                    created = client.create_page(args["parent"]["data_source_id"], args["pages"][0]["properties"])
                    page_id = created.get("page_id") or page_id
                    outcome["created_page_id"] = page_id
                    for change in changes:
                        change.result = {"created_page_id": page_id}
                else:
                    client.update_properties(page_id, args["properties"])
            except Exception as exc:  # noqa: BLE001 - the error is the result
                outcome.update({"state": FAILED, "error": str(exc)})
                for change in changes:
                    change.state = FAILED
                    change.result = {"error": str(exc), "at": _now()}
                results.append(outcome)
                continue
            sent_at = _now()
            for change in changes:
                change.state = SENT
                change.sent_at = sent_at
            if verify and page_id:
                schema = schema_for_room(call["room_id"])
                try:
                    readback = client.page_properties(page_id)
                except Exception as exc:  # noqa: BLE001
                    outcome.update({"state": SENT, "verify_error": str(exc)})
                    results.append(outcome)
                    continue
                mismatches = []
                for change in changes:
                    prop_type = (schema.get(change.property) or {}).get("type") or "text"
                    from .values import decode

                    seen = decode(prop_type, readback, change.property)
                    if values_equal(prop_type, seen, change.to_value):
                        change.state = VERIFIED
                        change.result = {"verified_at": _now(), "read_back": seen}
                    else:
                        change.state = FAILED
                        change.result = {"error": "read-back mismatch", "expected": change.to_value, "read_back": seen, "at": _now()}
                        mismatches.append({"property": change.property, "expected": change.to_value, "read_back": seen})
                    if change.kind == "create":
                        change.result = dict(change.result or {}, created_page_id=page_id)
                outcome.update({"state": FAILED if mismatches else VERIFIED, "mismatches": mismatches})
            else:
                outcome.update({"state": SENT})
            results.append(outcome)
        self.save()
        return results

    def summary(self) -> dict[str, Any]:
        pend = self.pending()
        return {
            "pending": len(pend),
            "queued": len(self.sendable()),
            "proposed": len(self.proposals()),
            "by_person": sum(1 for c in pend if c.by == cfg.AUTHOR_PERSON),
            "by_agent": sum(1 for c in pend if c.by == cfg.AUTHOR_AGENT),
        }
