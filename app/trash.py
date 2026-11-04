"""The Trash: what was taken out of the studio, in data/trash, and the two ways out of it.

What an entry is, told by what it holds rather than by its name alone:
- dashboard: a folder with dashboard.json (Remove): its queries and metabase.json, with conversation.json,
  attachments/ and history/ (its versions) beside them;
- draft: <draft key>-<when>.json, its conversation, with the folder of the same name for its attachments/;
- chat: a folder with conversation.json and no dashboard.json, a dashboard's cleared conversation
  (<slug>-chat-<when>), with its attachments/;
- other: anything else, such as the attachments-only folder an older Clear left. It can only be deleted.

put_back() returns one to where it came from; every move is an os.replace, and when one fails, what already
moved goes back. delete() and empty() delete forever, inside data/trash only, never through a link out of
it. All of it runs under the dashboards' lock (specs.LOCK).
"""
import json
import os
import re
import shutil
import stat
import sys
from datetime import datetime

from . import config, paths, specs
from .assistant import DRAFT, Assistant, new_draft_key

_STAMPED = re.compile(r"^(.+)-(\d{8}-\d{6})(?:-\d+)?$")   # <what>-<YYYYmmdd-HHMMSS>, -2 for a second in one second
_DRAFT_KEY = re.compile(rf"^{DRAFT}[a-z0-9]{{6,32}}$")


class TrashError(Exception):
    """Why something cannot come out of the trash, or be deleted, in words for the page."""


def _paths():
    """{name: path} of everything in data/trash but hidden names."""
    try:
        return {p.name: p for p in specs.trash_dir().iterdir() if not p.name.startswith(".")}
    except OSError:
        return {}


def _split(name):
    """(what it was, when it was removed as ISO text or "") from an entry's name, its extension left out."""
    found = _STAMPED.match(name)
    if not found:
        return name, ""
    try:
        return found.group(1), datetime.strptime(found.group(2), "%Y%m%d-%H%M%S").isoformat(timespec="seconds")
    except ValueError:
        return found.group(1), ""


def _json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _messages(path):
    found = _json(path)
    return [m for m in found if isinstance(m, dict)] if isinstance(found, list) else []


def _size(path):
    try:
        if not path.is_dir():
            return path.stat().st_size
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    except OSError:
        return 0


def _count_files(folder):
    """The files a conversation attached: everything under attachments/ but the hidden readable copies."""
    try:
        return sum(1 for p in folder.rglob("*") if p.is_file() and not any(
            part.startswith(".") for part in p.relative_to(folder).parts))
    except OSError:
        return 0


def _database(ident):
    """(its name, a note when it is no longer in Settings) for a database id; the first database for none."""
    entry = config.database(ident) if ident else config.database()
    if entry:
        return entry["name"], ""
    if ident:
        return str(ident), f"Its database “{ident}” is no longer in Settings."
    return "", ""


def _is_draft(path):
    return path.is_file() and path.suffix == ".json" and bool(_DRAFT_KEY.match(_split(path.stem)[0])) and isinstance(_json(path), list)


def _slug_from(text):
    return re.sub(r"[^a-z0-9_]+", "_", str(text).lower()).strip("_")[:48] or "dashboard"


def _taken(slug, busy=()):
    """Whether a slug is in use: by a dashboard, a conversation, attached files, versions, or the running Claude."""
    return slug in busy or any(p.exists() for p in (config.DASHBOARDS_DIR / slug, config.THREADS_DIR / f"{slug}.json",
                                                    config.ATTACH_DIR / slug, specs.history_dir(slug)))


def _free_slug(wanted, busy=()):
    slug, n = wanted, 2
    while _taken(slug, busy):
        slug, n = f"{wanted}_{n}", n + 1
    return slug


def _dashboard_name(slug):
    found = specs.summary(slug)
    return found["name"] if found else slug


