"""Workspace people. An owner is shown as a name, never as a raw identifier.

An id that resolves to no workspace user is real dirt and is surfaced as
`unresolved user <first eight characters>`, which is the convention the
prototypes established. It is never guessed at.
"""
from __future__ import annotations

from typing import Any

from .values import dashed, normalise_id, person_ids


class People:
    def __init__(self, users: list[dict[str, Any]] | None = None) -> None:
        self.by_id: dict[str, dict[str, Any]] = {}
        for user in users or []:
            uid = normalise_id(user.get("id"))
            if uid and user.get("type", "person") == "person":
                self.by_id[uid] = {"id": dashed(uid), "name": user.get("name") or user.get("email") or uid, "email": user.get("email")}
        self.unresolved: dict[str, int] = {}

    def name(self, user_id: str | None) -> str | None:
        uid = normalise_id(user_id)
        if not uid:
            return None
        user = self.by_id.get(uid)
        if user:
            return user["name"]
        self.unresolved[uid] = self.unresolved.get(uid, 0) + 1
        return f"unresolved user {uid[:8]}"

    def names(self, value: Any) -> list[str]:
        return [n for n in (self.name(uid) for uid in person_ids(value)) if n]

    def is_resolved(self, user_id: str | None) -> bool:
        uid = normalise_id(user_id)
        return bool(uid) and uid in self.by_id

    def options(self) -> list[dict[str, str]]:
        """Choices for a person editor: every workspace person, by name."""
        return sorted(({"id": u["id"], "name": u["name"]} for u in self.by_id.values()), key=lambda u: u["name"].lower())
