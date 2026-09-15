"""The Room Register: rooms grouped by Area, and each room's configuration."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import config as cfg
from . import schema as sch
from .values import page_id_from_url

AREA_ORDER_FALLBACK = ("Personal", "Sightbox", "Flux", "Other")


@dataclass
class Room:
    id: str
    name: str
    area: str | None
    status: str | None
    contract: str | None
    tasks_url: str | None
    tasks_source: str | None
    notion_home: str | None
    clipboard: str | None
    clipboard_standard: str | None
    notes: str | None
    grouping_field: str | None = None
    status_vocabulary: list[str] | None = None
    owner_field: str | None = None
    url: str | None = None

    @property
    def wired(self) -> bool:
        return bool(self.tasks_source)


@dataclass
class RoomConfig:
    grouping_field: str | None
    status_vocabulary: list[str]
    owner_field: str | None
    source: str  # "register", "register+derived" or "derived"
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "grouping_field": self.grouping_field,
            "status_vocabulary": list(self.status_vocabulary),
            "owner_field": self.owner_field,
            "source": self.source,
            "warnings": list(self.warnings),
        }


def parse_vocabulary(text: str | None) -> list[str] | None:
    """The Status vocabulary field: option names separated by ' | ' or ', '."""
    if text is None:
        return None
    text = str(text).strip()
    if not text:
        return None
    sep = "|" if "|" in text else ","
    return [part.strip() for part in text.split(sep) if part.strip()]


def format_vocabulary(options: list[str]) -> str:
    sep = " | " if any("," in o for o in options) else ", "
    return sep.join(options)


def room_from_row(row: dict[str, Any]) -> Room:
    source = (row.get("Tasks source") or "").strip() or None
    return Room(
        id=page_id_from_url(row.get("url")) or row.get("url") or "",
        name=(row.get("Room") or "").strip(),
        area=row.get("Area"),
        status=row.get("Status"),
        contract=row.get("Contract"),
        tasks_url=row.get("Tasks"),
        tasks_source=source.lower() if source else None,
        notion_home=row.get("Notion home"),
        clipboard=row.get("Clipboard"),
        clipboard_standard=row.get("Clipboard standard"),
        notes=row.get("Notes"),
        grouping_field=(row.get(cfg.REGISTER_GROUPING_FIELD) or "").strip() or None,
        status_vocabulary=parse_vocabulary(row.get(cfg.REGISTER_STATUS_VOCABULARY)),
        owner_field=(row.get(cfg.REGISTER_OWNER_FIELD) or "").strip() or None,
        url=row.get("url"),
    )


def load_rooms(rows: list[dict[str, Any]]) -> list[Room]:
    return [room_from_row(row) for row in rows if (row.get("Room") or "").strip()]


def rooms_by_area(rooms: list[Room], area_order: list[str] | None = None) -> list[tuple[str, list[Room]]]:
    order = list(area_order or AREA_ORDER_FALLBACK)
    for room in rooms:
        if room.area and room.area not in order:
            order.append(room.area)
    if any(r.area is None for r in rooms):
        order.append("No area")
    grouped: list[tuple[str, list[Room]]] = []
    for area in order:
        members = sorted((r for r in rooms if (r.area or "No area") == area), key=lambda r: r.name.lower())
        if members:
            grouped.append((area, members))
    return grouped


def wired_rooms(rooms: list[Room]) -> list[Room]:
    return [r for r in rooms if r.wired]


def room_config(room: Room, schema: dict[str, dict]) -> RoomConfig:
    """Effective per-room configuration: the register's fields, checked against the schema.

    The register is the configuration store. The database schema is the fact
    on the ground. When they disagree the schema wins for what can be written,
    and the disagreement is surfaced as a warning rather than hidden.
    """
    warnings: list[str] = []
    from_register = 0
    grouping = room.grouping_field
    if grouping:
        from_register += 1
        if grouping not in schema:
            warnings.append(f"Register names grouping field '{grouping}' but the database has no such property; using a derived guess.")
            grouping = sch.guess_grouping_field(schema)
        elif schema[grouping].get("type") not in sch.GROUPABLE_TYPES:
            warnings.append(f"Register grouping field '{grouping}' is a {schema[grouping].get('type')} property, not a select, multi-select, status or relation.")
    else:
        grouping = sch.guess_grouping_field(schema)
        if grouping:
            warnings.append(f"Register carries no grouping field for this room; '{grouping}' is a guess from the schema.")

    real_vocabulary = sch.status_vocabulary(schema)
    if room.status_vocabulary is not None:
        from_register += 1
        if list(room.status_vocabulary) != real_vocabulary:
            missing = [o for o in real_vocabulary if o not in room.status_vocabulary]
            extra = [o for o in room.status_vocabulary if o not in real_vocabulary]
            detail = []
            if missing:
                detail.append("database also has " + ", ".join(missing))
            if extra:
                detail.append("register also lists " + ", ".join(extra))
            if not detail:
                detail.append("same options in a different order")
            warnings.append("Status vocabulary on the register differs from the database: " + "; ".join(detail) + ". Writes use the database's own options.")
    else:
        warnings.append("Register carries no status vocabulary for this room; using the database's options.")

    owner = room.owner_field
    if owner:
        from_register += 1
        if owner not in schema:
            warnings.append(f"Register names owner field '{owner}' but the database has no such property.")
            owner = sch.guess_owner_field(schema)
    else:
        owner = sch.guess_owner_field(schema)
        if owner:
            warnings.append(f"Register carries no owner field for this room; '{owner}' is a guess from the schema.")
        elif room.status_vocabulary is not None or room.grouping_field:
            # The register is filled in and says nothing about an owner because the database has no owner property.
            from_register += 1

    if from_register == 3:
        source = "register"
    elif from_register:
        source = "register+derived"
    else:
        source = "derived"
    return RoomConfig(grouping_field=grouping, status_vocabulary=real_vocabulary, owner_field=owner, source=source, warnings=warnings)


def register_values_for(schema: dict[str, dict]) -> dict[str, str]:
    """What the three register fields should say for a database with this schema."""
    grouping = sch.guess_grouping_field(schema) or ""
    owner = sch.guess_owner_field(schema) or ""
    return {
        cfg.REGISTER_GROUPING_FIELD: grouping,
        cfg.REGISTER_STATUS_VOCABULARY: format_vocabulary(sch.status_vocabulary(schema)),
        cfg.REGISTER_OWNER_FIELD: owner,
    }
