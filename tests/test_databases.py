"""Several databases: each dashboard belongs to one, and everything about it stays on that one."""
import http.client
import json
import os
import unittest
from unittest import mock

from fake_metabase import DATABASE, KEY, FakeMetabase
from support import StudioCase, canned_result, database, read_json, serve

from app import config, db, mcp, metabase, schema, specs
from app.assistant import ALL_TOOLS, Job, build_prompt, edit_prompt, plan_prompt

SQL = "select 7 as n"
CARD = {"key": "n", "name": "N", "display": "scalar", "row": 0, "col": 0, "size_x": 6, "size_y": 3}


class TwoDatabases(StudioCase):
    """The first database and a second one, "sales", each with its own table list."""

    def setUp(self):
        super().setUp()
        self.databases(database(metabase_id=DATABASE), database("sales", "Sales", metabase_id=8))
        self.tables("main", ["orders", "customers"])
        self.tables("sales", ["leads"])

    def make(self, slug, on=None, sql=SQL, asks=None, **card):
        """A one-card dashboard. Without `on` its dashboard.json names no database, like one made before there were several.

        `asks` are the dashboard's filters; anything else goes on the card.
        """
        folder = self.tmp / "dashboards" / slug
        folder.mkdir()
        (folder / "n.sql").write_text(sql, encoding="utf-8")
        spec = {"name": slug.title(), **({"database": on} if on else {}), **({"filters": asks} if asks else {}),
                "cards": [{**CARD, **card}]}
        (folder / "dashboard.json").write_text(json.dumps(spec), encoding="utf-8")

    def ask(self, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", config.PORT, timeout=20)
        headers = {"Host": f"127.0.0.1:{config.PORT}", "Cookie": "studio_session=test-token",
                   "Origin": f"http://127.0.0.1:{config.PORT}", "Content-Type": "application/json"}
        conn.request("POST" if body is not None else "GET", path, body=json.dumps(body) if body is not None else None, headers=headers)
        text = conn.getresponse().read().decode()
        conn.close()
        return json.loads(text)


class DashboardTest(TwoDatabases):
    def test_a_dashboard_belongs_to_one_database(self):
        self.make("old")
        self.make("leads", on="sales")
        old, leads = specs.load("old"), specs.load("leads")
        self.assertEqual((old["database"], old["database_name"], old["problems"]), ("main", "Warehouse", []))
        self.assertEqual((leads["database"], leads["database_name"], leads["problems"]), ("sales", "Sales", []))
        self.assertEqual(specs.summary("leads")["database_name"], "Sales")
        # The same query on two databases is two results.
        self.assertNotEqual(old["cards"][0]["sql_hash"], leads["cards"][0]["sql_hash"])
        db.cache_put(SQL, canned_result(SQL), "sales")
        self.assertIsNone(db.cache_get(SQL))
        self.assertIsNone(db.cache_get(SQL, "main"))
        self.assertEqual(db.cache_get(SQL, "sales")["rows"], [[7]])

    def test_the_first_database_keeps_the_results_it_had(self):
        # Results saved before there were several databases are still found.
        self.assertEqual(db.sql_hash(SQL), db.sql_hash(SQL, "main"))
        with mock.patch.object(config, "DATABASES", []):
            alone = db.sql_hash(SQL)
        self.assertEqual(alone, db.sql_hash(SQL, "main"))

    def test_a_database_that_is_no_longer_in_settings(self):
        self.make("lost", on="archive")
        spec = specs.load("lost")
        self.assertEqual(spec["database"], "archive")
        self.assertIn('belongs to the database "archive", which is not in Settings', spec["problems"][0])
        self.assertNotEqual(spec["cards"][0]["sql_hash"], db.sql_hash(SQL, "main"))

    def test_filters_look_in_the_dashboards_own_table_list(self):
        asks = [{"key": "date", "name": "Date", "type": "date"}]
        sql = "select count(*) as n from leads where {{date}}"
        self.make("a", sql=sql, asks=asks, filters={"date": "leads.created_at"})
        self.make("b", on="sales", sql=sql, asks=asks, filters={"date": "leads.created_at"})
        self.assertIn("The table list has no column leads.created_at", " ".join(specs.load("a")["problems"]))
        spec = specs.load("b")
        self.assertEqual(spec["problems"], [])
        self.assertIn('"public"."leads"."created_at" >=', specs.query(spec["cards"][0], spec, {"date": "thisday"}))

    def test_recording_the_database_in_the_file(self):
        self.make("x")
        path = self.tmp / "dashboards" / "x" / "dashboard.json"
        specs.assign("x", "sales")
        raw = read_json(path)
        self.assertEqual((list(raw)[:2], raw["database"], raw["cards"]), (["name", "database"], "sales", [CARD]))
        written = path.read_text(encoding="utf-8")
        specs.assign("x", "sales")
        specs.assign("x", None)
        self.assertEqual(path.read_text(encoding="utf-8"), written)


class PageTest(TwoDatabases):
    def setUp(self):
        super().setUp()
        serve(self)

    def test_each_card_runs_on_its_dashboards_database(self):
        self.make("old")
        self.make("leads", on="sales")
        self.assertIn("result", self.ask("/api/run", {"slug": "old", "key": "n"}))
        # The other dashboard has the same query, but nothing drawn on its own database yet.
        self.assertIsNone(self.ask("/api/dashboard?slug=leads")["data"]["n"])
        self.assertIn("result", self.ask("/api/run", {"slug": "leads", "key": "n"}))
        self.assertEqual(self.ran_on, ["main", "sales"])
        old, leads = self.ask("/api/dashboard?slug=old"), self.ask("/api/dashboard?slug=leads")
        self.assertEqual((old["database_name"], leads["database_name"]), ("Warehouse", "Sales"))
        self.assertTrue(old["data"]["n"] and leads["data"]["n"])
        state = self.ask("/api/state")
        self.assertEqual(state["databases"], [{"id": "main", "name": "Warehouse", "ready": True},
                                              {"id": "sales", "name": "Sales", "ready": True}])
        self.assertEqual({d["slug"]: d["database_name"] for d in state["dashboards"]}, {"old": "Warehouse", "leads": "Sales"})

    def test_a_filter_list_runs_on_the_dashboards_database(self):
        self.make("leads", on="sales")
        folder = self.tmp / "dashboards" / "leads"
        (folder / "kinds.sql").write_text("select distinct kind from leads", encoding="utf-8")
        raw = read_json(folder / "dashboard.json")
        raw["filters"] = [{"key": "kind", "name": "Kind", "type": "text", "values": "kinds"}]
        (folder / "dashboard.json").write_text(json.dumps(raw), encoding="utf-8")
        self.assertEqual(self.ask("/api/options", {"slug": "leads", "key": "kind"})["options"], [7])
        self.assertEqual(self.ran_on, ["sales"])
        self.assertEqual(self.ask("/api/dashboard?slug=leads")["options"]["kind"], [7])

    def test_adding_changing_and_removing_a_database(self):
        new = {"name": "Sales EU", "host": "eu.example.com", "dbname": "sales", "user": "reader", "password": "pw-3",
               "metabase_database_id": "8"}
        reply = self.ask("/api/settings/database", {"values": new})
        self.assertEqual((reply["id"], [d["id"] for d in reply["settings"]["databases"]]), ("sales_eu", ["main", "sales", "sales_eu"]))
        self.assertNotIn("pw-3", json.dumps(reply))
        self.assertEqual(config.database("sales_eu")["metabase_database_id"], 8)
        self.assertIn("already a database named", self.ask("/api/settings/database", {"values": {**new, "name": "sales eu"}})["problems"][0])
        # Changing one thing keeps the rest, the password included.
        self.assertTrue(self.ask("/api/settings/database", {"values": {"id": "sales_eu", "metabase_database_id": ""}})["ok"])
        entry = config.database("sales_eu")
        self.assertEqual((entry["metabase_database_id"], entry["password"], entry["host"]), (None, "pw-3", "eu.example.com"))
        # A database leaves only when no dashboard is on it. One that names no database is on the first.
        self.make("old")
        self.make("leads", on="sales")
        self.tables("sales_eu", ["x"])
        self.assertIn('"Sales" is used by 1 dashboard', self.ask("/api/settings/database/remove", {"database": "sales"})["problems"][0])
        self.assertIn('"Warehouse" is used by 1 dashboard', self.ask("/api/settings/database/remove", {"database": "main"})["problems"][0])
        self.assertTrue(self.ask("/api/settings/database/remove", {"database": "sales_eu"})["ok"])
        self.assertEqual([d["id"] for d in config.DATABASES], ["main", "sales"])
        self.assertFalse(config.SCHEMA_FILE.with_name("schema-sales_eu.json").exists())
        self.assertIn("no such database", self.ask("/api/settings/database/remove", {"database": "sales_eu"})["problems"][0])

    def test_the_table_list_is_read_for_one_database(self):
        asked = []

        def catalog(statements, database=None):
            asked.append(database)
            return [[("public", "leads_2", "r", 5, 100, None)], [("public", "leads_2", "id", "bigint", True)], [], []]

        with mock.patch.object(db, "fetch_catalog", catalog):
            reply = self.ask("/api/settings/tables", {"database": "sales"})
        self.assertEqual((reply["tables"], asked), (1, ["sales"]))
        self.assertIn("leads_2", schema.list_tables("", "sales"))
        self.assertNotIn("leads_2", schema.list_tables(""))
        self.assertIn("orders", schema.list_tables("", "main"))
        self.assertEqual([d["tables"]["tables"] for d in reply["settings"]["databases"]], [2, 1])
        self.assertIn("no database", self.ask("/api/settings/tables", {"database": "archive"})["error"])


def call(job, name, **args):
    reply = mcp.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": name, "arguments": args}}, job)
    return reply["result"]["content"][0]["text"]


class ClaudeTest(TwoDatabases):
    def setUp(self):
        super().setUp()
        self.assistant = serve(self).studio.assistant

    def settle(self):
        self.wait(lambda: not self.assistant.busy())

    def test_a_new_dashboard_is_planned_and_built_on_the_chosen_database(self):
        self.mode("plan")
        self.assertIn("no longer in Settings", self.assistant.ask("new", None, "Leads per day", "archive"))
        self.databases(*config.DATABASES, database("empty", "Empty"))
        self.assertIn('table list of "Empty" has not been read', self.assistant.ask("new", None, "Leads per day", "empty"))
        self.assertEqual(self.assistant.drafts(), [])

        self.assertIsNone(self.assistant.ask("new", None, "Leads per day", "sales"))
        self.assertEqual(self.assistant.job.database, "sales")
        self.settle()
        plan = self.assistant.thread(self.assistant.job.key)[-1]
        self.assertEqual((plan["role"], plan["database"], plan["database_name"]), ("plan", "sales", "Sales"))

        self.mode("build")
        self.assertIsNone(self.assistant.build(plan["id"]))
        self.assertEqual(self.assistant.state()["job"]["database"], "sales")
        self.settle()
        self.assertEqual(self.assistant.job.status, "done")
        self.assertEqual(self.ran_on, ["sales"])
        # The stand-in wrote no "database" line; the studio records it.
        self.assertEqual(read_json(self.tmp / "dashboards" / "daily_ops" / "dashboard.json")["database"], "sales")
        spec = specs.load("daily_ops")
        self.assertEqual((spec["database"], spec["problems"]), ("sales", []))
        self.assertEqual(db.cache_get(spec["cards"][0]["sql"], "sales")["rows"], [[7]])
        self.assertIsNone(db.cache_get(spec["cards"][0]["sql"], "main"))

        # A change works on the same database, and cannot move the dashboard off it.
        self.mode("edit")
        os.environ["FAKE_DROPS_DATABASE"] = "daily_ops"
        self.addCleanup(os.environ.pop, "FAKE_DROPS_DATABASE", None)
        self.assertIsNone(self.assistant.ask("edit", "daily_ops", "Make it stacked"))
        self.assertEqual(self.assistant.job.database, "sales")
        self.settle()
        self.assertEqual(specs.load("daily_ops")["database"], "sales")

    def test_without_a_choice_it_is_the_first_database(self):
        self.mode("plan")
        self.assertIsNone(self.assistant.ask("new", None, "Orders per day"))
        self.assertEqual(self.assistant.job.database, "main")
        self.settle()
        plan = self.assistant.thread(self.assistant.job.key)[-1]
        self.assertEqual(plan["database_name"], "Warehouse")
        # A plan made for a database that has since been removed is not built.
        self.databases(database("sales", "Sales"))
        self.assertIn("no longer in Settings", self.assistant.build(plan["id"]))

    def test_claude_is_told_which_database(self):
        self.assertIn('for the database "Sales"', plan_prompt("x", [], "sales"))
        built = build_prompt("leads", {"slug": "leads"}, "x", "sales")
        self.assertIn('for the database "Sales"', built)
        self.assertIn('write "database": "sales"', built)
        self.assertIn('Leave "database" in dashboard.json as it is', edit_prompt("leads", "x", [], "sales"))
        self.assertNotIn("database", plan_prompt("x", []))

    def test_claudes_tools_stay_on_the_jobs_database(self):
        job = Job(id="j", kind="build", key="_new", slug="leads", label="leads", tools=ALL_TOOLS, token="t", started="now",
                  database="sales")
        listed = call(job, "list_tables")
        self.assertIn("leads", listed)
        self.assertNotIn("orders", listed)
        self.assertIn("No table named 'orders'", call(job, "describe_table", table="orders"))
        self.assertIn("created_at", call(job, "describe_table", table="leads"))
        call(job, "run_query", sql="select 1")
        self.make("leads", sql="select count(*) as n from leads where {{date}}", asks=[{"key": "date", "name": "Date", "type": "date"}],
                  filters={"date": "leads.created_at"})
        call(job, "run_query", file="dashboards/leads/n.sql")
        self.assertEqual(self.ran_on, ["sales", "sales"])
        self.assertIsNotNone(db.cache_get("select 1", "sales"))
        # Until the file says so, the check asks for the line.
        self.assertIn('must say "database": "sales"', call(job, "check_dashboard", slug="leads"))
        specs.assign("leads", "sales")
        self.assertIn("no problems", call(job, "check_dashboard", slug="leads"))


class GoLiveTest(TwoDatabases):
    def setUp(self):
        super().setUp()
        self.fake = FakeMetabase()
        self.addCleanup(self.fake.stop)
        metabase._seen.clear()
        for patch in [mock.patch.object(config, "METABASE_URL", self.fake.url), mock.patch.object(config, "METABASE_API_KEY", KEY),
                      mock.patch.object(config, "METABASE_COLLECTION", "Studio dashboards"),
                      mock.patch.object(config, "METABASE_COLLECTION_ID", None)]:
            patch.start()
            self.addCleanup(patch.stop)

    def test_cards_are_published_on_the_dashboards_own_metabase_database(self):
        self.make("old", name="Old N")
        self.make("leads", on="sales", name="Leads N")
        db.cache_put(SQL, canned_result(SQL), "main")
        # Drawn on another database is not drawn.
        self.assertIn("has not drawn yet", metabase.plan("leads")["blockers"][0])
        db.cache_put(SQL, canned_result(SQL), "sales")
        plan = metabase.plan("leads")
        where = plan["target"]
        self.assertEqual((plan["blockers"], where["database_id"], where["database"], where["studio_database"]), ([], 8, "Marketing", "Sales"))
        metabase.publish("leads")
        metabase.publish("old")
        self.assertEqual({c["name"]: c["dataset_query"]["database"] for c in self.fake.cards.values()}, {"Leads N": 8, "Old N": DATABASE})
        self.assertEqual(read_json(self.tmp / "dashboards" / "leads" / "metabase.json")["database_id"], 8)

    def test_a_database_with_no_metabase_database_chosen(self):
        self.databases(database(metabase_id=DATABASE), database("sales", "Sales"))
        self.make("leads", on="sales")
        db.cache_put(SQL, canned_result(SQL), "sales")
        self.assertIn('No Metabase database is chosen for "Sales"', metabase.plan("leads")["blockers"][0])
        with self.assertRaises(metabase.MetabaseError):
            metabase.publish("leads")
        self.assertEqual(self.fake.writes(), [])
        text = "\n".join(metabase.doctor(with_queries=False))
        self.assertIn('Go live is NOT ready for "Sales"', text)
        self.assertIn('For "Warehouse", Go live uses the Metabase database "Warehouse (read-only)"', text)
        self.assertNotIn("Go live is ready.", text)

    def test_doctor_names_every_database(self):
        text = "\n".join(metabase.doctor(with_queries=False))
        self.assertIn('For "Sales", Go live uses the Metabase database "Marketing" (id 8).', text)
        self.assertIn("Go live is ready.", text)


if __name__ == "__main__":
    unittest.main()
