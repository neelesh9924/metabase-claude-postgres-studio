"""Personal data is hidden until the user says to show it. Secrets are never shown. Claude decides neither."""
import http.client
import json
import unittest
from collections import namedtuple
from unittest import mock

from fake_metabase import DATABASE, KEY, FakeMetabase
from support import StudioCase, canned_result, database, serve

from app import config, db, guard, mcp, metabase, specs
from app.assistant import ALL_TOOLS, Job, extract_plan, write_policy
from app.textio import format_result

Column = namedtuple("Column", "name type_code")
TEXT, NUMBER = 25, 23
FOUND = [Column("Operator", TEXT), Column("Phone", TEXT), Column("otp", TEXT), Column("phone_count", NUMBER)]
ROW = ("North Lines", "555-0142", "481516", 3)
SQL = 'select name as "Operator", contact as "Phone", otp, 3 as phone_count from operators'


def shaped(reveal=()):
    columns, rows, truncated = db._shape(FOUND, [ROW], 10, reveal)
    return {"columns": columns, "rows": rows, "row_count": 1, "truncated": truncated, "ms": 2, "cost": 1.0,
            "ran_at": "2026-01-01T00:00:00"}


class HidingTest(unittest.TestCase):
    def test_which_names_are_which(self):
        personal = ["phone", "Phone", "mobile_no", "Email", "e_mail", "aadhaar_number", "pan", "company_pan_no"]
        secret = ["otp", "fcm_token", "password_hash", "client_secret", "api_key", "pin", "phone_otp"]
        self.assertEqual({guard.sensitivity(n) for n in personal}, {"personal"})
        self.assertEqual({guard.sensitivity(n) for n in secret}, {"secret"})
        self.assertEqual({guard.sensitivity(n) for n in ["pincode", "panel", "Tickets", "contact"]}, {None})

    def test_hidden_by_default(self):
        result = shaped()
        self.assertEqual(result["rows"], [["North Lines", "***", "***", 3]])
        self.assertEqual([(c["name"], c["pii"], c.get("sensitive")) for c in result["columns"]],
                         [("Operator", False, None), ("Phone", True, "personal"), ("otp", True, "secret"), ("phone_count", False, None)])

    def test_only_personal_data_can_be_shown(self):
        result = shaped(reveal=["Phone", "otp", "Operator"])
        self.assertEqual(result["rows"], [["North Lines", "555-0142", "***", 3]])
        phone, otp = result["columns"][1], result["columns"][2]
        self.assertEqual((phone["pii"], phone["revealed"], phone["sensitive"]), (False, True, "personal"))
        self.assertEqual((otp["pii"], otp.get("revealed"), otp["sensitive"]), (True, None, "secret"))
        self.assertNotIn("revealed", result["columns"][0])

    def test_what_claude_is_told(self):
        text = format_result(shaped())
        self.assertIn("Personal data hidden in: Phone.", text)
        self.assertIn("Never shown: otp.", text)
        self.assertNotIn("555-0142", text)
        self.assertNotIn("481516", text)

    def test_a_plan_names_the_personal_data_it_will_show(self):
        plan, _ = extract_plan('x\n<<<STUDIO_JSON\n{"slug": "ops", "name": "Ops", "cards": [{"name": "c", "display": "table"}], '
                               '"sensitive": ["Phone: asked for, to call the operator"]}\nSTUDIO_JSON>>>')
        self.assertEqual(plan["sensitive"], ["Phone: asked for, to call the operator"])


