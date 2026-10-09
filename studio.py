"""Command line for the studio.

  python studio.py                   start the studio in a browser tab
  python studio.py tables [text]     list tables from the local snapshot
  python studio.py describe <table>  columns, indexes and links of one table
  python studio.py q "<select ...>"  run one read-only query
  python studio.py check <slug>      check a dashboard's files, without the database
  python studio.py snapshot          refresh the schema snapshot (catalog only)
  python studio.py doctor            what the Metabase key can reach, and whether Go live is ready
  python studio.py info              the databases, time zone and limits in force

With several databases, tables, describe, q and snapshot take --db <id or name>;
without it they use the first one (q -f uses the dashboard's own).
"""
import argparse
import sys

from pathlib import Path

from app import config, db, filters, guard, metabase, schema, server, settings, specs
from app.textio import format_result


def _database(args):
    """The id of the database named with --db, by id or by name. None means the first one."""
    wanted = (getattr(args, "db", None) or "").strip().lower()
    if not wanted:
        return None
    for entry in config.DATABASES:
        if wanted in (entry["id"].lower(), entry["name"].lower()):
            return entry["id"]
    known = ", ".join(f"{e['id']} ({e['name']})" for e in config.DATABASES) or "none yet"
    raise SystemExit(f"No database '{args.db}'. Databases: {known}")


def cmd_q(args):
    database = _database(args)
    if args.file:
        try:
            sql = open(args.file, encoding="utf-8").read()
        except OSError as exc:
            print(f"Could not read {args.file}: {exc}")
            return 1
    elif args.sql:
        sql = args.sql
    else:
        print("Give the query in quotes, or a file with -f.")
        return 1
    try:
        if args.file:
            # A dashboard's file runs on that dashboard's database, unless --db says otherwise.
            spec = specs.load(Path(args.file).resolve().parent.name)
            database = database or (spec or {}).get("database")
        if args.file and filters.tags(sql):
            # A card query with {{filters}}: run it for the dashboard's default values.
            card = next((c for c in (spec or {}).get("cards", []) if c["key"] == Path(args.file).stem), None)
            if card is None:
                print("This query uses {{filters}}. Add the card and the filters to dashboard.json first.")
                return 1
            sql = specs.query(card, {**spec, "database": database})
        result = db.run(sql, limit=args.limit, allow_heavy=args.allow_heavy, source="cli", database=database)
    except filters.FilterError as exc:
        print(f"Filter problem: {exc}")
        return 1
    except guard.Rejected as exc:
        print(f"Refused: {exc}")
        return 2
    except db.Heavy as exc:
        print(f"Refused: {exc}")
        print("Narrow it (shorter date range, an indexed filter). --allow-heavy runs it anyway, only if the user agrees.")
        return 2
    except db.QueryError as exc:
        print(f"Failed: {exc}")
        return 1
    print(format_result(result, args.rows))
    return 0


def cmd_check(args):
    spec = specs.load(args.slug)
    if spec is None:
        known = ", ".join(specs.slugs()) or "none yet"
        print(f"No dashboard '{args.slug}'. Dashboards: {known}")
        return 1
    print(f"{spec['name']}: {len(spec['cards'])} cards")
    for problem in spec["problems"]:
        print(f"  problem: {problem}")
    if not spec["problems"]:
        print("  no problems")
    return 1 if spec["problems"] else 0


def cmd_snapshot(args):
    database = _database(args)
    try:
        data = schema.snapshot(database)
    except db.QueryError as exc:
        print(f"Failed: {exc}")
        return 1
    print(f"Saved {len(data['tables'])} tables of {config.database(database)['name']}.")
    return 0


def cmd_info(_args):
    if not config.DATABASES:
        print("Databases:  none set up yet")
    for entry in config.DATABASES:
        print(f"Database:   {entry['name']} (--db {entry['id']}): {entry['dbname']} on {entry['host']}, "
              f"schemas {', '.join(entry['schemas'])}")
    print(f"Time zone:  {config.TIMEZONE}")
    print(f"Limits:     {config.STATEMENT_TIMEOUT_MS // 1000} s per query, plan cost {config.MAX_PLAN_COST:,.0f}, "
          f"{config.PREVIEW_ROW_LIMIT:,} rows per card")
    return 0


def cmd_doctor(_args):
    try:
        for line in metabase.doctor():
            print(line)
    except metabase.MetabaseError as exc:
        print(f"Failed: {exc}")
        return 1
    return 0


def _from_snapshot(render):
    try:
        print(render())
    except schema.SchemaMissing as exc:
        print(exc)
        return 1
    return 0


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("serve", help="start the preview page")
    p.add_argument("--no-open", action="store_true", help="do not open the browser")

    p = sub.add_parser("tables", help="list tables from the local snapshot")
    p.add_argument("text", nargs="?", default="")
    p.add_argument("--db", help="which database, by id or name (default: the first)")

    p = sub.add_parser("describe", help="columns, indexes and links of one table")
    p.add_argument("table")
    p.add_argument("--db", help="which database, by id or name (default: the first)")

    p = sub.add_parser("q", help="run one read-only query")
    p.add_argument("sql", nargs="?")
    p.add_argument("-f", "--file")
    p.add_argument("--rows", type=int, default=20, help="rows to print (default 20)")
    p.add_argument("--limit", type=int, default=None, help="rows to fetch (default PREVIEW_ROW_LIMIT)")
    p.add_argument("--allow-heavy", action="store_true", help="run even when the plan cost is over the limit")
    p.add_argument("--db", help="which database, by id or name (default: the first, or the dashboard's own with -f)")

    p = sub.add_parser("check", help="check a dashboard's files")
    p.add_argument("slug")

    p = sub.add_parser("snapshot", help="refresh the schema snapshot")
    p.add_argument("--db", help="which database, by id or name (default: the first)")
    sub.add_parser("doctor", help="what the Metabase key can reach")
    sub.add_parser("info", help="the time zone, schemas and limits in force")

    args = parser.parse_args()
    settings.start()
    if args.command in (None, "serve"):
        specs.ensure_sample()
        return server.serve(open_browser=not getattr(args, "no_open", False))
    if args.command == "info":
        return cmd_info(args)
    if args.command == "tables":
        return _from_snapshot(lambda: schema.list_tables(args.text, _database(args)))
    if args.command == "describe":
        return _from_snapshot(lambda: schema.describe(args.table, _database(args)))
    if args.command == "q":
        return cmd_q(args)
    if args.command == "check":
        return cmd_check(args)
    if args.command == "doctor":
        return cmd_doctor(args)
    return cmd_snapshot(args)


if __name__ == "__main__":
    sys.exit(main())
