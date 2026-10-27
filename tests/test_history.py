import http.client
import io
import json
import unittest
from unittest import mock

from support import StudioCase, serve, watching_shares

from app import config, history, specs


class HistoryTest(StudioCase):
    def make(self, slug="ops"):
        self.folder = config.DASHBOARDS_DIR / slug
        self.folder.mkdir()
        self.write("dashboard.json", json.dumps({"name": "Ops", "cards": [
            {"key": "a", "name": "A", "display": "scalar", "row": 0, "col": 0, "size_x": 6, "size_y": 3},
            {"key": "b", "name": "B", "display": "scalar", "row": 0, "col": 6, "size_x": 6, "size_y": 3}]}))
        self.write("a.sql", "select 1 as a")
        self.write("b.sql", "select 2 as b\r\n")

    def write(self, name, text):
        (self.folder / name).write_bytes(text.encode("utf-8"))

    def read(self, name):
        return (self.folder / name).read_bytes().decode("utf-8")

    def publish(self):
        """What Go live leaves behind: its record, with the content it sent."""
        self.write("metabase.json", json.dumps({"dashboard_id": 5, "url": "http://mb/dashboard/5", "content": specs.content_hash("ops")}))

    def test_a_version_is_kept_only_when_the_dashboard_changed(self):
        self.make()
        first = history.record("ops", "claude", "Built it")
        self.assertEqual(sorted(first["files"]), ["a.sql", "b.sql", "dashboard.json"])
        self.assertIsNone(history.record("ops", "claude", "Nothing new"))
        self.publish()
        (config.DATA_DIR / "shown").mkdir(parents=True)
        (config.DATA_DIR / "shown" / "ops.json").write_text('{"a": ["Phone"]}', encoding="utf-8")
        self.assertIsNone(history.record("ops", "claude", "Go live and Show are not the dashboard"))
        self.write("a.sql", "select 10 as a")
        self.assertEqual(history.record("ops", "claude", "Ten  times\nbigger")["note"], "Ten times bigger")
        self.assertEqual([v["by"] for v in history.versions("ops")], ["claude", "claude"])
        self.assertIsNone(history.record("nope", "claude", "x"))

    def test_catch_up_keeps_what_changed_outside_the_studio(self):
        self.make()
        self.assertEqual((history.catch_up("ops")["by"], history.versions("ops")[0]["note"]), ("studio", "As first opened here"))
        self.assertIsNone(history.catch_up("ops"))
        self.write("b.sql", "select 3 as b")
        self.assertEqual(history.catch_up("ops")["by"], "outside")
        self.assertIsNone(history.on_run("ops", "before"))
        self.write("b.sql", "select 4 as b")
        self.assertEqual(history.on_run("ops", "after", "Made b bigger")["by"], "claude")
        with mock.patch.object(history, "record", side_effect=OSError("disk full")), \
                mock.patch("sys.stderr", new_callable=io.StringIO) as log:
            self.assertIsNone(history.on_run("ops", "after", "x"), "a failing hook never stops the run")
        self.assertIn("disk full", log.getvalue())

    def test_go_back_puts_the_files_back_and_can_be_undone(self):
        self.make()
        first = history.catch_up("ops")
        self.write("dashboard.json", json.dumps({"name": "Ops, renamed", "cards": []}))
        self.write("a.sql", "select 99 as a")
        (self.folder / "b.sql").unlink()
        self.write("c.sql", "select 3 as c")
        self.publish()
        published = self.read("metabase.json")
        before, changed = history.go_back("ops", first["id"])
        self.assertTrue(changed)
        self.assertEqual(json.loads(self.read("dashboard.json"))["name"], "Ops")
        self.assertEqual((self.read("a.sql"), self.read("b.sql")), ("select 1 as a", "select 2 as b\r\n"))
        self.assertFalse((self.folder / "c.sql").exists(), "a query the version did not have goes")
        self.assertEqual(self.read("metabase.json"), published, "Go live's record is never touched")
        listed = history.listing("ops")
        self.assertEqual((listed[0]["by"], listed[0]["note"], listed[0]["now"]), ("you", f"Went back to {history.label(first['at'])}", True))
        self.assertEqual((listed[1]["by"], listed[1]["live"]), ("outside", True), "the files as they were are kept first")
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["a.sql", "b.sql", "dashboard.json", "metabase.json"])
        history.go_back("ops", listed[1]["id"])
        self.assertEqual(self.read("c.sql"), "select 3 as c", "going back is undone by going back")
        self.assertEqual(history.go_back("ops", history.listing("ops")[0]["id"])[1], False)

    def test_listing_marks_now_and_live(self):
        self.make()
        history.catch_up("ops")
        self.publish()
        self.write("a.sql", "select 11 as a")
        history.record("ops", "claude", "Changed a")
        listed = history.listing("ops")
        self.assertEqual([(v["by"], v["now"], v["live"]) for v in listed], [("claude", True, False), ("studio", False, True)])
        self.assertEqual(listed[0]["when"], history.label(listed[0]["at"]))

    def test_the_newest_versions_stay_and_the_live_one(self):
        self.make()
        history.catch_up("ops")
        self.publish()
        with mock.patch.object(history, "KEEP", 3):
            for n in range(5):
                self.write("a.sql", f"select {n + 10} as a")
                history.record("ops", "claude", f"Change {n}")
        notes = [v["note"] for v in history.versions("ops")]
        self.assertEqual(notes, ["Change 4", "Change 3", "Change 2", "As first opened here"])

    def test_a_version_that_names_other_files_is_never_used(self):
        """Only dashboard.json and queries come back from a version: never Go live's record, never a path out."""
        self.make()
        history.catch_up("ops")
        self.publish()
        published = self.read("metabase.json")
        for n, extra in enumerate([{"metabase.json": "{}"}, {"../escape.sql": "select 1"}, {"sub/x.sql": "select 1"}]):
            version = {"id": f"20990101-00000{n}-000000-abcd", "at": "2099-01-01T00:00:00", "by": "you", "note": "odd",
                       "files": {"dashboard.json": self.read("dashboard.json"), **extra}}
            (specs.history_dir("ops") / f"{version['id']}.json").write_text(json.dumps(version), encoding="utf-8")
            with self.assertRaises(history.HistoryError):
                history.go_back("ops", version["id"])
        self.assertEqual([v["note"] for v in history.listing("ops")], ["As first opened here"])
        self.assertEqual(self.read("metabase.json"), published)
        self.assertFalse((config.DASHBOARDS_DIR / "escape.sql").exists())

    def test_a_label_reads_as_the_user_reads_a_time(self):
        self.assertEqual(history.label("2026-10-27T15:49:12"), "27 Oct, 3:49 pm")
        self.assertEqual(history.label("2026-10-07T00:05:00"), "7 Oct, 12:05 am")


