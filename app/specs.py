"""Dashboards on disk: dashboards/<slug>/dashboard.json plus one <key>.sql per card.

Positions use Metabase's grid: 24 columns, rows of equal height.
"""
import hashlib
import json
import re
import shutil

from . import config, db, guard

GRID_COLUMNS = 24
DATA_DISPLAYS = {"scalar", "smartscalar", "line", "bar", "area", "combo", "row", "pie", "table"}
TEXT_DISPLAYS = {"heading", "text"}
_KEY = re.compile(r"^[a-z0-9_]+$")
_SLUG = re.compile(r"^[a-z0-9_-]+$")


def slugs():
    if not config.DASHBOARDS_DIR.is_dir():
        return []
    return sorted(
        d.name
        for d in config.DASHBOARDS_DIR.iterdir()
        if d.is_dir() and _SLUG.match(d.name) and (d / "dashboard.json").is_file()
    )


PUBLISHED_FILE = "metabase.json"


def ensure_sample():
    """Give a new install the example dashboard to look at."""
    sample = config.EXAMPLES_DIR / "sample"
    if not slugs() and sample.is_dir():
        shutil.copytree(sample, config.DASHBOARDS_DIR / "sample", dirs_exist_ok=True)


def version(slug):
    """Changes whenever any file of the dashboard changes."""
    folder = config.DASHBOARDS_DIR / slug
    stamp = hashlib.sha256()
    for path in sorted(folder.iterdir()):
        if path.is_file():
            info = path.stat()
            stamp.update(f"{path.name}:{info.st_mtime_ns}:{info.st_size};".encode())
    return stamp.hexdigest()[:16]


def content_hash(slug):
    """Changes when the dashboard itself changes: its dashboard.json or a query."""
    folder = config.DASHBOARDS_DIR / slug
    stamp = hashlib.sha256()
    for path in sorted(folder.iterdir()):
        if path.is_file() and (path.name == "dashboard.json" or path.suffix == ".sql"):
            stamp.update(path.name.encode() + b"|" + path.read_bytes() + b"|")
    return stamp.hexdigest()[:16]


def published(slug):
    """What Go live recorded for this dashboard, or {}."""
    try:
        state = json.loads((config.DASHBOARDS_DIR / slug / PUBLISHED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _int(card, field, problems, key):
    value = card.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        problems.append(f"Card '{key}': '{field}' must be a whole number.")
        return 0
    return value


def _overlaps(a, b):
    return (
        a["col"] < b["col"] + b["size_x"]
        and b["col"] < a["col"] + a["size_x"]
        and a["row"] < b["row"] + b["size_y"]
        and b["row"] < a["row"] + a["size_y"]
    )


def load(slug):
    if not _SLUG.match(slug or "") or slug not in slugs():
        return None
    folder = config.DASHBOARDS_DIR / slug
    out = {
        "slug": slug,
        "name": slug,
        "description": "",
        "version": version(slug),
        "live": False,
        "changed": False,
        "url": "",
        "cards": [],
        "problems": [],
    }
    state = published(slug)
    if state.get("dashboard_id"):
        out["live"] = True
        out["url"] = state.get("url") or ""
        out["changed"] = state.get("content") != content_hash(slug)
    problems = out["problems"]
    try:
        raw = json.loads((folder / "dashboard.json").read_text(encoding="utf-8"))
    except ValueError as exc:
        problems.append(f"dashboard.json is not valid JSON: {exc}")
        return out
    except OSError as exc:
        problems.append(f"dashboard.json could not be read: {exc}")
        return out
    if not isinstance(raw, dict):
        problems.append("dashboard.json must be an object.")
        return out

    if isinstance(raw.get("name"), str) and raw["name"].strip():
        out["name"] = raw["name"].strip()
    else:
        problems.append("The dashboard has no 'name'.")
    out["description"] = raw.get("description") or ""

    cards = raw.get("cards")
    if not isinstance(cards, list):
        problems.append("'cards' must be a list.")
        return out

    seen = set()
    for index, card in enumerate(cards):
        if not isinstance(card, dict):
            problems.append(f"Card {index + 1} must be an object.")
            continue
        key = card.get("key")
        if not isinstance(key, str) or not _KEY.match(key):
            problems.append(f"Card {index + 1}: 'key' must use only a-z, 0-9 and _.")
            continue
        if key in seen:
            problems.append(f"Card '{key}' appears twice.")
            continue
        seen.add(key)
        display = card.get("display")
        item = {
            "key": key,
            "name": card.get("name") or "",
            "display": display,
            "viz": card.get("viz") if isinstance(card.get("viz"), dict) else {},
            "row": _int(card, "row", problems, key),
            "col": _int(card, "col", problems, key),
            "size_x": max(1, _int(card, "size_x", problems, key)),
            "size_y": max(1, _int(card, "size_y", problems, key)),
            "sql": None,
            "sql_hash": None,
            "text": card.get("text") or "",
        }
        if item["row"] < 0 or item["col"] < 0 or item["col"] + item["size_x"] > GRID_COLUMNS:
            problems.append(f"Card '{key}' does not fit the {GRID_COLUMNS}-column grid.")
            item["col"] = max(0, min(item["col"], GRID_COLUMNS - 1))
            item["size_x"] = min(item["size_x"], GRID_COLUMNS - item["col"])
            item["row"] = max(0, item["row"])
        if display in TEXT_DISPLAYS:
            if not item["text"]:
                problems.append(f"Card '{key}' has no 'text'.")
        elif display in DATA_DISPLAYS:
            if not item["name"]:
                problems.append(f"Card '{key}' has no 'name'.")
            try:
                sql = (folder / f"{key}.sql").read_text(encoding="utf-8")
                guard.check(sql)
                item["sql"] = sql.strip()
                item["sql_hash"] = db.sql_hash(sql)
            except OSError:
                problems.append(f"Card '{key}': the file {key}.sql is missing.")
            except guard.Rejected as exc:
                problems.append(f"Card '{key}': {exc}")
        else:
            problems.append(f"Card '{key}': unknown display '{display}'.")
            continue
        out["cards"].append(item)

    placed = out["cards"]
    for i, a in enumerate(placed):
        for b in placed[i + 1 :]:
            if _overlaps(a, b):
                problems.append(f"Cards '{a['key']}' and '{b['key']}' overlap.")
    return out


def summary(slug):
    spec = load(slug)
    if spec is None:
        return None
    return {
        "slug": slug,
        "name": spec["name"],
        "cards": len(spec["cards"]),
        "version": spec["version"],
        "live": spec["live"],
        "changed": spec["changed"],
        "problems": len(spec["problems"]),
    }
