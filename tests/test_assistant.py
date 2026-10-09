import os
import unittest

from support import StudioCase, pid_alive, read_json, serve

from app import db, specs
from app.assistant import NEW, extract_plan, plan_policy, write_policy


class AssistantTest(StudioCase):
    def setUp(self):
        super().setUp()
        self.assistant = serve(self).studio.assistant

    def settle(self):
        self.wait(lambda: not self.assistant.busy())

    def test_plan_then_build_then_change(self):
        # 1. A new dashboard starts with a plan, and planning cannot query.
        self.mode("plan")
        self.assertIsNone(self.assistant.ask("new", None, "Tickets per day"))
        self.assertEqual(self.assistant.ask("new", None, "again"), "Claude is still working on the last request.")
        self.settle()
        thread = self.assistant.thread(NEW)
        self.assertEqual([m["role"] for m in thread], ["user", "plan"])
        plan = thread[1]["plan"]
        self.assertEqual(plan["slug"], "daily_ops")
        self.assertIn("offered: describe_table,list_tables", plan["assumptions"])
        self.assertIn("query in plan refused: True", plan["assumptions"])
        self.assertEqual(self.queries, [])
        self.assertEqual(specs.slugs(), [])

        # 2. Building happens only when asked, writes the dashboard and tests its query once.
        self.mode("build")
        self.assertIsNone(self.assistant.build(thread[1]["id"]))
        self.settle()
        self.assertEqual(self.assistant.job.status, "done")
        self.assertEqual(self.assistant.job.slug, "daily_ops")
        self.assertEqual(specs.slugs(), ["daily_ops"])
        self.assertEqual(self.queries, [("claude daily_ops", 'select 7 as "Tickets today"')])
        self.assertEqual(self.assistant.job.queries, 1)
        # The preview finds Claude's result in the cache, so it does not ask the database again.
        card = specs.load("daily_ops")["cards"][0]
        self.assertEqual(db.cache_get(card["sql"])["rows"], [[7]])
        # The planning conversation moved to the dashboard.
        self.assertEqual(self.assistant.thread(NEW), [])
        moved = self.assistant.thread("daily_ops")
        self.assertEqual([m["role"] for m in moved], ["user", "plan", "info", "claude"])
        self.assertTrue(moved[1]["built"])
        self.assertIn("Built one card", moved[3]["text"])

        # 3. A change to an existing dashboard runs directly.
        self.mode("edit")
        self.assertIsNone(self.assistant.ask("edit", "daily_ops", "Make it stacked"))
        self.settle()
        self.assertEqual(self.assistant.thread("daily_ops")[-1]["text"], "Made the chart stacked.")
        self.assertEqual(self.assistant.ask("edit", "no_such", "x"), "Open a dashboard first, or start a new one.")

    def test_a_second_build_gets_its_own_folder(self):
        self.mode("plan")
        self.assistant.ask("new", None, "Tickets per day")
        self.settle()
        self.mode("build")
        self.assistant.build(self.assistant.thread(NEW)[1]["id"])
        self.settle()
        self.mode("plan")
        self.assistant.ask("new", None, "The same again")
        self.settle()
        self.mode("build")
        self.assistant.build(self.assistant.thread(NEW)[-1]["id"])
        self.settle()
        self.assertEqual(specs.slugs(), ["daily_ops", "daily_ops_2"])

    def test_stop(self):
        self.mode("hang")
        pidfile = self.tmp / "pids.json"
        os.environ["FAKE_PIDFILE"] = str(pidfile)
        self.addCleanup(os.environ.pop, "FAKE_PIDFILE", None)
        self.assistant.ask("new", None, "Anything")
        self.wait(pidfile.exists)
        pids = read_json(pidfile)
        self.assistant.stop()
        self.settle()
        self.assertEqual(self.assistant.job.status, "cancelled")
        self.assertEqual(self.assistant.thread(NEW)[-1]["role"], "info")
        self.assertFalse(pid_alive(pids["parent"]))
        self.assertFalse(pid_alive(pids["child"]))

    def test_a_reply_without_a_plan_offers_nothing_to_build(self):
        self.mode("edit")
        self.assistant.ask("new", None, "Something")
        self.settle()
        self.assertEqual([m["role"] for m in self.assistant.thread(NEW)], ["user", "claude", "error"])
        self.assertEqual(self.assistant.build("nope"), "That plan is no longer there. Ask again.")

    def test_clear_forgets_the_conversation_only(self):
        self.mode("edit")
        self.assistant.ask("new", None, "Something")
        self.settle()
        self.assertIsNone(self.assistant.clear(NEW))
        self.assertEqual(self.assistant.thread(NEW), [])


class PolicyTest(unittest.TestCase):
    def test_planning_has_no_write_and_no_query(self):
        policy = plan_policy()
        self.assertNotIn("Write", policy.tools)
        self.assertNotIn("Bash", policy.tools)
        self.assertFalse(any("run_query" in rule or "Edit" in rule for rule in policy.allow))

    def test_writing_is_limited_to_the_dashboard_folder(self):
        policy = write_policy("daily_ops")
        self.assertIn("Edit(./dashboards/daily_ops/**)", policy.allow)
        self.assertIn("Write(./dashboards/daily_ops/**)", policy.allow)
        self.assertNotIn("Bash", policy.tools)
        # Reading is never pre-approved, so it stays inside the project folder.
        self.assertFalse(any(rule in ("Read", "Glob", "Grep", "Edit", "Write") for rule in policy.allow))
        self.assertIn("Read(./.env)", policy.deny)

    def test_plan_block(self):
        plan, clean = extract_plan('Two cards.\n<<<STUDIO_JSON\n{"slug": "A b", "name": "N", "cards": [{"name": "c", "display": "line"}]}\nSTUDIO_JSON>>>')
        self.assertEqual(clean, "Two cards.")
        self.assertEqual(plan["slug"], "a_b")
        self.assertEqual(extract_plan("no block")[0], None)
        self.assertEqual(extract_plan("<<<STUDIO_JSON\nnot json\nSTUDIO_JSON>>>")[0], None)
        self.assertEqual(extract_plan('<<<STUDIO_JSON\n{"slug": "x", "name": "N", "cards": []}\nSTUDIO_JSON>>>')[0], None)


if __name__ == "__main__":
    unittest.main()
