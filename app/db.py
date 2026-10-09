"""Read-only access to the databases set up in Settings, one query at a time across all of them.

Every statement runs as a subquery of a row-capped SELECT inside a read-only
transaction, so a write cannot run even if the guard misses it. Nothing here
runs on its own: a query happens only when a caller asks for one.
"""
import atexit
import hashlib
import json
import math
import shutil
import threading
import time
from datetime import date, datetime, time as clock
from decimal import Decimal

import psycopg2

from . import config, guard

IDLE_CLOSE_SECONDS = 20
MASK = "***"
SHOWN = "shown"   # the cache folder for results that show personal data

_NUMBER_OIDS = {20, 21, 23, 26, 700, 701, 790, 1700}
_BOOL_OIDS = {16}
_DATE_OIDS = {1082}
_DATETIME_OIDS = {1114, 1184}

_lock = threading.Lock()
_conns = {}       # database id -> connection
_last_used = 0.0


class QueryError(Exception):
    pass


class Heavy(Exception):
    def __init__(self, cost):
        super().__init__(
            f"Estimated cost {cost:,.0f} is over the limit of {config.MAX_PLAN_COST:,.0f}."
        )
        self.cost = cost


def _settings(database):
    entry = config.database(database)
    if entry is None:
        if database:
            raise QueryError(f'There is no database "{database}" in Settings.')
        raise QueryError("No database is set up yet. Open Settings and add one.")
    if not config.database_complete(entry):
        raise QueryError(f'The database "{entry["name"]}" is not fully set up. Open Settings.')
    return entry


def _connect(entry):
    options = " ".join(
        [
            "-c default_transaction_read_only=on",
            f"-c statement_timeout={config.STATEMENT_TIMEOUT_MS}",
            "-c lock_timeout=3000",
            "-c idle_in_transaction_session_timeout=60000",
        ]
    )
    conn = psycopg2.connect(
        host=entry["host"],
        port=entry["port"],
        dbname=entry["dbname"],
        user=entry["user"],
        password=entry["password"],
        sslmode=entry["sslmode"],
        connect_timeout=10,
        application_name=config.APP_ID,
        options=options,
    )
    conn.set_session(readonly=True, autocommit=False)
    return conn


def _connection(entry):
    conn = _conns.get(entry["id"])
    if conn is None or conn.closed:
        conn = _conns[entry["id"]] = _connect(entry)
    return conn


def _drop(ident):
    conn = _conns.pop(ident, None)
    if conn is not None:
        try:
            conn.close()
        except psycopg2.Error:
            pass


def close():
    for ident in list(_conns):
        _drop(ident)


def reset():
    """Drop every connection once no query is using one, so the next query connects afresh."""
    with _lock:
        close()


atexit.register(close)


def _end_transaction(ident):
    conn = _conns.get(ident)
    if conn is None:
        return
    if conn.closed:
        return _drop(ident)
    try:
        conn.rollback()
    except psycopg2.Error:
        _drop(ident)


def _message(exc):
    if isinstance(exc, psycopg2.errors.QueryCanceled):
        return f"Stopped after {config.STATEMENT_TIMEOUT_MS // 1000} s (statement timeout)."
    return str(exc).strip() or exc.__class__.__name__


def _plain(value):
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, (date, clock)):
        return value.isoformat()
    if isinstance(value, (bytes, memoryview)):
        return f"<{len(value)} bytes>"
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return str(value)


def _column_type(oid):
    if oid in _NUMBER_OIDS:
        return "number"
    if oid in _BOOL_OIDS:
        return "bool"
    if oid in _DATE_OIDS:
        return "date"
    if oid in _DATETIME_OIDS:
        return "datetime"
    return "text"


def _mask(value, kind):
    """Hide a value in a personal-data column. Flags, dates and small numbers (counts) stay."""
    if value is None or kind in ("bool", "date", "datetime"):
        return value
    if kind == "number" and abs(value) < 1_000_000_000:
        return value
    return MASK


def _shape(description, raw, limit, reveal=()):
    """Rows as the page gets them. A hidden column is marked "pii"; `reveal` names the personal ones the user allowed."""
    columns = [{"name": d.name, "type": _column_type(d.type_code), "pii": False} for d in description]
    kinds = {i: guard.sensitivity(d.name) for i, d in enumerate(description)}
    shown = [i for i, kind in kinds.items() if kind == "personal" and description[i].name in reveal]
    hidden = [i for i, kind in kinds.items() if kind and i not in shown]
    truncated = len(raw) > limit
    rows = []
    for record in raw[:limit]:
        row = [_plain(v) for v in record]
        for i in shown:
            if _mask(row[i], columns[i]["type"]) == MASK:
                columns[i].update(sensitive="personal", revealed=True)
        for i in hidden:
            row[i] = _mask(row[i], columns[i]["type"])
        rows.append(row)
    for i in hidden:
        if any(row[i] == MASK for row in rows):
            columns[i].update(type="text", pii=True, sensitive=kinds[i])
    return columns, rows, truncated


