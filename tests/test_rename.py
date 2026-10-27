import http.client
import json
import unittest
from unittest import mock

from support import StudioCase, serve, watching_shares

from app import config, history, specs

# The keys in an order of their own, so that a writer that sorts or drops them is seen.
DASHBOARD = {
    "name": "Ventes – été",
    "description": "Orders, today and the last 30 days.",
    "database": "main",
    "filters": [{"key": "date", "name": "Date", "type": "date", "default": "past30days"}],
    "cards": [{"key": "a", "name": "A", "display": "scalar", "row": 0, "col": 0, "size_x": 6, "size_y": 3}],
    "notes": {"kept": True},
}


class RenameTest(StudioCase):
    def setUp(self):
        super().setUp()
        self.assistant = serve(self).studio.assistant
        self.folder = config.DASHBOARDS_DIR / "ops"
        self.folder.mkdir()
        self.file = self.folder / "dashboard.json"
        self.file.write_text(json.dumps(DASHBOARD, indent=2), encoding="utf-8")
        (self.folder / "a.sql").write_bytes(b"select 1 as a\r\n")
        (self.folder / "metabase.json").write_text(json.dumps({"dashboard_id": 5, "url": "http://mb/dashboard/5"}), encoding="utf-8")

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", config.PORT, timeout=20)
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers={
            "Host": f"127.0.0.1:{config.PORT}", "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{config.PORT}", "Cookie": "studio_session=test-token"})
        reply = json.loads(conn.getresponse().read())
        conn.close()
        return reply

    def rename(self, name, slug="ops"):
        return self.call("POST", "/api/rename", {"slug": slug, "name": name})

    def raw(self):
        return json.loads(self.file.read_text(encoding="utf-8"))

    def test_only_the_name_changes(self):
        self.assertEqual(self.rename("  Daily orders  "), {"ok": True, "name": "Daily orders"})
        raw = self.raw()
        self.assertEqual(list(raw), list(DASHBOARD), "every key stays where it was")
        self.assertEqual(raw, {**DASHBOARD, "name": "Daily orders"})
        self.assertEqual(specs.slugs(), ["ops"], "the folder and the slug stay")
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["a.sql", "dashboard.json", "metabase.json"])
        self.assertEqual((self.folder / "a.sql").read_bytes(), b"select 1 as a\r\n")
        self.assertEqual(specs.published("ops")["dashboard_id"], 5, "Go live's record is not touched")
        self.assertEqual(self.call("GET", "/api/dashboard?slug=ops")["name"], "Daily orders")
        self.assertEqual(self.rename("Été – 2026")["name"], "Été – 2026")
        text = self.file.read_text(encoding="utf-8")
        self.assertIn('"name": "Été – 2026"', text, "UTF-8 as it is, no \\u escapes")
        self.assertTrue(text.startswith('{\n  "name"') and text.endswith("}\n"), "indented by 2, one line feed at the end")

    def test_a_version_is_kept_and_going_back_brings_the_old_name_back(self):
        self.call("GET", "/api/dashboard?slug=ops")   # its first version, as opened here
        (self.folder / "a.sql").write_text("select 2 as a", encoding="utf-8")   # a change made outside the studio
        self.rename("Daily orders")
        listed = self.call("GET", "/api/versions?slug=ops")["versions"]
        self.assertEqual([(v["by"], v["note"]) for v in listed],
                         [("you", "Renamed"), ("outside", "Changed outside the studio"), ("studio", "As first opened here")])
        self.assertTrue(listed[0]["now"])
        self.assertEqual(self.call("POST", "/api/versions/back", {"slug": "ops", "id": listed[1]["id"]}), {"ok": True, "changed": True})
        self.assertEqual(self.raw()["name"], DASHBOARD["name"])
        self.assertEqual((self.folder / "a.sql").read_text(encoding="utf-8"), "select 2 as a", "only the rename is undone")

    def test_a_name_must_be_one_line_of_1_to_120_characters_and_new(self):
        before = self.file.read_bytes()
        for name, error in [("", "Give the dashboard a name."), ("   ", "Give the dashboard a name."), (None, "Give the dashboard a name."),
                            (5, "Give the dashboard a name."), ("Daily\norders", "Keep the name to one line."),
                            ("Daily\r\norders", "Keep the name to one line."), ("Daily orders", "Keep the name to one line."),
                            ("x" * 121, "Keep the name under 120 characters."), (f" {DASHBOARD['name']} ", "It has that name already.")]:
            self.assertEqual(self.rename(name), {"error": error}, name)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(self.rename("x" * 120)["name"], "x" * 120)

    def test_refused_while_claude_works_on_it_and_for_a_dashboard_that_is_not_there(self):
        before = self.file.read_bytes()
        self.assistant.job = mock.Mock(status="running", slug="ops", key="ops")
        self.assertEqual(self.rename("Daily orders"), {"error": "Claude is working on it. Stop it or wait."})
        self.assistant.job = None
        with watching_shares() as tried:
            for slug in ["nope", "../ops", "ops/..", "//host/share/x", "\\\\host\\share\\x", "", None, 5, ["ops"]]:
                self.assertEqual(self.rename("Daily orders", slug), {"error": "No such dashboard."}, slug)
        self.assertEqual(tried, [])
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(history.versions("ops"), [], "nothing refused is kept as a version")

    def test_a_dashboard_json_that_cannot_be_read_is_never_written_over(self):
        for broken in [b'{"name": "Ops", "cards": [', b'["not", "an", "object"]', b"\xff\xfe not text"]:
            self.file.write_bytes(broken)
            self.assertEqual(self.rename("Daily orders")["error"],
                             "Its dashboard.json could not be read, so nothing was changed. Fix the file first.")
            self.assertEqual(self.file.read_bytes(), broken)

    def test_assign_puts_a_new_database_right_after_the_description(self):
        self.file.write_text(json.dumps({"name": "Ventes – été", "description": "x", "cards": [], "notes": 1}), encoding="utf-8")
        specs.assign("ops", "sales")
        self.assertEqual(list(self.raw()), ["name", "description", "database", "cards", "notes"])
        self.assertIn('"name": "Ventes – été"', self.file.read_text(encoding="utf-8"), "the one writer: UTF-8 as it is")
        self.file.write_text(json.dumps({"cards": [], "database": "old", "name": "Ops"}), encoding="utf-8")
        specs.assign("ops", "sales")
        self.assertEqual(self.raw(), {"cards": [], "database": "sales", "name": "Ops"}, "one already there is changed in place")


if __name__ == "__main__":
    unittest.main()
