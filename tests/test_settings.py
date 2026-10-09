import http.client
import json
import unittest
from unittest import mock

import psycopg2
from fake_metabase import DATABASE, KEY, OURS, FakeMetabase
from support import StudioCase, serve

from app import checks, config, settings

DB = {"db_host": "db.example.com", "db_name": "shop", "db_user": "reader", "db_password": "s3cret-pw"}


class SettingsFileTest(StudioCase):
    def test_saved_settings_come_back_and_take_effect(self):
        self.assertEqual(settings.save({**DB, "timezone": "Europe/Paris", "db_schemas": "public, sales", "port": "9000"}), [])
        self.assertEqual((config.DB_HOST, config.DB_PASS, config.TIMEZONE, config.PORT), ("db.example.com", "s3cret-pw", "Europe/Paris", 9000))
        self.assertEqual(config.DB_SCHEMAS, ["public", "sales"])
        settings._current.clear()
        config.apply(config.DEFAULTS)
        settings.start()
        self.assertEqual((config.DB_NAME, config.DB_PASS, config.DB_SCHEMAS), ("shop", "s3cret-pw", ["public", "sales"]))
        self.assertTrue(settings.database_ready())

    def test_secrets_are_not_readable_in_the_file_or_sent_to_the_page(self):
        settings.save({**DB, "metabase_url": "https://mb.example.com/", "metabase_api_key": "mb_secret-key"})
        stored = config.SETTINGS_FILE.read_text(encoding="utf-8")
        if hasattr(__import__("ctypes"), "WinDLL"):
            self.assertNotIn("s3cret-pw", stored)
            self.assertNotIn("mb_secret-key", stored)
        seen = settings.public()
        self.assertNotIn("db_password", seen)
        self.assertNotIn("metabase_api_key", seen)
        self.assertEqual((seen["db_password_set"], seen["metabase_api_key_set"]), (True, True))
        self.assertEqual(seen["metabase_url"], "https://mb.example.com")
        self.assertNotIn("s3cret-pw", json.dumps(seen))

    def test_an_empty_secret_keeps_the_saved_one(self):
        settings.save(DB)
        settings.save({"db_host": "other.example.com", "db_password": ""})
        self.assertEqual((config.DB_HOST, config.DB_PASS), ("other.example.com", "s3cret-pw"))

    def test_bad_values_are_refused_and_nothing_changes(self):
        settings.save(DB)
        for bad, word in [({"port": "80"}, "Local port"), ({"timezone": "Paris; drop table"}, "time zone"),
                          ({"metabase_url": "mb.example.com"}, "http"), ({"db_schemas": "public, bad name"}, "schema"),
                          ({"statement_timeout_ms": "abc"}, "cannot be used"), ({"db_sslmode": "maybe"}, "SSL")]:
            problems = settings.save(bad)
            self.assertTrue(problems and word in problems[0], (bad, problems))
        self.assertEqual((config.PORT, config.TIMEZONE, config.METABASE_URL), (config.DEFAULTS["port"], "UTC", ""))

    def test_one_remembered_value(self):
        settings.save(DB)
        settings.remember("metabase_collection_id", 12)
        self.assertEqual(config.METABASE_COLLECTION_ID, 12)
        self.assertEqual(json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))["metabase_collection_id"], 12)


class FakeCursor:
    def __init__(self, row, bad_zone=False):
        self.row, self.bad_zone = row, bad_zone

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        if "at time zone" in sql and self.bad_zone:
            raise psycopg2.DataError("time zone not recognized")

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, row, bad_zone=False):
        self.row, self.bad_zone, self.closed = row, bad_zone, False

    def cursor(self):
        return FakeCursor(self.row, self.bad_zone)

    def rollback(self):
        pass

    def close(self):
        self.closed = True


