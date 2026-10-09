"""Read-only access to the business database, one query at a time.

Every statement runs as a subquery of a row-capped SELECT inside a read-only
transaction, so a write cannot run even if the guard misses it. Nothing here
runs on its own: a query happens only when a caller asks for one.
"""
import atexit
import hashlib
import json
import math
import threading
import time
from datetime import date, datetime, time as clock
from decimal import Decimal

import psycopg2

from . import config, guard

IDLE_CLOSE_SECONDS = 20
MASK = "***"

_NUMBER_OIDS = {20, 21, 23, 26, 700, 701, 790, 1700}
_BOOL_OIDS = {16}
_DATE_OIDS = {1082}
_DATETIME_OIDS = {1114, 1184}

_lock = threading.Lock()
_conn = None
_last_used = 0.0


class QueryError(Exception):
    pass


class Heavy(Exception):
    def __init__(self, cost):
        super().__init__(
            f"Estimated cost {cost:,.0f} is over the limit of {config.MAX_PLAN_COST:,.0f}."
        )
        self.cost = cost


def _connect():
    if not (config.DB_HOST and config.DB_NAME and config.DB_USER and config.DB_PASS):
        raise QueryError("The database is not set up yet. Open Settings and fill in the Database section.")
    options = " ".join(
        [
            "-c default_transaction_read_only=on",
            f"-c statement_timeout={config.STATEMENT_TIMEOUT_MS}",
            "-c lock_timeout=3000",
            "-c idle_in_transaction_session_timeout=60000",
        ]
    )
    conn = psycopg2.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        dbname=config.DB_NAME,
        user=config.DB_USER,
        password=config.DB_PASS,
        sslmode=config.DB_SSLMODE,
        connect_timeout=10,
        application_name=config.APP_ID,
        options=options,
    )
    conn.set_session(readonly=True, autocommit=False)
    return conn


def close():
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        except psycopg2.Error:
            pass
        _conn = None


def reset():
    """Drop the connection once no query is using it, so the next one connects afresh."""
    with _lock:
        close()


atexit.register(close)


def _end_transaction():
    if _conn is None:
        return
    if _conn.closed:
        close()
        return
    try:
        _conn.rollback()
    except psycopg2.Error:
        close()


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


def _shape(description, raw, limit):
    columns = [{"name": d.name, "type": _column_type(d.type_code), "pii": False} for d in description]
    suspect = [i for i, d in enumerate(description) if guard.is_pii_column(d.name)]
    truncated = len(raw) > limit
    rows = []
    for record in raw[:limit]:
        row = [_plain(v) for v in record]
        for i in suspect:
            row[i] = _mask(row[i], columns[i]["type"])
        rows.append(row)
    for i in suspect:
        if any(row[i] == MASK for row in rows):
            columns[i]["type"] = "text"
            columns[i]["pii"] = True
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


def run(sql, limit=None, allow_heavy=False, source="cli"):
    """Run one SELECT and return its columns and rows.

    Raises guard.Rejected, Heavy or QueryError.
    """
    global _conn, _last_used
    clean = guard.check(sql)
    limit = int(limit or config.PREVIEW_ROW_LIMIT)
    # The newlines keep a trailing "-- comment" from swallowing the closing bracket.
    wrapped = f"SELECT * FROM (\n{clean}\n) AS _q LIMIT {limit + 1}"
    cost = None
    with _lock:
        try:
            if _conn is None or _conn.closed:
                _conn = _connect()
            with _conn.cursor() as cur:
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
            _log(source, "refused: heavy", clean, cost=cost)
            raise
        except psycopg2.Error as exc:
            message = _message(exc)
            _log(source, "failed: " + message.splitlines()[0][:80], clean, cost=cost)
            raise QueryError(message) from None
        finally:
            _end_transaction()
            _last_used = time.time()
    columns, rows, truncated = _shape(description, raw, limit)
    _log(source, "ok", clean, ms=ms, rows=len(rows), cost=cost)
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "ms": ms,
        "cost": cost,
        "ran_at": datetime.now().isoformat(timespec="seconds"),
    }


def fetch_catalog(statements):
    """Run fixed catalog queries for the schema snapshot. Not for user SQL."""
    global _conn, _last_used
    results = []
    with _lock:
        try:
            if _conn is None or _conn.closed:
                _conn = _connect()
            with _conn.cursor() as cur:
                for statement, params in statements:
                    cur.execute(statement, params)
                    results.append(cur.fetchall())
        except psycopg2.Error as exc:
            raise QueryError(_message(exc)) from None
        finally:
            _end_transaction()
            _last_used = time.time()
    _log("snapshot", "ok", "catalog only", rows=sum(len(r) for r in results))
    return results


_closer_started = False


def start_idle_closer():
    """Close the connection once it has been unused for a while. Sends nothing to the database."""
    global _closer_started
    if _closer_started:
        return
    _closer_started = True

    def loop():
        while True:
            time.sleep(5)
            with _lock:
                if _conn is not None and time.time() - _last_used > IDLE_CLOSE_SECONDS:
                    close()

    threading.Thread(target=loop, daemon=True, name="idle-closer").start()


def sql_hash(sql):
    return hashlib.sha256(sql.strip().encode("utf-8")).hexdigest()[:16]


def cache_get(sql):
    path = config.CACHE_DIR / f"{sql_hash(sql)}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def cache_put(sql, result):
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = config.CACHE_DIR / f"{sql_hash(sql)}.json"
    path.write_text(json.dumps(result), encoding="utf-8")
