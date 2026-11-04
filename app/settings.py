"""The settings file: read it, check it, save it.

The page gets every setting except the secrets; for those it only learns whether one is
saved. A secret left empty in a save keeps the one already there. There can be several
databases; each has an id that never changes, because dashboards refer to it.
"""
import json
import os
import re

from . import config, secretbox

_TIMEZONE = re.compile(r"^[A-Za-z0-9_+\-/]{1,64}$")
_SCHEMA = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")
_SSL_MODES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")
_RANGES = {
    "port": (1024, 65535), "statement_timeout_ms": (1000, 600000),
    "max_plan_cost": (1, 10**12), "preview_row_limit": (1, 10000), "max_queries": (1, 500),
    "max_turns": (5, 1000), "plan_timeout": (30, 3600), "build_timeout": (60, 7200),
}
_LABELS = {
    "port": "Local port", "statement_timeout_ms": "Query time limit",
    "max_plan_cost": "Heavy-query limit", "preview_row_limit": "Rows per card", "max_queries": "Queries per request",
    "max_turns": "Claude turns", "plan_timeout": "Plan time limit", "build_timeout": "Build time limit",
}
_current = dict(config.DEFAULTS)


def _read():
    try:
        raw = json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _from_one_database(raw):
    """A settings file written when the app knew one database only: that database becomes the first entry."""
    if "databases" in raw or not raw.get("db_host"):
        return raw
    name = str(raw.get("db_name") or "Database")
    if name == "postgres":  # the default database's name says nothing; the server's does
        name = str(raw["db_host"]).split(".")[0] or name
    raw["databases"] = [{
        "id": config.MAIN, "name": name, "host": raw.get("db_host", ""),
        "port": raw.get("db_port", 5432), "dbname": raw.get("db_name", ""), "user": raw.get("db_user", ""),
        "password": raw.get("db_password", ""), "sslmode": raw.get("db_sslmode", "require"),
        "schemas": raw.get("db_schemas") or ["public"], "metabase_database_id": raw.get("metabase_database_id"),
    }]
    return raw


def _entry(stored):
    entry = {**config.DATABASE_FIELDS, **{k: v for k, v in stored.items() if k in config.DATABASE_FIELDS}}
    entry["password"] = secretbox.reveal(entry["password"])
    entry["schemas"] = list(entry["schemas"] or ["public"])
    return entry


def start():
    """Load the saved settings and put them in force. Called once by whatever starts the app."""
    raw = _from_one_database(_read())
    values = dict(config.DEFAULTS)
    for key in config.DEFAULTS:
        if key not in raw:
            continue
        if key == "databases":
            values[key] = [_entry(e) for e in raw[key] if isinstance(e, dict) and e.get("id")]
        else:
            values[key] = secretbox.reveal(raw[key]) if key in config.SECRETS else raw[key]
    _current.clear()
    _current.update(values)
    config.apply(values)
    return values


def _write():
    stored = {k: (secretbox.protect(v) if k in config.SECRETS else v) for k, v in _current.items()}
    stored["databases"] = [{**e, "password": secretbox.protect(e["password"])} for e in _current["databases"]]
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    draft = config.SETTINGS_FILE.with_suffix(".tmp")
    draft.write_text(json.dumps(stored, indent=2) + "\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(draft, 0o600)
    os.replace(draft, config.SETTINGS_FILE)


def current():
    """Every setting, secrets included. For this process only; never for the page."""
    return dict(_current)


def public():
    """The settings as the page may see them."""
    from . import schema

    out = {k: v for k, v in _current.items() if k not in config.SECRETS and k != "databases"}
    for key in config.SECRETS:
        out[f"{key}_set"] = bool(_current[key])
    out["databases"] = [
        {**{k: v for k, v in e.items() if k != "password"}, "password_set": bool(e["password"]),
         "ready": config.database_complete(e), "tables": schema.summary(e["id"])}
        for e in _current["databases"]
    ]
    return out


def database_ready():
    return any(config.database_complete(e) for e in _current["databases"])


def metabase_ready():
    return bool(_current["metabase_url"] and _current["metabase_api_key"]
                and any(e["metabase_database_id"] for e in _current["databases"]))


def _coerce(key, value):
    if key == "metabase_collection_id":
        return None if value in (None, "") else int(value)
    if isinstance(config.DEFAULTS[key], int):
        return int(value)
    return str(value or "").strip()


def check(values):
    """What is wrong with these settings, in plain words. Empty means fine."""
    problems = []
    for key, (low, high) in _RANGES.items():
        if not low <= values[key] <= high:
            problems.append(f"{_LABELS[key]} must be between {low:,} and {high:,}.")
    if not _TIMEZONE.match(values["timezone"]):
        problems.append('The time zone must be a name like "Asia/Kolkata" or "UTC".')
    if values["metabase_url"] and not re.match(r"^https?://[^\s/]+", values["metabase_url"]):
        problems.append("The Metabase address must start with http:// or https://.")
    return problems


def _apply():
    from . import db

    _write()
    config.apply(_current)
    db.reset()  # the next query connects with the new settings


def save(changes):
    """Take changed settings from the page. Returns the problems; nothing is saved when there are any."""
    values = dict(_current)
    for key, value in (changes or {}).items():
        if key not in config.DEFAULTS or key == "databases":
            continue
        if key in config.SECRETS and not value:
            continue
        try:
            values[key] = _coerce(key, value)
        except (TypeError, ValueError):
            return [f"{_LABELS.get(key, key)} has a value that cannot be used."]
    values["metabase_url"] = values["metabase_url"].rstrip("/")
    problems = check(values)
    if problems:
        return problems
    _current.clear()
    _current.update(values)
    _apply()
    return []


def _new_id(name, entries):
    if not entries:
        return config.MAIN
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:24] or "db"
    taken = {e["id"] for e in entries} | {config.MAIN}
    ident, n = base, 2
    while ident in taken:
        ident, n = f"{base}_{n}", n + 1
    return ident


