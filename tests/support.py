"""Shared test setup: a temporary project folder, no database, a stand-in for Claude."""
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# The tests never see the real settings, secrets or conversations.
os.environ["STUDIO_DATA_DIR"] = tempfile.mkdtemp(prefix="studio-test-data-")

from app import config, db, settings  # noqa: E402
from app.claude import ClaudeRunner  # noqa: E402

FAKE = [sys.executable, str(Path(__file__).resolve().parent / "fake_claude.py")]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def database(ident="main", name="Warehouse", metabase_id=None, **extra):
    """One database's settings, as the app keeps them."""
    return {**config.DATABASE_FIELDS, "id": ident, "name": name, "host": "db.example.com", "dbname": ident, "user": "reader",
            "password": "s3cret-pw", "schemas": ["public"], "metabase_database_id": metabase_id, **extra}


def canned_result(sql):
    return {"columns": [{"name": "Tickets today", "type": "number", "pii": False}], "rows": [[7]], "row_count": 1,
            "truncated": False, "ms": 3, "cost": 1.0, "ran_at": datetime.now().isoformat(timespec="seconds")}


class StudioCase(unittest.TestCase):
    """Points the app at a temporary folder and replaces the database with a canned answer."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="studio-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(self._default_settings)  # runs last, after the patches below are undone
        # The port is in the settings too, so a save in a test keeps the test's own port.
        port = settings._current["port"] = free_port()
        self.queries = []
        self.ran_on = []   # the database each of those queries was sent to

        def run(sql, limit=None, allow_heavy=False, source="cli", database=None):
            self.queries.append((source, sql.strip()))
            self.ran_on.append(database)
            return canned_result(sql)

        patches = [
            mock.patch.object(config, "ROOT", self.tmp),
            mock.patch.object(config, "DASHBOARDS_DIR", self.tmp / "dashboards"),
            mock.patch.object(config, "THREADS_DIR", self.tmp / "data" / "threads"),
            mock.patch.object(config, "CACHE_DIR", self.tmp / ".cache"),
            mock.patch.object(config, "LOG_FILE", self.tmp / "logs" / "queries.log"),
            mock.patch.object(config, "DATA_DIR", self.tmp / "data"),
            mock.patch.object(config, "SETTINGS_FILE", self.tmp / "data" / "settings.json"),
            mock.patch.object(config, "SCHEMA_FILE", self.tmp / "data" / "schema.json"),
            mock.patch.object(config, "PORT", port),
            mock.patch.object(config, "STUDIO_MODEL", ""),
            mock.patch.object(db, "run", run),
            mock.patch.dict(os.environ, {"FAKE_ROOT": str(self.tmp), "FAKE_MODE": "echo"}),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        (self.tmp / "dashboards").mkdir()

    @staticmethod
    def _default_settings():
        settings._current.clear()
        settings._current.update(config.DEFAULTS)
        config.apply(config.DEFAULTS)

    def mode(self, name):
        os.environ["FAKE_MODE"] = name

    def databases(self, *entries):
        """Put these databases in force, as if they were saved in Settings."""
        settings._current["databases"] = list(entries)
        config.DATABASES = list(entries)

    def tables(self, ident, names):
        """Give a database a table list: every table gets an id and a created_at column."""
        tables = {name: {"schema": "public", "kind": "table", "rows": 10, "size_bytes": 1, "comment": None, "indexes": [],
                         "foreign_keys": [], "columns": [{"name": "id", "type": "bigint", "nullable": False},
                                                         {"name": "created_at", "type": "timestamp with time zone", "nullable": False}]}
                  for name in names}
        path = config.SCHEMA_FILE if ident == "main" else config.SCHEMA_FILE.with_name(f"schema-{ident}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"generated_at": "2026-01-01T00:00:00", "tables": tables}), encoding="utf-8")

    def runner(self):
        return ClaudeRunner(prefix=FAKE)

    def wait(self, check, seconds=30):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if check():
                return
            time.sleep(0.05)
        self.fail("timed out waiting")


def serve(case, token="test-token"):
    """Start the real server for a test and stop it afterwards."""
    from app import server

    httpd = server.make_server(token, runner=case.runner())
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    def stop():
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()
        httpd.studio.assistant.shutdown(timeout=10)

    case.addCleanup(stop)
    return httpd


def pid_alive(pid):
    import subprocess

    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return str(pid) in out


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
