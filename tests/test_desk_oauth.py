import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from desk.notion.oauth import NotionOAuth, OAuthError, TokenStore, discover, parse_challenge


class FakeAuthServer(BaseHTTPRequestHandler):
    """A stand-in for mcp.notion.com: the MCP endpoint's challenge, both well-known documents, registration, tokens."""

    base = ""
    token_requests = []
    registrations = []
    challenge = True            # the MCP endpoint answers 401 with a WWW-Authenticate challenge
    resource_metadata = True    # protected resource metadata is published at all
    root_form = True            # ... at the root well-known location as well as the path-insertion one
    issue_secret = False        # registration hands out a client secret
    reject_registration = False

    @classmethod
    def reset(cls):
        cls.token_requests = []
        cls.registrations = []
        cls.challenge = True
        cls.resource_metadata = True
        cls.root_form = True
        cls.issue_secret = False
        cls.reject_registration = False

    def log_message(self, *_):
        return

    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        cls = FakeAuthServer
        resource = {"resource": cls.base + "/mcp", "authorization_servers": [cls.base], "scopes_supported": ["read", "write"]}
        if self.path == "/.well-known/oauth-protected-resource/mcp" and cls.resource_metadata:
            self._json(200, resource)
        elif self.path == "/.well-known/oauth-protected-resource" and cls.resource_metadata and cls.root_form:
            self._json(200, resource)
        elif self.path == "/.well-known/oauth-authorization-server":
            self._json(200, {"issuer": cls.base, "authorization_endpoint": cls.base + "/authorize", "token_endpoint": cls.base + "/token",
                             "registration_endpoint": cls.base + "/register", "code_challenge_methods_supported": ["S256"],
                             "scopes_supported": ["read", "write", "admin"]})
        else:
            self._json(404, {"error": "nope"})

    def do_POST(self):
        cls = FakeAuthServer
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode()
        if self.path == "/mcp":
            self.send_response(401)
            if cls.challenge:
                self.send_header("WWW-Authenticate", f'Bearer realm="mcp", resource_metadata="{cls.base}/.well-known/oauth-protected-resource/mcp", scope="read write"')
            self.end_headers()
            self.wfile.write(b"unauthorized")
        elif self.path == "/register":
            payload = json.loads(raw)
            cls.registrations.append(payload)
            if cls.reject_registration:
                self._json(400, {"error": "invalid_redirect_uri", "error_description": "loopback redirects must name a registered port"})
                return
            answer = {"client_id": "client-123", "redirect_uris": payload["redirect_uris"], "token_endpoint_auth_method": "none"}
            if cls.issue_secret:
                answer.update({"client_id": "client-456", "client_secret": "s3cret", "token_endpoint_auth_method": "client_secret_post"})
            self._json(201, answer)
        elif self.path == "/token":
            fields = dict(urllib.parse.parse_qsl(raw))
            cls.token_requests.append(fields)
            if cls.issue_secret and fields.get("client_secret") != "s3cret":
                self._json(401, {"error": "invalid_client"})
            elif fields.get("grant_type") == "authorization_code" and fields.get("code") == "good-code" and fields.get("code_verifier"):
                self._json(200, {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600, "token_type": "bearer"})
            elif fields.get("grant_type") == "refresh_token" and fields.get("refresh_token") == "rt-1":
                self._json(200, {"access_token": "at-2", "refresh_token": "rt-2", "expires_in": 3600, "token_type": "bearer"})
            else:
                self._json(400, {"error": "invalid_grant"})
        else:
            self._json(404, {"error": "nope"})


