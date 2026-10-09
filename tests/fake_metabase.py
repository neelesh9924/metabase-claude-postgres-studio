"""A small in-memory Metabase for tests: the endpoints Go live and doctor use, nothing more."""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEY = "mb_test"
DATABASE = 7    # the database the studio is set up to use
OURS = 5        # the collection the key may write to
THEIRS = 6      # somebody else's collection: readable, not writable


class FakeMetabase:
    def __init__(self):
        self.calls = []                  # (method, path, body) of every accepted request
        self.dashboards = {}
        self.cards = {}
        self.collections = {
            OURS: {"id": OURS, "name": "Studio dashboards", "can_write": True, "archived": False, "location": "/"},
            THEIRS: {"id": THEIRS, "name": "OPS", "can_write": False, "archived": False, "location": "/"},
        }
        self.databases = [
            {"id": DATABASE, "name": "Warehouse (read-only)", "native_permissions": "write"},
            {"id": 8, "name": "Marketing", "native_permissions": "write"},
        ]
        self.fields = [
            {"id": 501, "schema": "public", "table_name": "orders", "name": "created_at"},
            {"id": 502, "schema": "public", "table_name": "orders", "name": "status"},
        ]
        self.report_timezone = "Asia/Kolkata"
        self.dashboard_questions = True  # False: a Metabase that refuses dashboard_id on a card
        self.fail = None                 # (method, path, nth): answer 500 to the nth such request, once
        self._seen = {}
        self._next = 100
        self._tick = 0
        self._lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, status, payload=None):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _handle(self, method):
                length = int(self.headers.get("Content-Length", "0") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                if self.headers.get("x-api-key") != KEY:
                    return self._reply(401, {"message": "Unauthenticated"})
                with fake._lock:
                    status, payload = fake.answer(method, self.path, body)
                self._reply(status, payload)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def do_PUT(self):
                self._handle("PUT")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    # ---- helpers for tests ----

    def writes(self):
        return [(m, p, b) for m, p, b in self.calls if m != "GET"]

    def live_cards(self):
        return [c for c in self.cards.values() if not c["archived"]]

    def _id(self):
        self._next += 1
        return self._next

    def _now(self):
        self._tick += 1
        return f"2026-10-09T10:00:{self._tick:02d}Z"

    def _writable(self, collection_id):
        if collection_id is None:
            return True  # "Our analytics": the key may write there, and the studio must never do it
        return self.collections.get(collection_id, {}).get("can_write", False)

    def add_dashboard(self, collection_id, name="Someone's dashboard"):
        dash = {"id": self._id(), "name": name, "description": None, "collection_id": collection_id, "archived": False,
                "can_write": self._writable(collection_id), "dashcards": [], "tabs": [], "updated_at": self._now()}
        self.dashboards[dash["id"]] = dash
        return dash

    # ---- the API ----

    def answer(self, method, path, body):
        self.calls.append((method, path, body))
        count = self._seen[(method, path)] = self._seen.get((method, path), 0) + 1
        if self.fail and self.fail[:2] == (method, path) and self.fail[2] == count:
            self.fail = None
            return 500, {"message": "boom"}
        if (method, path) == ("GET", "/api/user/current"):
            return 200, {"id": 1, "common_name": "studio-key", "is_superuser": False}
        if (method, path) == ("GET", "/api/session/properties"):
            return 200, {"report-timezone-long": self.report_timezone}
        if (method, path) == ("GET", "/api/database"):
            return 200, {"data": self.databases}
        if (method, path) == ("GET", "/api/collection"):
            return 200, [{"id": "root", "name": "Our analytics", "can_write": True}] + list(self.collections.values())
        if (method, path) == ("GET", "/api/collection/root"):
            return 200, {"id": "root", "name": "Our analytics", "can_write": True}
        if (method, path) == ("POST", "/api/dataset"):
            value = "30s" if "statement_timeout" in body["native"]["query"] else "on"
            return 200, {"data": {"rows": [[value]]}}
        if method == "GET" and re.fullmatch(r"/api/database/\d+/fields", path):
            return 200, self.fields
        match = re.fullmatch(r"/api/collection/(\d+)", path)
        if match and method == "GET":
            found = self.collections.get(int(match.group(1)))
            return (200, found) if found else (404, {"message": "Not found."})
        if (method, path) == ("POST", "/api/dashboard"):
            if not self._writable(body.get("collection_id")):
                return 403, {"message": "You don't have permissions to do that."}
            dash = self.add_dashboard(body.get("collection_id"), body["name"])
            dash["description"] = body.get("description")
            return 200, dash
        match = re.fullmatch(r"/api/dashboard/(\d+)", path)
        if match:
            dash = self.dashboards.get(int(match.group(1)))
            if dash is None:
                return 404, {"message": "Not found."}
            if method == "GET":
                return 200, dash
            if not self._writable(dash["collection_id"]):
                return 403, {"message": "You don't have permissions to do that."}
            for field in ("name", "description", "archived", "parameters"):
                if field in body:
                    dash[field] = body[field]
            if "dashcards" in body:
                dash["dashcards"] = [{**dc, "id": dc["id"] if dc["id"] > 0 else self._id()} for dc in body["dashcards"]]
            if "tabs" in body:
                dash["tabs"] = body["tabs"]
            dash["updated_at"] = self._now()
            return 200, dash
        if (method, path) == ("POST", "/api/card"):
            dashboard_id = body.get("dashboard_id")
            if dashboard_id is not None:
                if not self.dashboard_questions:
                    return 400, {"errors": {"dashboard_id": "unknown key"}}
                dash = self.dashboards[dashboard_id]
                collection_id = dash["collection_id"]
                # Like the real thing, a question saved into a dashboard is placed on it straight away.
            else:
                collection_id = body.get("collection_id")
            if not self._writable(collection_id):
                return 403, {"message": "You don't have permissions to do that."}
            card = {**body, "id": self._id(), "archived": False, "dashboard_id": dashboard_id, "collection_id": collection_id}
            self.cards[card["id"]] = card
            if dashboard_id is not None:
                dash["dashcards"].append({"id": self._id(), "card_id": card["id"], "row": 0, "col": 0, "size_x": 4, "size_y": 4})
            return 200, card
        match = re.fullmatch(r"/api/card/(\d+)", path)
        if match:
            card = self.cards.get(int(match.group(1)))
            if card is None:
                return 404, {"message": "Not found."}
            if method == "GET":
                return 200, card
            if not self._writable(card["collection_id"]):
                return 403, {"message": "You don't have permissions to do that."}
            card.update(body)
            return 200, card
        return 404, {"message": f"fake Metabase has no {method} {path}"}
