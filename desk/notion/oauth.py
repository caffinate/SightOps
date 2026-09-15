"""OAuth 2.1 for a hosted MCP server, standard library only.

Discovery (RFC 9728 protected resource metadata, RFC 8414 authorization
server metadata, the WWW-Authenticate challenge that points at them), dynamic
client registration (RFC 7591), PKCE, resource indicators (RFC 8707), a
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
import re
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

_AUTH_PARAM = re.compile(r'([A-Za-z_]+)\s*=\s*"([^"]*)"')


class OAuthError(Exception):
    pass


def _get_json(url: str, opener: Any) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with opener.open(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, payload: dict[str, Any], opener: Any) -> dict[str, Any]:
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
    try:
        with opener.open(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise OAuthError(f"{url} answered {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}") from exc


def _post_form(url: str, fields: dict[str, str], opener: Any, headers: dict[str, str] | None = None) -> dict[str, Any]:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    all_headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    all_headers.update(headers or {})
    request = urllib.request.Request(url, data=body, headers=all_headers, method="POST")
    try:
        with opener.open(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise OAuthError(f"{url} answered {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}") from exc


def parse_challenge(header: str | None) -> dict[str, str]:
    """The parameters of a `WWW-Authenticate: Bearer ...` challenge."""
    if not header or not header.strip().lower().startswith("bearer"):
        return {}
    return dict(_AUTH_PARAM.findall(header))


def challenge(mcp_url: str, opener: Any) -> dict[str, str]:
    """Ask the MCP server, unauthenticated, how it is protected.

    A compliant server answers 401 with a WWW-Authenticate challenge naming its
    resource metadata document and, sometimes, the scope to ask for.
    """
    body = json.dumps({"jsonrpc": "2.0", "id": 0, "method": "ping"}).encode("utf-8")
    request = urllib.request.Request(mcp_url, data=body, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    try:
        with opener.open(request, timeout=30):
            return {}
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403) and exc.headers:
            return parse_challenge(exc.headers.get("WWW-Authenticate"))
        return {}
    except (urllib.error.URLError, OSError):
        return {}


def _well_known(origin: str, path: str, kind: str) -> list[str]:
    """RFC 8414 / RFC 9728 well-known locations: the path-insertion form first, then the root form."""
    urls = []
    if path and path != "/":
        urls.append(f"{origin}/.well-known/{kind}{path}")
    urls.append(f"{origin}/.well-known/{kind}")
    return urls


def discover(mcp_url: str, opener: Any = None) -> dict[str, Any]:
    """Authorization server metadata for the MCP server at mcp_url.

    The returned metadata also carries `protected_resource` (the resource
    metadata document, or {} when the server publishes none) and `challenge`
    (the parameters of the server's WWW-Authenticate answer, or {}).
    """
    opener = opener or urllib.request.build_opener()
    parts = urllib.parse.urlsplit(mcp_url)
    origin = f"{parts.scheme}://{parts.netloc}"
    path = parts.path.rstrip("/")

    challenge_params = challenge(mcp_url, opener)
    resource: dict[str, Any] = {}
    candidates = [challenge_params["resource_metadata"]] if challenge_params.get("resource_metadata") else []
    candidates += _well_known(origin, path, "oauth-protected-resource")
    for candidate in candidates:
        try:
            loaded = _get_json(candidate, opener)
        except (urllib.error.URLError, json.JSONDecodeError, OSError, ValueError):
            continue
        if isinstance(loaded, dict) and loaded.get("authorization_servers"):
            resource = loaded
            break

    issuer = str((resource.get("authorization_servers") or [origin])[0]).rstrip("/")
    issuer_parts = urllib.parse.urlsplit(issuer)
    issuer_origin = f"{issuer_parts.scheme}://{issuer_parts.netloc}"
    issuer_path = issuer_parts.path.rstrip("/")
    candidates = (_well_known(issuer_origin, issuer_path, "oauth-authorization-server")
                  + _well_known(issuer_origin, issuer_path, "openid-configuration")
                  + ([f"{issuer}/.well-known/openid-configuration"] if issuer_path else [])
                  + _well_known(origin, "", "oauth-authorization-server"))
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            metadata = _get_json(candidate, opener)
        except (urllib.error.URLError, json.JSONDecodeError, OSError, ValueError):
            continue
        if isinstance(metadata, dict) and metadata.get("authorization_endpoint") and metadata.get("token_endpoint"):
            metadata["protected_resource"] = resource
            metadata["challenge"] = challenge_params
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

    def clear(self) -> None:
        self.data = {}
        if self.path.exists():
            self.path.unlink()

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

    @property
    def resource(self) -> str:
        """The canonical URI of the MCP server, sent as the RFC 8707 resource indicator."""
        return self.mcp_url

    def forget(self) -> None:
        """Drop the stored registration and tokens so the next login starts from scratch."""
        self.store.clear()

    # ---- registration -----------------------------------------------------
    def register_client(self, metadata: dict[str, Any]) -> str:
        data = self.store.data
        if (data.get("client_id") and data.get("token_endpoint") == metadata["token_endpoint"]
                and data.get("redirect_uri", self.redirect_uri) == self.redirect_uri):
            return data["client_id"]
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
        if not isinstance(registered, dict) or not registered.get("client_id"):
            raise OAuthError(f"{endpoint} returned no client_id: {json.dumps(registered)[:300]}")
        data.update({
            "client_id": registered["client_id"],
            "token_endpoint": metadata["token_endpoint"],
            "authorization_endpoint": metadata["authorization_endpoint"],
            "redirect_uri": self.redirect_uri,
            "token_endpoint_auth_method": registered.get("token_endpoint_auth_method") or "none",
        })
        if registered.get("client_secret"):
            data["client_secret"] = registered["client_secret"]
        else:
            data.pop("client_secret", None)
        self.store.save()
        return data["client_id"]

    # ---- authorization ----------------------------------------------------
    def _scope(self, metadata: dict[str, Any]) -> str | None:
        """The scope to ask for: what the server's challenge named, else what its resource metadata lists, else nothing."""
        named = (metadata.get("challenge") or {}).get("scope")
        if named:
            return named
        supported = (metadata.get("protected_resource") or {}).get("scopes_supported")
        return " ".join(supported) if supported else None

    def authorization_url(self, metadata: dict[str, Any], client_id: str, state: str, verifier: str) -> str:
        code_challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": self.redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "resource": self.resource,
        }
        scope = self._scope(metadata)
        if scope:
            params["scope"] = scope
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
                received["error_description"] = query.get("error_description", [""])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"Desk Harness is connected to Notion. You can close this tab.")
                done.set()

            def log_message(self, *_: Any) -> None:
                return

        try:
            server = http.server.HTTPServer(("127.0.0.1", self.redirect_port), Handler)
        except OSError as exc:
            raise OAuthError(f"cannot listen at {self.redirect_uri} for the browser to come back ({exc}); free the port or pass another redirect port") from exc
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            if not done.wait(timeout):
                raise OAuthError("timed out waiting for the browser to come back")
        finally:
            server.shutdown()
            server.server_close()
        if received.get("error"):
            detail = f": {received['error_description']}" if received.get("error_description") else ""
            raise OAuthError(f"authorization refused: {received['error']}{detail}")
        if not received.get("code"):
            raise OAuthError("the browser came back without a code")
        return received["code"]

    # ---- tokens -----------------------------------------------------------
    def _client_auth(self, fields: dict[str, str]) -> dict[str, str]:
        """Client authentication at the token endpoint, the way the client was registered."""
        secret = self.store.data.get("client_secret")
        if not secret:
            return {}
        if self.store.data.get("token_endpoint_auth_method") == "client_secret_basic":
            raw = f"{urllib.parse.quote(fields['client_id'], safe='')}:{urllib.parse.quote(secret, safe='')}".encode("utf-8")
            return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}
        fields["client_secret"] = secret
        return {}

    def exchange(self, metadata: dict[str, Any], client_id: str, code: str, verifier: str) -> dict[str, Any]:
        fields = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
            "resource": self.resource,
        }
        headers = self._client_auth(fields)
        token = _post_form(metadata["token_endpoint"], fields, self.opener, headers)
        if not token.get("access_token"):
            raise OAuthError(f"the token endpoint returned no access_token: {json.dumps(token)[:300]}")
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
        fields = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id, "resource": self.resource}
        headers = self._client_auth(fields)
        token = _post_form(endpoint, fields, self.opener, headers)
        if not token.get("access_token"):
            raise OAuthError(f"the token endpoint returned no access_token on refresh: {json.dumps(token)[:300]}")
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