def _entry(path, listing):
    """One thing in the trash as the Trash shows it, or None for a draft's own folder (it goes with the draft)."""
    named, removed = _split(path.stem if path.is_file() else path.name)
    if not removed:
        try:
            removed = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
        except OSError:
            removed = ""
    out = {"key": path.name, "kind": "other", "name": path.name, "at": removed,
           "size": 0, "database_name": "", "cards": 0, "live": False, "messages": 0, "files": 0,
           "note": "It is not something the studio can put back. It can only be deleted."}
    if _is_link(path):   # never followed: what it points to is not the trash's
        return out
    out["size"] = _size(path)
    if path.is_dir() and (path / "dashboard.json").is_file():
        raw = _json(path / "dashboard.json")
        raw = raw if isinstance(raw, dict) else {}
        published = _json(path / specs.PUBLISHED_FILE)
        wanted = named if specs._SLUG.match(named) else _slug_from(raw.get("name") or named)
        database, note = _database(str(raw["database"]) if raw.get("database") else None)
        free = _free_slug(wanted)
        if not note and free != wanted:
            note = f"“{wanted}” is taken now: it comes back as {free}."
        name = raw.get("name") if isinstance(raw.get("name"), str) and raw["name"].strip() else wanted
        return {**out, "kind": "dashboard", "name": name.strip(), "database_name": database, "note": note,
                "cards": len(raw["cards"]) if isinstance(raw.get("cards"), list) else 0,
                "live": isinstance(published, dict) and bool(published.get("dashboard_id")),
                "messages": len(_messages(path / "conversation.json")), "files": _count_files(path / "attachments")}
    if path.is_dir() and (path / "conversation.json").is_file():
        slug = named[:-len("-chat")] if named.endswith("-chat") else named
        messages = _messages(path / "conversation.json")
        here = slug in specs.slugs()
        about = _dashboard_name(slug) if here else slug
        note = "" if here else f"Its dashboard “{slug}” is not in the studio. Put that back first."
        if here and (Assistant._describe(slug, _messages(config.THREADS_DIR / f"{slug}.json")) or (config.ATTACH_DIR / slug).exists()):
            note = f"“{about}” has a newer conversation. Clear it first, then put this one back."
        return {**out, "kind": "chat", "name": f"Conversation about {about} ({len(messages)} {'message' if len(messages) == 1 else 'messages'})",
                "database_name": (specs.summary(slug) or {}).get("database_name", "") if here else "", "note": note,
                "messages": len(messages), "files": _count_files(path / "attachments")}
    if _is_draft(path):
        messages = _messages(path)
        described = Assistant._describe(named, messages)
        database, note = _database(next((str(m["database"]) for m in messages if m.get("database")), None))
        paired = listing.get(path.stem)
        return {**out, "kind": "draft", "name": described["title"] if described else "New dashboard", "database_name": database,
                "note": note, "messages": len(messages), "size": out["size"] + (_size(paired) if paired else 0),
                "files": _count_files(paired / "attachments") if paired and paired.is_dir() else 0}
    paired = listing.get(f"{path.name}.json")
    if path.is_dir() and paired is not None and _is_draft(paired):
        return None
    if path.is_dir() and (path / "attachments").is_dir():
        out["name"] = f"Files from a cleared conversation about {named}"
        out["note"] = "An older version of the studio kept them without the conversation. They can only be deleted."
        out["files"] = _count_files(path / "attachments")
    return out


def entries():
    """Everything in the trash, newest first."""
    with specs.LOCK:
        listing = _paths()
        found = [e for e in (_entry(path, listing) for path in listing.values()) if e]
    return sorted(found, key=lambda e: (e["at"], e["key"]), reverse=True)


# ---- putting one back ----

def _move_all(moves, name):
    """Make every move, or none: when one fails, what already moved goes back. Raises TrashError."""
    done = []
    try:
        for source, target in moves:
            target.parent.mkdir(parents=True, exist_ok=True)
            specs.replace(source, target)
            done.append((source, target))
    except OSError as exc:
        for source, target in reversed(done):
            try:
                specs.replace(target, source)
            except OSError:
                continue
        if isinstance(exc, PermissionError):
            raise TrashError(f"A file of “{name}” is open in another program. Close it and try again.") from exc
        raise TrashError(f"“{name}” could not be put back just now. Try again.") from exc


def _tidy(folder):
    """A trash folder left empty once its parts moved out."""
    try:
        folder.rmdir()
    except OSError:
        pass


