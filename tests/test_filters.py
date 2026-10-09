import http.client
import json
import unittest
from unittest import mock
from urllib.parse import quote

from fake_metabase import DATABASE, KEY, FakeMetabase
from support import StudioCase, canned_result, serve

from app import config, db, filters, guard, mcp, metabase, specs
from app.assistant import ALL_TOOLS, Job

DEFS = [{"key": "date", "type": "date"}, {"key": "status", "type": "text"}, {"key": "amount", "type": "number"}]
MAP = {"date": "orders.created_at", "status": "orders.status", "amount": "orders.amount"}
SQL = "select count(*) from orders where {{date}} [[and {{status}}]]"


def column(name, kind):
    return {"name": name, "type": kind, "nullable": True}


class FilterCase(StudioCase):
    def setUp(self):
        super().setUp()
        tables = {
            "orders": {"schema": "public", "columns": [
                column("created_at", "timestamp with time zone"), column("made", "timestamp without time zone"),
                column("day", "date"), column("status", "character varying(20)"), column("amount", "numeric(10,2)")]},
            "sales.returns": {"schema": "sales", "columns": [column("reason", "text")]},
        }
        for table in tables.values():
            table.update(kind="table", rows=10, size_bytes=1, comment=None, indexes=[], foreign_keys=[])
        config.SCHEMA_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.SCHEMA_FILE.write_text(json.dumps({"generated_at": "now", "tables": tables}))
        zone = mock.patch.object(config, "TIMEZONE", "Asia/Kolkata")
        zone.start()
        self.addCleanup(zone.stop)

    def dashboard(self, drawn=True, values_file=True):
        """An "ops" dashboard with a date filter, a status filter with a list, and one card that obeys both."""
        folder = self.tmp / "dashboards" / "ops"
        folder.mkdir(exist_ok=True)
        (folder / "orders.sql").write_text(SQL, encoding="utf-8")
        (folder / "total.sql").write_text('select 5 as "N"', encoding="utf-8")
        if values_file:
            (folder / "status_list.sql").write_text("select distinct status from orders order by 1", encoding="utf-8")
        (folder / "dashboard.json").write_text(json.dumps({
            "name": "Ops",
            "filters": [{"key": "date", "name": "Date", "type": "date", "default": "past30days"},
                        {"key": "status", "name": "Status", "type": "text", "values": "status_list"}],
            "cards": [
                {"key": "orders", "name": "Orders", "display": "scalar", "filters": {"date": "orders.created_at", "status": "orders.status"},
                 "row": 0, "col": 0, "size_x": 6, "size_y": 3},
                {"key": "total", "name": "Total", "display": "scalar", "row": 0, "col": 6, "size_x": 6, "size_y": 3},
            ]}), encoding="utf-8")
        spec = specs.load("ops")
        if drawn:
            for card in spec["cards"]:
                db.cache_put(specs.query(card, spec), canned_result("x"))
            listing = canned_result("x")
            listing["rows"] = [["new"], ["paid"], [None]]
            db.cache_put(specs.options_query(spec, "status"), listing)
        return spec


