"""The studio's tools, offered to Claude as an MCP server over HTTP.

One JSON-RPC message per POST, one JSON reply. Which tools exist depends on the
running job: a plan may look at the table list but cannot query.
"""
from . import config, db, guard, schema, specs
from .textio import format_result

FALLBACK_PROTOCOL = "2025-06-18"

TOOLS = {
    "list_tables": {
        "description": "List the database tables whose name contains the text, with row counts and sizes. "
                       "Reads a local snapshot, not the database.",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string", "description": "Part of a table name. Empty lists every table."}}},
    },
    "describe_table": {
        "description": "Columns, types, indexes and links of one table. Reads a local snapshot, not the database.",
        "inputSchema": {"type": "object", "properties": {"table": {"type": "string"}}, "required": ["table"]},
    },
    "run_query": {
        "description": "Run one read-only SELECT on the production database and return the first rows. "
                       "Give either sql, or file: the path of a .sql file inside dashboards/. Prefer file once the "
                       "card's query is written, so the preview reuses the result.",
        "inputSchema": {"type": "object", "properties": {
            "sql": {"type": "string"},
            "file": {"type": "string", "description": "For example dashboards/daily_ops/tickets_today.sql"},
            "rows": {"type": "integer", "description": "Rows to show, 1 to 50. Default 20."}}},
    },
    "check_dashboard": {
        "description": "Check a dashboard's files and list any problems. Does not use the database.",
        "inputSchema": {"type": "object", "properties": {"slug": {"type": "string"}}, "required": ["slug"]},
    },
}


class ToolError(Exception):
    pass


def _list_tables(job, args):
    return schema.list_tables(str(args.get("text") or ""))


def _describe_table(job, args):
    return schema.describe(str(args.get("table") or ""))


def _query_text(args):
    if args.get("file"):
        path = (config.ROOT / str(args["file"])).resolve()
        if not path.is_relative_to(config.DASHBOARDS_DIR.resolve()) or path.suffix != ".sql":
            raise ToolError("file must be a .sql file inside dashboards/.")
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            raise ToolError(f"{args['file']} does not exist.") from None
    if args.get("sql"):
        return str(args["sql"])
    raise ToolError("Give sql or file.")


def _run_query(job, args):
    sql = _query_text(args)
    try:
        guard.check(sql)
    except guard.Rejected as exc:
        raise ToolError(f"Refused: {exc}") from None
    if job.queries >= config.STUDIO_MAX_QUERIES:
        raise ToolError(f"Refused: this request has used its {config.STUDIO_MAX_QUERIES} queries. "
                        "Finish with what you have.")
    job.queries += 1
    try:
        result = db.run(sql, source=f"claude {job.label}")
    except db.Heavy as exc:
        job.note("Query refused as heavy", f"cost {exc.cost:,.0f}")
        raise ToolError(f"Refused: {exc} Narrow it with a shorter date range or a filter on an indexed "
                        "column. Only the user can allow a heavy query, from the card's Run anyway button.") from None
    except (db.QueryError, guard.Rejected) as exc:
        job.note("Query failed", str(exc).splitlines()[0])
        raise ToolError(f"Failed: {exc}") from None
    db.cache_put(sql, result)
    count = result["row_count"]
    job.note("Query done", f"{count} {'row' if count == 1 else 'rows'} in {result['ms']} ms")
    try:
        show = max(1, min(int(args.get("rows") or 20), 50))
    except (TypeError, ValueError):
        show = 20
    return format_result(result, show)


def _check_dashboard(job, args):
    spec = specs.load(str(args.get("slug") or ""))
    if spec is None:
        return "No such dashboard yet: its dashboard.json is missing."
    head = f"{spec['name']}: {len(spec['cards'])} cards"
    if not spec["problems"]:
        return head + ", no problems."
    return "\n".join([head] + ["problem: " + p for p in spec["problems"]])


_RUN = {
    "list_tables": _list_tables,
    "describe_table": _describe_table,
    "run_query": _run_query,
    "check_dashboard": _check_dashboard,
}


def _call(job, params):
    name = params.get("name")
    args = params.get("arguments") or {}
    if name not in job.tools:
        text, failed = f"The tool {name} is not available in this run.", True
    else:
        try:
            text, failed = _RUN[name](job, args), False
        except ToolError as exc:
            text, failed = str(exc), True
        except schema.SchemaMissing as exc:
            text, failed = str(exc), True
    return {"content": [{"type": "text", "text": text}], "isError": failed}


def handle(message, job):
    """Answer one JSON-RPC message. Returns None for a notification."""
    if not isinstance(message, dict) or "id" not in message:
        return None
    method = message.get("method")
    params = message.get("params") or {}
    if method == "initialize":
        result = {
            "protocolVersion": params.get("protocolVersion") or FALLBACK_PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": config.APP_ID, "version": "1"},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [{"name": name, **TOOLS[name]} for name in job.tools]}
    elif method == "tools/call":
        result = _call(job, params)
    else:
        return {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": message["id"], "result": result}
