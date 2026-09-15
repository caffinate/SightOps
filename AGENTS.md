# SightOps

Home of the Desk Harness (`desk/`), a Notion and Claude harness over the rooms on the Room Register. The retired Kanban (`sightops/`, the Swift app) is left untouched.

## Read first

- `claude/desk-harness-brief.md`, a pointer to the canonical brief in Notion. Read the Notion page before building.
- `README.md` for how to run the desk.

## Boundaries

- **Notion** is the canonical task system. The desk does not become a second task list; the snapshot is a read copy and the queue is a pending list, nothing more.
- Writes go through **Notion MCP** (`notion-update-page`, `notion-create-pages`), never `api.notion.com`.
- Write back only a database's own vocabulary. Normalised status is a display concern.
- Known dirt in the data is surfaced, never cleaned without a ruling.
- Per-room configuration lives on the Room Register (Grouping field, Status vocabulary, Owner field), not in code.

## Run

```bash
python3 -m desk serve 3100
python3 -m unittest discover -s tests -v
```

## Layout

- `desk/` — the harness: registry, tasks, normalise, dirt, changes (the queue and payload), notion/ (MCP client, OAuth, fixture client), service, server
- `app/desk.html` — the four-column surface
- `fixtures/notion-2026-09-14/` — the real data as read through Notion MCP on 14 September 2026
- `tests/` — unit tests for both the desk and the retired Kanban
