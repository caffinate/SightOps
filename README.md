# SightOps

Two things live here.

**The desk** (`desk/`, `app/desk.html`) is the Desk Harness: a working surface over the rooms on the Notion Room Register. Notion holds the data. The desk reads it, lets a person edit it the way Notion would, lets an agent propose changes through the same write path, and records what was decided. The canonical brief is in Notion: [Desk Harness — Build Brief](https://app.notion.com/p/3dbb26df546781718e6aeb1cebb187d4). The pointer is `claude/desk-harness-brief.md`.

**The Kanban** (`sightops/`, `app/ops-kanban.html`, the Swift app) is the earlier local board. It was retired by ruling on 14 September 2026 and is left in place untouched.

## Run the desk

```bash
python3 -m desk serve            # http://127.0.0.1:3100/desk.html
python3 -m desk findings         # the dirt, room by room, on the terminal
python3 -m desk payload          # the exact notion-update-page calls pending
python3 -m desk send             # send them and read every write back
python3 -m desk auth             # give the desk its own Notion MCP connection
python3 -m desk snapshot         # pull a live snapshot into DESK_HOME
python3 -m unittest discover -s tests
```

With no live snapshot the desk serves the real data captured on 14 September 2026 from `fixtures/notion-2026-09-14/`. State lives under `DESK_HOME` (default `~/.config/desk`): the live snapshot, the pending queue, the Notion credential. Nothing of that is in the repository.

## One write path, two authors

Every change is the same object: task, property, from, to, by. A change Nathan makes applies to the local copy at once and queues. A change the agent proposes waits at the gate (accept, edit, respond, ignore) and, once accepted, queues the same way. One pending list, one payload, attributed. The payload is the literal `notion-update-page` call per task, in that database's own vocabulary; nothing normalised is ever written. Every sent write is read back before it counts as done.

Writes go through Notion MCP only, never `api.notion.com`. The desk gets its own connection with `python3 -m desk auth` (OAuth against the MCP server, tokens under `DESK_HOME`). Until a credential is on file the payload can be copied out of the desk and sent by any session that holds one.

## Per-room configuration

Nine rooms have a Tasks source. Their grouping field, status vocabulary and owner field are on the Room Register, in three text fields added on 14 September 2026. The desk reads its configuration from there and checks it against each database's real schema; a mismatch is surfaced as a finding, never hidden, and the database's own option list is what gets written.

## Known dirt

The desk surfaces what it finds and cleans nothing: parents closed with open children, owner ids that resolve to no workspace person, rows with no title or no status, rows outside a room's grouping on an older shape, a row that names another room's database, a room marked Live with every row terminal. Each is a signal for a ruling.