class PageCase(StudioCase):
    """An "ops" dashboard whose one card returns a phone number and a one-time code."""

    def setUp(self):
        super().setUp()
        self.reveals = []

        def run(sql, limit=None, allow_heavy=False, source="cli", database=None, reveal=()):
            self.reveals.append((source.split()[0], list(reveal)))
            return shaped(reveal)

        patch = mock.patch.object(db, "run", run)
        patch.start()
        self.addCleanup(patch.stop)
        folder = self.tmp / "dashboards" / "ops"
        folder.mkdir()
        (folder / "people.sql").write_text(SQL, encoding="utf-8")
        (folder / "dashboard.json").write_text(json.dumps({"name": "Ops", "cards": [
            {"key": "people", "name": "Operators", "display": "table", "row": 0, "col": 0, "size_x": 12, "size_y": 6}]}), encoding="utf-8")

    def ask(self, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", config.PORT, timeout=20)
        headers = {"Host": f"127.0.0.1:{config.PORT}", "Cookie": "studio_session=test-token",
                   "Origin": f"http://127.0.0.1:{config.PORT}", "Content-Type": "application/json"}
        conn.request("POST" if body is not None else "GET", path, body=json.dumps(body) if body is not None else None, headers=headers)
        text = conn.getresponse().read().decode()
        conn.close()
        return json.loads(text)

    def card(self):
        return self.ask("/api/dashboard?slug=ops")


class ShowTest(PageCase):
    def setUp(self):
        super().setUp()
        serve(self)

    def test_the_users_word_shows_it_and_takes_it_back(self):
        hidden = self.ask("/api/run", {"slug": "ops", "key": "people"})
        self.assertEqual(hidden["result"]["rows"], [["North Lines", "***", "***", 3]])
        before = self.card()
        self.assertEqual((before["cards"][0]["shown"], before["cards"][0]["sql_hash"]), ([], hidden["sql_hash"]))

        self.assertEqual(self.ask("/api/show", {"slug": "ops", "key": "people", "columns": ["Phone"], "show": True}), {"ok": True})
        after = self.card()
        self.assertEqual(after["cards"][0]["shown"], ["Phone"])
        self.assertNotEqual(after["cards"][0]["sql_hash"], hidden["sql_hash"], "the page sees a changed card and draws it again")
        self.assertNotEqual(after["version"], before["version"])
        self.assertIsNone(after["data"]["people"], "the hidden result is not passed off as the shown one")
        shown = self.ask("/api/run", {"slug": "ops", "key": "people"})
        self.assertEqual(shown["result"]["rows"], [["North Lines", "555-0142", "***", 3]])
        self.assertEqual(self.reveals, [("preview", []), ("preview", ["Phone"])])
        self.assertEqual(self.card()["data"]["people"]["rows"][0][1], "555-0142")
        self.assertEqual(len(list((config.CACHE_DIR / "shown").glob("*.json"))), 1)

        # Hiding it again also drops the saved result that held the values.
        self.assertEqual(self.ask("/api/show", {"slug": "ops", "key": "people", "columns": ["Phone"], "show": False}), {"ok": True})
        self.assertFalse((config.CACHE_DIR / "shown").exists())
        again = self.card()
        self.assertEqual((again["cards"][0]["shown"], again["cards"][0]["sql_hash"]), ([], hidden["sql_hash"]))
        self.assertEqual(again["data"]["people"]["rows"], [["North Lines", "***", "***", 3]])
        self.assertEqual(specs.shown("ops"), {})

    def test_what_cannot_be_shown(self):
        ask = lambda columns: self.ask("/api/show", {"slug": "ops", "key": "people", "columns": columns, "show": True})
        self.assertIn("otp cannot be shown", ask(["Phone", "otp"])["error"])
        self.assertIn("nothing hidden", ask(["Operator"])["error"])
        self.assertIn("nothing hidden", ask("Phone")["error"])
        self.assertIn("No such card", self.ask("/api/show", {"slug": "ops", "key": "nope", "columns": ["Phone"], "show": True})["error"])
        self.assertEqual(specs.shown("ops"), {})

    def test_claude_gets_the_stars_whatever_the_user_chose(self):
        specs.show("ops", "people", ["Phone"], True)
        job = Job(id="j", kind="edit", key="ops", slug="ops", label="ops", tools=ALL_TOOLS, token="t", started="now")
        for args in ({"file": "dashboards/ops/people.sql"}, {"sql": SQL}):
            reply = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "run_query", "arguments": args}}, job)
            text = reply["result"]["content"][0]["text"]
            self.assertIn("***", text)
            self.assertNotIn("555-0142", text)
        self.assertEqual(self.reveals, [("claude", []), ("claude", [])])
        # Nor can it give the word itself: that is kept where a run can neither read nor write.
        self.assertTrue(specs._shown_file("ops").is_relative_to(config.DATA_DIR))
        policy = write_policy("ops")
        self.assertEqual([rule for rule in policy.allow if rule.startswith(("Edit", "Write"))], ["Edit(./dashboards/ops/**)", "Write(./dashboards/ops/**)"])
        self.assertIn("Read(./data/**)", policy.deny)

    def test_a_removed_dashboard_takes_its_word_with_it(self):
        self.ask("/api/show", {"slug": "ops", "key": "people", "columns": ["Phone"], "show": True})
        self.ask("/api/run", {"slug": "ops", "key": "people"})
        self.assertEqual(self.ask("/api/remove", {"slug": "ops"}), {"ok": True})
        self.assertEqual(specs.shown("ops"), {})
        self.assertFalse((config.CACHE_DIR / "shown").exists())


