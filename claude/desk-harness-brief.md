Desk Harness — Build Brief (pointer)

Canonical copy is in Notion: https://app.notion.com/p/3dbb26df546781718e6aeb1cebb187d4 Filed under the Operating System hub, 14 September 2026.

Read that page first in any Claude Code session that picks up this build. This file is a pointer, per the canonical-copies rule.

Load-bearing facts it carries:

* Write access to the Tasks databases is PROVEN, 14 Sep, by two no-op writes read back. SightOps was blocked by its own credential, not by the workspace. Do not re-test this as though it were unknown.
* The desk's own Notion MCP connection is PROVEN, 14 Sep. It authorised with OAuth as its own client, not a personal token, with the token under ~/.config/desk. Through it the desk pulled a live snapshot of 268 rows and made a verified Notes round trip on OLG Robotics' "Get laptops set up", with the row restored. Do not re-test this as though it were unknown.
* Notion's MCP server sits behind Cloudflare, which refuses Python's default User-Agent with a 403. Every request must name its client.
* Build orders 1 to 4 are built on branch claude/pensive-ramanujan-xhqpm8, pull request #1: https://github.com/caffinate/SightOps/pull/1
* The Clipboard carries a Task property since 15 Sep: a URL holding the task page's address, ruled that day in place of the brief's relation because a relation targets one database and the tasks live in nine. Build order 5 joins on it: a decision is a Clipboard row that points at a task.
* Nine rooms have a Tasks source. Their data source IDs and grouping fields are in the brief.
* Build order puts the write path SECOND, not last.
* Parent task is not a schema gap. Parent item and Sub-item already exist in Alkaline.
* Three new Room Register fields carry the per-room config: grouping field, status vocabulary, owner field name. They exist and are populated for the nine rooms, 14 Sep.
* A list of known dirt in the data that must be surfaced, not silently cleaned.

Related: claude/agent-task-surface-state.md holds the full session record.