def _query_of(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


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
        FakeAuthServer.reset()
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TokenStore(Path(self.tmp.name) / "auth.json")
        self.flow = NotionOAuth(FakeAuthServer.base + "/mcp", store=self.store, redirect_port=18765, open_browser=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_discovery_registration_and_exchange(self):
        metadata = discover(FakeAuthServer.base + "/mcp")
        self.assertEqual(metadata["token_endpoint"], FakeAuthServer.base + "/token")
        self.assertEqual(metadata["protected_resource"]["resource"], FakeAuthServer.base + "/mcp")
        self.assertEqual(metadata["challenge"]["scope"], "read write")
        client_id = self.flow.register_client(metadata)
        self.assertEqual(client_id, "client-123")
        self.assertEqual(FakeAuthServer.registrations[-1]["redirect_uris"], ["http://127.0.0.1:18765/callback"])
        self.assertEqual(FakeAuthServer.registrations[-1]["token_endpoint_auth_method"], "none")
        url = self.flow.authorization_url(metadata, client_id, "state-1", "verifier-verifier-verifier-verifier-verifier")
        query = _query_of(url)
        self.assertEqual(query["code_challenge_method"], "S256")
        self.assertEqual(query["redirect_uri"], "http://127.0.0.1:18765/callback")
        self.assertEqual(query["resource"], FakeAuthServer.base + "/mcp")
        self.assertEqual(query["scope"], "read write")
        token = self.flow.exchange(metadata, client_id, "good-code", "verifier-verifier-verifier-verifier-verifier")
        self.assertEqual(token["access_token"], "at-1")
        self.assertEqual(self.store.access_token, "at-1")
        saved = json.loads(self.store.path.read_text())
        self.assertEqual(saved["refresh_token"], "rt-1")
        self.assertNotIn("client_secret", saved)
        sent = FakeAuthServer.token_requests[-1]
        self.assertEqual(sent["resource"], FakeAuthServer.base + "/mcp")
        self.assertEqual(sent["client_id"], "client-123")
        self.assertNotIn("client_secret", sent)

    def test_discovery_without_a_challenge_or_root_document(self):
        FakeAuthServer.challenge = False
        FakeAuthServer.root_form = False
        metadata = discover(FakeAuthServer.base + "/mcp")
        self.assertEqual(metadata["protected_resource"]["resource"], FakeAuthServer.base + "/mcp")
        self.assertEqual(metadata["challenge"], {})
        self.assertEqual(_query_of(self.flow.authorization_url(metadata, "c", "s", "v" * 43))["scope"], "read write")

    def test_no_scope_is_guessed_from_the_authorization_server_alone(self):
        FakeAuthServer.challenge = False
        FakeAuthServer.resource_metadata = False
        metadata = discover(FakeAuthServer.base + "/mcp")
        self.assertEqual(metadata["protected_resource"], {})
        self.assertEqual(metadata["token_endpoint"], FakeAuthServer.base + "/token")
        self.assertNotIn("scope", _query_of(self.flow.authorization_url(metadata, "c", "s", "v" * 43)))

    def test_refresh_when_expired(self):
        metadata = discover(FakeAuthServer.base + "/mcp")
        self.flow.register_client(metadata)
        self.flow.exchange(metadata, "client-123", "good-code", "v" * 43)
        self.store.data["expires_at"] = time.time() - 10
        self.assertEqual(self.flow.token(), "at-2")
        self.assertEqual(FakeAuthServer.token_requests[-1]["grant_type"], "refresh_token")
        self.assertEqual(FakeAuthServer.token_requests[-1]["resource"], FakeAuthServer.base + "/mcp")

    def test_bad_code_is_an_error(self):
        metadata = discover(FakeAuthServer.base + "/mcp")
        self.flow.register_client(metadata)
        with self.assertRaises(OAuthError):
            self.flow.exchange(metadata, "client-123", "bad-code", "v" * 43)

    def test_registration_refusal_is_readable(self):
        FakeAuthServer.reject_registration = True
        metadata = discover(FakeAuthServer.base + "/mcp")
        with self.assertRaises(OAuthError) as caught:
            self.flow.register_client(metadata)
        self.assertIn("invalid_redirect_uri", str(caught.exception))

    def test_registration_is_reused_until_the_redirect_changes(self):
        metadata = discover(FakeAuthServer.base + "/mcp")
        self.flow.register_client(metadata)
        self.flow.register_client(metadata)
        self.assertEqual(len(FakeAuthServer.registrations), 1)
        other = NotionOAuth(FakeAuthServer.base + "/mcp", store=self.store, redirect_port=18767, open_browser=False)
        other.register_client(metadata)
        self.assertEqual(len(FakeAuthServer.registrations), 2)
        self.assertEqual(FakeAuthServer.registrations[-1]["redirect_uris"], ["http://127.0.0.1:18767/callback"])

    def test_confidential_client_sends_its_secret(self):
        FakeAuthServer.issue_secret = True
        metadata = discover(FakeAuthServer.base + "/mcp")
        client_id = self.flow.register_client(metadata)
        self.assertEqual(client_id, "client-456")
        self.flow.exchange(metadata, client_id, "good-code", "v" * 43)
        self.assertEqual(FakeAuthServer.token_requests[-1]["client_secret"], "s3cret")
        again = NotionOAuth(FakeAuthServer.base + "/mcp", store=TokenStore(self.store.path), open_browser=False)
        again.store.data["expires_at"] = time.time() - 10
        self.assertEqual(again.token(), "at-2")
        self.assertEqual(FakeAuthServer.token_requests[-1]["grant_type"], "refresh_token")
        self.assertEqual(FakeAuthServer.token_requests[-1]["client_secret"], "s3cret")

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

    def test_loopback_reports_a_refusal(self):
        result = {}

        def waiter():
            try:
                self.flow.wait_for_code("state-xyz", timeout=10)
            except OAuthError as exc:
                result["error"] = str(exc)

        thread = threading.Thread(target=waiter)
        thread.start()
        time.sleep(0.2)
        with urllib.request.urlopen("http://127.0.0.1:18765/callback?error=access_denied&error_description=Nathan+said+no&state=state-xyz", timeout=5):
            pass
        thread.join(5)
        self.assertIn("access_denied", result.get("error", ""))
        self.assertIn("Nathan said no", result.get("error", ""))

    def test_loopback_port_in_use_is_an_error(self):
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", 18766))
        blocker.listen(1)
        try:
            flow = NotionOAuth(FakeAuthServer.base + "/mcp", store=self.store, redirect_port=18766, open_browser=False)
            with self.assertRaises(OAuthError) as caught:
                flow.wait_for_code("s", timeout=1)
            self.assertIn("18766", str(caught.exception))
        finally:
            blocker.close()

    def test_forget_clears_the_store(self):
        self.store.data = {"client_id": "x", "access_token": "y"}
        self.store.save()
        self.flow.forget()
        self.assertFalse(self.store.path.exists())
        self.assertEqual(TokenStore(self.store.path).data, {})


class ChallengeParsingTests(unittest.TestCase):
    def test_bearer_parameters(self):
        parsed = parse_challenge('Bearer realm="mcp", resource_metadata="https://mcp.example/.well-known/oauth-protected-resource/mcp", scope="read write"')
        self.assertEqual(parsed["resource_metadata"], "https://mcp.example/.well-known/oauth-protected-resource/mcp")
        self.assertEqual(parsed["scope"], "read write")
        self.assertEqual(parse_challenge(None), {})
        self.assertEqual(parse_challenge('Basic realm="x"'), {})


if __name__ == "__main__":
    unittest.main()