class GoLiveTest(PageCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeMetabase()
        self.addCleanup(self.fake.stop)
        metabase._seen.clear()
        self.databases(database(metabase_id=DATABASE))
        for patch in [mock.patch.object(config, "METABASE_URL", self.fake.url), mock.patch.object(config, "METABASE_API_KEY", KEY),
                      mock.patch.object(config, "METABASE_COLLECTION", "Studio dashboards"),
                      mock.patch.object(config, "METABASE_COLLECTION_ID", None)]:
            patch.start()
            self.addCleanup(patch.stop)

    def drawn(self, names, reveal=()):
        """Save a result for the card, as if it had been drawn with these columns."""
        columns, rows, _ = db._shape([Column(name, TEXT) for name in names], [tuple("555-0142" for _ in names)], 10, reveal)
        db.cache_put(SQL, {**canned_result(SQL), "columns": columns, "rows": rows}, reveal=reveal)

    def test_personal_data_is_published_only_on_the_users_word(self):
        self.drawn(["Operator", "Phone", "Email"])
        plan = metabase.plan("ops")
        self.assertEqual((plan["blockers"], plan["sensitive"]), ([], [{"card": "Operators", "columns": ["Phone", "Email"]}]))
        with self.assertRaises(metabase.MetabaseError) as refused:
            metabase.publish("ops")
        self.assertIn("Confirm that in the Go live window", str(refused.exception))
        self.assertEqual(self.fake.writes(), [], "nothing is published before the user confirms")
        serve(self)
        self.assertIn("Confirm", self.ask("/api/golive", {"slug": "ops"})["error"])
        self.assertIn("Confirm", self.ask("/api/golive", {"slug": "ops", "sensitive_ok": "yes"})["error"])
        self.assertEqual(self.ask("/api/golive", {"slug": "ops", "sensitive_ok": True})["published"]["mode"], "create")

    def test_it_counts_whether_or_not_the_preview_shows_it(self):
        # Metabase runs the query as written, so what the preview hides is still published.
        specs.show("ops", "people", ["Phone"], True)
        self.assertIn("has not drawn yet", metabase.plan("ops")["blockers"][0])
        self.drawn(["Operator", "Phone"], reveal=["Phone"])
        plan = metabase.plan("ops")
        self.assertEqual((plan["blockers"], plan["sensitive"]), ([], [{"card": "Operators", "columns": ["Phone"]}]))

    def test_a_secret_is_never_published(self):
        self.drawn(["Operator", "Phone", "otp"])
        plan = metabase.plan("ops")
        self.assertIn('"Operators" has a column that is never published (otp)', plan["blockers"][0])
        with self.assertRaises(metabase.MetabaseError):
            metabase.publish("ops", sensitive_ok=True)
        self.assertEqual(self.fake.writes(), [])

    def test_a_result_saved_before_columns_said_their_kind(self):
        old = canned_result(SQL)
        old["columns"] = [{"name": "phone", "type": "text", "pii": True}, {"name": "fcm_token", "type": "text", "pii": True}]
        db.cache_put(SQL, old)
        self.assertEqual([c["sensitive"] for c in db.cache_get(SQL)["columns"]], ["personal", "secret"])


if __name__ == "__main__":
    unittest.main()