class ChecksTest(StudioCase):
    def look(self, row, **extra):
        conn = FakeConnection(row, **extra)
        with mock.patch.object(psycopg2, "connect", return_value=conn) as connect:
            reply = checks.database({**DB, "db_schemas": "public", "timezone": "UTC"})
        self.assertTrue(conn.closed)
        self.assertIn("default_transaction_read_only=on", connect.call_args.kwargs["options"])
        return reply

    def test_database_that_only_reads(self):
        reply = self.look(("reader", "17.2", 1, 40, 0, 0, False))
        self.assertEqual((reply["ok"], reply["read_only"], reply["tables"], reply["notes"]), (True, True, 40, []))

    def test_database_user_that_can_write_is_pointed_out(self):
        reply = self.look(("app", "16.1", 1, 40, 12, 1, False))
        self.assertFalse(reply["read_only"])
        self.assertIn("can change data in 12 tables", reply["notes"][0])
        self.assertIn("superuser", self.look(("postgres", "16.1", 1, 40, 40, 1, True))["notes"][0])
        self.assertIn("may create new ones", self.look(("reader", "16.1", 1, 40, 0, 1, False))["notes"][0])

    def test_database_problems_are_said_plainly(self):
        self.assertIn("time zone", self.look(("reader", "17.2", 1, 40, 0, 0, False), bad_zone=True)["notes"][0])
        self.assertIn("does not exist", self.look(("reader", "17.2", 0, 0, 0, 0, False))["notes"][0])
        with mock.patch.object(psycopg2, "connect", side_effect=psycopg2.OperationalError("password authentication failed\nmore")):
            reply = checks.database(DB)
        self.assertEqual(reply, {"ok": False, "error": "password authentication failed"})
        self.assertFalse(checks.database({"db_host": "x"})["ok"])

    def test_the_saved_password_is_used_when_the_form_leaves_it_empty(self):
        settings.save(DB)
        conn = FakeConnection(("reader", "17.2", 1, 1, 0, 0, False))
        with mock.patch.object(psycopg2, "connect", return_value=conn) as connect:
            checks.database({"db_host": "db.example.com", "db_password": ""})
        self.assertEqual(connect.call_args.kwargs["password"], "s3cret-pw")

    def test_metabase(self):
        fake = FakeMetabase()
        self.addCleanup(fake.stop)
        reply = checks.metabase_side({"metabase_url": fake.url, "metabase_api_key": KEY})
        self.assertTrue(reply["ok"] and reply["root_can_write"])
        self.assertIn({"id": DATABASE, "name": "Warehouse (read-only)", "engine": None, "can_query": True}, reply["databases"])
        self.assertEqual([c["id"] for c in reply["collections"] if c["can_write"]], [OURS])
        self.assertEqual(fake.writes(), [])
        self.assertIn("did not accept", checks.metabase_side({"metabase_url": fake.url, "metabase_api_key": "mb_wrong"})["error"])
        self.assertFalse(checks.metabase_side({})["ok"])


class SettingsPageTest(StudioCase):
    def setUp(self):
        super().setUp()
        serve(self)

    def ask(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", config.PORT, timeout=20)
        headers = {"Host": f"127.0.0.1:{config.PORT}", "Cookie": "studio_session=test-token",
                   "Origin": f"http://127.0.0.1:{config.PORT}", "Content-Type": "application/json"}
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        response = conn.getresponse()
        text = response.read().decode()
        conn.close()
        return response.status, text

    def test_a_new_install_is_asked_to_set_up(self):
        setup = json.loads(self.ask("GET", "/api/state")[1])["setup"]
        self.assertEqual((setup["database"], setup["metabase"], setup["tables"]), (False, False, None))

    def test_saving_from_the_page(self):
        port = config.PORT
        status, text = self.ask("POST", "/api/settings", {"values": {**DB, "port": port, "metabase_api_key": "mb_secret-key"}})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(text)["setup"]["database"])
        for reply in (text, self.ask("GET", "/api/settings")[1], self.ask("GET", "/api/state")[1]):
            self.assertNotIn("s3cret-pw", reply)
            self.assertNotIn("mb_secret-key", reply)
        self.assertIn("Local port", json.loads(self.ask("POST", "/api/settings", {"values": {"port": 1}})[1])["problems"][0])

    def test_settings_need_the_session_like_everything_else(self):
        conn = http.client.HTTPConnection("127.0.0.1", config.PORT, timeout=10)
        conn.request("GET", "/api/settings", headers={"Host": f"127.0.0.1:{config.PORT}"})
        self.assertEqual(conn.getresponse().status, 401)
        conn.close()


if __name__ == "__main__":
    unittest.main()
