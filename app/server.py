"""Local server for the page and for Claude's tools. Listens on 127.0.0.1 only.

Other websites open in the same browser are kept away from it:
- Host must be exactly 127.0.0.1:<port>.
- Every request except /health, /auth and /static needs the per-launch session cookie,
  which /auth sets from the token in the launcher's link.
- Every request that changes something needs Origin (or Referer) on this origin.
/mcp is for Claude alone and takes the running job's own token instead of the cookie.

The page never sends SQL: it names a dashboard and a card, and the server runs the
SQL it reads from that card's file. The page never receives a saved password or key.
"""
import hmac
import json
import mimetypes
import os
import secrets
import sys
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import checks, config, db, filters, guard, mcp, metabase, schema, settings, specs
from .assistant import Assistant

COOKIE = "studio_session"
_TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}
DENIED_PAGE = f"""<!doctype html><html lang="en"><meta charset="utf-8"><title>{config.APP_NAME}</title>
<link rel="icon" type="image/svg+xml" href="/static/icon.svg"><script src="/static/theme.js"></script>
<link rel="stylesheet" href="/static/app.css">
<body><div class="empty"><h2>Open the studio from its shortcut</h2>
<p>This page only works in the window the launcher opens. If you closed it, start the studio again.</p>
</div></body></html>"""


def _same(a, b):
    return bool(a) and hmac.compare_digest(a.encode(), b.encode())


def databases_state():
    """The databases as the page lists them. A new dashboard can be made on the ready ones."""
    return [{"id": e["id"], "name": e["name"],
             "ready": config.database_complete(e) and schema.summary(e["id"]) is not None} for e in config.DATABASES]


def setup_state():
    tables = next((t for t in (schema.summary(e["id"]) for e in config.DATABASES) if t), None)
    return {"database": settings.database_ready(), "metabase": settings.metabase_ready(),
            "claude": bool(config.claude_bin()), "tables": tables}


