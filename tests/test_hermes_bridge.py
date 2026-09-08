import json
import sys
import tempfile
import unittest
from pathlib import Path

from sightops.hermes_bridge import refresh_snapshot


class HermesBridgeTests(unittest.TestCase):
    def test_refresh_runs_hermes_and_reports_snapshot_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "fake_refresh.py"
            snapshot = root / "snapshot.json"
            script.write_text(
                "import json, os\n"
                "json.dump({'generated_at': '2026-09-08T10:00:00', 'overdue': [{'page_id': 'a'}], 'due_now': [{'page_id': 'b'}]}, open(os.environ['SNAPSHOT'], 'w'))\n"
            )

            result = refresh_snapshot(
                python=sys.executable,
                script=script,
                snapshot_path=snapshot,
                environment={"SNAPSHOT": str(snapshot)},
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["count"], 2)
            self.assertEqual(result["generated_at"], "2026-09-08T10:00:00")

    def test_refresh_reports_command_failure_without_claiming_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "fake_refresh.py"
            script.write_text("raise SystemExit('no credentials')\n")

            result = refresh_snapshot(
                python=sys.executable,
                script=script,
                snapshot_path=root / "missing.json",
            )

            self.assertEqual(result["status"], "error")
            self.assertIn("no credentials", result["error"])
            self.assertNotIn("count", result)


if __name__ == "__main__":
    unittest.main()
