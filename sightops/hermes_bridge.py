#!/usr/bin/env python3
"""Small local boundary between the native app and Hermes task refreshes."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any


def snapshot_count(snapshot: dict[str, Any]) -> int:
    if isinstance(snapshot.get("cards"), list):
        return len(snapshot["cards"])
    seen: set[str] = set()
    for bucket in ("overdue", "due_now", "due_soon", "no_due"):
        for task in snapshot.get(bucket) or []:
            page_id = str(task.get("page_id") or "")
            if page_id:
                seen.add(page_id)
    return len(seen)


def refresh_snapshot(
    *,
    python: str | Path,
    script: str | Path,
    snapshot_path: str | Path,
    timeout: int = 180,
    environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    env = os.environ.copy()
    if environment:
        env.update(environment)
    try:
        completed = subprocess.run(
            [str(python), str(script), "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "error", "error": str(exc)}
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "Hermes refresh failed"
        return {"status": "error", "error": message[-1200:]}
    try:
        with Path(snapshot_path).open(encoding="utf-8") as handle:
            snapshot = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "error", "error": f"Snapshot unavailable after refresh: {exc}"}
    return {
        "status": "ok",
        "count": snapshot_count(snapshot),
        "generated_at": snapshot.get("generated_at", ""),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("refresh",))
    parser.add_argument("--python", default=os.environ.get("HERMES_PYTHON", sys.executable))
    parser.add_argument("--script")
    parser.add_argument("--snapshot")
    args = parser.parse_args()
    profile = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes/profiles/max-ea"))
    script = Path(args.script) if args.script else profile / "scripts/notion_task_dashboard.py"
    snapshot = Path(args.snapshot) if args.snapshot else profile / "notion_tasks_latest.json"
    print(json.dumps(refresh_snapshot(python=args.python, script=script, snapshot_path=snapshot)))


if __name__ == "__main__":
    import sys

    main()
