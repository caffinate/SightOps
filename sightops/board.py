"""Kanban mapping for SightOps.

Normalizes heterogeneous Notion statuses into a small set of columns and
resolves the native option to write back to each source database.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any


COLUMNS = (
    {"id": "inbox", "label": "Inbox"},
    {"id": "ready", "label": "Ready"},
    {"id": "doing", "label": "Doing"},
    {"id": "review", "label": "Review"},
    {"id": "waiting", "label": "Waiting"},
    {"id": "done", "label": "Done"},
)

COLUMN_ALIASES = {
    "inbox": {"", "backlog", "inbox", "idea", "someday"},
    "ready": {"not started", "to do", "todo", "up next", "ready", "next", "open"},
    "doing": {"in progress", "doing", "active", "started", "working"},
    "review": {"to review", "in review", "review", "reviewing"},
    "waiting": {"waiting", "blocked", "on hold", "waiting on", "waiting for"},
    "done": {"done", "decided", "completed", "complete", "closed", "shipped"},
}

PARKED_ALIASES = {"dropped", "canceled", "cancelled", "archived"}

NATIVE_PREFS = {
    "inbox": ("Backlog",),
    "ready": ("Not started", "Not Started", "Up next", "To do"),
    "doing": ("In progress", "In Progress"),
    "review": ("To review", "In review"),
    "waiting": ("Waiting", "Blocked"),
    "done": ("Done", "Decided", "Completed"),
}

BUCKETS = ("overdue", "due_now", "due_soon", "no_due")
PRIORITY_ATTENTION = ("high", "urgent", "now", "p1")


def _norm(status: str | None) -> str:
    return (status or "").strip().lower()


def column_for_status(status: str | None) -> str:
    key = _norm(status)
    if key in PARKED_ALIASES:
        return "parked"
    for column_id, aliases in COLUMN_ALIASES.items():
        if key in aliases:
            return column_id
    return "inbox"


def observed_statuses(tasks: list[dict[str, Any]]) -> dict[str, list[str]]:
    by_db: dict[str, list[str]] = {}
    seen: dict[str, set[str]] = {}
    for task in tasks:
        db_name = task.get("db_name") or ""
        status = task.get("status") or ""
        if not status:
            continue
        bucket = seen.setdefault(db_name, set())
        if status in bucket:
            continue
        bucket.add(status)
        by_db.setdefault(db_name, []).append(status)
    return by_db


def native_status_for_move(
    task: dict[str, Any],
    column_id: str,
    observed_by_db: dict[str, list[str]] | None = None,
) -> str:
    if column_id == "parked":
        observed = (observed_by_db or {}).get(task.get("db_name") or "", [])
        for status in observed:
            if column_for_status(status) == "parked":
                return status
        return "Dropped"
    observed = (observed_by_db or {}).get(task.get("db_name") or "", [])
    for status in observed:
        if column_for_status(status) == column_id:
            return status
    prefs = NATIVE_PREFS.get(column_id) or ("Not started",)
    return prefs[0]


def _parse_today(value: str | None) -> date:
    if value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            pass
    return date.today()


def _is_attention(task: dict[str, Any], today: date) -> bool:
    column = column_for_status(task.get("status"))
    if column in {"doing", "review", "waiting"}:
        return True
    if column in {"done", "parked"}:
        return False
    priority = _norm(task.get("priority"))
    if any(token in priority for token in PRIORITY_ATTENTION):
        return True
    parsed = task.get("parsed_due")
    if not parsed:
        return False
    try:
        due = date.fromisoformat(str(parsed)[:10])
    except ValueError:
        return False
    return due <= today + timedelta(days=7)


def flatten_tasks(data: dict[str, Any]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for bucket in BUCKETS:
        for task in data.get(bucket) or []:
            page_id = task.get("page_id") or ""
            if page_id in seen:
                continue
            seen.add(page_id)
            item = dict(task)
            item["bucket"] = bucket
            item["column"] = column_for_status(item.get("status"))
            tasks.append(item)
    return tasks


def needs_you(tasks: list[dict[str, Any]], today: date) -> dict[str, Any] | None:
    due_today = []
    overdue = []
    for task in tasks:
        parsed = task.get("parsed_due")
        if not parsed:
            continue
        try:
            due = date.fromisoformat(str(parsed)[:10])
        except ValueError:
            continue
        if due == today:
            due_today.append(task)
        elif due < today:
            overdue.append(task)
    overdue.sort(key=lambda t: t.get("parsed_due") or "")
    pick = due_today[0] if due_today else (overdue[0] if overdue else None)
    if not pick:
        return None
    return {
        "title": pick.get("title") or "",
        "page_id": pick.get("page_id") or "",
        "page_url": pick.get("page_url") or "",
        "due_date": pick.get("parsed_due") or pick.get("due_date") or "",
        "db_name": pick.get("db_name") or "",
        "kind": "today" if due_today else "overdue",
    }


def build_board(data: dict[str, Any]) -> dict[str, Any]:
    today = _parse_today(data.get("today"))
    tasks = flatten_tasks(data)
    cards = []
    for task in tasks:
        card = dict(task)
        card["attention"] = _is_attention(task, today)
        cards.append(card)
    counts = {col["id"]: 0 for col in COLUMNS}
    counts["parked"] = 0
    for card in cards:
        counts[card["column"]] = counts.get(card["column"], 0) + 1
    return {
        "generated_at": data.get("generated_at") or "",
        "today": str(today),
        "columns": list(COLUMNS),
        "cards": cards,
        "counts": counts,
        "needs_you": needs_you(tasks, today),
        "observed": observed_statuses(tasks),
        "total": len(cards),
        "attention_total": sum(1 for card in cards if card["attention"]),
        "jobs": data.get("jobs") or [],
        "hourly_summaries": data.get("hourly_summaries") or [],
        "calendar_events": data.get("calendar_events") or [],
    }


def all_snapshot_tasks(data: dict[str, Any]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for bucket in BUCKETS:
        tasks.extend(data.get(bucket) or [])
    return tasks


def find_snapshot_task(data: dict[str, Any], page_id: str) -> dict[str, Any] | None:
    for task in all_snapshot_tasks(data):
        if task.get("page_id") == page_id:
            return task
    return None


def patch_snapshot_status(data: dict[str, Any], page_id: str, native_status: str) -> dict[str, Any]:
    for bucket in BUCKETS:
        for task in data.get(bucket) or []:
            if task.get("page_id") == page_id:
                task["status"] = native_status
    return data
