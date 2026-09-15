"""OAuth 2.1 for a hosted MCP server, standard library only.

Discovery (RFC 8414), dynamic client registration (RFC 7591), PKCE, a
loopback redirect, and refresh. Tokens are stored under DESK_HOME, never in
the repository. This gives the desk a connection of its own rather than a
personal integration token; which pages that connection may see is granted in
Notion when the authorisation completes.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

from .. import config as cfg


class OAuthError(Exception):
    pass


def _get_json(url: str, opener: Any) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with opener.open(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, payload: dict[str, Any], opener: Any) -> dict[str, Any]:
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
    with opener.open(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_form(url: str, fields: dict[str, str], opener: Any) -> dict[str, Any]:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}, method="POST")
    try:
        with opener.open(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise OAuthError(f"{url} answered {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}") from exc


def discover(mcp_url: str, opener: Any = None) -> dict[str, Any]:
    """Authorization server metadata for the MCP server at mcp_url."""
    opener = opener or urllib.request.build_opener()
    parts = urllib.parse.urlsplit(mcp_url)
    origin = f"{parts.scheme}://{parts.netloc}"
    issuer = origin
    try:
        resource = _get_json(f"{origin}/.well-known/oauth-protected-resource", opener)
        servers = resource.get("authorization_servers") or []
        if servers:
            issuer = servers[0].rstrip("/")
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        pass
    for candidate in (f"{issuer}/.well-known/oauth-authorization-server", f"{origin}/.well-known/oauth-authorization-server"):
        try:
            metadata = _get_json(candidate, opener)
        except (urllib.error.URLError, json.JSONDecodeError, OSError):
            continue
        if metadata.get("authorization_endpoint") and metadata.get("token_endpoint"):
            return metadata
    raise OAuthError(f"no authorization server metadata found for {mcp_url}")


class TokenStore:
    def __init__(self, path: Path | str = cfg.NOTION_AUTH_PATH) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = {}
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @property
    def access_token(self) -> str | None:
        return self.data.get("access_token")

    def expired(self, skew: int = 60) -> bool:
        expires_at = self.data.get("expires_at")
        return bool(expires_at) and time.time() > float(expires_at) - skew


class NotionOAuth:
    def __init__(self, mcp_url: str = cfg.NOTION_MCP_URL, store: TokenStore | None = None, opener: Any = None,
                 redirect_port: int = 8765, open_browser: bool = True) -> None:
        self.mcp_url = mcp_url
        self.store = store or TokenStore()
        self.opener = opener or urllib.request.build_opener()
        self.redirect_port = redirect_port
        self.open_browser = open_browser

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.redirect_port}/callback"

    def register_client(self, metadata: dict[str, Any]) -> str:
        if self.store.data.get("client_id") and self.store.data.get("token_endpoint") == metadata["token_endpoint"]:
            return self.store.data["client_id"]
        endpoint = metadata.get("registration_endpoint")
        if not endpoint:
            raise OAuthError("the authorization server does not offer dynamic client registration; set client_id by hand in the token store")
        registered = _post_json(endpoint, {
            "client_name": "Desk Harness",
            "redirect_uris": [self.redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }, self.opener)
        self.store.data.update({"client_id": registered["client_id"], "token_endpoint": metadata["token_endpoint"],
                                "authorization_endpoint": metadata["authorization_endpoint"]})
        self.store.save()
        return registered["client_id"]

    def authorization_url(self, metadata: dict[str, Any], client_id: str, state: str, verifier: str) -> str:
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": self.redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if metadata.get("scopes_supported"):
            params["scope"] = " ".join(metadata["scopes_supported"])
        return metadata["authorization_endpoint"] + "?" + urllib.parse.urlencode(params)

    def wait_for_code(self, state: str, timeout: float = 300.0) -> str:
        """Run a loopback server until the browser comes back with the code."""
        received: dict[str, str] = {}
        done = threading.Event()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                if query.get("state", [None])[0] != state:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"state mismatch")
                    return
                received["code"] = query.get("code", [""])[0]
                received["error"] = query.get("error", [""])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"Desk Harness is connected to Notion. You can close this tab.")
                done.set()

            def log_message(self, *_: Any) -> None:
                return

        server = http.server.HTTPServer(("127.0.0.1", self.redirect_port), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            if not done.wait(timeout):
                raise OAuthError("timed out waiting for the browser to come back")
        finally:
            server.shutdown()
            server.server_close()
        if received.get("error"):
            raise OAuthError(f"authorization refused: {received['error']}")
        return received["code"]

    def exchange(self, metadata: dict[str, Any], client_id: str, code: str, verifier: str) -> dict[str, Any]:
        token = _post_form(metadata["token_endpoint"], {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        }, self.opener)
        self._store_token(token)
        return token

    def _store_token(self, token: dict[str, Any]) -> None:
        self.store.data["access_token"] = token["access_token"]
        if token.get("refresh_token"):
            self.store.data["refresh_token"] = token["refresh_token"]
        if token.get("expires_in"):
            self.store.data["expires_at"] = time.time() + float(token["expires_in"])
        self.store.data["obtained_at"] = time.time()
        self.store.save()

    def login(self) -> str:
        metadata = discover(self.mcp_url, self.opener)
        client_id = self.register_client(metadata)
        state = secrets.token_urlsafe(16)
        verifier = secrets.token_urlsafe(48)
        url = self.authorization_url(metadata, client_id, state, verifier)
        print(f"Open this address to connect Desk Harness to Notion:\n{url}")
        if self.open_browser:
            webbrowser.open(url)
        code = self.wait_for_code(state)
        self.exchange(metadata, client_id, code, verifier)
        return self.store.access_token or ""

    def refresh(self) -> str:
        refresh_token = self.store.data.get("refresh_token")
        endpoint = self.store.data.get("token_endpoint")
        client_id = self.store.data.get("client_id")
        if not (refresh_token and endpoint and client_id):
            raise OAuthError("no refresh token on file; run the login again")
        token = _post_form(endpoint, {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id}, self.opener)
        self._store_token(token)
        return self.store.access_token or ""

    def token(self) -> str | None:
        """A usable access token, refreshed if it has expired."""
        if self.store.access_token and self.store.expired():
            try:
                return self.refresh()
            except OAuthError:
                return None
        return self.store.access_token
