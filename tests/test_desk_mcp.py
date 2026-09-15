import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from desk.notion.client import MCPNotionClient, parse_fetch_properties, parse_fetch_schema, parse_results
from desk.notion.mcp import MCPAuthError, MCPClient, MCPError, parse_sse

FETCH_PAGE = json.dumps({"metadata": {"type": "page"}, "text": "<page url=\"x\">\n<properties>\n{\"Status\":\"Done\",\"date:Due Date:start\":\"2026-09-26\",\"Owner\":\"[\\\"user://1cb13004-8266-40b6-92da-63771be9b248\\\"]\"}\n</properties>\n</page>"})
FETCH_DS = "<data-source url=\"{{collection://ab}}\">\n<data-source-state>\n{\"name\":\"Tasks\",\"schema\":{\"Task\":{\"type\":\"title\"},\"Status\":{\"type\":\"status\",\"groups\":{\"complete\":[{\"name\":\"Done\"}],\"to_do\":[{\"name\":\"Not started\"}]}}}}\n</data-source-state>\n</data-source>"


class FakeMCPHandler(BaseHTTPRequestHandler):
    calls = []
    require_token = "secret"

    def log_message(self, *_):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        message = json.loads(self.rfile.read(length))
        FakeMCPHandler.calls.append({"headers": {k.lower(): v for k, v in self.headers.items()}, "message": message})
        if self.headers.get("Authorization") != f"Bearer {self.require_token}":
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Bearer resource_metadata="https://x/.well-known/oauth-protected-resource"')
            self.end_headers()
            self.wfile.write(b"unauthorized")
            return
        method = message.get("method")
        if method == "initialize":
            body = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "fake"}}})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Mcp-Session-Id", "sess-1")
            self.end_headers()
            self.wfile.write(body.encode())
            return
        if method == "notifications/initialized":
            self.send_response(202)
            self.end_headers()
            return
        if method == "tools/call":
            name = message["params"]["name"]
            if name == "notion-update-page":
                result = {"content": [{"type": "text", "text": json.dumps({"ok": True})}], "isError": False}
                payload = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result})
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b"event: message\ndata: " + payload.encode() + b"\n\n")
                return
            if name == "notion-fetch":
                text = FETCH_PAGE if not message["params"]["arguments"]["id"].startswith("collection") else FETCH_DS
                result = {"content": [{"type": "text", "text": text}]}
            elif name == "notion-query-data-sources":
                result = {"content": [{"type": "text", "text": json.dumps({"results": [{"url": "https://app.notion.com/" + "1" * 32, "Task": "A"}]})}]}
            elif name == "broken":
                result = {"content": [{"type": "text", "text": "boom"}], "isError": True}
            else:
                result = {"content": [{"type": "text", "text": json.dumps({"results": [], "has_more": False})}]}
            body = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())
            return
        self.send_response(404)
        self.end_headers()


class MCPClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeMCPHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/mcp"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        FakeMCPHandler.calls = []

    def test_initialize_then_call_with_session_id_and_sse_response(self):
        client = MCPClient(self.url, token_provider=lambda: "secret")
        result = client.call_tool("notion-update-page", {"page_id": "x", "command": "update_properties", "properties": {"Status": "Done"}})
        self.assertEqual(json.loads(result.text), {"ok": True})
        methods = [c["message"].get("method") for c in FakeMCPHandler.calls]
        self.assertEqual(methods, ["initialize", "notifications/initialized", "tools/call"])
        self.assertEqual(FakeMCPHandler.calls[2]["headers"].get("mcp-session-id"), "sess-1")
        self.assertEqual(FakeMCPHandler.calls[2]["headers"].get("mcp-protocol-version"), "2025-06-18")
        self.assertEqual(FakeMCPHandler.calls[2]["headers"].get("authorization"), "Bearer secret")

    def test_missing_credential_is_an_auth_error(self):
        client = MCPClient(self.url, token_provider=lambda: None)
        with self.assertRaises(MCPAuthError):
            client.call_tool("notion-fetch", {"id": "x"})

    def test_tool_errors_raise(self):
        client = MCPClient(self.url, token_provider=lambda: "secret")
        with self.assertRaises(MCPError):
            client.call_tool("broken", {})

    def test_notion_client_parses_fetch_query_and_readback(self):
        notion = MCPNotionClient(MCPClient(self.url, token_provider=lambda: "secret"))
        title, schema = notion.fetch_schema("ab" * 16)
        self.assertEqual(title, "Tasks")
        self.assertEqual(schema["Status"]["type"], "status")
        rows = notion.query_rows("ab" * 16)
        self.assertEqual(rows[0]["Task"], "A")
        props = notion.page_properties("1" * 32)
        self.assertEqual(props["Status"], "Done")
        self.assertEqual(notion.list_users(), [])
        query_call = next(c for c in FakeMCPHandler.calls if c["message"].get("params", {}).get("name") == "notion-query-data-sources")
        self.assertIn('SELECT * FROM "collection://abababab-abab-abab-abab-abababababab"', query_call["message"]["params"]["arguments"]["data"]["query"])


class ParserTests(unittest.TestCase):
    def test_sse_parsing_collects_json_payloads(self):
        body = 'event: message\ndata: {"a": 1}\n\ndata: not json\n\ndata: {"b":\ndata: 2}\n\n'
        self.assertEqual(parse_sse(body), [{"a": 1}, {"b": 2}])

    def test_fetch_parsers_accept_envelope_or_markup(self):
        self.assertEqual(parse_fetch_properties(FETCH_PAGE)["Status"], "Done")
        title, schema = parse_fetch_schema(FETCH_DS)
        self.assertEqual((title, list(schema)), ("Tasks", ["Task", "Status"]))
        self.assertEqual(parse_results('{"results": [1, 2]}'), [1, 2])
        with self.assertRaises(MCPError):
            parse_fetch_properties("<page/>")


if __name__ == "__main__":
    unittest.main()