def save_database(values):
    """Add a database, or change the one whose id is given. Returns (problems, id)."""
    values = values or {}
    entries = [dict(e) for e in _current["databases"]]
    existing = next((e for e in entries if e["id"] == str(values.get("id") or "")), None)
    entry = existing if existing is not None else {**config.DATABASE_FIELDS, "schemas": ["public"]}
    try:
        for key in ("name", "host", "dbname", "user", "sslmode"):
            if key in values:
                entry[key] = str(values[key] or "").strip()
        if "port" in values:
            entry["port"] = int(values["port"])
        if "schemas" in values:
            listed = values["schemas"].split(",") if isinstance(values["schemas"], str) else list(values["schemas"])
            entry["schemas"] = [str(s).strip() for s in listed if str(s).strip()]
        if "metabase_database_id" in values:
            entry["metabase_database_id"] = None if values["metabase_database_id"] in (None, "") else int(values["metabase_database_id"])
        if values.get("password"):
            entry["password"] = str(values["password"])
    except (TypeError, ValueError):
        return ["One of the values cannot be used. The port and the Metabase database must be numbers."], None
    problems = []
    if not entry["name"]:
        problems.append("Give the database a name.")
    elif any(e is not entry and e["name"].lower() == entry["name"].lower() for e in entries):
        problems.append(f'There is already a database named "{entry["name"]}".')
    if not (entry["host"] and entry["dbname"] and entry["user"] and entry["password"]):
        problems.append("Fill in the host, the database, the user and the password.")
    if not 1 <= entry["port"] <= 65535:
        problems.append("The database port must be between 1 and 65,535.")
    if not entry["schemas"] or not all(_SCHEMA.match(s) for s in entry["schemas"]):
        problems.append("Give at least one schema name, such as public.")
    if entry["sslmode"] not in _SSL_MODES:
        problems.append("The SSL mode is not one Postgres knows.")
    if problems:
        return problems, None
    if existing is None:
        entry["id"] = _new_id(entry["name"], entries)
        entries.append(entry)
    _current["databases"] = entries
    _apply()
    return [], entry["id"]


def remove_database(ident):
    """Take a database out of the settings. Refused while a dashboard still belongs to it."""
    from . import schema, specs

    entry = next((e for e in _current["databases"] if e["id"] == ident), None)
    if entry is None:
        return ["There is no such database."]
    using = [s for s in specs.slugs() if (specs.load(s) or {}).get("database") == ident]
    if using:
        count = f"{len(using)} {'dashboard' if len(using) == 1 else 'dashboards'}"
        return [f'"{entry["name"]}" is used by {count}. Remove {"it" if len(using) == 1 else "them"} from the studio first.']
    _current["databases"] = [e for e in _current["databases"] if e["id"] != ident]
    _apply()
    schema.forget(ident)
    return []


def remember(key, value):
    """Record one thing the app worked out by itself, such as a collection's id."""
    _current[key] = value
    _write()
    setattr(config, config.name_of(key), value)