def _log(source, status, sql, ms=None, rows=None, cost=None):
    try:
        config.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        parts = [
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            source,
            status,
            f"{ms} ms" if ms is not None else "-",
            f"{rows} rows" if rows is not None else "-",
            f"cost {cost:,.0f}" if cost is not None else "-",
            " ".join(sql.split())[:200],
        ]
        with config.LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(" | ".join(parts) + "\n")
    except OSError:
        pass


def run(sql, limit=None, allow_heavy=False, source="cli", database=None, reveal=()):
    """Run one SELECT on one database and return its columns and rows.

    Personal-data columns come back hidden, except those named in `reveal`.
    Raises guard.Rejected, Heavy or QueryError.
    """
    global _last_used
    clean = guard.check(sql)
    entry = _settings(database)
    where = source if entry["id"] == config.MAIN else f"{source} on {entry['id']}"
    limit = int(limit or config.PREVIEW_ROW_LIMIT)
    # The newlines keep a trailing "-- comment" from swallowing the closing bracket.
    wrapped = f"SELECT * FROM (\n{clean}\n) AS _q LIMIT {limit + 1}"
    cost = None
    with _lock:
        try:
            with _connection(entry).cursor() as cur:
                cur.execute("EXPLAIN (FORMAT JSON) " + wrapped)
                plan = cur.fetchone()[0]
                if isinstance(plan, str):
                    plan = json.loads(plan)
                cost = float(plan[0]["Plan"]["Total Cost"])
                if cost > config.MAX_PLAN_COST and not allow_heavy:
                    raise Heavy(cost)
                started = time.perf_counter()
                cur.execute(wrapped)
                raw = cur.fetchall()
                ms = int((time.perf_counter() - started) * 1000)
                description = cur.description
        except Heavy:
            _log(where, "refused: heavy", clean, cost=cost)
            raise
        except psycopg2.Error as exc:
            message = _message(exc)
            _log(where, "failed: " + message.splitlines()[0][:80], clean, cost=cost)
            raise QueryError(message) from None
        finally:
            _end_transaction(entry["id"])
            _last_used = time.time()
    columns, rows, truncated = _shape(description, raw, limit, reveal)
    revealed = [c["name"] for c in columns if c.get("revealed")]
    _log(where, "ok, shows " + ", ".join(revealed) if revealed else "ok", clean, ms=ms, rows=len(rows), cost=cost)
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "ms": ms,
        "cost": cost,
        "ran_at": datetime.now().isoformat(timespec="seconds"),
    }


def fetch_catalog(statements, database=None):
    """Run fixed catalog queries for the table list. Not for user SQL."""
    global _last_used
    entry = _settings(database)
    results = []
    with _lock:
        try:
            with _connection(entry).cursor() as cur:
                for statement, params in statements:
                    cur.execute(statement, params)
                    results.append(cur.fetchall())
        except psycopg2.Error as exc:
            raise QueryError(_message(exc)) from None
        finally:
            _end_transaction(entry["id"])
            _last_used = time.time()
    _log(f"snapshot {entry['id']}", "ok", "catalog only", rows=sum(len(r) for r in results))
    return results


_closer_started = False


def start_idle_closer():
    """Close connections once they have been unused for a while. Sends nothing to a database."""
    global _closer_started
    if _closer_started:
        return
    _closer_started = True

    def loop():
        while True:
            time.sleep(5)
            with _lock:
                if _conns and time.time() - _last_used > IDLE_CLOSE_SECONDS:
                    close()

    threading.Thread(target=loop, daemon=True, name="idle-closer").start()


def sql_hash(sql, database=None, reveal=()):
    """Names a query's saved result. The first database keeps the names it had when it was the only one."""
    entry = config.database(database)
    ident = entry["id"] if entry else (database or None)
    text = sql.strip() if ident in (None, config.MAIN) else f"{ident}\n{sql.strip()}"
    if reveal:
        text += "\nshown: " + ", ".join(sorted(reveal))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _cache_file(sql, database, reveal):
    # Results that show personal data are kept apart, so they can all be dropped at once.
    folder = config.CACHE_DIR / SHOWN if reveal else config.CACHE_DIR
    return folder / f"{sql_hash(sql, database, reveal)}.json"


def cache_get(sql, database=None, reveal=()):
    try:
        result = json.loads(_cache_file(sql, database, reveal).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for column in result["columns"]:
        # A result saved before hidden columns said which kind they are.
        if column.get("pii") and not column.get("sensitive"):
            column["sensitive"] = guard.sensitivity(column["name"]) or "secret"
    return result


def cache_put(sql, result, database=None, reveal=()):
    path = _cache_file(sql, database, reveal)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result), encoding="utf-8")


def cache_forget_shown():
    """Drop every saved result that shows personal data. Those cards run again when next opened."""
    shutil.rmtree(config.CACHE_DIR / SHOWN, ignore_errors=True)
