"""The tests behind the Settings page: does the database answer, does Metabase, is Claude there.

Each takes the values on the form, which may not be saved yet. An empty secret means
"the saved one". Nothing here reads a table's rows.
"""
import os
import subprocess

import psycopg2

from . import config, metabase, settings
from .claude import ClaudeRefused, ClaudeRunner, RunRequest, ToolPolicy, build_env
from .winjob import CREATE_NO_WINDOW

_ROLE = """
select current_user, split_part(version(), ' ', 2),
       (select count(*) from pg_namespace where nspname = any(%(schemas)s)),
       (select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace
         where n.nspname = any(%(schemas)s) and c.relkind in ('r', 'p', 'v', 'm')
           and has_schema_privilege(n.oid, 'USAGE') and has_table_privilege(c.oid, 'SELECT')),
       (select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace
         where n.nspname = any(%(schemas)s) and c.relkind in ('r', 'p') and has_schema_privilege(n.oid, 'USAGE')
           and (has_table_privilege(c.oid, 'INSERT') or has_table_privilege(c.oid, 'UPDATE')
                or has_table_privilege(c.oid, 'DELETE') or has_table_privilege(c.oid, 'TRUNCATE'))),
       (select count(*) from pg_namespace where nspname = any(%(schemas)s) and has_schema_privilege(oid, 'CREATE')),
       (select rolsuper from pg_roles where rolname = current_user)
"""


def _filled(values, keys):
    """The form's values over the saved ones; an empty field on the form means the saved value."""
    saved = settings.current()
    out = {}
    for key in keys:
        given = (values or {}).get(key)
        out[key] = saved[key] if given in (None, "") else given
    return out


def database(values):
    v = _filled(values, ("db_host", "db_port", "db_name", "db_user", "db_password", "db_sslmode", "db_schemas", "timezone"))
    schemas = v["db_schemas"].split(",") if isinstance(v["db_schemas"], str) else list(v["db_schemas"])
    schemas = [s.strip() for s in schemas if s.strip()]
    if not (v["db_host"] and v["db_name"] and v["db_user"] and v["db_password"]):
        return {"ok": False, "error": "Fill in the host, the database, the user and the password."}
    try:
        conn = psycopg2.connect(
            host=v["db_host"], port=int(v["db_port"]), dbname=v["db_name"], user=v["db_user"], password=v["db_password"],
            sslmode=v["db_sslmode"], connect_timeout=10, application_name=config.APP_ID,
            options="-c default_transaction_read_only=on -c statement_timeout=10000",
        )
    except (psycopg2.Error, ValueError) as exc:
        return {"ok": False, "error": str(exc).strip().splitlines()[0] if str(exc).strip() else "Could not connect."}
    notes = []
    try:
        with conn.cursor() as cur:
            cur.execute(_ROLE, {"schemas": schemas})
            user, version, found, readable, writable, creatable, superuser = cur.fetchone()
            try:
                cur.execute("select now() at time zone %s", (v["timezone"],))
            except psycopg2.Error:
                conn.rollback()
                notes.append(f'Postgres does not know the time zone "{v["timezone"]}".')
    except psycopg2.Error as exc:
        return {"ok": False, "error": str(exc).strip().splitlines()[0]}
    finally:
        conn.close()
    if found < len(schemas):
        notes.append("One of the schemas does not exist in this database.")
    if not readable:
        notes.append("This user cannot read any table in the chosen schemas.")
    if superuser:
        notes.append("This user is a superuser. A user that can only read is safer.")
    elif writable:
        notes.append(f"This user can change data in {writable} {'table' if writable == 1 else 'tables'}. "
                     "A user that can only read is safer.")
    elif creatable:
        notes.append("This user cannot change existing tables, but may create new ones.")
    return {"ok": True, "server": f"PostgreSQL {version}", "user": user, "tables": readable,
            "read_only": not (writable or superuser), "notes": notes}


def _path(collection, names):
    parents = [names.get(p, "") for p in str(collection.get("location") or "/").strip("/").split("/") if p]
    return " / ".join(p for p in parents if p)


def metabase_side(values):
    v = _filled(values, ("metabase_url", "metabase_api_key"))
    if not (v["metabase_url"] and v["metabase_api_key"]):
        return {"ok": False, "error": "Fill in the Metabase address and an API key."}
    ask = {"base": v["metabase_url"], "key": v["metabase_api_key"], "timeout": 20}
    try:
        me = metabase._call("GET", "/api/user/current", **ask)
        databases = (metabase._call("GET", "/api/database", **ask) or {}).get("data") or []
        listed = [c for c in metabase._call("GET", "/api/collection", **ask) if isinstance(c, dict)]
        root = metabase._call("GET", "/api/collection/root", **ask)
    except metabase.MetabaseError as exc:
        return {"ok": False, "error": str(exc)}
    names = {str(c.get("id")): c.get("name") for c in listed}
    collections = [
        {"id": c["id"], "name": c.get("name"), "inside": _path(c, names), "can_write": bool(c.get("can_write"))}
        for c in listed if isinstance(c.get("id"), int) and not c.get("archived") and not c.get("personal_owner_id")
    ]
    return {
        "ok": True,
        "who": me.get("common_name") or "",
        "admin": bool(me.get("is_superuser")),
        "databases": [{"id": d.get("id"), "name": d.get("name"), "engine": d.get("engine"),
                       "can_query": d.get("native_permissions") == "write"} for d in databases],
        "collections": sorted(collections, key=lambda c: (not c["can_write"], str(c["name"]).lower())),
        "root_can_write": bool((root or {}).get("can_write")),
    }


def claude(run=False):
    binary = config.claude_bin()
    if not binary:
        return {"ok": False, "error": "Claude Code was not found on this PC. Install it, or give its path below."}
    try:
        done = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=20, env=build_env(),
                              creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
        version = (done.stdout or done.stderr).strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        return {"ok": False, "error": f"Claude Code did not start from {binary}."}
    out = {"ok": True, "path": binary, "version": version}
    if run:
        request = RunRequest(prompt="Reply with the single word OK.", system="This is a sign-in check. Reply with one word.",
                             policy=ToolPolicy(tools=("Read",), allow=()), mcp_url=f"http://127.0.0.1:{config.PORT}/mcp",
                             mcp_token="none", max_turns=2, timeout=90, kind="check")
        try:
            result = ClaudeRunner().run(request)
        except ClaudeRefused as exc:
            return {"ok": False, "error": str(exc)}
        if result.outcome != "ok":
            return {"ok": False, "path": binary, "version": version,
                    "error": result.error or "Claude did not answer. Run `claude` once in a terminal and sign in."}
        out.update(model=result.model, answered=True)
    return out
