"""Command line for the desk.

  python3 -m desk serve [port]      serve the desk on localhost
  python3 -m desk auth [--fresh]    connect the desk to Notion MCP (OAuth, its own connection); --fresh registers anew
  python3 -m desk snapshot          pull a live snapshot through Notion MCP into DESK_HOME
  python3 -m desk findings          print the dirt, room by room
  python3 -m desk payload           print the notion-update-page calls that would be sent
  python3 -m desk send              send the payload and read every write back
  python3 -m desk propose <task> <property> <value> [note]   queue an agent proposal for the gate
  python3 -m desk register-config   print the three Room Register values per wired room
"""
from __future__ import annotations

import json
import sys

from . import config as cfg
from .registry import register_values_for
from .service import DeskService


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "serve"
    if command == "serve":
        from .server import serve

        serve(int(argv[2]) if len(argv) > 2 and argv[2].isdigit() else cfg.DEFAULT_PORT)
        return 0
    if command == "auth":
        from .notion.mcp import MCPClient, MCPError
        from .notion.oauth import NotionOAuth, OAuthError

        flow = NotionOAuth()
        if "--fresh" in argv[2:]:
            flow.forget()
        try:
            token = flow.login()
        except OAuthError as exc:
            print(f"Not connected: {exc}", file=sys.stderr)
            return 1
        if not token:
            print("Not connected: no token received.", file=sys.stderr)
            return 1
        print(f"Token stored at {cfg.NOTION_AUTH_PATH}.")
        try:
            info = MCPClient(cfg.NOTION_MCP_URL, token_provider=flow.token).initialize()
        except MCPError as exc:
            print(f"Token stored, but the first request to {cfg.NOTION_MCP_URL} failed: {exc}", file=sys.stderr)
            return 1
        server = info.get("serverInfo") or {}
        print(f"Connected to {server.get('name') or cfg.NOTION_MCP_URL} {server.get('version') or ''}".rstrip() + ".")
        return 0
    service = DeskService()
    if command == "snapshot":
        print(json.dumps(service.refresh(), indent=1))
        return 0
    if command == "findings":
        for finding in service.desk.findings:
            print(f"[{finding['rule']}] {finding['room']}: {finding['note']}")
            for item in finding["items"][:12]:
                print(f"    - {item['title']}: {item['detail']}")
            if len(finding["items"]) > 12:
                print(f"    ... and {len(finding['items']) - 12} more")
        return 0
    if command == "payload":
        print(json.dumps(service.payload(), indent=1, ensure_ascii=False))
        return 0
    if command == "send":
        print(json.dumps(service.send(), indent=1, ensure_ascii=False))
        return 0
    if command == "propose":
        if len(argv) < 5:
            print("usage: propose <task id> <property> <value> [note]", file=sys.stderr)
            return 2
        value: object = argv[4]
        if isinstance(value, str) and value.startswith("["):
            value = json.loads(value)
        change = service.change(argv[2], argv[3], value, cfg.AUTHOR_AGENT, argv[5] if len(argv) > 5 else "")
        print(json.dumps(change.as_dict(), indent=1, ensure_ascii=False))
        return 0
    if command == "register-config":
        for room in service.desk.rooms:
            if room.id in service.desk.tasks_by_room:
                values = register_values_for(service.desk.schema_for_room(room.id))
                print(json.dumps({"room": room.name, "page_id": room.id, **values}, ensure_ascii=False))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