class RenderTest(FilterCase):
    def render(self, sql=SQL, **values):
        return filters.render(sql, MAP, DEFS, values)

    def test_nothing_picked(self):
        self.assertEqual(self.render(), "select count(*) from orders where TRUE ")

    def test_a_relative_date_is_counted_in_the_studio_time_zone(self):
        out = self.render(date="past30days")
        self.assertIn('"public"."orders"."created_at" >= (', out)
        self.assertIn("interval '30 day'", out)
        self.assertIn("at time zone 'Asia/Kolkata'", out)
        self.assertIn('"public"."orders"."created_at" < (', out)
        self.assertTrue(guard.check(out))

    def test_a_date_range(self):
        out = self.render(date="2026-01-01~2026-01-31")
        self.assertIn("\"created_at\" >= (date '2026-01-01')::timestamp at time zone 'Asia/Kolkata'", out)
        self.assertIn("\"created_at\" < ((date '2026-01-31' + 1))::timestamp at time zone 'Asia/Kolkata'", out)
        self.assertNotIn(">=", self.render(date="~2026-01-31"))
        self.assertNotIn("<", self.render(date="2026-01-01~").split("where")[1])

    def test_the_column_type_decides_the_comparison(self):
        plain = filters.render("select 1 from orders where {{date}}", {"date": "orders.day"}, DEFS, {"date": "2026-01-01~2026-01-31"})
        self.assertEqual(plain, "select 1 from orders where (\"public\".\"orders\".\"day\" >= date '2026-01-01' and \"public\".\"orders\".\"day\" < (date '2026-01-31' + 1))")
        naive = filters.render("select 1 from orders where {{date}}", {"date": "orders.made"}, DEFS, {"date": "2026-01-01"})
        self.assertIn("(date '2026-01-01')::timestamp and", naive)
        self.assertNotIn("time zone", naive)
        with self.assertRaises(filters.FilterError):
            filters.render("select 1 from orders where {{date}}", {"date": "orders.status"}, DEFS, {"date": "thisday"})

    def test_text_is_quoted_whatever_it_holds(self):
        self.assertTrue(self.render(status="paid").endswith('where TRUE and "public"."orders"."status" = \'paid\''))
        self.assertIn("= 'O''Brien'", self.render(status="O'Brien"))
        attack = self.render(status="x'; drop table orders; --")
        self.assertIn("= 'x''; drop table orders; --'", attack)
        self.assertTrue(guard.check(attack), "still one SELECT")
        self.assertEqual(self.render(status="{{date}}").count("created_at"), 0, "a value is never read as a filter")
        self.assertIn("in ('new', 'paid')", self.render(status=["new", "paid"]))

    def test_numbers_and_dates_are_parsed_not_pasted(self):
        self.assertIn('"amount" = 12', self.render("select 1 from orders where {{amount}}", amount="12"))
        self.assertIn('"amount" = 12.5', self.render("select 1 from orders where {{amount}}", amount=12.5))
        for bad in [{"amount": "12; drop table orders"}, {"date": "tomorrow"}, {"date": "2026-13-45"}, {"date": "past30days; select 1"}]:
            with self.assertRaises(filters.FilterError, msg=bad):
                self.render("select 1 from orders where {{date}} and {{amount}}", **bad)

    def test_tags_and_columns_must_be_known(self):
        for sql, mapping in [("select {{nope}}", MAP), ("select 1 from orders where {{status}}", {}),
                             ("select 1 from orders where {{status}}", {"status": "orders.missing"}),
                             ("select 1 from orders where {{status}}", {"status": "missing.status"}),
                             ("select 1 from orders where {{status}}", {"status": "status"})]:
            with self.assertRaises(filters.FilterError, msg=sql):
                filters.render(sql, mapping, DEFS, {"status": "x", "nope": "x"})

    def test_a_table_outside_public(self):
        out = filters.render("select 1 from sales.returns where {{status}}", {"status": "sales.returns.reason"}, DEFS, {"status": "late"})
        self.assertIn('"sales"."returns"."reason" = \'late\'', out)


class DashboardFiltersTest(FilterCase):
    def test_a_filtered_dashboard_loads_with_its_defaults(self):
        spec = self.dashboard()
        self.assertEqual(spec["problems"], [])
        self.assertEqual(spec["cards"][0]["tags"], ["date", "status"])
        self.assertEqual(specs.effective(spec), {"date": "past30days"})
        self.assertIn("interval '30 day'", specs.query(spec["cards"][0], spec))
        self.assertNotIn("status", specs.query(spec["cards"][0], spec).split("where")[1])
        self.assertEqual(specs.query(spec["cards"][1], spec), 'select 5 as "N"')
        self.assertEqual(specs.options_query(spec, "status"), "select distinct status from orders order by 1")

    def test_mistakes_are_listed(self):
        self.dashboard()
        folder = self.tmp / "dashboards" / "ops"
        raw = json.loads((folder / "dashboard.json").read_text())
        raw["cards"][0]["filters"] = {"date": "orders.nope"}
        raw["filters"].append({"key": "bad", "type": "colour"})
        raw["filters"][1]["values"] = "no_such_file"
        (folder / "dashboard.json").write_text(json.dumps(raw))
        problems = " | ".join(specs.load("ops")["problems"])
        for word in ["orders.nope", "{{status}}; add it", "'type' must be one of", "must name a .sql file"]:
            self.assertIn(word, problems)

    def ask(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", config.PORT, timeout=20)
        headers = {"Host": f"127.0.0.1:{config.PORT}", "Cookie": "studio_session=test-token",
                   "Origin": f"http://127.0.0.1:{config.PORT}", "Content-Type": "application/json"}
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        reply = json.loads(conn.getresponse().read())
        conn.close()
        return reply

    def test_the_page_picks_values_and_only_obeying_cards_change(self):
        serve(self)
        self.dashboard()
        first = self.ask("GET", "/api/dashboard?slug=ops")
        self.assertEqual(first["values"], {"date": "past30days"})
        self.assertEqual(first["options"], {"date": None, "status": ["new", "paid", None]})
        self.assertTrue(first["data"]["orders"] and first["data"]["total"])
        picked = {"date": "thismonth", "status": "paid"}
        second = self.ask("GET", "/api/dashboard?slug=ops&values=" + quote(json.dumps(picked)))
        hashes = lambda reply: {c["key"]: c["sql_hash"] for c in reply["cards"]}
        self.assertNotEqual(hashes(first)["orders"], hashes(second)["orders"])
        self.assertEqual(hashes(first)["total"], hashes(second)["total"])
        self.assertIsNone(second["data"]["orders"])
        ran = self.ask("POST", "/api/run", {"slug": "ops", "key": "orders", "values": picked})
        self.assertEqual(ran["sql_hash"], hashes(second)["orders"])
        self.assertIn("\"status\" = 'paid'", self.queries[-1][1])
        self.assertIn("date_trunc('month'", self.queries[-1][1])
        self.assertIn("not a date range", self.ask("POST", "/api/run", {"slug": "ops", "key": "orders", "values": {"date": "x"}})["error"])
        listed = self.ask("POST", "/api/options", {"slug": "ops", "key": "status"})
        self.assertEqual(listed["options"], [7])
        self.assertEqual(self.queries[-1], ("preview ops/list:status", "select distinct status from orders order by 1"))

    def test_claude_tests_a_filtered_query_by_file(self):
        self.dashboard(drawn=False)
        job = Job(id="j", kind="build", key="_new", slug="ops", label="ops", tools=ALL_TOOLS, token="t", started="now")
        call = lambda **args: mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "run_query", "arguments": args}}, job)["result"]
        self.assertFalse(call(file="dashboards/ops/orders.sql")["isError"])
        self.assertIn("interval '30 day'", self.queries[-1][1])
        self.assertTrue(call(sql=SQL)["isError"])
        self.assertEqual(len(self.queries), 1)


