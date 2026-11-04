"""Keeps the database password and the Metabase key unreadable in the settings file.

On Windows a secret is encrypted with the signed-in user's own key (DPAPI), so the
file is useless on another PC or to another Windows user. Elsewhere it is stored as
it is, in a file only its owner may read.
"""
import base64
import ctypes
import sys
from ctypes import wintypes


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]


def _dpapi(call, payload):
    source = _Blob(len(payload), ctypes.cast(ctypes.create_string_buffer(payload, len(payload)), ctypes.POINTER(ctypes.c_char)))
    result = _Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    if not getattr(crypt32, call)(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)):
        raise OSError(ctypes.get_last_error(), f"{call} failed")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel32.LocalFree(result.data)


def protect(text):
    """What to write to the file for this secret."""
    if not text:
        return ""
    if sys.platform == "win32":
        return {"dpapi": base64.b64encode(_dpapi("CryptProtectData", text.encode("utf-8"))).decode("ascii")}
    return {"plain": text}


def reveal(stored):
    """The secret behind what the file holds. An entry this user cannot open reads as empty."""
    if isinstance(stored, str):
        return stored
    if not isinstance(stored, dict):
        return ""
    if "plain" in stored:
        return str(stored["plain"])
    try:
        return _dpapi("CryptUnprotectData", base64.b64decode(stored["dpapi"])).decode("utf-8")
    except (KeyError, OSError, ValueError, AttributeError):
        return ""
