import unittest
from unittest import mock

from support import StudioCase

from app import config, db, mcp
from app.assistant import ALL_TOOLS, LOOK_TOOLS, Job


def job(tools):
    return Job(id="j", kind="build", key="_new", slug="ops", label="ops", tools=tools, token="t", started="now")


def call(a_job, name, **args):
    reply = mcp.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": name, "arguments": args}}, a_job)
    return reply["result"]["content"][0]["text"], reply["result"]["isError"]


class McpTest(StudioCase):
    def test_handshake(self):
        reply = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}, job(ALL_TOOLS))
        self.assertEqual(reply["result"]["protocolVersion"], "2025-11-25")
        self.assertIn("tools", reply["result"]["capabilities"])
        self.assertIsNone(mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, job(ALL_TOOLS)))
        self.assertEqual(mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "server/discover"}, job(ALL_TOOLS))["error"]["code"], -32601)

    def test_a_plan_cannot_query(self):
        planning = job(LOOK_TOOLS)
        listed = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, planning)["result"]["tools"]
        self.assertEqual([t["name"] for t in listed], ["list_tables", "describe_table"])
        text, failed = call(planning, "run_query", sql="select 1")
        self.assertTrue(failed)
        self.assertIn("not available", text)
        self.assertEqual(self.queries, [])

    def test_query_runs_is_counted_and_cached(self):
        building = job(ALL_TOOLS)
        text, failed = call(building, "run_query", sql="select 7")
        self.assertFalse(failed, text)
        self.assertIn("Tickets today", text)
        self.assertEqual(building.queries, 1)
        self.assertEqual(self.queries, [("claude ops", "select 7")])
        self.assertEqual(db.cache_get("select 7")["rows"], [[7]])

    def test_query_from_a_card_file(self):
        folder = self.tmp / "dashboards" / "ops"
        folder.mkdir()
        (folder / "a.sql").write_text("select 7 as n\n", encoding="utf-8")
        text, failed = call(job(ALL_TOOLS), "run_query", file="dashboards/ops/a.sql")
        self.assertFalse(failed, text)
        self.assertEqual(self.queries[0][1], "select 7 as n")
        for outside in ["../.env", ".env", "dashboards/../.env", "dashboards/ops/a.txt"]:
            text, failed = call(job(ALL_TOOLS), "run_query", file=outside)
            self.assertTrue(failed, outside)
        self.assertEqual(len(self.queries), 1)

    def test_writes_never_reach_the_database(self):
        text, failed = call(job(ALL_TOOLS), "run_query", sql="delete from t")
        self.assertTrue(failed)
        self.assertIn("Refused", text)
        self.assertEqual(self.queries, [])

    def test_query_cap(self):
        building = job(ALL_TOOLS)
        with mock.patch.object(config, "STUDIO_MAX_QUERIES", 2):
            self.assertFalse(call(building, "run_query", sql="select 1")[1])
            self.assertFalse(call(building, "run_query", sql="select 2")[1])
            text, failed = call(building, "run_query", sql="select 3")
        self.assertTrue(failed)
        self.assertIn("used its 2 queries", text)
        self.assertEqual(len(self.queries), 2)

    def test_heavy_query_is_refused_and_not_overridable(self):
        def heavy(sql, **_):
            raise db.Heavy(900000.0)

        with mock.patch.object(db, "run", heavy):
            text, failed = call(job(ALL_TOOLS), "run_query", sql="select count(*) from big", allow_heavy=True)
        self.assertTrue(failed)
        self.assertIn("Only the user can allow a heavy query", text)

    def test_check_dashboard(self):
        text, failed = call(job(ALL_TOOLS), "check_dashboard", slug="nope")
        self.assertFalse(failed)
        self.assertIn("missing", text)


if __name__ == "__main__":
    unittest.main()
