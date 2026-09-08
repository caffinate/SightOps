# SightOps

Local Kanban PWA for Nathan’s personal and Sightbox execution. MAX (Hermes profile `max-ea`) operates it.

## Read first

- `context/project-context.md`
- `context/session-handoff.md`

## Boundaries

- **Notion** is the canonical task system. SightOps does not become a second task list.
- Status writes go through **Notion MCP** (`notion-update-page`), never `api.notion.com`.
- Obsidian holds private project context. Do not mirror tasks there.

## Run

```bash
HERMES_HOME="$HOME/.hermes/profiles/max-ea" \
  "$HOME/.hermes/hermes-agent/venv/bin/python3.11" -m sightops.server 3000
```

Board: `http://127.0.0.1:3000/ops-kanban.html`  
Tests: `python3.11 -m unittest discover -s tests -v`

## Layout

- `app/` — PWA shell
- `sightops/board.py` — column mapping
- `sightops/server.py` — localhost server + MCP writes
- Task snapshot JSON remains `$HERMES_HOME/notion_tasks_latest.json`
