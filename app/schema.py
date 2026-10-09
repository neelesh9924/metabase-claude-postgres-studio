"""Local snapshot of each database's structure, read from the Postgres catalog only.

Tables in `public` are known by their bare name, tables in any other schema as
`schema.table`. Every database in Settings has its own snapshot file.
"""
import json
import re
from datetime import datetime

from . import config, db

_TABLES = """
select n.nspname, c.relname, c.relkind, c.reltuples::bigint, pg_total_relation_size(c.oid), obj_description(c.oid)
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = any(%s) and c.relkind in ('r', 'p', 'v', 'm')
order by n.nspname, c.relname
"""
_COLUMNS = """
select n.nspname, c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull
from pg_attribute a
join pg_class c on c.oid = a.attrelid
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = any(%s) and c.relkind in ('r', 'p', 'v', 'm')
  and a.attnum > 0 and not a.attisdropped
order by n.nspname, c.relname, a.attnum
"""
_INDEXES = """
select n.nspname, t.relname, i.relname, ix.indisunique, ix.indisprimary, pg_get_indexdef(ix.indexrelid)
from pg_index ix
join pg_class i on i.oid = ix.indexrelid
join pg_class t on t.oid = ix.indrelid
join pg_namespace n on n.oid = t.relnamespace
where n.nspname = any(%s)
order by n.nspname, t.relname, i.relname
"""
_FOREIGN_KEYS = """
select n.nspname, t.relname, pg_get_constraintdef(con.oid)
from pg_constraint con
join pg_class t on t.oid = con.conrelid
join pg_namespace n on n.oid = t.relnamespace
where con.contype = 'f' and n.nspname = any(%s)
order by n.nspname, t.relname
"""
_KINDS = {"r": "table", "p": "table", "v": "view", "m": "materialized view"}
_FK = re.compile(r"FOREIGN KEY \((.+?)\) REFERENCES (.+?)\((.+?)\)")


class SchemaMissing(Exception):
    pass


def _names(text):
    return [part.strip().strip('"') for part in text.split(",")]


def _known_as(schema, table):
    return table if schema == "public" else f"{schema}.{table}"


def _file(database=None):
    """Where a database's snapshot is kept. Raises SchemaMissing for a database that is not in Settings."""
    entry = config.database(database)
    if entry is None and database:
        raise SchemaMissing(f'There is no database "{database}" in Settings.')
    ident = entry["id"] if entry else config.MAIN
    return config.SCHEMA_FILE if ident == config.MAIN else config.SCHEMA_FILE.with_name(f"schema-{ident}.json")


