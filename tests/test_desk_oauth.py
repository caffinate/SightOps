import json
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from desk.notion.oauth import NotionOAuth, OAuthError, TokenStore, discover


class FakeAuthServer(BaseHTTPRequestHandler):
    base = ""
    token_requests = []

    def log_message(self, *_):
        return

    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/.well-known/oauth-protected-resource":
            self._json(200, {"resource": self.base + "/mcp", "authorization_servers": [self.base]})
        elif self.path == "/.well-known/oauth-authorization-server":
            self._json(200, {"issuer": self.base, "authorization_endpoint": self.base + "/authorize", "token_endpoint": self.base + "/token",
                             "registration_endpoint": self.base + "/register", "code_challenge_methods_supported": ["S256"]})
        else:
            self._json(404, {"error": "nope"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode()
        if self.path == "/register":
            payload = json.loads(raw)
            self.assertions = payload
            self._json(201, {"client_id": "client-123", "redirect_uris": payload["redirect_uris"]})
        elif self.path == "/token":
            fields = dict(urllib.parse.parse_qsl(raw))
            FakeAuthServer.token_requests.append(fields)
            if fields.get("grant_type") == "authorization_code" and fields.get("code") == "good-code" and fields.get("code_verifier"):
                self._json(200, {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600, "token_type": "bearer"})
            elif fields.get("grant_type") == "refresh_token" and fields.get("refresh_token") == "rt-1":
                self._json(200, {"access_token": "at-2", "refresh_token": "rt-2", "expires_in": 3600, "token_type": "bearer"})
            else:
                self._json(400, {"error": "invalid_grant"})
        else:
            self._json(404, {"error": "nope"})


class OAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeAuthServer)
        FakeAuthServer.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TokenStore(Path(self.tmp.name) / "auth.json")
        self.flow = NotionOAuth(FakeAuthServer.base + "/mcp", store=self.store, redirect_port=18765, open_browser=False)
        FakeAuthServer.token_requests = []

    def tearDown(self):
        self.tmp.cleanup()

    def test_discovery_registration_and_exchange(self):
        metadata = discover(FakeAuthServer.base + "/mcp")
        self.assertEqual(metadata["token_endpoint"], FakeAuthServer.base + "/token")
        client_id = self.flow.register_client(metadata)
        self.assertEqual(client_id, "client-123")
        url = self.flow.authorization_url(metadata, client_id, "state-1", "verifier-verifier-verifier-verifier-verifier")
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.assertEqual(query["code_challenge_method"], "S256")
        self.assertEqual(query["redirect_uri"], "http://127.0.0.1:18765/callback")
        token = self.flow.exchange(metadata, client_id, "good-code", "verifier-verifier-verifier-verifier-verifier")
        self.assertEqual(token["access_token"], "at-1")
        self.assertEqual(self.store.access_token, "at-1")
        self.assertEqual(json.loads(self.store.path.read_text())["refresh_token"], "rt-1")

    def test_refresh_when_expired(self):
        metadata = discover(FakeAuthServer.base + "/mcp")
        self.flow.register_client(metadata)
        self.flow.exchange(metadata, "client-123", "good-code", "v" * 43)
        self.store.data["expires_at"] = time.time() - 10
        self.assertEqual(self.flow.token(), "at-2")
        self.assertEqual(FakeAuthServer.token_requests[-1]["grant_type"], "refresh_token")

    def test_bad_code_is_an_error(self):
        metadata = discover(FakeAuthServer.base + "/mcp")
        self.flow.register_client(metadata)
        with self.assertRaises(OAuthError):
            self.flow.exchange(metadata, "client-123", "bad-code", "v" * 43)

    def test_loopback_receives_the_code(self):
        result = {}

        def waiter():
            result["code"] = self.flow.wait_for_code("state-xyz", timeout=10)

        thread = threading.Thread(target=waiter)
        thread.start()
        time.sleep(0.2)
        with urllib.request.urlopen("http://127.0.0.1:18765/callback?code=abc&state=state-xyz", timeout=5) as response:
            self.assertEqual(response.status, 200)
        thread.join(5)
        self.assertEqual(result.get("code"), "abc")


if __name__ == "__main__":
    unittest.main()
