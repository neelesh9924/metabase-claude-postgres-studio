"""Paths asked for from outside the studio: by a request, or by Claude through a tool."""
from urllib.parse import unquote


def parts_of(relative):
    """A path asked for inside a folder, as its parts; None for one that could leave it: absolute, with a
    drive or a share (//host/share), a "..", a hidden name, or a stream (name:stream). Checked as text,
    before the filesystem is touched: resolving or opening //host/share has Windows connect to that host,
    and offer it the user's sign-in."""
    text = unquote(relative)
    if not text or text.startswith("/") or any(c in text for c in "\\:\0"):
        return None
    parts = text.split("/")
    if any(not part or part.startswith(".") for part in parts):
        return None
    return parts