def snapshot(database=None):
    source = config.database(database)
    if source is None:
        raise db.QueryError(f'There is no database "{database}" in Settings.' if database
                            else "No database is set up yet. Open Settings and add one.")
    schemas = list(source["schemas"])
    tables, columns, indexes, fks = db.fetch_catalog(
        [(q, (schemas,)) for q in (_TABLES, _COLUMNS, _INDEXES, _FOREIGN_KEYS)], database=source["id"])
    out = {}
    for schema, name, kind, rows, size, comment in tables:
        out[_known_as(schema, name)] = {
            "schema": schema,
            "kind": _KINDS.get(kind, kind),
            "rows": None if rows is None or rows < 0 else int(rows),
            "size_bytes": int(size or 0),
            "comment": comment,
            "columns": [],
            "indexes": [],
            "foreign_keys": [],
        }
    for schema, table, column, type_name, not_null in columns:
        entry = out.get(_known_as(schema, table))
        if entry:
            entry["columns"].append({"name": column, "type": type_name, "nullable": not not_null})
    for schema, table, _index, unique, primary, definition in indexes:
        entry = out.get(_known_as(schema, table))
        if entry:
            entry["indexes"].append({"unique": bool(unique), "primary": bool(primary),
                                     "definition": definition.split(" USING ", 1)[-1]})
    for schema, table, definition in fks:
        entry = out.get(_known_as(schema, table))
        match = _FK.search(definition)
        if entry and match:
            target = match.group(2).replace('"', "")
            entry["foreign_keys"].append({
                "columns": _names(match.group(1)),
                "ref_table": target[len("public."):] if target.startswith("public.") else target,
                "ref_columns": _names(match.group(3)),
            })
    data = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "database": f"{source['host']}/{source['dbname']}",
        "schemas": schemas,
        "tables": out,
    }
    path = _file(source["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


def forget(database):
    """Drop a removed database's snapshot."""
    path = config.SCHEMA_FILE.with_name(f"schema-{database}.json")
    if database and database != config.MAIN:
        path.unlink(missing_ok=True)


def load(database=None):
    try:
        return json.loads(_file(database).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SchemaMissing("The table list has not been read yet. Read it from Settings, or run: python studio.py snapshot") from None


_summaries = {}  # snapshot file -> (its size and time, its summary)


def summary(database=None):
    """How many tables the snapshot holds and when it was taken, or None."""
    try:
        path = _file(database)
        info = path.stat()
        stamp = (info.st_mtime_ns, info.st_size)
        if _summaries.get(path, (None, None))[0] != stamp:
            data = load(database)
            _summaries[path] = (stamp, {"tables": len(data["tables"]), "generated_at": data["generated_at"]})
    except (SchemaMissing, OSError, KeyError, TypeError):
        return None
    return _summaries[path][1]


def _size(n):
    for unit in ("B", "kB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "kB") else f"{n:.1f} {unit}"
        n /= 1024


def _rows(n):
    return "rows unknown" if n is None else f"~{n:,} rows"


def list_tables(pattern="", database=None):
    tables = load(database)["tables"]
    names = [n for n in tables if pattern.lower() in n.lower()]
    if not names:
        return f"No table matches '{pattern}'."
    width = max(len(n) for n in names)
    lines = [
        f"{n.ljust(width)}  {_rows(tables[n]['rows']).rjust(18)}  {_size(tables[n]['size_bytes']).rjust(9)}"
        + ("" if tables[n]["kind"] == "table" else f"  ({tables[n]['kind']})")
        for n in names
    ]
    return "\n".join(lines)


def _leading_column(definition):
    match = re.search(r"\((.+)\)", definition)
    if not match:
        return None
    first = match.group(1).split(",")[0].strip()
    return first.split()[0].strip('"') if first and "(" not in first else None


def describe(name, database=None):
    data = load(database)
    tables = data["tables"]
    table = tables.get(name)
    if table is None:
        close = [n for n in tables if name.lower() in n.lower()][:15]
        hint = "\nDid you mean: " + ", ".join(close) if close else ""
        return f"No table named '{name}'.{hint}"
    primary = {_leading_column(i["definition"]) for i in table["indexes"] if i["primary"]}
    indexed = {_leading_column(i["definition"]) for i in table["indexes"]}
    refs = {}
    for fk in table["foreign_keys"]:
        for column, ref_column in zip(fk["columns"], fk["ref_columns"]):
            refs[column] = f"{fk['ref_table']}.{ref_column}"
    name_width = max(len(c["name"]) for c in table["columns"])
    type_width = max(len(c["type"]) for c in table["columns"])
    lines = [f"{name}   {_rows(table['rows'])}   {_size(table['size_bytes'])}   ({table['kind']})"]
    if table["comment"]:
        lines.append(table["comment"])
    lines.append("")
    for column in table["columns"]:
        notes = []
        if column["name"] in primary:
            notes.append("PK")
        elif column["name"] in indexed:
            notes.append("indexed")
        if column["name"] in refs:
            notes.append("-> " + refs[column["name"]])
        if not column["nullable"]:
            notes.append("not null")
        lines.append(
            f"  {column['name'].ljust(name_width)}  {column['type'].ljust(type_width)}  {', '.join(notes)}".rstrip()
        )
    if table["indexes"]:
        lines += ["", "Indexes"]
        for index in table["indexes"]:
            flag = "primary" if index["primary"] else "unique" if index["unique"] else ""
            lines.append(f"  {index['definition']}  {flag}".rstrip())
    used_by = sorted(
        f"{other}.{column}"
        for other, info in tables.items()
        for fk in info["foreign_keys"]
        if fk["ref_table"] == name
        for column in fk["columns"]
    )
    if used_by:
        lines += ["", "Referenced by", "  " + ", ".join(used_by)]
    lines += ["", f"Snapshot taken {data['generated_at']}"]
    return "\n".join(lines)
