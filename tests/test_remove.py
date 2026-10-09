import http.client
import json
import unittest
from unittest import mock

from support import StudioCase, serve

from app import config, specs


class RemoveTest(StudioCase):
    def make(self, slug="ops"):
        folder = self.tmp / "dashboards" / slug
        folder.mkdir()
        (folder / "n.sql").write_text("select 7 as n", encoding="utf-8")
        (folder / "dashboard.json").write_text(json.dumps({"name": "Ops", "cards": [
            {"key": "n", "name": "N", "display": "scalar", "row": 0, "col": 0, "size_x": 6, "size_y": 3}]}), encoding="utf-8")
        (folder / "metabase.json").write_text(json.dumps({"dashboard_id": 12, "url": "http://mb/dashboard/12"}), encoding="utf-8")

    def post(self, path, body):
        conn = http.client.HTTPConnection("127.0.0.1", config.PORT, timeout=20)
        conn.request("POST", path, body=json.dumps(body), headers={
            "Host": f"127.0.0.1:{config.PORT}", "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{config.PORT}", "Cookie": "studio_session=test-token"})
        reply = json.loads(conn.getresponse().read())
        conn.close()
        return reply

    def test_a_removed_dashboard_leaves_the_studio_and_can_be_put_back(self):
        self.make()
        moved = specs.remove("ops")
        self.assertEqual(specs.slugs(), [])
        self.assertEqual(moved.parent, self.tmp / "data" / "trash")
        self.assertEqual(sorted(p.name for p in moved.iterdir()), ["dashboard.json", "metabase.json", "n.sql"])
        self.assertIsNone(specs.remove("ops"))
        for bad in ["../data", "", None, "no/such"]:
            self.assertIsNone(specs.remove(bad))

    def test_from_the_page_and_metabase_is_left_alone(self):
        assistant = serve(self).studio.assistant
        self.make()
        assistant._add("ops", "user", "make it blue")
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("Metabase must not be called")):
            self.assertEqual(self.post("/api/remove", {"slug": "ops"}), {"ok": True})
        self.assertEqual(specs.slugs(), [])
        trashed = next((self.tmp / "data" / "trash").iterdir())
        self.assertIn("make it blue", (trashed / "conversation.json").read_text(encoding="utf-8"))
        self.assertEqual(assistant.thread("ops"), [])
        self.assertEqual(self.post("/api/remove", {"slug": "ops"}), {"error": "No such dashboard."})

    def test_not_while_claude_is_working_on_it(self):
        assistant = serve(self).studio.assistant
        self.make()
        assistant.job = mock.Mock(status="running", slug="ops", key="ops")
        self.assertIn("Claude is still working", self.post("/api/remove", {"slug": "ops"})["error"])
        self.assertEqual(specs.slugs(), ["ops"])
        assistant.job = None

    def test_the_example_is_given_once_only(self):
        with mock.patch.object(config, "DASHBOARDS_DIR", self.tmp / "fresh"):
            specs.ensure_sample()
            self.assertEqual(specs.slugs(), ["sample"])
            specs.remove("sample")
            specs.ensure_sample()
            self.assertEqual(specs.slugs(), [], "removing the example must stick")


if __name__ == "__main__":
    unittest.main()
