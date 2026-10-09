"""A new dashboard's conversation is a draft: listed from its first message, kept until built or removed."""
import http.client
import json
import os
import unittest

from support import StudioCase, read_json, serve

from app import config, specs
from app.assistant import Assistant

PLAN = {"slug": "leads", "name": "Leads", "description": "", "cards": [{"name": "Leads today", "display": "scalar", "shows": ""}],
        "reads": [], "assumptions": [], "left_out": []}


class DraftsTest(StudioCase):
    def setUp(self):
        super().setUp()
        self.assistant = serve(self).studio.assistant

    def settle(self, assistant=None):
        self.wait(lambda: not (assistant or self.assistant).busy())

    def say(self, text, mode="plan", draft=None):
        """Send one message about a new dashboard and wait for the answer. Returns the draft's key."""
        self.mode(mode)
        self.assertIsNone(self.assistant.ask("new", None, text, draft=draft))
        key = self.assistant.job.key
        self.settle()
        return key

    def test_a_draft_is_there_from_the_first_message_and_after_a_restart(self):
        self.assertEqual(self.assistant.drafts(), [])
        self.mode("hang")
        pidfile = self.tmp / "pids.json"
        os.environ["FAKE_PIDFILE"] = str(pidfile)
        self.addCleanup(os.environ.pop, "FAKE_PIDFILE", None)
        self.assertIsNone(self.assistant.ask("new", None, "Tickets   per day,\nplease"))
        key = self.assistant.job.key
        # Listed while Claude has not answered yet, under the user's own words.
        self.assertEqual([(d["key"], d["title"], d["plan"]) for d in self.assistant.drafts()], [(key, "Tickets per day, please", False)])
        self.assertEqual(self.assistant.remove_draft(key), "Stop Claude first.")
        self.wait(pidfile.exists)
        self.assistant.stop()
        self.settle()
        # Nothing was built, and nothing is thrown away: another start of the app finds the conversation.
        again = Assistant(config.PORT, self.runner())
        self.assertEqual([d["key"] for d in again.drafts()], [key])
        self.assertEqual([m["role"] for m in again.thread(key)], ["user", "info"])

    def test_several_drafts_each_with_its_own_conversation(self):
        first = self.say("Tickets per day")
        second = self.say("Something about refunds", mode="edit")   # a reply with no plan in it
        self.assertNotEqual(first, second)
        listed = {d["key"]: d for d in self.assistant.drafts()}
        self.assertEqual((listed[first]["title"], listed[first]["plan"]), ("Daily ops", True))
        self.assertEqual((listed[second]["title"], listed[second]["plan"]), ("Something about refunds", False))
        # More words go to the draft they are meant for.
        self.assertEqual(self.say("For the last 7 days", draft=second), second)
        self.assertEqual([m["role"] for m in self.assistant.thread(second)], ["user", "claude", "error", "user", "plan"])
        self.assertEqual([m["role"] for m in self.assistant.thread(first)], ["user", "plan"])
        self.assertEqual([d["key"] for d in self.assistant.drafts()], [second, first], "the one last worked on comes first")
        # Asking for a change after a plan means the old plan no longer waits to be built.
        self.say("Per week instead", mode="edit", draft=first)
        self.assertFalse({d["key"]: d for d in self.assistant.drafts()}[first]["plan"])

    def test_building_turns_the_draft_into_the_dashboard(self):
        kept = self.say("Refunds", mode="edit")
        built = self.say("Tickets per day")
        plan = self.assistant.thread(built)[-1]["id"]
        self.assertEqual(self.assistant.build(plan, kept), "That plan is no longer there. Ask again.")
        self.mode("build")
        self.assertIsNone(self.assistant.build(plan, built))
        self.assertEqual(self.assistant.job.key, built)
        self.settle()
        self.assertEqual(specs.slugs(), ["daily_ops"])
        self.assertEqual([d["key"] for d in self.assistant.drafts()], [kept])
        self.assertEqual(self.assistant.thread(built), [])
        self.assertEqual([m["role"] for m in self.assistant.thread("daily_ops")], ["user", "plan", "info", "claude"])

    def test_a_removed_draft_goes_to_the_trash_folder(self):
        key = self.say("Something", mode="edit")
        self.assertIsNone(self.assistant.remove_draft(key))
        self.assertEqual(self.assistant.drafts(), [])
        kept = list((config.DATA_DIR / "trash").glob(f"{key}-*.json"))
        self.assertEqual(len(kept), 1)
        self.assertEqual(read_json(kept[0])[0]["text"], "Something")
        for gone in (key, "draft-", "../settings", "daily_ops", None):
            self.assertEqual(self.assistant.remove_draft(gone), "That draft is no longer there.")

    def test_the_one_conversation_of_earlier_versions_becomes_a_draft(self):
        old = [{"id": "a1", "role": "user", "text": "Leads per day", "at": "2026-01-01T10:00:00"},
               {"id": "p1", "role": "plan", "text": "One card.", "plan": PLAN, "at": "2026-01-01T10:00:20"}]
        config.THREADS_DIR.mkdir(parents=True, exist_ok=True)
        (config.THREADS_DIR / "_new.json").write_text(json.dumps(old), encoding="utf-8")
        drafts = Assistant(config.PORT, self.runner()).drafts()   # what a start of the app does
        self.assertEqual([(d["title"], d["plan"]) for d in drafts], [("Leads", True)])
        self.assertFalse((config.THREADS_DIR / "_new.json").exists())
        self.assertEqual(self.assistant.thread(drafts[0]["key"]), old)
        # Its plan can still be built.
        self.mode("build")
        self.assertIsNone(self.assistant.build("p1"))
        self.settle()
        self.assertEqual((specs.slugs(), self.assistant.drafts()), (["leads"], []))
        # An empty one is simply dropped.
        (config.THREADS_DIR / "_new.json").write_text("[]", encoding="utf-8")
        self.assertEqual(Assistant(config.PORT, self.runner()).drafts(), [])
        self.assertFalse((config.THREADS_DIR / "_new.json").exists())

    def test_an_oddly_shaped_conversation_does_not_break_the_sidebar(self):
        config.THREADS_DIR.mkdir(parents=True, exist_ok=True)
        odd = ["words", {"role": "user"}, {"role": "plan", "plan": "not a plan"}, {"role": "plan", "plan": {}}, {"text": "no role"}]
        (config.THREADS_DIR / "draft-0000000000aa.json").write_text(json.dumps(odd), encoding="utf-8")
        (config.THREADS_DIR / "draft-0000000000bb.json").write_text('{"not": "a list"}', encoding="utf-8")
        (config.THREADS_DIR / "draft-0000000000cc.json").write_text("not json", encoding="utf-8")
        self.assertEqual([(d["key"], d["title"], d["plan"]) for d in self.assistant.drafts()],
                         [("draft-0000000000aa", "New dashboard", True)])

    def ask(self, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", config.PORT, timeout=20)
        headers = {"Host": f"127.0.0.1:{config.PORT}", "Cookie": "studio_session=test-token",
                   "Origin": f"http://127.0.0.1:{config.PORT}", "Content-Type": "application/json"}
        conn.request("POST" if body is not None else "GET", path, body=json.dumps(body) if body is not None else None, headers=headers)
        text = conn.getresponse().read().decode()
        conn.close()
        return json.loads(text)

    def test_from_the_page(self):
        key = "draft-0a1b2c3d4e5f"   # the page names the draft, so it can show it at once
        self.mode("plan")
        self.assertEqual(self.ask("/api/ask", {"mode": "new", "text": "Tickets per day", "draft": key}), {"ok": True})
        self.settle()
        state = self.ask("/api/state")
        self.assertEqual([(d["key"], d["title"], d["plan"]) for d in state["drafts"]], [(key, "Daily ops", True)])
        thread = self.ask(f"/api/thread?key={key}")["messages"]
        self.mode("build")
        self.assertEqual(self.ask("/api/build", {"plan": thread[-1]["id"], "draft": key}), {"ok": True})
        self.settle()
        self.assertEqual(self.ask("/api/state")["drafts"], [])
        other = self.say("Refunds", mode="edit")
        self.assertEqual(self.ask("/api/draft/remove", {"key": other}), {"ok": True})
        self.assertEqual(self.ask("/api/state")["drafts"], [])
        self.assertIn("no longer there", self.ask("/api/draft/remove", {"key": other})["error"])


if __name__ == "__main__":
    unittest.main()
