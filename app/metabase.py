"""Publish a dashboard to Metabase, and report what the API key can reach.

Writes go to one collection, chosen in Settings, and use the Metabase database chosen
there for the dashboard's own database. A dashboard's folder gets its own metabase.json
recording what it was published as, so the next Go live updates it. Before any write, every recorded id is
checked to really sit in that collection, so a wrong record cannot reach other work.

Nothing here runs a query, apart from doctor's two SHOW statements.
"""
import hashlib
import json
import threading
import time
import uuid
import urllib.error
import urllib.request
from datetime import datetime

from . import config, db, filters, settings, specs

_lock = threading.Lock()
GRANT_HINT = "In Metabase, open the collection, choose … > Edit permissions, and give the key's group Curate."


class MetabaseError(Exception):
    """Something the user can read and act on."""


class ApiError(MetabaseError):
    def __init__(self, status, method, path, detail):
        if status == 401:
            text = "Metabase did not accept the API key."
        elif status == 403:
            text = f"The key is not allowed to do this in Metabase ({method} {path})."
        else:
            text = f"Metabase answered {status} to {method} {path}: {detail[:200]}"
        super().__init__(text)
        self.status = status


def _call(method, path, body=None, timeout=40, base=None, key=None):
    base = base or config.METABASE_URL
    key = key or config.METABASE_API_KEY
    if not (base and key):
        raise MetabaseError("Metabase is not set up yet. Open Settings and fill in the Metabase section.")
    request = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        method=method,
        headers={"x-api-key": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        raise ApiError(exc.code, method, path, exc.read().decode("utf-8", "replace")) from None
    except (OSError, ValueError) as exc:
        raise MetabaseError(f"Metabase did not answer: {exc}") from None


# ---- where to publish ----

def _collection():
    name = str(config.METABASE_COLLECTION or "").strip()
    stored = config.METABASE_COLLECTION_ID
    if isinstance(stored, int):
        try:
            found = _call("GET", f"/api/collection/{stored}")
        except ApiError as exc:
            if exc.status not in (403, 404):
                raise
            raise MetabaseError(f'The key can no longer reach the collection "{name}". {GRANT_HINT}') from None
    else:
        if not name:
            raise MetabaseError("No collection is chosen. Pick one in Settings, under Metabase.")
        listed = [c for c in _call("GET", "/api/collection") if isinstance(c, dict)]
        matches = [c for c in listed if str(c.get("name", "")).strip().lower() == name.lower()
                   and not c.get("archived") and isinstance(c.get("id"), int)]
        if not matches:
            raise MetabaseError(f'The key cannot see a collection named "{name}". {GRANT_HINT}')
        if len(matches) > 1:
            raise MetabaseError(f'More than one collection is named "{name}". Pick the right one in Settings.')
        found = matches[0]
    if found.get("archived"):
        raise MetabaseError(f'The collection "{found.get("name")}" is in Metabase\'s trash.')
    if not found.get("can_write"):
        raise MetabaseError(f'The key can see "{found.get("name")}" but cannot write to it. {GRANT_HINT}')
    if not isinstance(stored, int):
        # Remembered by id from now on, so renaming the collection breaks nothing.
        settings.remember("metabase_collection_id", found["id"])
    return found


def target(database=None):
    """Where Go live puts a dashboard of this database: its Metabase database and the one collection. Raises MetabaseError."""
    entry = config.database(database)
    if entry is None:
        raise MetabaseError("This dashboard's database is not in Settings." if database
                            else "No database is set up yet. Open Settings and add one.")
    database_id = entry["metabase_database_id"]
    if not isinstance(database_id, int):
        raise MetabaseError(f'No Metabase database is chosen for "{entry["name"]}". Pick one in Settings, under Databases.')
    databases = (_call("GET", "/api/database") or {}).get("data") or []
    database = next((d for d in databases if d.get("id") == database_id), None)
    if database is None:
        raise MetabaseError(f"The key cannot see the database with id {database_id}.")
    if database.get("native_permissions") != "write":
        raise MetabaseError(f'The key may not save SQL questions on "{database.get("name")}".')
    collection = _collection()
    return {"database_id": database_id, "database": database.get("name"), "studio_database": entry["name"],
            "collection_id": collection["id"], "collection": collection.get("name")}


# ---- what a dashboard was published as ----

def _state_path(slug):
    return config.DASHBOARDS_DIR / slug / specs.PUBLISHED_FILE


def _save_state(slug, state):
    _state_path(slug).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _live_dashboard(state, where):
    """(dashboard, blocker, warning) for a recorded dashboard id. dashboard is None when it must be made anew."""
    try:
        dashboard = _call("GET", f"/api/dashboard/{state['dashboard_id']}")
    except ApiError as exc:
        if exc.status == 404:
            return None, None, "The dashboard no longer exists in Metabase. A new one will be created."
        if exc.status == 403:
            return None, (f'The dashboard was moved where the key cannot reach it. The studio only writes to '
                          f'"{where["collection"]}". Move it back in Metabase.'), None
        raise
    if dashboard.get("archived"):
        return None, None, "The dashboard is in Metabase's trash. A new one will be created."
    if dashboard.get("collection_id") != where["collection_id"]:
        return None, (f'The dashboard is no longer in "{where["collection"]}". The studio only writes there. '
                      "Move it back in Metabase."), None
    warning = None
    if state.get("dashboard_updated_at") and dashboard.get("updated_at") != state["dashboard_updated_at"]:
        warning = ("The dashboard was changed in Metabase after the last Go live. Going live replaces its layout "
                   "and the cards the studio made.")
    return dashboard, None, warning


def _owned_card(card_id, dashboard_id, where):
    """The card, if it is one the studio made for this dashboard and it is still in place. Else None."""
    try:
        card = _call("GET", f"/api/card/{card_id}")
    except ApiError as exc:
        if exc.status in (403, 404):
            return None
        raise
    if card.get("archived"):
        return None
    if card.get("dashboard_id") == dashboard_id:
        return card
    if card.get("dashboard_id") is None and card.get("collection_id") == where["collection_id"]:
        return card
    return None


# ---- is it still there? ----

_seen = {}  # slug -> (when, state)
STATUS_MAX_AGE = 10


def remembered(slug):
    """The last state status() found, without asking Metabase."""
    return _seen.get(slug, (0, None))[1]


def forget(slug):
    _seen.pop(slug, None)


def status(slug):
    """What became of a published dashboard: live, trashed, gone, moved, or unknown when Metabase cannot say."""
    state = specs.published(slug)
    if not state.get("dashboard_id"):
        _seen.pop(slug, None)
        return None
    when, known = _seen.get(slug, (0, None))
    if known and time.monotonic() - when < STATUS_MAX_AGE:
        return known
    try:
        dashboard = _call("GET", f"/api/dashboard/{state['dashboard_id']}", timeout=8)
        if dashboard.get("archived"):
            found = "trashed"
        elif dashboard.get("collection_id") != state.get("collection_id"):
            found = "moved"
        else:
            found = "live"
    except ApiError as exc:
        found = {404: "gone", 403: "moved"}.get(exc.status, "unknown")
    except MetabaseError:
        found = "unknown"
    if found == "unknown":
        return known or found  # a Metabase that does not answer changes nothing we knew
    _seen[slug] = (time.monotonic(), found)
    return found


# ---- the confirmation ----

def plan(slug):
    """What Go live would do, what stops it and what the user should know first. Writes nothing."""
    spec = specs.load(slug)
    if spec is None:
        raise MetabaseError("No such dashboard.")
    blockers = list(spec["problems"])
    warnings = []
    cards = [c for c in spec["cards"] if c["sql"]]
    if not cards:
        blockers.append("The dashboard has no card with a query.")
    for card in cards:
        try:
            result = db.cache_get(specs.query(card, spec), spec["database"])
        except filters.FilterError as exc:
            blockers.append(f'"{card["name"]}": {exc}')
            continue
        if result is None:
            blockers.append(f'"{card["name"]}" has not drawn yet. Open the dashboard and let every card load.')
            continue
        hidden = [c["name"] for c in result["columns"] if c.get("pii")]
        if hidden:
            blockers.append(f'"{card["name"]}" shows personal data ({", ".join(hidden)}). Remove that column.')
        if (result.get("cost") or 0) > config.MAX_PLAN_COST:
            warnings.append(f'"{card["name"]}" is a heavy query. Everyone who opens the dashboard in Metabase runs it.')

    for entry in spec["filters"]:
        listing = specs.options_query(spec, entry["key"])
        if listing and db.cache_get(listing, spec["database"]) is None:
            blockers.append(f'The list for the filter "{entry["name"]}" has not loaded yet. Open the dashboard and let it load.')
    state = specs.published(slug)
    where = None
    live = None
    try:
        where = target(spec["database"])
        if spec["filters"]:
            _field_ids(spec, where)
        if any(f["type"] == "date" for f in spec["filters"]):
            theirs = (_call("GET", "/api/session/properties") or {}).get("report-timezone-long")
            if theirs and theirs != config.TIMEZONE:
                warnings.append(f"Metabase counts days in {theirs}, the studio in {config.TIMEZONE}. Around midnight a date "
                                "filter can show a different day there. Set the same time zone in Settings.")
        if state.get("dashboard_id"):
            live, blocker, warning = _live_dashboard(state, where)
            if blocker:
                blockers.append(blocker)
            if warning:
                warnings.append(warning)
    except MetabaseError as exc:
        blockers.append(str(exc))

    recorded = (state.get("cards") or {}) if live else {}
    keys = [c["key"] for c in cards]
    gone = [key for key in recorded if key not in keys]
    if gone:
        warnings.append(f"{len(gone)} {'card' if len(gone) == 1 else 'cards'} removed here will go to Metabase's trash.")
    return {
        "slug": slug,
        "name": spec["name"],
        "target": where,
        "mode": "update" if live else "create",
        "cards": {"create": len([k for k in keys if k not in recorded]),
                  "update": len([k for k in keys if k in recorded]), "trash": len(gone)},
        "blockers": blockers,
        "warnings": warnings,
    }


# ---- the publish ----

def _field_ids(spec, where):
    """Metabase's id for every column a filter points at: {(card key, filter key): id}. Raises MetabaseError."""
    wanted = {}
    for card in spec["cards"]:
        for key in card.get("tags") or []:
            try:
                wanted[(card["key"], key)] = filters.column(card["filters"][key], spec["database"])[2]
            except (KeyError, filters.FilterError) as exc:
                raise MetabaseError(f'"{card["name"]}", filter "{key}": {exc}') from None
    if not wanted:
        return {}
    listed = _call("GET", f"/api/database/{where['database_id']}/fields") or []
    known = {(f.get("schema"), f.get("table_name"), f.get("name")): f.get("id") for f in listed}
    out = {}
    for pair, path in wanted.items():
        if path not in known:
            raise MetabaseError(f"Metabase does not know the column {'.'.join(path)} yet. Let Metabase sync the database, then try again.")
        out[pair] = known[path]
    return out


def _parameter_id(slug, key):
    return hashlib.sha256(f"{slug}:{key}".encode()).hexdigest()[:8]


def _parameters(spec):
    """The dashboard's filter widgets, the way Metabase stores them."""
    out = []
    for entry in spec["filters"]:
        widget, section = filters.WIDGETS[entry["type"]]
        parameter = {"id": _parameter_id(spec["slug"], entry["key"]), "name": entry["name"], "slug": entry["key"],
                     "type": widget, "sectionId": section}
        if entry["default"] not in (None, ""):
            parameter["default"] = entry["default"] if entry["type"] == "date" else [entry["default"]]
        listing = specs.options_query(spec, entry["key"])
        found = db.cache_get(listing, spec["database"]) if listing else None
        if found:
            # The choices as they were when the dashboard was last drawn here; opening the list in Metabase runs nothing.
            parameter.update({"values_query_type": "list", "values_source_type": "static-list",
                              "values_source_config": {"values": [row[0] for row in found["rows"] if row[0] is not None]}})
        out.append(parameter)
    return out


def _card_body(card, where, spec, fields):
    names = {f["key"]: f for f in spec["filters"]}
    tags = {}
    for key in card.get("tags") or []:
        tags[key] = {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{spec['slug']}/{card['key']}/{key}")),
            "name": key, "display-name": names[key]["name"], "type": "dimension",
            "dimension": ["field", fields[(card["key"], key)], None],
            "widget-type": filters.WIDGETS[names[key]["type"]][0],
        }
    return {
        "name": card["name"],
        "display": card["display"],
        "type": "question",
        "dataset_query": {"database": where["database_id"], "type": "native",
                          "native": {"query": card["sql"], "template-tags": tags}},
        "visualization_settings": card["viz"],
    }


def _create_card(body, dashboard_id, where):
    """Save the question inside the dashboard; on a Metabase that cannot, beside it in the collection."""
    try:
        return _call("POST", "/api/card", {**body, "dashboard_id": dashboard_id})["id"]
    except ApiError as exc:
        if exc.status != 400:
            raise
    return _call("POST", "/api/card", {**body, "collection_id": where["collection_id"]})["id"]


def _mappings(spec, card, card_id):
    return [{"parameter_id": _parameter_id(spec["slug"], key), "card_id": card_id,
             "target": ["dimension", ["template-tag", key], {"stage-number": 0}]} for key in card.get("tags") or []]


def _dashcards(spec, card_ids, current):
    placed = {dc["card_id"]: dc["id"] for dc in current.get("dashcards") or [] if dc.get("card_id")}
    tabs = current.get("tabs") or []
    out = []
    fresh = -1
    for card in spec["cards"]:
        entry = {"row": card["row"], "col": card["col"], "size_x": card["size_x"], "size_y": card["size_y"],
                 "parameter_mappings": [], "series": []}
        if tabs:
            entry["dashboard_tab_id"] = tabs[0]["id"]
        if card["sql"]:
            card_id = card_ids[card["key"]]
            entry.update(id=placed.get(card_id, fresh), card_id=card_id, visualization_settings={},
                         parameter_mappings=_mappings(spec, card, card_id))
        else:
            entry.update(id=fresh, card_id=None, visualization_settings={
                "virtual_card": {"name": None, "display": card["display"], "visualization_settings": {},
                                 "dataset_query": {}, "archived": False},
                "text": card["text"]})
        if entry["id"] == fresh:
            fresh -= 1
        out.append(entry)
    return out


def publish(slug):
    """Create or update the dashboard in Metabase. Returns its link. Raises MetabaseError."""
    with _lock:
        check = plan(slug)
        if check["blockers"]:
            raise MetabaseError(check["blockers"][0])
        spec = specs.load(slug)
        where = check["target"]
        state = specs.published(slug) if check["mode"] == "update" else {}

        if not state.get("dashboard_id"):
            made = _call("POST", "/api/dashboard", {"name": spec["name"], "description": spec["description"] or None,
                                                    "collection_id": where["collection_id"]})
            state = {"dashboard_id": made["id"], "collection_id": where["collection_id"],
                     "database_id": where["database_id"], "cards": {}}
            _save_state(slug, state)  # saved after every step, so a retry continues instead of duplicating
        dashboard_id = state["dashboard_id"]

        fields = _field_ids(spec, where) if spec["filters"] else {}
        cards = [c for c in spec["cards"] if c["sql"]]
        for card in cards:
            body = _card_body(card, where, spec, fields)
            known = state["cards"].get(card["key"])
            if known and _owned_card(known, dashboard_id, where):
                _call("PUT", f"/api/card/{known}", body)
            else:
                state["cards"][card["key"]] = _create_card(body, dashboard_id, where)
                _save_state(slug, state)

        keep = {c["key"] for c in cards}
        for key in [k for k in state["cards"] if k not in keep]:
            card_id = state["cards"].pop(key)
            if _owned_card(card_id, dashboard_id, where):
                _call("PUT", f"/api/card/{card_id}", {"archived": True})
            _save_state(slug, state)

        current = _call("GET", f"/api/dashboard/{dashboard_id}")
        # Tabs are sent back as they are: leaving them out would delete tabs added in Metabase.
        saved = _call("PUT", f"/api/dashboard/{dashboard_id}", {
            "name": spec["name"], "description": spec["description"] or None,
            "dashcards": _dashcards(spec, state["cards"], current),
            "parameters": _parameters(spec),
            "tabs": [{"id": tab["id"], "name": tab["name"]} for tab in current.get("tabs") or []]})
        state.update({
            "url": f"{config.METABASE_URL.rstrip('/')}/dashboard/{dashboard_id}",
            "published_at": datetime.now().isoformat(timespec="seconds"),
            "dashboard_updated_at": (saved or {}).get("updated_at"),
            "content": specs.content_hash(slug),
            "database_id": where["database_id"],
        })
        _save_state(slug, state)
        _seen[slug] = (time.monotonic(), "live")
        return {"url": state["url"], "mode": check["mode"], "cards": check["cards"], "target": where}


# ---- doctor ----

def doctor(with_queries=True):
    """What the key is and can reach, as lines of text."""
    lines = []
    me = _call("GET", "/api/user/current")
    lines.append(f"Key: accepted. It is the API user \"{me.get('common_name')}\"" + (", an ADMIN." if me.get("is_superuser") else ", not an admin."))
    databases = (_call("GET", "/api/database") or {}).get("data") or []
    lines.append(f"Databases it can query: {len(databases)}")
    for d in databases:
        lines.append(f"  id {d.get('id')}: {d.get('name')}")
    root = _call("GET", "/api/collection/root")
    if root.get("can_write"):
        lines.append('Notice: it can write to "Our analytics" itself. The studio never does.')
    if not config.DATABASES:
        lines.append("Go live is NOT ready: no database is set up yet.")
        return lines
    ready = 0
    for entry in config.DATABASES:
        try:
            where = target(entry["id"])
        except MetabaseError as exc:
            lines.append(f"Go live is NOT ready for \"{entry['name']}\": {exc}")
            continue
        if not ready:
            lines.append(f"Go live writes to the collection \"{where['collection']}\" (id {where['collection_id']}).")
        ready += 1
        lines.append(f"For \"{entry['name']}\", Go live uses the Metabase database \"{where['database']}\" (id {where['database_id']}).")
        if with_queries:
            for statement, expected in (("SHOW statement_timeout", "30s"), ("SHOW default_transaction_read_only", "on")):
                reply = _call("POST", "/api/dataset", {"database": where["database_id"], "type": "native",
                                                       "native": {"query": statement}})
                rows = (reply.get("data") or {}).get("rows") or [[None]]
                value = rows[0][0]
                lines.append(f"  {statement}: {value}" + ("" if value == expected else f"   <-- expected {expected}"))
    if ready == len(config.DATABASES):
        lines.append("Go live is ready.")
    return lines
