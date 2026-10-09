"""Run the studio with stand-ins for the database, Claude and Metabase, in a temporary folder.

For working on the page and for screenshots. Nothing here reaches a real database,
Claude or Metabase: cards are drawn from made-up results, and Go live talks to the
in-memory Metabase from the tests.

Usage: python dev/demo_server.py [port]        (default 8790)
       DEMO_SETUP=1          start as a new install, on the setup page
       DEMO_EMPTY=1          start with no dashboards
       DEMO_NO_COLLECTION=1  make Go live fail the way a missing permission does
"""
import json
import math
import os
import re
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
TMP = Path(tempfile.mkdtemp(prefix="studio-demo-"))
os.environ["STUDIO_DATA_DIR"] = str(TMP / "data")

from app import checks, config, db, schema, server, settings  # noqa: E402
from app.claude import ClaudeRunner  # noqa: E402
from fake_metabase import DATABASE, KEY, OURS, FakeMetabase  # noqa: E402

TOKEN = "demo"
TABLES = {"orders": 1840000, "order_items": 5210000, "customers": 96000, "products": 1200}


class DemoRunner(ClaudeRunner):
    """Tells the stand-in which kind of request it is answering."""

    def run(self, req, cancel=None, on_event=None):
        os.environ["FAKE_MODE"] = req.kind
        return super().run(req, cancel=cancel, on_event=on_event)


def _column(name, kind):
    return {"name": name, "type": kind, "pii": False}


def canned(sql, limit=None, allow_heavy=False, source="cli"):
    """Made-up rows shaped like the sample dashboard's queries."""
    text = " ".join(sql.split())
    today = date.today()
    days = [today - timedelta(days=n) for n in range(29, -1, -1)]
    if "from orders" in text:
        return canned_orders(text, today)
    if '"Channel"' in text:
        columns = [_column("Day", "date"), _column("Channel", "text"), _column("Tickets", "number")]
        rows = [[d.isoformat(), name, int(base + 40 * math.sin(d.toordinal() / 2 + shift))]
                for d in days[-14:] for name, base, shift in (("App", 420, 0), ("POS machine", 610, 1.5), ("Website", 150, 3))]
    elif '"Day"' in text and '"Revenue"' in text:
        columns, rows = [_column("Day", "date"), _column("Revenue", "number")], [[d.isoformat(), 412000 + 9000 * (d.day % 7)] for d in days[-7:]]
    elif '"Day"' in text and '"Trips"' in text:
        columns, rows = [_column("Day", "date"), _column("Trips", "number")], [[d.isoformat(), 640 - 11 * (d.day % 5)] for d in days[-7:]]
    elif '"Day"' in text:
        columns = [_column("Day", "date"), _column("Tickets", "number")]
        rows = [[d.isoformat(), int(900 + 180 * math.sin(d.toordinal() / 3) + 12 * d.weekday())] for d in days]
    elif '"Route"' in text:
        columns = [_column("Route", "text"), _column("Tickets", "number")]
        rows = [["North - Central", 1840], ["Harbour - Airport", 1525], ["East - Central", 1310], ["Ring road", 990], ["West - Market", 760], ["South loop", 540]]
    elif '"Payment mode"' in text:
        columns = [_column("Payment mode", "text"), _column("Tickets", "number")]
        rows = [["Cash", 5400], ["UPI", 3100], ["Card", 900], ["Pass", 350], ["Wallet", 120], ["Other online", 90]]
    elif '"Operator"' in text:
        columns = [_column("Operator", "text"), _column("Last trip", "date"), _column("Tickets", "number"), _column("Revenue", "number")]
        rows = [[f"Operator {chr(64 + n)}", (today - timedelta(days=n)).isoformat(), 1200 - 85 * n, round((1200 - 85 * n) * 348.5)] for n in range(1, 9)]
    elif '"Active buses"' in text:
        columns, rows = [_column("Active buses", "number")], [[312]]
    else:
        columns, rows = [_column("Tickets today", "number")], [[1248]]
    return {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": False, "ms": 12, "cost": 4.0,
            "ran_at": datetime.now().isoformat(timespec="seconds")}


STATUSES = {"new": 0.5, "paid": 1.0, "shipped": 0.8, "returned": 0.12}


def canned_orders(text, today):
    """Made-up rows for the filtered demo dashboard; the filters picked change them."""
    span = re.search(r"interval '(\d+) (day|month|year)'", text)
    count = min(int(span.group(1)) * {"day": 1, "month": 30, "year": 365}[span.group(2)], 365) if span else (1 if "created_at" in text else 120)
    picked = re.search(r"\"status\" = '(\w+)'", text)
    scale = STATUSES.get(picked.group(1), 1.0) if picked else sum(STATUSES.values())
    days = [today - timedelta(days=n) for n in range(count, 0, -1)]
    per_day = [int(scale * (310 + 70 * math.sin(d.toordinal() / 4) + 9 * d.weekday())) for d in days]
    if "select distinct status" in text:
        columns, rows = [_column("status", "text")], [[s] for s in STATUSES]
    elif '"Day"' in text:
        columns, rows = [_column("Day", "date"), _column("Orders", "number")], [[d.isoformat(), n] for d, n in zip(days, per_day)]
    elif '"Status"' in text:
        columns = [_column("Status", "text"), _column("Orders", "number")]
        rows = [[s, int(sum(per_day) * share / scale)] for s, share in STATUSES.items() if not picked or s == picked.group(1)]
    elif '"Revenue"' in text:
        columns, rows = [_column("Revenue", "number")], [[sum(per_day) * 742]]
    else:
        columns, rows = [_column("Orders", "number")], [[sum(per_day)]]
    return {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": False, "ms": 14, "cost": 310.0,
            "ran_at": datetime.now().isoformat(timespec="seconds")}