class VersionRoutesTest(StudioCase):
    def setUp(self):
        super().setUp()
        self.assistant = serve(self).studio.assistant
        HistoryTest.make(self)

    write = HistoryTest.write

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", config.PORT, timeout=20)
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers={
            "Host": f"127.0.0.1:{config.PORT}", "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{config.PORT}", "Cookie": "studio_session=test-token"})
        reply = json.loads(conn.getresponse().read())
        conn.close()
        return reply

    def test_opening_a_dashboard_keeps_its_first_version_and_going_back_works(self):
        self.call("GET", "/api/dashboard?slug=ops")
        [first] = self.call("GET", "/api/versions?slug=ops")["versions"]
        self.assertEqual((first["by"], first["now"]), ("studio", True))
        self.write("a.sql", "select 5 as a")
        listed = self.call("GET", "/api/versions?slug=ops")["versions"]
        self.assertEqual([v["by"] for v in listed], ["outside", "studio"])
        reply = self.call("POST", "/api/versions/back", {"slug": "ops", "id": first["id"]})
        self.assertEqual(reply, {"ok": True, "when": first["when"], "changed": True})
        self.assertEqual((self.folder / "a.sql").read_text(encoding="utf-8"), "select 1 as a")

    def test_not_while_claude_works_on_it_and_never_outside_its_history(self):
        self.call("GET", "/api/dashboard?slug=ops")
        [first] = self.call("GET", "/api/versions?slug=ops")["versions"]
        self.assistant.job = mock.Mock(status="running", slug="ops", key="ops")
        self.write("a.sql", "select 6 as a")
        self.call("GET", "/api/dashboard?slug=ops")
        self.assertEqual(len(self.call("GET", "/api/versions?slug=ops")["versions"]), 1, "nothing is kept while Claude writes")
        self.assertEqual(self.call("POST", "/api/versions/back", {"slug": "ops", "id": first["id"]})["error"],
                         "Claude is working on it. Stop it or wait.")
        self.assistant.job = None
        (self.tmp / "data" / "x.json").write_text(json.dumps({"id": "x", "files": {"dashboard.json": "{}"}}), encoding="utf-8")
        with watching_shares() as tried:
            for bad in ["../x", "../../data/x", "//host/share/x", "\\\\host\\share\\x", "20261027-101010-000000-abcd", None, 5]:
                self.assertIn("error", self.call("POST", "/api/versions/back", {"slug": "ops", "id": bad}), bad)
            for slug in ["../ops", "//host/share/x", "nope"]:
                self.assertIn("error", self.call("POST", "/api/versions/back", {"slug": slug, "id": first["id"]}), slug)
                self.assertIn("error", self.call("GET", f"/api/versions?slug={slug}"), slug)
        self.assertEqual(tried, [])
        self.assertEqual((self.folder / "a.sql").read_text(encoding="utf-8"), "select 6 as a")


if __name__ == "__main__":
    unittest.main()
