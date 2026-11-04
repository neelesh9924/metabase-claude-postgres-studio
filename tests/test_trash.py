import http.client
import json
import os
import unittest
from unittest import mock

from support import StudioCase, serve, watching_shares

from app import config, history, specs

DRAFT = "draft-0123456789ab"


class TrashTest(StudioCase):
    def setUp(self):
        super().setUp()
        self.assistant = serve(self).studio.assistant

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", config.PORT, timeout=20)
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers={
            "Host": f"127.0.0.1:{config.PORT}", "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{config.PORT}", "Cookie": "studio_session=test-token"})
        reply = json.loads(conn.getresponse().read())
        conn.close()
        return reply

    def post(self, path, body):
        return self.call("POST", path, body)

    def entries(self):
        return self.call("GET", "/api/trash")["entries"]

    def make(self, slug="orders", name="Orders", live=True):
        folder = config.DASHBOARDS_DIR / slug
        folder.mkdir()
        (folder / "n.sql").write_text("select 7 as n", encoding="utf-8")
        (folder / "dashboard.json").write_text(json.dumps({"name": name, "cards": [
            {"key": "n", "name": "N", "display": "scalar", "row": 0, "col": 0, "size_x": 6, "size_y": 3}]}), encoding="utf-8")
        if live:
            (folder / "metabase.json").write_text(json.dumps({"dashboard_id": 12, "url": "http://mb/dashboard/12"}), encoding="utf-8")
        return folder

    def attach(self, key, name="targets.csv"):
        folder = config.ATTACH_DIR / key
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text("Region,Target\nNorth,120\n", encoding="utf-8")

    def thread(self, key):
        path = config.THREADS_DIR / f"{key}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def test_a_dashboard_comes_back_with_its_conversation_files_and_versions(self):
        self.make()
        self.assistant._add("orders", "user", "make it blue")
        self.attach("orders")
        history.catch_up("orders")
        removed = self.post("/api/remove", {"slug": "orders"})
        self.assertEqual((removed["ok"], removed["name"]), (True, "Orders"))
        self.assertFalse(specs.history_dir("orders").exists(), "its versions go to the Trash with it")
        self.assertEqual(sorted(p.name for p in (specs.trash_dir() / removed["key"]).iterdir()),
                         ["attachments", "conversation.json", "dashboard.json", "history", "metabase.json", "n.sql"])
        [entry] = self.entries()
        self.assertEqual({k: entry[k] for k in ("key", "kind", "name", "cards", "live", "messages", "files")},
                         {"key": removed["key"], "kind": "dashboard", "name": "Orders", "cards": 1, "live": True,
                          "messages": 1, "files": 1})
        self.assertRegex(entry["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")
        self.assertNotIn("when", entry, "the page writes the time from at, its one way")
        rev = self.assistant.rev
        back = self.post("/api/trash/back", {"key": removed["key"]})
        self.assertEqual(back, {"ok": True, "kind": "dashboard", "name": "Orders", "slug": "orders", "moved": False})
        self.assertGreater(self.assistant.rev, rev, "the page fetches the conversation again")
        self.assertEqual(specs.slugs(), ["orders"])
        self.assertEqual(self.thread("orders")[0]["text"], "make it blue")
        self.assertTrue((config.ATTACH_DIR / "orders" / "targets.csv").is_file())
        self.assertEqual(len(history.versions("orders")), 1)
        self.assertEqual(specs.published("orders")["dashboard_id"], 12, "a later Go live updates the same dashboard")
        self.assertEqual(self.entries(), [])
        self.assertEqual(list(specs.trash_dir().iterdir()), [])

    def test_onto_a_taken_slug_it_comes_back_beside_it(self):
        self.make()
        removed = self.post("/api/remove", {"slug": "orders"})
        self.make(name="New orders")
        self.assertIn("comes back as orders_2", self.entries()[0]["note"])
        back = self.post("/api/trash/back", {"key": removed["key"]})
        self.assertEqual((back["slug"], back["moved"]), ("orders_2", True))
        self.assertEqual(specs.slugs(), ["orders", "orders_2"])
        self.assertEqual(specs.summary("orders")["name"], "New orders")

    def test_when_one_move_fails_what_moved_goes_back(self):
        self.make()
        self.assistant._add("orders", "user", "make it blue")
        self.attach("orders")
        removed = self.post("/api/remove", {"slug": "orders"})
        folder = specs.trash_dir() / removed["key"]
        real = os.replace

        def stuck(source, target):
            if str(source) == str(folder):
                raise PermissionError(32, "in use")
            return real(source, target)

        with mock.patch("os.replace", stuck):
            reply = self.post("/api/trash/back", {"key": removed["key"]})
        self.assertEqual(reply["error"], "A file of “Orders” is open in another program. Close it and try again.")
        self.assertEqual(sorted(p.name for p in folder.iterdir()),
                         ["attachments", "conversation.json", "dashboard.json", "metabase.json", "n.sql"])
        self.assertIsNone(self.thread("orders"))
        self.assertFalse((config.ATTACH_DIR / "orders").exists())
        self.assertEqual(specs.slugs(), [])

    def test_a_draft_comes_back_to_the_sidebar(self):
        self.assistant._add(DRAFT, "user", "Orders per day for the last month")
        self.attach(DRAFT)
        self.assertIsNone(self.assistant.remove_draft(DRAFT))
        [entry] = self.entries()
        self.assertEqual((entry["kind"], entry["name"], entry["messages"], entry["files"]),
                         ("draft", "Orders per day for the last month", 1, 1))
        back = self.post("/api/trash/back", {"key": entry["key"]})
        self.assertEqual(back, {"ok": True, "kind": "draft", "name": "Orders per day for the last month", "key": DRAFT, "moved": False})
        self.assertEqual([d["key"] for d in self.assistant.drafts()], [DRAFT])
        self.assertTrue((config.ATTACH_DIR / DRAFT / "targets.csv").is_file())
        self.assertEqual(list(specs.trash_dir().iterdir()), [])

    def cleared(self, slug, text="make it green", stamp="20261027-154900"):
        folder = specs.trash_dir() / f"{slug}-chat-{stamp}"
        folder.mkdir(parents=True)
        (folder / "conversation.json").write_text(json.dumps([{"id": "m1", "role": "user", "text": text, "at": "2026-10-27T15:40:00"}]),
                                                  encoding="utf-8")
        (folder / "attachments").mkdir()
        (folder / "attachments" / "brief.txt").write_text("x", encoding="utf-8")
        return folder.name

    def test_a_cleared_chat_comes_back_only_onto_an_empty_one(self):
        self.make()
        key = self.cleared("orders")
        [entry] = self.entries()
        self.assertEqual((entry["kind"], entry["name"]), ("chat", "Conversation about Orders (1 message)"))
        self.assistant._add("orders", "user", "a newer request")
        self.assertEqual(self.post("/api/trash/back", {"key": key})["error"],
                         "“Orders” has a newer conversation. Clear it first, then put this one back.")
        self.assertEqual(self.thread("orders")[0]["text"], "a newer request")
        self.assistant._save("orders", [])   # as Clear leaves it
        self.assertEqual(self.post("/api/trash/back", {"key": key}),
                         {"ok": True, "kind": "chat", "name": "Conversation about Orders (1 message)", "slug": "orders", "moved": False})
        self.assertEqual(self.thread("orders")[0]["text"], "make it green")
        self.assertTrue((config.ATTACH_DIR / "orders" / "brief.txt").is_file())
        orphan = self.cleared("gone", stamp="20261027-155000")
        self.assertIn("not in the studio", self.post("/api/trash/back", {"key": orphan})["error"])

    def test_not_while_claude_works_on_it(self):
        self.assistant._add(DRAFT, "user", "Orders per day")
        self.assistant.remove_draft(DRAFT)
        self.assistant.job = mock.Mock(status="running", slug=None, key=DRAFT)
        self.assertEqual(self.post("/api/trash/back", {"key": self.entries()[0]["key"]})["error"], "Claude is working on it. Stop it or wait.")
        self.assistant.job = None

    def test_delete_and_empty_need_confirmation_and_leave_the_studio_alone(self):
        for slug in ("a", "b", "c", "kept"):
            self.make(slug, name=slug.upper())
        for slug in ("a", "b", "c"):
            self.post("/api/remove", {"slug": slug})
        old = specs.trash_dir() / "x-20261001-101010"
        (old / "attachments").mkdir(parents=True)   # what an older Clear left
        (old / "attachments" / "f.txt").write_text("x", encoding="utf-8")
        os.chmod(old / "attachments" / "f.txt", 0o444)
        listed = self.entries()
        self.assertEqual([e["kind"] for e in listed].count("other"), 1)
        first = listed[0]["key"]
        self.assertEqual(self.post("/api/trash/delete", {"keys": [first]}), {"error": "Confirm first."})
        self.assertEqual(self.post("/api/trash/empty", {}), {"error": "Confirm first."})
        self.assertEqual(len(self.entries()), 4)
        self.assertEqual(self.post("/api/trash/delete", {"keys": [first], "confirmed": True}), {"ok": True, "count": 1, "failed": []})
        self.assertEqual(len(self.entries()), 3)
        self.assertIn("changed", self.post("/api/trash/empty", {"confirmed": True, "keys": [first]})["error"])
        keys = [e["key"] for e in self.entries()]
        self.assertEqual(self.post("/api/trash/empty", {"confirmed": True, "keys": keys}), {"ok": True, "count": 3, "failed": []})
        self.assertEqual(list(specs.trash_dir().iterdir()), [], "a read-only file goes too")
        self.assertEqual(specs.slugs(), ["kept"])
        self.assertEqual(sorted(p.name for p in (config.DASHBOARDS_DIR / "kept").iterdir()), ["dashboard.json", "metabase.json", "n.sql"])

    def test_names_that_leave_the_trash_are_refused(self):
        self.make()
        self.post("/api/remove", {"slug": "orders"})
        (self.tmp / "data" / "x").write_text("not the trash's", encoding="utf-8")
        tries = ["../x", "a/b", "//host/share/x", "\\\\host\\share\\x", "..", ".", "", "%2e%2e/x", "C:/x", "x::$DATA"]
        with watching_shares() as tried:
            for key in tries:
                self.assertIn("error", self.post("/api/trash/delete", {"keys": [key], "confirmed": True}), key)
                self.assertIn("error", self.post("/api/trash/back", {"key": key}), key)
        self.assertEqual(tried, [])
        self.assertTrue((self.tmp / "data" / "x").is_file())
        self.assertEqual(len(self.entries()), 1)

    @unittest.skipUnless(os.name == "nt", "a junction is a Windows link")
    def test_a_link_out_of_the_trash_is_never_followed(self):
        import _winapi

        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("the user's", encoding="utf-8")
        inner = specs.trash_dir() / "odd-20261027-101010"
        inner.mkdir(parents=True)
        _winapi.CreateJunction(str(outside), str(inner / "link"))
        _winapi.CreateJunction(str(outside), str(specs.trash_dir() / "link-20261027-101011"))
        listed = {e["key"]: e for e in self.entries()}
        self.assertEqual(listed["link-20261027-101011"]["kind"], "other")
        self.assertIn("error", self.post("/api/trash/back", {"key": "link-20261027-101011"}))
        self.assertEqual(self.post("/api/trash/empty", {"confirmed": True}), {"ok": True, "count": 2, "failed": []})
        self.assertEqual(list(specs.trash_dir().iterdir()), [])
        self.assertEqual((outside / "keep.txt").read_text(encoding="utf-8"), "the user's")


if __name__ == "__main__":
    unittest.main()