class Studio:
    """What one running server shares between requests."""

    def __init__(self, token, runner=None):
        self.token = token
        self.instance = secrets.token_hex(8)
        self.origin = f"http://127.0.0.1:{config.PORT}"
        self.assistant = Assistant(config.PORT, runner)

    def close(self):
        self.assistant.shutdown()
        db.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "Studio"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    @property
    def studio(self):
        return self.server.studio

    # ---- replies ----

    def _send(self, status, body=b"", content_type="application/json; charset=utf-8", headers=()):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status=200):
        self._send(status, json.dumps(payload, default=str).encode("utf-8"))

    def _static(self, relative):
        path = (config.STATIC_DIR / relative).resolve()
        if not path.is_relative_to(config.STATIC_DIR.resolve()) or not path.is_file():
            return self._json({"error": "Not found."}, 404)
        content_type = _TYPES.get(path.suffix) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/"):
            content_type += "; charset=utf-8"
        self._send(200, path.read_bytes(), content_type)

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return None
        return data

    # ---- who may ask ----

    def _host_ok(self):
        return self.headers.get("Host", "") == f"127.0.0.1:{config.PORT}"

    def _signed_in(self):
        jar = SimpleCookie(self.headers.get("Cookie", ""))
        return COOKIE in jar and _same(jar[COOKIE].value, self.studio.token)

    def _own_origin(self):
        origin = self.headers.get("Origin")
        if origin is not None:
            return origin == self.studio.origin
        referer = urlparse(self.headers.get("Referer", ""))
        return f"{referer.scheme}://{referer.netloc}" == self.studio.origin

    # ---- routes ----

    def do_GET(self):
        if not self._host_ok():
            return self._json({"error": "Wrong host name."}, 421)
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path == "/health":
            return self._json({"ok": True, "app": config.APP_ID, "instance": self.studio.instance, "pid": os.getpid()})
        if url.path.startswith("/static/"):
            return self._static(url.path[len("/static/"):])
        if url.path == "/auth":
            if not _same(query.get("token", [""])[0], self.studio.token):
                return self._send(403, DENIED_PAGE.encode("utf-8"), "text/html; charset=utf-8")
            cookie = f"{COOKIE}={self.studio.token}; HttpOnly; SameSite=Strict; Path=/"
            return self._send(303, headers=(("Location", "/"), ("Set-Cookie", cookie)))
        if url.path == "/mcp":
            return self._send(405)
        if not self._signed_in():
            if url.path.startswith("/api/"):
                return self._json({"error": "No valid session for this launch."}, 401)
            return self._send(401, DENIED_PAGE.encode("utf-8"), "text/html; charset=utf-8")
        if url.path == "/":
            return self._static("index.html")
        if url.path == "/api/state":
            found = [d for d in (specs.summary(s) for s in specs.slugs()) if d]
            for dashboard in found:
                dashboard["metabase"] = metabase.remembered(dashboard["slug"])
            return self._json({"dashboards": found, "assistant": self.studio.assistant.state(),
                               "drafts": self.studio.assistant.drafts(),
                               "setup": setup_state(), "databases": databases_state(), "app": config.APP_NAME})
        if url.path == "/api/live":
            # Asks Metabase whether a published dashboard is still there. Metabase only, never the database.
            slug = query.get("slug", [""])[0]
            return self._json({"state": metabase.status(slug) if specs.load(slug) else None})
        if url.path == "/api/dashboard":
            spec = specs.load(query.get("slug", [""])[0])
            if spec is None:
                return self._json({"error": "No such dashboard."}, 404)
            try:
                picked = json.loads(query.get("values", ["null"])[0])
            except ValueError:
                picked = None
            spec["values"] = specs.effective(spec, picked)
            spec["data"] = {}
            for card in spec["cards"]:
                if not card["sql"]:
                    continue
                try:
                    runs = specs.query(card, spec, spec["values"])
                except filters.FilterError as exc:
                    card["filter_error"] = str(exc)
                    continue
                # The page tells a changed card by this: the query as it runs for the filters picked.
                card["sql_hash"] = db.sql_hash(runs, spec["database"], card["shown"])
                spec["data"][card["key"]] = db.cache_get(runs, spec["database"], card["shown"])
            spec["options"] = {}
            for entry in spec["filters"]:
                listing = specs.options_query(spec, entry["key"])
                found = db.cache_get(listing, spec["database"]) if listing else None
                spec["options"][entry["key"]] = [row[0] for row in found["rows"]] if found else None
            spec["limits"] = {"plan_cost": config.MAX_PLAN_COST, "rows": config.PREVIEW_ROW_LIMIT}
            spec["metabase"] = metabase.remembered(spec["slug"])
            return self._json(spec)
        if url.path == "/api/thread":
            key = query.get("key", [""])[0]
            return self._json({"key": key, "messages": self.studio.assistant.thread(key)})
        if url.path == "/api/settings":
            return self._json({"settings": settings.public(), "setup": setup_state()})
        return self._json({"error": "Not found."}, 404)

    def do_POST(self):
        if not self._host_ok():
            return self._json({"error": "Wrong host name."}, 421)
        path = urlparse(self.path).path
        if path == "/mcp":
            return self._mcp()
        if not self._signed_in():
            return self._json({"error": "No valid session for this launch."}, 401)
        if not self._own_origin() or "application/json" not in self.headers.get("Content-Type", ""):
            return self._json({"error": "Request from another site was blocked."}, 403)
        body = self._body()
        if not isinstance(body, dict):
            return self._json({"error": "Bad request."}, 400)
        assistant = self.studio.assistant
        if path == "/api/run":
            return self._run_card(body)
        if path == "/api/options":
            return self._options(body)
        if path == "/api/show":
            return self._show(body)
        if path == "/api/remove":
            return self._remove(body)
        if path.startswith("/api/golive") or path == "/api/open":
            return self._go_live(path, body)
        if path.startswith("/api/settings"):
            return self._settings(path, body)
        if path == "/api/ask":
            error = assistant.ask(body.get("mode"), body.get("slug"), body.get("text"), body.get("database"), body.get("draft"))
        elif path == "/api/build":
            error = assistant.build(body.get("plan"), body.get("draft"))
        elif path == "/api/draft/remove":
            error = assistant.remove_draft(body.get("key"))
        elif path == "/api/stop":
            error = assistant.stop()
        elif path == "/api/clear":
            error = assistant.clear(body.get("key"))
        else:
            return self._json({"error": "Not found."}, 404)
        return self._json({"error": error} if error else {"ok": True})

    def _run_card(self, body):
        spec = specs.load(body.get("slug"))
        card = next((c for c in (spec or {}).get("cards", []) if c["key"] == body.get("key")), None)
        if card is None or not card["sql"]:
            return self._json({"error": "No such card."}, 404)
        source = f"preview {spec['slug']}/{card['key']}"
        try:
            runs = specs.query(card, spec, body.get("values"))
            result = db.run(runs, allow_heavy=bool(body.get("allow_heavy")), source=source, database=spec["database"],
                            reveal=card["shown"])
        except db.Heavy as exc:
            return self._json({"heavy": {"cost": exc.cost, "limit": config.MAX_PLAN_COST}})
        except (db.QueryError, guard.Rejected, filters.FilterError) as exc:
            return self._json({"error": str(exc)})
        db.cache_put(runs, result, spec["database"], card["shown"])
        return self._json({"result": result, "sql_hash": db.sql_hash(runs, spec["database"], card["shown"])})

    def _show(self, body):
        """The user's say on showing personal data in one card. Only the page can ask; Claude has no way to."""
        spec = specs.load(body.get("slug"))
        card = next((c for c in (spec or {}).get("cards", []) if c["key"] == body.get("key")), None)
        if card is None or not card["sql"]:
            return self._json({"error": "No such card."}, 404)
        columns = [c for c in body.get("columns") or [] if isinstance(c, str)] if isinstance(body.get("columns"), list) else []
        if body.get("show") is not True:
            specs.show(spec["slug"], card["key"], columns, False)
            db.cache_forget_shown()
            return self._json({"ok": True})
        secret = [c for c in columns if guard.sensitivity(c) == "secret"]
        if secret:
            return self._json({"error": f"{', '.join(secret)} cannot be shown: passwords, codes and tokens stay hidden."})
        personal = [c for c in columns if guard.sensitivity(c) == "personal"]
        if not personal:
            return self._json({"error": "There is nothing hidden to show."})
        specs.show(spec["slug"], card["key"], personal, True)
        return self._json({"ok": True})

    def _options(self, body):
        """The choices of one filter, from the query its dashboard names for them."""
        spec = specs.load(body.get("slug"))
        listing = specs.options_query(spec, body.get("key")) if spec else None
        if not listing:
            return self._json({"error": "This filter has no list."})
        try:
            result = db.run(listing, limit=1000, source=f"preview {spec['slug']}/list:{body.get('key')}",
                            database=spec["database"])
        except (db.QueryError, guard.Rejected, db.Heavy) as exc:
            return self._json({"error": str(exc)})
        db.cache_put(listing, result, spec["database"])
        return self._json({"options": [row[0] for row in result["rows"]]})

    def _remove(self, body):
        """Take a dashboard out of the studio. Metabase is not touched."""
        slug = body.get("slug")
        job = self.studio.assistant.job
        if job and job.status == "running" and slug in (job.slug, job.key):
            return self._json({"error": "Claude is still working on this dashboard."})
        showed = bool(specs.shown(slug)) if isinstance(slug, str) else False
        try:
            moved = specs.remove(slug)
        except OSError:
            return self._json({"error": "The folder could not be moved just now. Try again."})
        if moved is None:
            return self._json({"error": "No such dashboard."})
        self.studio.assistant.discard(slug, moved)
        metabase.forget(slug)
        if showed:
            db.cache_forget_shown()
        return self._json({"ok": True})

    def _go_live(self, path, body):
        slug = body.get("slug")
        job = self.studio.assistant.job
        if job and job.status == "running" and job.slug == slug:
            return self._json({"error": "Claude is still working on this dashboard."})
        try:
            if path == "/api/golive/plan":
                return self._json({"plan": metabase.plan(slug)})
            if path == "/api/golive":
                return self._json({"published": metabase.publish(slug, sensitive_ok=body.get("sensitive_ok") is True)})
            # Opens only the link Go live recorded, in the browser where the user is signed in to Metabase.
            link = specs.published(slug).get("url") if specs.load(slug) else None
            if not link or not config.METABASE_URL or not link.startswith(config.METABASE_URL.rstrip("/") + "/dashboard/"):
                return self._json({"error": "This dashboard is not live yet."})
            webbrowser.open(link)
            return self._json({"ok": True})
        except metabase.MetabaseError as exc:
            return self._json({"error": str(exc)})

    def _settings(self, path, body):
        values = body.get("values") if isinstance(body.get("values"), dict) else {}
        if path == "/api/settings/check":
            what = body.get("what")
            if what == "database":
                return self._json(checks.database(values))
            if what == "metabase":
                return self._json(checks.metabase_side(values))
            if what == "claude":
                if body.get("run") and self.studio.assistant.busy():
                    return self._json({"ok": False, "error": "Claude is busy with a request. Try again when it is done."})
                return self._json(checks.claude(run=bool(body.get("run"))))
            return self._json({"error": "Not found."}, 404)
        if self.studio.assistant.busy():
            return self._json({"problems": ["Claude is working. Stop it or wait, then save."]})
        if path == "/api/settings/tables":
            # Reads the catalog only: table and column names, never a table's rows.
            try:
                found = schema.snapshot(str(body.get("database") or "") or None)
            except db.QueryError as exc:
                return self._json({"error": str(exc)})
            return self._json({"tables": len(found["tables"]), "settings": settings.public(), "setup": setup_state()})
        if path == "/api/settings/database":
            problems, ident = settings.save_database(values)
            if problems:
                return self._json({"problems": problems})
            return self._json({"ok": True, "id": ident, "settings": settings.public(), "setup": setup_state()})
        if path == "/api/settings/database/remove":
            problems = settings.remove_database(str(body.get("database") or ""))
            if problems:
                return self._json({"problems": problems})
            return self._json({"ok": True, "settings": settings.public(), "setup": setup_state()})
        if path == "/api/settings":
            problems = settings.save(values)
            return self._json({"problems": problems} if problems else {"ok": True, "settings": settings.public(), "setup": setup_state()})
        return self._json({"error": "Not found."}, 404)

    def _mcp(self):
        scheme, _, token = self.headers.get("Authorization", "").partition(" ")
        job = self.studio.assistant.job_for_token(token) if scheme == "Bearer" else None
        if job is None:
            self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
            return self._json({"error": "No running request for this token."}, 401)
        message = self._body()
        if message is None:
            return self._json({"error": "Bad request."}, 400)
        reply = mcp.handle(message, job)
        if reply is None:
            return self._send(202)
        return self._json(reply)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # A browser that closes a connection early is not worth a traceback in the log.
        if not isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            super().handle_error(request, client_address)


def make_server(token, runner=None):
    httpd = Server(("127.0.0.1", config.PORT), Handler)
    httpd.studio = Studio(token, runner)
    db.start_idle_closer()
    return httpd


def serve(open_browser=True):
    """Run in a terminal, with the page in an ordinary browser tab."""
    token = secrets.token_urlsafe(32)
    try:
        httpd = make_server(token)
    except OSError:
        print(f"Port {config.PORT} is already in use. Is the studio already open?")
        return 1
    link = f"http://127.0.0.1:{config.PORT}/auth?token={token}"
    print(f"{config.APP_NAME} is running. Open this link (valid until it stops):\n  {link}")
    print("It queries the database only when a card is loaded or refreshed, or when Claude is asked to. "
          "Press Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(link)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        httpd.studio.close()
    return 0