ORDERS = {
    "dashboard.json": {
        "name": "Orders",
        "description": "A made-up shop. Pick a date range and a status; the cards that use them follow.",
        "filters": [{"key": "date", "name": "Date", "type": "date", "default": "past30days"},
                    {"key": "status", "name": "Status", "type": "text", "values": "status_list"}],
        "cards": [
            {"key": "orders", "name": "Orders", "display": "scalar", "filters": {"date": "orders.created_at", "status": "orders.status"},
             "row": 0, "col": 0, "size_x": 6, "size_y": 3},
            {"key": "revenue", "name": "Revenue", "display": "scalar", "filters": {"date": "orders.created_at", "status": "orders.status"},
             "viz": {"column_settings": {"[\"name\",\"Revenue\"]": {"number_style": "currency", "currency": "USD", "decimals": 0}}},
             "row": 0, "col": 6, "size_x": 6, "size_y": 3},
            {"key": "by_status", "name": "Orders by status", "display": "row", "filters": {"date": "orders.created_at"},
             "viz": {"graph.dimensions": ["Status"], "graph.metrics": ["Orders"]}, "row": 0, "col": 12, "size_x": 12, "size_y": 9},
            {"key": "per_day", "name": "Orders per day", "display": "line", "filters": {"date": "orders.created_at", "status": "orders.status"},
             "viz": {"graph.dimensions": ["Day"], "graph.metrics": ["Orders"]}, "row": 3, "col": 0, "size_x": 12, "size_y": 6},
        ],
    },
    "orders.sql": 'select count(*) as "Orders"\nfrom orders\nwhere {{date}} [[and {{status}}]]',
    "revenue.sql": 'select sum(amount) as "Revenue"\nfrom orders\nwhere {{date}} [[and {{status}}]]',
    "by_status.sql": 'select status as "Status", count(*) as "Orders"\nfrom orders\nwhere {{date}}\ngroup by 1\norder by 2 desc',
    "per_day.sql": 'select created_at::date as "Day", count(*) as "Orders"\nfrom orders\nwhere {{date}} [[and {{status}}]]\ngroup by 1\norder by 1',
    "status_list.sql": "select distinct status\nfrom orders\norder by 1",
}


def write_orders(folder):
    folder.mkdir(parents=True)
    for name, content in ORDERS.items():
        (folder / name).write_text(content if isinstance(content, str) else json.dumps(content, indent=2), encoding="utf-8")


def canned_tables():
    now = datetime.now().isoformat(timespec="seconds")
    tables = {name: {"schema": "public", "kind": "table", "rows": rows, "size_bytes": rows * 180, "comment": None,
                     "columns": [{"name": "id", "type": "bigint", "nullable": False}, {"name": "created_at", "type": "timestamp with time zone", "nullable": False},
                                 {"name": "status", "type": "text", "nullable": False}, {"name": "amount", "type": "numeric(10,2)", "nullable": False}],
                     "indexes": [{"unique": True, "primary": True, "definition": "btree (id)"}, {"unique": False, "primary": False, "definition": "btree (created_at)"}],
                     "foreign_keys": []} for name, rows in TABLES.items()}
    config.SCHEMA_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.SCHEMA_FILE.write_text(json.dumps({"generated_at": now, "database": "demo", "schemas": ["public"], "tables": tables}), encoding="utf-8")
    return {"tables": tables}


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8790
    (TMP / "dashboards").mkdir()
    if not os.environ.get("DEMO_EMPTY"):
        shutil.copytree(ROOT / "examples" / "sample", TMP / "dashboards" / "sample")
        if not os.environ.get("DEMO_SETUP"):
            write_orders(TMP / "dashboards" / "orders")
    config.ROOT = TMP
    config.DASHBOARDS_DIR = TMP / "dashboards"
    fake = FakeMetabase()
    if os.environ.get("DEMO_NO_COLLECTION"):
        del fake.collections[OURS]
    settings.start()
    values = {"port": port, "timezone": "Asia/Kolkata"}
    if not os.environ.get("DEMO_SETUP"):
        values.update(db_host="db.example.com", db_name="shop", db_user="reader", db_password="demo",
                      metabase_url=fake.url, metabase_api_key=KEY, metabase_database_id=DATABASE, metabase_collection="Studio dashboards")
        canned_tables()
    settings.save(values)
    db.run = canned
    schema.snapshot = canned_tables
    checks.database = lambda values: {"ok": True, "server": "PostgreSQL 17.2", "user": "reader", "tables": len(TABLES),
                                      "read_only": True, "notes": []}
    checks.claude = lambda run=False: {"ok": True, "path": r"C:\Users\you\.local\bin\claude.exe", "version": "2.1 (Claude Code)",
                                       **({"model": "claude-opus", "answered": True} if run else {})}
    real_metabase_check = checks.metabase_side
    checks.metabase_side = lambda values: real_metabase_check({"metabase_url": fake.url, "metabase_api_key": KEY})
    os.environ.update({"FAKE_ROOT": str(TMP), "FAKE_DELAY": os.environ.get("FAKE_DELAY", "1.2"), "FAKE_PRETTY": "1"})
    httpd = server.make_server(TOKEN, runner=DemoRunner(prefix=[sys.executable, str(ROOT / "tests" / "fake_claude.py")]))
    print(f"Demo studio: http://127.0.0.1:{port}/auth?token={TOKEN}   (folder {TMP})", flush=True)
    print(f"Fake Metabase: {fake.url}   (key {KEY})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        httpd.studio.assistant.shutdown()
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
