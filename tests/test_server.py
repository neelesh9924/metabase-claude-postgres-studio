import http.client
import json
import unittest

from support import StudioCase, serve

from app import config


class ServerTest(StudioCase):
    def setUp(self):
        super().setUp()
        serve(self, token="launch-token")
        self.cookie = "studio_session=launch-token"
        self.origin = f"http://127.0.0.1:{config.PORT}"

    def ask(self, method, path, body=None, **headers):
        conn = http.client.HTTPConnection("127.0.0.1", config.PORT, timeout=10)
        headers.setdefault("Host", f"127.0.0.1:{config.PORT}")
        payload = None
        if body is not None:
            payload = json.dumps(body)
            headers.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response, data

    def test_health_is_open_and_names_the_app(self):
        response, data = self.ask("GET", "/health")
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(data)["app"], config.APP_ID)

    def test_pages_need_the_launch_cookie(self):
        self.assertEqual(self.ask("GET", "/api/state")[0].status, 401)
        self.assertEqual(self.ask("GET", "/")[0].status, 401)
        self.assertEqual(self.ask("GET", "/api/state", Cookie="studio_session=wrong")[0].status, 401)
        response, data = self.ask("GET", "/api/state", Cookie=self.cookie)
        self.assertEqual(response.status, 200)
        self.assertIn("assistant", json.loads(data))

    def test_auth_sets_the_cookie_only_for_the_right_token(self):
        self.assertEqual(self.ask("GET", "/auth?token=nope")[0].status, 403)
        response, _ = self.ask("GET", "/auth?token=launch-token")
        self.assertEqual(response.status, 303)
        cookie = response.getheader("Set-Cookie")
        self.assertIn("studio_session=launch-token", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)

    def test_other_host_names_are_refused(self):
        self.assertEqual(self.ask("GET", "/health", Host="evil.example")[0].status, 421)
        self.assertEqual(self.ask("GET", "/health", Host=f"localhost:{config.PORT}")[0].status, 421)

    def test_changes_need_this_origin(self):
        body = {"mode": "new", "text": "x"}
        self.assertEqual(self.ask("POST", "/api/ask", body, Cookie=self.cookie)[0].status, 403)
        self.assertEqual(self.ask("POST", "/api/ask", body, Cookie=self.cookie, Origin="https://evil.example")[0].status, 403)
        self.assertEqual(self.ask("POST", "/api/ask", body, Origin=self.origin)[0].status, 401)
        response, _ = self.ask("POST", "/api/stop", {}, Cookie=self.cookie, Origin=self.origin)
        self.assertEqual(response.status, 200)

    def test_tool_server_needs_a_running_request(self):
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        self.assertEqual(self.ask("POST", "/mcp", message)[0].status, 401)
        self.assertEqual(self.ask("POST", "/mcp", message, Authorization="Bearer guess")[0].status, 401)
        # The page's own cookie is not a way in either.
        self.assertEqual(self.ask("POST", "/mcp", message, Cookie=self.cookie, Origin=self.origin)[0].status, 401)
        self.assertEqual(self.ask("GET", "/mcp")[0].status, 405)

    def test_static_files_cannot_escape(self):
        self.assertEqual(self.ask("GET", "/static/app.js")[0].status, 200)
        self.assertEqual(self.ask("GET", "/static/../.env")[0].status, 404)
        self.assertEqual(self.ask("GET", "/static/%2e%2e/.env")[0].status, 404)

    def test_a_card_runs_only_from_its_file(self):
        folder = self.tmp / "dashboards" / "ops"
        folder.mkdir()
        (folder / "n.sql").write_text("select 7 as n", encoding="utf-8")
        (folder / "dashboard.json").write_text(json.dumps({"name": "Ops", "cards": [
            {"key": "n", "name": "N", "display": "scalar", "row": 0, "col": 0, "size_x": 6, "size_y": 3}]}), encoding="utf-8")
        body = {"slug": "ops", "key": "n", "sql": "select 999"}
        response, data = self.ask("POST", "/api/run", body, Cookie=self.cookie, Origin=self.origin)
        self.assertEqual(response.status, 200)
        self.assertEqual(self.queries, [("preview ops/n", "select 7 as n")])
        self.assertEqual(json.loads(data)["result"]["rows"], [[7]])


if __name__ == "__main__":
    unittest.main()
