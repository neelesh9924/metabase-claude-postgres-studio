"""Paths, and the live settings the rest of the app reads as config.NAME.

Settings are kept in data/settings.json and changed on the Settings page. apply()
copies them onto this module, so a change takes effect without a restart.
"""
import os
import shutil
from pathlib import Path

APP_NAME = "Metabase Claude Studio for Postgres"
APP_ID = "metabase-claude-postgres-studio"

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"
EXAMPLES_DIR = ROOT / "examples"
DASHBOARDS_DIR = ROOT / "dashboards"
# Everything personal lives here and is ignored by git. Tests point it at a temporary folder.
DATA_DIR = Path(os.environ.get("STUDIO_DATA_DIR") or ROOT / "data")
SETTINGS_FILE = DATA_DIR / "settings.json"
SCHEMA_FILE = DATA_DIR / "schema.json"
CACHE_DIR = DATA_DIR / "cache"
LOG_FILE = DATA_DIR / "logs" / "queries.log"
APP_LOG = DATA_DIR / "logs" / "studio.log"
THREADS_DIR = DATA_DIR / "threads"
SESSION_FILE = DATA_DIR / "session.json"
PROFILE_DIR = DATA_DIR / "browser-profile"

DEFAULTS = {
    "db_host": "", "db_port": 5432, "db_name": "", "db_user": "", "db_password": "", "db_sslmode": "require",
    "db_schemas": ["public"],
    "metabase_url": "", "metabase_api_key": "", "metabase_database_id": None,
    "metabase_collection_id": None, "metabase_collection": "",
    "claude_bin": "", "model": "",
    "timezone": "UTC", "port": 8787, "browser": "",
    "statement_timeout_ms": 30000, "max_plan_cost": 500000, "preview_row_limit": 2000,
    "max_queries": 40, "max_turns": 150, "plan_timeout": 300, "build_timeout": 900,
}
SECRETS = ("db_password", "metabase_api_key")

_NAMES = {
    "db_host": "DB_HOST", "db_port": "DB_PORT", "db_name": "DB_NAME", "db_user": "DB_USER", "db_password": "DB_PASS",
    "db_sslmode": "DB_SSLMODE", "db_schemas": "DB_SCHEMAS",
    "metabase_url": "METABASE_URL", "metabase_api_key": "METABASE_API_KEY",
    "metabase_database_id": "METABASE_DATABASE_ID", "metabase_collection_id": "METABASE_COLLECTION_ID",
    "metabase_collection": "METABASE_COLLECTION",
    "claude_bin": "STUDIO_CLAUDE_BIN", "model": "STUDIO_MODEL",
    "timezone": "TIMEZONE", "port": "PORT", "browser": "STUDIO_BROWSER",
    "statement_timeout_ms": "STATEMENT_TIMEOUT_MS", "max_plan_cost": "MAX_PLAN_COST",
    "preview_row_limit": "PREVIEW_ROW_LIMIT", "max_queries": "STUDIO_MAX_QUERIES", "max_turns": "STUDIO_MAX_TURNS",
    "plan_timeout": "STUDIO_PLAN_TIMEOUT", "build_timeout": "STUDIO_BUILD_TIMEOUT",
}


def apply(values):
    """Make these settings the ones in force."""
    for key, name in _NAMES.items():
        globals()[name] = values.get(key, DEFAULTS[key])


def name_of(key):
    return _NAMES[key]


apply(DEFAULTS)


def claude_bin():
    """Path of the Claude Code program, or None."""
    if STUDIO_CLAUDE_BIN:  # noqa: F821  (set by apply)
        return STUDIO_CLAUDE_BIN if Path(STUDIO_CLAUDE_BIN).is_file() else None  # noqa: F821
    found = shutil.which("claude")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / ("claude.exe" if os.name == "nt" else "claude")
    return str(fallback) if fallback.is_file() else None