def put_back(key, busy=()):
    """Put one entry back where it came from. busy: the slugs and keys the running Claude works on. Returns
    {kind, name, slug (a dashboard or a chat's) or key (a draft's), moved (it came back under a new name)}."""
    with specs.LOCK:
        listing = _paths()
        path = listing.get(key) if isinstance(key, str) and paths.parts_of(key) == [key] else None
        entry = _entry(path, listing) if path is not None else None
        if entry is None:
            raise TrashError("That is no longer in the Trash.")
        if _is_link(path):
            raise TrashError("This can only be deleted.")
        named = _split(path.stem if path.is_file() else path.name)[0]
        if entry["kind"] == "dashboard":
            wanted = named if specs._SLUG.match(named) else _slug_from(entry["name"])
            if wanted in busy:
                raise TrashError("Claude is working on it. Stop it or wait.")
            slug = _free_slug(wanted, busy)
            moves = [(path / name, target) for name, target in (
                ("conversation.json", config.THREADS_DIR / f"{slug}.json"), ("attachments", config.ATTACH_DIR / slug),
                ("history", specs.history_dir(slug))) if (path / name).exists()]
            # The choice to show personal data stays behind: shown again only on the user's word.
            specs._shown_file(slug).unlink(missing_ok=True)
            _move_all(moves + [(path, config.DASHBOARDS_DIR / slug)], entry["name"])
            return {"kind": "dashboard", "name": entry["name"], "slug": slug, "moved": slug != wanted}
        if entry["kind"] == "draft":
            if named in busy:
                raise TrashError("Claude is working on it. Stop it or wait.")
            draft = named
            while _taken(draft, busy):
                draft = new_draft_key()
            paired = listing.get(path.stem)
            moves = [(path, config.THREADS_DIR / f"{draft}.json")]
            if paired is not None and (paired / "attachments").is_dir():
                moves.append((paired / "attachments", config.ATTACH_DIR / draft))
            _move_all(moves, entry["name"])
            if paired is not None:
                _tidy(paired)
            return {"kind": "draft", "name": entry["name"], "key": draft, "moved": draft != named}
        if entry["kind"] == "chat":
            slug = named[:-len("-chat")] if named.endswith("-chat") else named
            if slug in busy:
                raise TrashError("Claude is working on it. Stop it or wait.")
            if slug not in specs.slugs():
                raise TrashError(f"Its dashboard “{slug}” is not in the studio. Put that back first.")
            about = _dashboard_name(slug)
            files = config.ATTACH_DIR / slug
            if files.is_dir() and not any(files.iterdir()):
                _tidy(files)
            if Assistant._describe(slug, _messages(config.THREADS_DIR / f"{slug}.json")) or files.exists():
                raise TrashError(f"“{about}” has a newer conversation. Clear it first, then put this one back.")
            moves = [(path / "conversation.json", config.THREADS_DIR / f"{slug}.json")]
            if (path / "attachments").is_dir():
                moves.append((path / "attachments", files))
            _move_all(moves, about)
            _tidy(path)
            return {"kind": "chat", "name": entry["name"], "slug": slug, "moved": False}
        raise TrashError("This can only be deleted.")


# ---- deleting forever ----

def _writable(func, path, exc):
    """shutil.rmtree's second try at a file Windows keeps read-only; one already gone is fine."""
    if isinstance(exc, FileNotFoundError):
        return
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _is_link(path):
    return path.is_symlink() or getattr(os.path, "isjunction", lambda _: False)(path)


def _delete(path):
    """Delete one thing in the trash for good. A link or junction goes itself: what it points to stays."""
    if _is_link(path):
        try:
            os.rmdir(path)
        except OSError:
            os.unlink(path)
    elif path.is_dir():
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_writable)
        else:
            shutil.rmtree(path, onerror=lambda func, p, info: _writable(func, p, info[1]))
    elif path.exists():
        os.chmod(path, stat.S_IWRITE)
        path.unlink()


def delete(keys):
    """Delete forever the entries named, by their names in data/trash, and a draft's folder with it. A name is
    taken only when it is one direct child of data/trash, checked as text before the disk is touched. Returns
    {count, failed: the names that could not be deleted just now, a file open in another program, say}."""
    wanted = [key for key in dict.fromkeys(keys if isinstance(keys, list) else [])
              if isinstance(key, str) and paths.parts_of(key) == [key]]
    if not wanted:
        raise TrashError("That is no longer in the Trash.")
    with specs.LOCK:
        listing = _paths()
        chosen = [key for key in wanted if key in listing]
        if not chosen:
            raise TrashError("That is no longer in the Trash.")
        done, failed = [], []
        for key in chosen:
            path = listing[key]
            parts = [path] + ([listing[path.stem]] if _is_draft(path) and path.stem in listing else [])
            try:
                for part in parts:
                    if os.path.lexists(part):
                        _delete(part)
                done.append(key)
            except OSError:
                failed.append(key)
    return {"count": len(done), "failed": failed}


def empty(keys=None):
    """Delete everything in the trash forever. keys: what the user saw in it; when it differs, nothing goes."""
    with specs.LOCK:
        listed = [e["key"] for e in entries()]
        if keys is not None and set(keys) != set(listed):
            raise TrashError("The Trash changed since you looked at it. Look at it again, then empty it.")
        if not listed:
            return {"count": 0, "failed": []}
        return delete(listed)