class GoLiveFiltersTest(FilterCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeMetabase()
        self.addCleanup(self.fake.stop)
        metabase._seen.clear()
        for patch in [mock.patch.object(config, "METABASE_URL", self.fake.url), mock.patch.object(config, "METABASE_API_KEY", KEY),
                      mock.patch.object(config, "METABASE_DATABASE_ID", DATABASE), mock.patch.object(config, "METABASE_COLLECTION", "Studio dashboards"),
                      mock.patch.object(config, "METABASE_COLLECTION_ID", None)]:
            patch.start()
            self.addCleanup(patch.stop)

    def test_filters_become_metabase_filters(self):
        self.dashboard()
        self.assertEqual(metabase.plan("ops")["blockers"], [])
        metabase.publish("ops")
        dash = next(iter(self.fake.dashboards.values()))
        date, status = dash["parameters"]
        self.assertEqual((date["type"], date["sectionId"], date["slug"], date["default"]), ("date/all-options", "date", "date", "past30days"))
        self.assertEqual((status["type"], status["values_source_type"], status["values_source_config"]), ("string/=", "static-list", {"values": ["new", "paid"]}))
        self.assertNotIn("default", status)
        cards = {c["name"]: c for c in self.fake.cards.values()}
        tags = cards["Orders"]["dataset_query"]["native"]["template-tags"]
        self.assertEqual(cards["Orders"]["dataset_query"]["native"]["query"], SQL, "the query is published as written")
        self.assertEqual((tags["date"]["type"], tags["date"]["dimension"], tags["date"]["widget-type"]), ("dimension", ["field", 501, None], "date/all-options"))
        self.assertEqual(tags["status"]["dimension"], ["field", 502, None])
        self.assertEqual(cards["Total"]["dataset_query"]["native"]["template-tags"], {})
        placed = {dc["card_id"]: dc for dc in dash["dashcards"]}
        wired = placed[cards["Orders"]["id"]]["parameter_mappings"]
        self.assertEqual([m["target"] for m in wired], [["dimension", ["template-tag", "date"], {"stage-number": 0}], ["dimension", ["template-tag", "status"], {"stage-number": 0}]])
        self.assertEqual({m["parameter_id"] for m in wired}, {date["id"], status["id"]})
        self.assertEqual(placed[cards["Total"]["id"]]["parameter_mappings"], [])
        self.assertFalse([c for c in self.fake.calls if c[1] == "/api/dataset"], "publishing runs nothing")
        ids = [p["id"] for p in dash["parameters"]]
        metabase.publish("ops")
        self.assertEqual([p["id"] for p in next(iter(self.fake.dashboards.values()))["parameters"]], ids, "filters keep their identity")

    def test_what_stops_a_filtered_go_live(self):
        self.dashboard(drawn=False)
        blockers = " | ".join(metabase.plan("ops")["blockers"])
        self.assertIn("has not drawn yet", blockers)
        self.assertIn('The list for the filter "Status" has not loaded yet', blockers)
        self.dashboard()
        self.fake.fields = self.fake.fields[:1]
        self.assertIn("Metabase does not know the column public.orders.status yet", " | ".join(metabase.plan("ops")["blockers"]))
        with self.assertRaises(metabase.MetabaseError):
            metabase.publish("ops")
        self.assertEqual(self.fake.writes(), [])


if __name__ == "__main__":
    unittest.main()
