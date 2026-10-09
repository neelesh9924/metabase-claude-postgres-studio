"""The settings file: read it, check it, save it.

The page gets every setting except the two secrets; for those it only learns whether
one is saved. A secret left empty in a save keeps the one already there.
"""
import json
import os
import re

from . import config, secretbox

_TIMEZONE = re.compile(r"^[A-Za-z0-9_+\-/]{1,64}$")
_SCHEMA = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")
_RANGES = {
    "db_port": (1, 65535), "port": (1024, 65535), "statement_timeout_ms": (1000, 600000),
    "max_plan_cost": (1, 10**12), "preview_row_limit": (1, 10000), "max_queries": (1, 500),
    "max_turns": (5, 1000), "plan_timeout": (30, 3600), "build_timeout": (60, 7200),
}
_LABELS = {
    "db_port": "Database port", "port": "Local port", "statement_timeout_ms": "Query time limit",
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


def start():
    """Load the saved settings and put them in force. Called once by whatever starts the app."""
    raw = _read()
    values = dict(config.DEFAULTS)
    for key in config.DEFAULTS:
        if key in raw:
            values[key] = secretbox.reveal(raw[key]) if key in config.SECRETS else raw[key]
    _current.clear()
    _current.update(values)
    config.apply(values)
    return values


def _write():
    stored = {k: (secretbox.protect(v) if k in config.SECRETS else v) for k, v in _current.items()}
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
    out = {k: v for k, v in _current.items() if k not in config.SECRETS}
    for key in config.SECRETS:
        out[f"{key}_set"] = bool(_current[key])
    return out


def database_ready():
    return bool(_current["db_host"] and _current["db_name"] and _current["db_user"] and _current["db_password"])


def metabase_ready():
    return bool(_current["metabase_url"] and _current["metabase_api_key"] and _current["metabase_database_id"])


def _coerce(key, value):
    default = config.DEFAULTS[key]
    if key == "db_schemas":
        if isinstance(value, str):
            value = value.split(",")
        return [str(v).strip() for v in value if str(v).strip()] if isinstance(value, list) else None
    if key in ("metabase_database_id", "metabase_collection_id"):
        return None if value in (None, "") else int(value)
    if isinstance(default, int):
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
    if not values["db_schemas"] or not all(_SCHEMA.match(s) for s in values["db_schemas"]):
        problems.append("Give at least one schema name, such as public.")
    if values["db_sslmode"] not in ("disable", "allow", "prefer", "require", "verify-ca", "verify-full"):
        problems.append("The SSL mode is not one Postgres knows.")
    if values["metabase_url"] and not re.match(r"^https?://[^\s/]+", values["metabase_url"]):
        problems.append("The Metabase address must start with http:// or https://.")
    return problems


def save(changes):
    """Take changed settings from the page. Returns the problems; nothing is saved when there are any."""
    from . import db

    values = dict(_current)
    for key, value in (changes or {}).items():
        if key not in config.DEFAULTS:
            continue
        if key in config.SECRETS and not value:
            continue
        try:
            coerced = _coerce(key, value)
        except (TypeError, ValueError):
            coerced = None
        if coerced is None and key not in ("metabase_database_id", "metabase_collection_id"):
            return [f"{_LABELS.get(key, key)} has a value that cannot be used."]
        values[key] = coerced
    values["metabase_url"] = values["metabase_url"].rstrip("/")
    problems = check(values)
    if problems:
        return problems
    _current.clear()
    _current.update(values)
    _write()
    config.apply(values)
    db.reset()  # the next query connects with the new settings
    return []


def remember(key, value):
    """Record one thing the app worked out by itself, such as a collection's id."""
    _current[key] = value
    _write()
    setattr(config, config.name_of(key), value)
