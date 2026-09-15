Desk Harness — Build Brief (pointer)

Canonical copy is in Notion: https://app.notion.com/p/3dbb26df546781718e6aeb1cebb187d4 Filed under the Operating System hub, 14 September 2026.

Read that page first in any Claude Code session that picks up this build. This file is a pointer, per the canonical-copies rule.

Load-bearing facts it carries:

* Write access to the Tasks databases is PROVEN, 14 Sep, by two no-op writes read back. SightOps was blocked by its own credential, not by the workspace. Do not re-test this as though it were unknown.
* Nine rooms have a Tasks source. Their data source IDs and grouping fields are in the brief.
* Build order puts the write path SECOND, not last.
* Parent task is not a schema gap. Parent item and Sub-item already exist in Alkaline.
* Three new Room Register fields carry the per-room config: grouping field, status vocabulary, owner field name.
* A list of known dirt in the data that must be surfaced, not silently cleaned.

Related: claude/agent-task-surface-state.md holds the full session record.
