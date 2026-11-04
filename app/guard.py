"""Accepts one plain SELECT and nothing else.

The database layer also runs every statement as a subquery inside a read-only
transaction, so this is the first of three checks, not the only one.
"""
import re


class Rejected(ValueError):
    pass


_DOLLAR_TAG = re.compile(r"\$[A-Za-z_]*\$")
_STARTS_WITH_SELECT = re.compile(r"^[\s(]*(select|with)\b", re.I)
_BLOCKED_CALL = re.compile(
    r"\b(set_config|pg_terminate_backend|pg_cancel_backend|pg_sleep\w*|dblink\w*|lo_\w+"
    r"|pg_read_\w+|pg_ls_\w+|pg_stat_file|pg_advisory\w*|nextval|setval|pg_reload_conf"
    r"|pg_rotate_logfile|pg_switch_wal|pg_create_\w+|pg_drop_\w+|pg_logical_\w+"
    r"|pg_replication_\w+|(query|table|cursor|schema|database)_to_xml\w*)\s*\(",
    re.I,
)
# Columns whose values are hidden, told by their name. A personal one is shown when the user
# says so for that card; a secret one never is.
PERSONAL_COLUMN = re.compile(r"phone|mobile|msisdn|e_?mail|aadhaa?r|(^|_)pan(_|$)", re.I)
SECRET_COLUMN = re.compile(r"otp|token|passw|secret|api_?key|(^|_)pin(_|$)", re.I)


def _code_only(sql):
    """The statement with comments, string literals and quoted names blanked out."""
    out = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        two = sql[i : i + 2]
        if two == "--":
            end = sql.find("\n", i)
            i = n if end == -1 else end
        elif two == "/*":
            end = sql.find("*/", i + 2)
            if end == -1:
                raise Rejected("Unterminated /* comment.")
            out.append(" ")
            i = end + 2
        elif ch == "'":
            # Backslash escapes a quote only in E'...' strings.
            escapes = i > 0 and sql[i - 1] in "eE" and not (i > 1 and (sql[i - 2].isalnum() or sql[i - 2] == "_"))
            j = i + 1
            while True:
                if j >= n:
                    raise Rejected("Unterminated string.")
                if escapes and sql[j] == "\\":
                    j += 2
                elif sql[j] == "'":
                    if sql[j + 1 : j + 2] == "'":
                        j += 2
                    else:
                        break
                else:
                    j += 1
            out.append("''")
            i = j + 1
        elif ch == '"':
            end = sql.find('"', i + 1)
            while end != -1 and sql[end + 1 : end + 2] == '"':
                end = sql.find('"', end + 2)
            if end == -1:
                raise Rejected("Unterminated quoted name.")
            out.append('"q"')
            i = end + 1
        elif ch == "$" and (tag := _DOLLAR_TAG.match(sql, i)):
            end = sql.find(tag.group(), tag.end())
            if end == -1:
                raise Rejected("Unterminated dollar-quoted string.")
            out.append("''")
            i = end + len(tag.group())
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def check(sql):
    """Return the statement ready to run, or raise Rejected."""
    if not isinstance(sql, str) or not sql.strip():
        raise Rejected("The query is empty.")
    clean = sql.strip()
    code = _code_only(clean).rstrip()
    if code.endswith(";"):
        if not clean.endswith(";"):
            raise Rejected("Remove the text after the final semicolon.")
        clean = clean[:-1].rstrip()
        code = code[:-1]
    if ";" in code:
        raise Rejected("Only one statement is allowed.")
    if not _STARTS_WITH_SELECT.match(code):
        raise Rejected("Only SELECT (or WITH ... SELECT) is allowed.")
    blocked = _BLOCKED_CALL.search(code)
    if blocked:
        raise Rejected(f"The function {blocked.group(1)}() is not allowed.")
    return clean


def sensitivity(name):
    """"secret", "personal" or None for a column of this name."""
    if SECRET_COLUMN.search(name or ""):
        return "secret"
    return "personal" if PERSONAL_COLUMN.search(name or "") else None


def is_pii_column(name):
    return sensitivity(name) is not None
