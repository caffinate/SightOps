import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from desk import config as cfg
from desk.server import make_server
from desk.service import fixture_service


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.service = fixture_service(cls.tmp.name)
        cls.server = make_server(cls.service, port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def test_desk_view_and_static_page(self):
        status, data = self.call("GET", "/api/desk")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["rooms"]), 39)
        self.assertEqual(data["source"]["kind"], "fixture")
        with urllib.request.urlopen(self.base + "/desk.html", timeout=10) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"<title>", response.read()[:4000])

    def test_change_payload_send_and_gate_over_http(self):
        task = next(t for t in self.service.desk.tasks.values() if t.title == "Pilot on sightbox.co — establish baseline orank score and target lift")
        status, data = self.call("POST", "/api/changes", {"task_id": task.id, "property": "Status", "to": "Up next", "by": cfg.AUTHOR_PERSON})
        self.assertEqual(status, 201, data)
        self.assertEqual(data["task"]["status_raw"], "Up next")
        status, data = self.call("POST", "/api/changes", {"task_id": task.id, "property": "Status", "to": "Nope", "by": cfg.AUTHOR_PERSON})
        self.assertEqual(status, 400)
        status, data = self.call("GET", "/api/payload")
        self.assertEqual(data["calls"][0]["arguments"]["properties"], {"Status": "Up next"})
        status, data = self.call("POST", "/api/changes", {"task_id": task.id, "property": "Priority", "to": "High", "by": cfg.AUTHOR_AGENT, "note": "a real reason"})
        self.assertEqual(status, 201)
        proposal_id = data["change"]["id"]
        status, view = self.call("GET", "/api/desk")
        self.assertEqual(view["queue"]["summary"]["proposed"], 1)
        status, data = self.call("POST", f"/api/changes/{proposal_id}/accept", {})
        self.assertEqual(data["change"]["state"], "queued")
        status, data = self.call("POST", "/api/send", {})
        self.assertTrue(data["sent"])
        self.assertEqual({r["state"] for r in data["results"]}, {"verified"})
        status, data = self.call("GET", "/api/queue")
        self.assertEqual(data["summary"]["pending"], 0)

    def test_rule_on_a_clipboard_row_over_http(self):
        item = next(i for i in self.service.desk.clipboard_items if i["state"] == "Open")
        status, data = self.call("POST", f"/api/clipboard/{item['id']}/rule", {"call": "Ruled from the desk", "state": "Adjudicated"})
        self.assertEqual(status, 201, data)
        self.assertEqual({c["property"] for c in data["changes"]}, {"Nathan call", "State", "Ruled"})
        self.assertEqual(data["item"]["state"], "Adjudicated")
        self.assertNotIn("props", data["item"])
        status, data = self.call("POST", f"/api/clipboard/{item['id']}/rule", {"call": "", "state": "Adjudicated"})
        self.assertEqual(status, 400)
        self.assertEqual(self.call("POST", "/api/clipboard/zzz/rule", {"call": "x"})[0], 404)
        self.assertEqual(self.call("POST", f"/api/clipboard/{item['id']}/nothing", {})[0], 404)
        status, data = self.call("POST", "/api/send", {})
        self.assertTrue(data["sent"])
        self.assertEqual({r["state"] for r in data["results"]}, {"verified"})

    def test_unknown_routes(self):
        self.assertEqual(self.call("GET", "/api/nothing")[0], 404)
        self.assertEqual(self.call("POST", "/api/changes/zzz/accept", {})[0], 404)


if __name__ == "__main__":
    unittest.main()
