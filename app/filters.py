"""Dashboard filters: Metabase's {{tag}} and [[optional]] syntax, turned into plain SQL for the preview.

A card's query is written the way Metabase wants it, so the same file is what gets
published. Here each {{tag}} becomes the condition Metabase would write for the value
picked: a date range counted in the studio's time zone, or an equality. A tag with no
value becomes TRUE, and an [[optional part]] whose tag has no value is left out.

Values never reach the query as they were typed: dates and numbers are parsed, text
is quoted, and column names are looked up in the table list.
"""
import re
from datetime import date

from . import config, schema

TYPES = ("date", "text", "number")
WIDGETS = {"date": ("date/all-options", "date"), "text": ("string/=", "string"), "number": ("number/=", "number")}
_PIECE = re.compile(r"\[\[(.*?)\]\]|\{\{\s*([A-Za-z0-9_]+)\s*\}\}", re.S)
_TAG = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
_DAY = r"\d{4}-\d{2}-\d{2}"
_RELATIVE = re.compile(r"^past(\d{1,4})(day|week|month|year)s(~?)$")
_THIS = {"thisday": "day", "thisweek": "week", "thismonth": "month", "thisyear": "year"}


class FilterError(ValueError):
    pass


class _NoValue(Exception):
    pass


def tags(sql):
    """The filter keys a query uses."""
    return sorted(set(_TAG.findall(sql or "")))


def defaults(definitions):
    return {d["key"]: d.get("default") for d in definitions if d.get("default") not in (None, "")}


def _day(text):
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise FilterError(f"'{text}' is not a date.") from None


def _range(value):
    """(first day, day after the last) as SQL date expressions; either may be None."""
    value = str(value).strip()
    today = f"(now() at time zone '{config.TIMEZONE}')::date"
    if value in _THIS:
        unit = _THIS[value]
        start = today if unit == "day" else f"date_trunc('{unit}', {today})::date"
        return start, f"({start} + interval '1 {unit}')::date"
    relative = _RELATIVE.match(value)
    if relative:
        count, unit, current = int(relative.group(1)), relative.group(2), relative.group(3)
        base = today if unit == "day" else f"date_trunc('{unit}', {today})::date"
        end = f"({base} + interval '1 {unit}')::date" if current else base
        return f"({base} - interval '{count} {unit}')::date", end
    if re.fullmatch(_DAY, value):
        return f"date '{_day(value)}'", f"(date '{_day(value)}' + 1)"
    between = re.fullmatch(f"({_DAY})?~({_DAY})?", value)
    if between and (between.group(1) or between.group(2)):
        return (f"date '{_day(between.group(1))}'" if between.group(1) else None,
                f"(date '{_day(between.group(2))}' + 1)" if between.group(2) else None)
    raise FilterError(f"'{value}' is not a date range the preview understands.")


def column(reference):
    """Look a 'table.column' up in the table list: (quoted name, type, (schema, table, column))."""
    parts = str(reference or "").split(".")
    if len(parts) not in (2, 3) or not all(parts):
        raise FilterError(f"'{reference}' must be written as table.column.")
    table_name, name = ".".join(parts[:-1]), parts[-1]
    try:
        tables = schema.load()["tables"]
    except schema.SchemaMissing as exc:
        raise FilterError(str(exc)) from None
    table = tables.get(table_name)
    found = next((c for c in (table or {}).get("columns", []) if c["name"] == name), None)
    if found is None:
        raise FilterError(f"The table list has no column {reference}.")
    path = (table.get("schema", "public"), table_name.split(".")[-1], name)
    return ".".join('"' + p.replace('"', '""') + '"' for p in path), found["type"], path


def _text(value):
    return "'" + str(value).replace("'", "''") + "'"


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise FilterError(f"'{value}' is not a number.") from None
    if number != number or number in (float("inf"), float("-inf")):
        raise FilterError(f"'{value}' is not a number.")
    return repr(int(number)) if number == int(number) else repr(number)


def _condition(kind, reference, value):
    name, column_type, _ = column(reference)
    if kind == "date":
        start, end = _range(value)
        if column_type.startswith("timestamp with time zone"):
            def edge(day):
                return f"({day})::timestamp at time zone '{config.TIMEZONE}'"
        elif column_type.startswith("timestamp"):
            def edge(day):
                return f"({day})::timestamp"
        elif column_type == "date":
            def edge(day):
                return day
        else:
            raise FilterError(f"A date filter needs a date or timestamp column; {reference} is {column_type}.")
        sides = ([f"{name} >= {edge(start)}"] if start else []) + ([f"{name} < {edge(end)}"] if end else [])
        return "(" + " and ".join(sides) + ")"
    quote = _number if kind == "number" else _text
    many = value if isinstance(value, list) else [value]
    if len(many) == 1:
        return f"{name} = {quote(many[0])}"
    return f"{name} in ({', '.join(quote(v) for v in many)})"


def render(sql, mapping, definitions, values):
    """The query as Postgres can run it, for these filter values."""
    kinds = {d["key"]: d["type"] for d in definitions}

    def condition(key):
        if key not in kinds:
            raise FilterError(f"The query uses {{{{{key}}}}}, but the dashboard has no filter with that key.")
        if key not in mapping:
            raise FilterError(f'The query uses {{{{{key}}}}}, but the card\'s "filters" does not say which column it is.')
        value = (values or {}).get(key)
        if value in (None, "", []):
            raise _NoValue
        return _condition(kinds[key], mapping[key], value)

    def optional(text):
        try:
            return _TAG.sub(lambda tag: condition(tag.group(1)), text)
        except _NoValue:
            return ""

    def piece(match):
        if match.group(1) is not None:
            return optional(match.group(1))
        try:
            return condition(match.group(2))
        except _NoValue:
            return "TRUE"

    return _PIECE.sub(piece, sql)


def check_value(kind, value):
    """Raise FilterError when a value picked on the page cannot be used."""
    if value in (None, "", []):
        return
    if kind == "date":
        _range(value)
    elif kind == "number":
        for one in value if isinstance(value, list) else [value]:
            _number(one)
