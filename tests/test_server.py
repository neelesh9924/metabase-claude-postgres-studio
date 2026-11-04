import http.client
import json
import unittest

from support import PNG, StudioCase, serve, watching_shares

from app import attachments, config

DRAFT = "draft-0123456789ab"


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

    def test_one_studio_per_port(self):
        """A second server on the same port would break the one-query-at-a-time rule."""
        from app import server

        with self.assertRaises(OSError):
            server.make_server("another-token")
        self.assertEqual(self.ask("GET", "/health")[0].status, 200)

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
        self.assertEqual(self.ask("GET", "/static/vendor/echarts.min.js")[0].status, 200)

    def test_a_share_is_never_opened(self):
        """No cookie is needed for /static: a page open in the browser could ask for //host/share."""
        tries = ["/static///attacker.example/share/x", "/static/%2F%2Fattacker.example%2Fshare%2Fx",
                 "/static/%5C%5Cattacker.example%5Cshare%5Cx", "/static/\\\\attacker.example\\share\\x",
                 "/static/C:/Windows/win.ini", "/static/app.js::$DATA"]
        with watching_shares() as tried:
            for path in tries:
                self.assertEqual(self.ask("GET", path)[0].status, 404, path)
        self.assertEqual(tried, [])

    def attach(self):
        """A draft's conversation with a picture and a CSV attached, and a stand-in secret beside the folders."""
        folder = config.ATTACH_DIR / DRAFT
        records = [attachments.save(folder, "chart.png", PNG), attachments.save(folder, "targets.csv", b"Region,Target\nNorth,120\n")]
        (folder / ".hidden.txt").write_text("not for the page", encoding="utf-8")
        (self.tmp / "data").mkdir(exist_ok=True)
        (self.tmp / "data" / "settings.json").write_text('{"password": "stand-in"}', encoding="utf-8")
        config.THREADS_DIR.mkdir(parents=True, exist_ok=True)
        (config.THREADS_DIR / f"{DRAFT}.json").write_text(json.dumps(
            [{"id": "m1", "role": "user", "text": "Like these", "at": "2026-10-27T10:00:00", "files": records}]), encoding="utf-8")

    def test_an_attached_file_comes_back_through_its_conversation(self):
        self.attach()
        files = json.loads(self.ask("GET", f"/api/thread?key={DRAFT}", Cookie=self.cookie)[1])["messages"][0]["files"]
        self.assertEqual([(f["href"], f["view"]) for f in files],
                         [(f"/api/file/{DRAFT}/chart.png", True), (f"/api/file/{DRAFT}/targets.csv", False)])
        response, data = self.ask("GET", files[0]["href"], Cookie=self.cookie)
        self.assertEqual((response.status, data, response.getheader("Content-Type")), (200, PNG, "image/png"))
        self.assertTrue(response.getheader("Content-Disposition").startswith("inline;"))
        self.assertTrue(response.getheader("Content-Security-Policy").startswith("sandbox;"))
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
        response, data = self.ask("GET", files[1]["href"], Cookie=self.cookie)
        self.assertEqual((response.status, response.getheader("Content-Type")), (200, "application/octet-stream"))
        self.assertTrue(response.getheader("Content-Disposition").startswith("attachment;"))
        self.assertEqual(self.ask("GET", files[0]["href"])[0].status, 401)

    def test_an_attachment_path_cannot_escape_its_folder(self):
        self.attach()
        tries = [f"/api/file/{DRAFT}/../../data/settings.json", f"/api/file/{DRAFT}/%2e%2e/%2e%2e/data/settings.json",
                 f"/api/file/{DRAFT}/..%2F..%2Fdata%2Fsettings.json", "/api/file/..%2Fdata/settings.json",
                 "/api/file/../data/settings.json", f"/api/file/{DRAFT}/.hidden.txt", f"/api/file/{DRAFT}",
                 f"/api/file/{DRAFT}///attacker.example/share/x", f"/api/file/{DRAFT}/%5C%5Cattacker.example%5Cshare%5Cx",
                 f"/api/file/{DRAFT}/C:/Windows/win.ini", f"/api/file/{DRAFT}/chart.png::$DATA", "/api/file/Draft-X/chart.png"]
        with watching_shares() as tried:
            for path in tries:
                self.assertEqual(self.ask("GET", path, Cookie=self.cookie)[0].status, 404, path)
        self.assertEqual(tried, [])

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
