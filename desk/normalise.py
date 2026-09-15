"""Status normalisation for display.

Normalise on read, keep the raw value, and write back only the raw vocabulary
of that database. A normalised state never reaches Notion.
"""
from __future__ import annotations

STATES = ("todo", "doing", "blocked", "review", "done", "cancelled", "unset", "unknown")
OPEN_STATES = frozenset({"todo", "doing", "blocked", "review"})
TERMINAL_STATES = frozenset({"done", "cancelled"})

_BY_NAME = {
    "not started": "todo", "to do": "todo", "todo": "todo", "backlog": "todo", "up next": "todo",
    "open": "todo", "idea": "todo", "someday": "todo", "planned": "todo",
    "in progress": "doing", "doing": "doing", "active": "doing", "started": "doing", "working": "doing",
    "blocked": "blocked", "waiting": "blocked", "on hold": "blocked", "waiting on": "blocked", "waiting for": "blocked",
    "in review": "review", "to review": "review", "review": "review", "reviewing": "review", "pending": "review",
    "done": "done", "complete": "done", "completed": "done", "closed": "done", "shipped": "done", "decided": "done",
    "canceled": "cancelled", "cancelled": "cancelled", "dropped": "cancelled", "archived": "cancelled",
}

_BY_GROUP = {"to_do": "todo", "in_progress": "doing", "complete": "done", "current": "doing", "future": "todo"}


def normalise(raw: str | None, groups: dict[str, list[str]] | None = None) -> str:
    """Map a raw status option name to a display state."""
    if raw is None or str(raw).strip() == "":
        return "unset"
    key = str(raw).strip().lower()
    if key in _BY_NAME:
        return _BY_NAME[key]
    for group, names in (groups or {}).items():
        if raw in names and group in _BY_GROUP:
            return _BY_GROUP[group]
    return "unknown"


def is_open(state: str) -> bool:
    return state in OPEN_STATES


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES
