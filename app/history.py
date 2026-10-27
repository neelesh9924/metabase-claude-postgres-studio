"""Versions of every dashboard, kept on this PC in data/history/<slug>/, so a change can be undone: a Claude
edit that spoils a dashboard, say.

A version is one file, <id>.json: {id, at, by, note, content, files}. files holds the dashboard itself, its
dashboard.json and every .sql, as text. Never metabase.json (Go live's own record) and never data/shown (the
user's say on personal data). content is their specs.content_hash, the figure Go live records too, so the
version Metabase was last sent is the live one.

- record() keeps a version when the files differ from the newest one, and nothing otherwise.
- catch_up() keeps the changes made outside the studio, or the files as first opened here.
- on_run() is called around Claude's runs: before, to keep what was there; after, to keep Claude's change.
- go_back() puts a version's files back, and is a version itself, so going back can be undone too.

The newest KEEP versions stay, and the live one. Everything here runs under the dashboards' lock (specs.LOCK).
"""
import json
import re
import secrets
import sys
from datetime import datetime

from . import config, paths, specs

BY = ("claude", "you", "outside", "studio")
KEEP = 50
_ID = re.compile(r"^\d{8}-\d{6}-\d{6}-[0-9a-f]{4}$")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class HistoryError(Exception):
    """Why a version cannot be gone back to, in words for the page."""


def new_id():
    """A version's id: in the order they were kept, to the microsecond, and fit for a file name."""
    return f"{datetime.now():%Y%m%d-%H%M%S-%f}-{secrets.token_hex(2)}"


def label(at, now=None):
    """A time in the page's own absolute words (static/time.js), for the note a Go back leaves, which is read later:
    27 Oct, 3:49 pm, and 27 Oct 2025, 3:49 pm when it is not this year. The page writes every other time itself."""
    try:
        when = datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return str(at or "")
    year = "" if when.year == (now or datetime.now()).year else f" {when.year}"
    hour = when.hour % 12 or 12
    return f"{when.day} {MONTHS[when.month - 1]}{year}, {hour}:{when.minute:02d} {'am' if when.hour < 12 else 'pm'}"


def _safe(name):
    """A file of the dashboard a version may hold: dashboard.json or a query, one plain name in its folder."""
    return isinstance(name, str) and paths.parts_of(name) == [name] and (name == "dashboard.json" or name.endswith(".sql"))


def _current(slug):
    """{name: bytes} of the dashboard's own files as they are on disk now."""
    return {name: body for name, body in specs.content_files(slug).items() if _safe(name)}


def _texts(files):
    # surrogateescape keeps bytes that are not UTF-8 as they are, both ways
    return {name: body.decode("utf-8", "surrogateescape") for name, body in files.items()}


def _bytes(files):
    return {name: text.encode("utf-8", "surrogateescape") for name, text in files.items()}


def _read(path):
    """One kept version, read with care, or None. Its content is worked out again from its files."""
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not (isinstance(entry, dict) and isinstance(entry.get("id"), str) and _ID.match(entry["id"])
            and isinstance(entry.get("files"), dict) and "dashboard.json" in entry["files"]
            and all(_safe(name) and isinstance(text, str) for name, text in entry["files"].items())):
        return None
    try:
        entry["content"] = specs.hash_files(_bytes(entry["files"]))
    except UnicodeEncodeError:
        return None
    entry["by"] = entry.get("by") if entry.get("by") in BY else "studio"
    entry["note"] = str(entry.get("note") or "")
    entry["at"] = str(entry.get("at") or "")
    return entry


def _files_of(slug):
    """The version files of a dashboard, newest first."""
    try:
        found = [p for p in specs.history_dir(slug).iterdir() if p.suffix == ".json" and _ID.match(p.stem)]
    except OSError:
        return []
    return sorted(found, key=lambda p: p.stem, reverse=True)


def versions(slug):
    """A dashboard's versions, newest first."""
    return [entry for entry in map(_read, _files_of(slug)) if entry]


def _newest(slug):
    return next((entry for entry in map(_read, _files_of(slug)) if entry), None)


def _live(slug):
    """The content Metabase was last sent, or None when the dashboard is not live."""
    state = specs.published(slug)
    return state.get("content") if state.get("dashboard_id") else None


def _write(path, body):
    """Write a file whole, then swap it in, so nobody reads half of it."""
    draft = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        draft.write_bytes(body)
        specs.replace(draft, path)
    finally:
        draft.unlink(missing_ok=True)


def _keep(slug, files, by, note):
    version = {"id": new_id(), "at": datetime.now().isoformat(timespec="seconds"), "by": by if by in BY else "studio",
               "note": " ".join(str(note or "").split())[:200], "content": specs.hash_files(files), "files": _texts(files)}
    folder = specs.history_dir(slug)
    folder.mkdir(parents=True, exist_ok=True)
    _write(folder / f"{version['id']}.json", json.dumps(version, indent=1).encode("utf-8"))
    _prune(slug)
    return version


def _prune(slug):
    """Only the newest KEEP versions stay, and the live one, so that it can always be gone back to."""
    listed = versions(slug)
    if len(listed) <= KEEP:
        return
    live = _live(slug)
    keep = {v["id"] for v in listed[:KEEP]}
    keep |= {next((v["id"] for v in listed if live and v["content"] == live), None)}
    for version in listed:
        if version["id"] not in keep:
            (specs.history_dir(slug) / f"{version['id']}.json").unlink(missing_ok=True)


def record(slug, by, note):
    """Keep a version of the dashboard's files when they differ from its newest one. Returns it, or None."""
    with specs.LOCK:
        if not isinstance(slug, str) or slug not in specs.slugs():
            return None
        files = _current(slug)
        newest = _newest(slug)
        if newest is not None and newest["content"] == specs.hash_files(files):
            return None
        return _keep(slug, files, by, note)


def catch_up(slug):
    """Keep the changes made outside the studio, if any, or the files as first opened here. Returns the version
    kept, or None."""
    with specs.LOCK:
        if not isinstance(slug, str) or slug not in specs.slugs():
            return None
        files = _current(slug)
        newest = _newest(slug)
        if newest is None:
            return _keep(slug, files, "studio", "As first opened here")
        if newest["content"] == specs.hash_files(files):
            return None
        return _keep(slug, files, "outside", "Changed outside the studio")


def on_run(slug, moment, note=""):
    """For the hooks around a Claude run that may write dashboards/<slug>: "before" keeps what is there, "after"
    keeps Claude's change with the note. A failure is logged, never raised: the run's work matters more."""
    try:
        if moment == "before":
            return catch_up(slug)
        if moment == "after":
            return record(slug, "claude", note or "Claude's change")
    except Exception as exc:  # noqa: BLE001
        print(f"{datetime.now():%Y-%m-%d %H:%M:%S} versions of {slug!r} ({moment}) not kept: {exc!r}", file=sys.stderr,
              flush=True)
    return None


def _name(slug):
    try:
        raw = json.loads((config.DASHBOARDS_DIR / slug / "dashboard.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = None
    name = raw.get("name") if isinstance(raw, dict) else None
    return name.strip() if isinstance(name, str) and name.strip() else slug


def go_back(slug, version_id):
    """Put a dashboard's files back as they were in one of its versions: its dashboard.json and queries, and no
    query it did not have. The files as they were just before are kept first, and going back is kept as a
    version too, so it can be undone. Returns (that version, whether anything changed). Raises HistoryError."""
    with specs.LOCK:
        if not isinstance(slug, str) or slug not in specs.slugs():
            raise HistoryError("That dashboard is no longer in the studio.")
        target = _read(specs.history_dir(slug) / f"{version_id}.json") if isinstance(version_id, str) and _ID.match(version_id) else None
        if target is None:
            raise HistoryError("That version is not in this dashboard’s history.")
        catch_up(slug)
        folder = config.DASHBOARDS_DIR / slug
        before = _current(slug)
        wanted = _bytes(target["files"])
        if specs.hash_files(before) == target["content"]:
            return target, False
        name = _name(slug)
        try:
            for file, body in wanted.items():
                if before.get(file) != body:
                    _write(folder / file, body)
            for file in before:
                if file not in wanted:
                    (folder / file).unlink()
        except OSError as exc:
            _restore(folder, before, wanted)
            if isinstance(exc, PermissionError):
                raise HistoryError(f"A file of “{name}” is open in another program. Close it and try again.") from exc
            raise HistoryError(f"“{name}” could not be changed just now. Try again.") from exc
        record(slug, "you", f"Went back to {label(target['at'])}")
        return target, True


def _restore(folder, before, wanted):
    """After a Go back that failed half way: the files as they were, as far as they can be put back."""
    for file, body in before.items():
        try:
            if not (folder / file).is_file() or (folder / file).read_bytes() != body:
                _write(folder / file, body)
        except OSError:
            continue
    for file in set(wanted) - set(before):
        try:
            (folder / file).unlink(missing_ok=True)
        except OSError:
            continue


def listing(slug):
    """A dashboard's versions as its settings show them, newest first. now: the newest one the files on disk
    match; live: the newest one Metabase was last sent."""
    with specs.LOCK:
        if not isinstance(slug, str) or slug not in specs.slugs():
            return []
        listed = versions(slug)
        now = specs.hash_files(_current(slug)) if listed else None
        live = _live(slug)
        out, seen_now, seen_live = [], False, False
        for version in listed:
            is_now = not seen_now and version["content"] == now
            is_live = not seen_live and live is not None and version["content"] == live
            seen_now, seen_live = seen_now or is_now, seen_live or is_live
            out.append({"id": version["id"], "at": version["at"], "by": version["by"], "note": version["note"],
                        "files": len(version["files"]), "now": is_now, "live": is_live})
        return out
