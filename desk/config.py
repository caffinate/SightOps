"""Paths, identifiers and environment for the desk."""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "app"

# Where the desk keeps its own state: the live snapshot, the pending queue and
# the Notion MCP credential. Never inside the repository.
DESK_HOME = Path(os.environ.get("DESK_HOME", Path.home() / ".config" / "desk"))
SNAPSHOT_PATH = DESK_HOME / "snapshot.json"
QUEUE_PATH = DESK_HOME / "queue.json"
NOTION_AUTH_PATH = DESK_HOME / "notion-mcp-auth.json"

# The real data captured on 14 September 2026, used when there is no live
# snapshot and by the tests.
FIXTURE_DIR = Path(os.environ.get("DESK_FIXTURE", REPO_ROOT / "fixtures" / "notion-2026-09-14"))

# Notion MCP, the only write path. Never api.notion.com.
NOTION_MCP_URL = os.environ.get("NOTION_MCP_URL", "https://mcp.notion.com/mcp")
NOTION_MCP_TOKEN_ENV = "NOTION_MCP_TOKEN"

# Sent on every request to Notion. mcp.notion.com sits behind Cloudflare,
# which answers Python's default "Python-urllib/x.y" with 403; proven live on
# 14 Sep 2026, when discovery failed from the Mac until the desk named itself.
USER_AGENT = "desk-harness/0.1.0 (+https://github.com/caffinate/SightOps)"

# Data sources named in the brief.
ROOM_REGISTER_ID = "bb434a7e-858d-47c6-9e66-f58817f5b3f8"
CLIPBOARD_ID = "9094f21d-feca-46b3-a8c8-24ab27048fd5"

# The three Room Register fields that carry per-room configuration (build order 3).
REGISTER_GROUPING_FIELD = "Grouping field"
REGISTER_STATUS_VOCABULARY = "Status vocabulary"
REGISTER_OWNER_FIELD = "Owner field"

DEFAULT_PORT = 3100

# Authors of a change. The mechanism is identical; only the gate differs.
AUTHOR_PERSON = "nathan"
AUTHOR_AGENT = "agent"
