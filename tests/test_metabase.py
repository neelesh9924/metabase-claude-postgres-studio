import http.client
import json
import unittest
from unittest import mock

from fake_metabase import DATABASE, KEY, OURS, THEIRS, FakeMetabase
from support import StudioCase, canned_result, database, serve

from app import config, db, metabase, specs


class GoLiveCase(StudioCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeMetabase()
        self.addCleanup(self.fake.stop)
        metabase._seen.clear()
        self.databases(database(metabase_id=DATABASE))
        for patch in [mock.patch.object(config, "METABASE_URL", self.fake.url),
                      mock.patch.object(config, "METABASE_API_KEY", KEY),
                      mock.patch.object(config, "METABASE_COLLECTION", "Studio dashboards"),
                      mock.patch.object(config, "METABASE_COLLECTION_ID", None)]:
            patch.start()
            self.addCleanup(patch.stop)

    def make(self, slug="ops", cards=("a", "b"), drawn=True):
        """A dashboard with a heading and one small card per key, each already drawn."""
        folder = self.tmp / "dashboards" / slug
        folder.mkdir(exist_ok=True)
        listed = [{"key": "head", "display": "heading", "text": "Today", "row": 0, "col": 0, "size_x": 24, "size_y": 1}]
        for i, key in enumerate(cards):
            self.query(slug, key, f'select {i} as "N{i}"', drawn)
            listed.append({"key": key, "name": key.upper(), "display": "scalar", "viz": {"scalar.field": f"N{i}"},
                           "row": 1, "col": i * 6, "size_x": 6, "size_y": 3})
        (folder / "dashboard.json").write_text(json.dumps({"name": "Ops", "description": "Daily numbers.", "cards": listed}))

    def query(self, slug, key, sql, drawn=True, result=None):
        (self.tmp / "dashboards" / slug / f"{key}.sql").write_text(sql, encoding="utf-8")
        if drawn:
            db.cache_put(sql, result or canned_result(sql))

    def dashboard(self):
        self.assertEqual(len(self.fake.dashboards), 1)
        return next(iter(self.fake.dashboards.values()))


class GoLiveTest(GoLiveCase):
    def test_first_go_live(self):
        self.make()
        plan = metabase.plan("ops")
        self.assertEqual((plan["mode"], plan["blockers"], plan["cards"]), ("create", [], {"create": 2, "update": 0, "trash": 0}))
        self.assertEqual(plan["target"]["collection"], "Studio dashboards")
        self.assertEqual(self.fake.writes(), [], "looking must not write")

        out = metabase.publish("ops")
        dash = self.dashboard()
        self.assertEqual(out["url"], f"{self.fake.url}/dashboard/{dash['id']}")
        self.assertEqual((dash["name"], dash["description"], dash["collection_id"]), ("Ops", "Daily numbers.", OURS))
        cards = self.fake.live_cards()
        self.assertEqual(sorted(c["name"] for c in cards), ["A", "B"])
        for card in cards:
            self.assertEqual(card["dashboard_id"], dash["id"])
            self.assertEqual(card["dataset_query"]["database"], DATABASE)
            self.assertEqual(card["dataset_query"]["type"], "native")
            self.assertIn("select", card["dataset_query"]["native"]["query"])
        # One heading and one tile per card, each where the studio put it, with no leftover from Metabase's own placing.
        placed = dash["dashcards"]
        self.assertEqual(len(placed), 3)
        self.assertEqual(sorted(dc["card_id"] for dc in placed if dc["card_id"]), sorted(c["id"] for c in cards))
        heading = next(dc for dc in placed if dc["card_id"] is None)
        self.assertEqual(heading["visualization_settings"]["text"], "Today")
        self.assertEqual(heading["visualization_settings"]["virtual_card"]["display"], "heading")
        self.assertEqual([(dc["row"], dc["col"], dc["size_x"], dc["size_y"]) for dc in placed], [(0, 0, 24, 1), (1, 0, 6, 3), (1, 6, 6, 3)])
        # Nothing was run, and nothing was written outside the one collection.
        self.assertFalse([c for c in self.fake.calls if c[1] == "/api/dataset"])
        self.assertTrue(all(d["collection_id"] == OURS for d in self.fake.dashboards.values()))
        self.assertTrue(all(c["collection_id"] == OURS for c in self.fake.cards.values()))
        # The folder remembers what it was published as, and the collection is remembered by id.
        spec = specs.load("ops")
        self.assertTrue(spec["live"])
        self.assertFalse(spec["changed"])
        self.assertEqual(spec["url"], out["url"])
        self.assertEqual(config.METABASE_COLLECTION_ID, OURS)

    def test_second_go_live_updates_in_place(self):
        self.make(cards=("a", "b"))
        metabase.publish("ops")
        first = {c["name"]: c["id"] for c in self.fake.live_cards()}

        self.make(cards=("a", "c"))                       # b removed, c added
        self.query("ops", "a", 'select 42 as "N0"')       # a's query changed
        self.assertTrue(specs.load("ops")["changed"])
        plan = metabase.plan("ops")
        self.assertEqual((plan["mode"], plan["cards"]), ("update", {"create": 1, "update": 1, "trash": 1}))
        self.assertIn("trash", plan["warnings"][0])

        self.assertEqual(metabase.publish("ops")["mode"], "update")
        dash = self.dashboard()
        live = {c["name"]: c for c in self.fake.live_cards()}
        self.assertEqual(sorted(live), ["A", "C"])
        self.assertEqual(live["A"]["id"], first["A"], "an existing card is updated, not replaced")
        self.assertIn("42", live["A"]["dataset_query"]["native"]["query"])
        self.assertTrue(self.fake.cards[first["B"]]["archived"])
        self.assertEqual(sorted(dc["card_id"] for dc in dash["dashcards"] if dc["card_id"]), sorted(c["id"] for c in live.values()))
        self.assertEqual(len(dash["dashcards"]), 3)
        self.assertFalse(specs.load("ops")["changed"])

    def test_a_metabase_without_dashboard_questions(self):
        self.fake.dashboard_questions = False
        self.make()
        metabase.publish("ops")
        cards = self.fake.live_cards()
        self.assertEqual(len(cards), 2)
        self.assertTrue(all(c["dashboard_id"] is None and c["collection_id"] == OURS for c in cards))
        self.assertEqual(len(self.dashboard()["dashcards"]), 3)

    def test_a_failed_go_live_can_be_retried_without_copies(self):
        self.make()
        self.fake.fail = ("POST", "/api/card", 2)
        with self.assertRaises(metabase.MetabaseError):
            metabase.publish("ops")
        self.assertEqual(len(self.fake.dashboards), 1)
        self.assertEqual(len(self.fake.cards), 1)
        metabase.publish("ops")
        self.assertEqual(len(self.fake.dashboards), 1)
        self.assertEqual(sorted(c["name"] for c in self.fake.live_cards()), ["A", "B"])
        self.assertEqual(len(self.fake.cards), 2)
        self.assertEqual(len(self.dashboard()["dashcards"]), 3)

    def test_changed_in_metabase_is_announced(self):
        self.make()
        metabase.publish("ops")
        self.assertEqual(metabase.plan("ops")["warnings"], [])
        self.dashboard()["updated_at"] = "2027-01-01T00:00:00Z"
        self.assertIn("changed in Metabase", metabase.plan("ops")["warnings"][0])

    def test_trashed_in_metabase_means_a_new_one(self):
        self.make()
        metabase.publish("ops")
        old = self.dashboard()
        old["archived"] = True
        plan = metabase.plan("ops")
        self.assertEqual(plan["mode"], "create")
        self.assertIn("trash", plan["warnings"][0])
        metabase.publish("ops")
        self.assertEqual(len(self.fake.dashboards), 2)
        self.assertTrue(old["archived"], "the trashed one is left alone")


class GoLiveRefusalTest(GoLiveCase):
    def refused(self, fragment):
        plan = metabase.plan("ops")
        self.assertTrue(any(fragment in b for b in plan["blockers"]), plan["blockers"])
        with self.assertRaises(metabase.MetabaseError):
            metabase.publish("ops")
        self.assertEqual(self.fake.writes(), [], "a refused Go live must not write anything")

    def test_a_card_that_has_not_drawn(self):
        self.make(drawn=False)
        self.refused("has not drawn yet")

    def test_a_secret_column(self):
        self.make()
        flagged = canned_result("x")
        flagged["columns"] = [{"name": "otp", "type": "text", "pii": True}]
        self.query("ops", "a", 'select 0 as "N0"', result=flagged)
        self.refused("never published (otp)")

    def test_files_with_problems(self):
        self.make()
        (self.tmp / "dashboards" / "ops" / "b.sql").unlink()
        self.refused("b.sql is missing")

    def test_no_key(self):
        self.make()
        with mock.patch.object(config, "METABASE_API_KEY", ""):
            self.refused("not set up yet")

    def test_a_key_metabase_does_not_accept(self):
        self.make()
        with mock.patch.object(config, "METABASE_API_KEY", "mb_wrong"):
            self.refused("did not accept the API key")

    def test_collection_the_key_cannot_see(self):
        self.make()
        del self.fake.collections[OURS]
        self.refused('cannot see a collection named "Studio dashboards"')
        self.assertIn("Curate", metabase.plan("ops")["blockers"][0])

    def test_collection_the_key_cannot_write(self):
        self.make()
        self.fake.collections[OURS]["can_write"] = False
        self.refused("cannot write to it")

    def test_a_database_the_key_cannot_use(self):
        self.make()
        self.fake.databases = [d for d in self.fake.databases if d["id"] != DATABASE]
        self.refused("cannot see the database")


class GoLiveContainmentTest(GoLiveCase):
    """A wrong record must not let Go live touch anything outside the studio's collection."""

    def state(self, **values):
        (self.tmp / "dashboards" / "ops" / "metabase.json").write_text(json.dumps(values))

    def test_a_record_pointing_at_someone_elses_dashboard(self):
        self.make()
        theirs = self.fake.add_dashboard(THEIRS, "Finance")
        root = self.fake.add_dashboard(None, "Board pack")       # sits in "Our analytics", where the key may write
        for foreign in (theirs, root):
            self.state(dashboard_id=foreign["id"], cards={})
            plan = metabase.plan("ops")
            self.assertTrue(any("only writes there" in b for b in plan["blockers"]), plan["blockers"])
            with self.assertRaises(metabase.MetabaseError):
                metabase.publish("ops")
        self.assertEqual(self.fake.writes(), [])
        self.assertEqual((theirs["name"], root["name"]), ("Finance", "Board pack"))

    def test_a_record_pointing_at_someone_elses_card(self):
        self.make()
        metabase.publish("ops")
        self.fake.cards[900] = {"id": 900, "name": "Revenue", "archived": False, "dashboard_id": None, "collection_id": None,
                                "dataset_query": {"native": {"query": "select 1"}}}
        state = specs.published("ops")
        state["cards"]["a"] = 900                                 # a card in "Our analytics" that the studio never made
        state["cards"]["gone"] = 900                              # and the same card listed as one to trash
        self.state(**state)
        metabase.publish("ops")
        untouched = self.fake.cards[900]
        self.assertEqual((untouched["name"], untouched["archived"]), ("Revenue", False))
        self.assertFalse([c for c in self.fake.writes() if c[1] == "/api/card/900"])
        # The dashboard still shows its own two cards; a fresh one was made in place of the wrong record.
        on_dashboard = [dc["card_id"] for dc in self.dashboard()["dashcards"] if dc["card_id"]]
        self.assertNotIn(900, on_dashboard)
        self.assertEqual(sorted(self.fake.cards[i]["name"] for i in on_dashboard), ["A", "B"])

    def test_moved_out_of_the_collection(self):
        self.make()
        metabase.publish("ops")
        self.dashboard()["collection_id"] = THEIRS
        before = len(self.fake.writes())
        self.assertTrue(any("Move it back" in b for b in metabase.plan("ops")["blockers"]))
        with self.assertRaises(metabase.MetabaseError):
            metabase.publish("ops")
        self.assertEqual(len(self.fake.writes()), before)


class StillThereTest(GoLiveCase):
    """After Go live, the studio can tell what became of the dashboard in Metabase."""

    def check(self):
        metabase._seen.clear()  # the answer is kept for a few seconds; tests ask afresh
        return metabase.status("ops")

    def test_what_became_of_it(self):
        self.make()
        self.assertIsNone(metabase.status("ops"), "a draft has no state in Metabase")
        metabase.publish("ops")
        self.assertEqual(metabase.remembered("ops"), "live")
        dash = self.dashboard()
        self.assertEqual(self.check(), "live")
        dash["archived"] = True
        self.assertEqual(self.check(), "trashed")
        self.assertEqual(metabase.remembered("ops"), "trashed")
        dash["archived"] = False
        dash["collection_id"] = THEIRS
        self.assertEqual(self.check(), "moved")
        del self.fake.dashboards[dash["id"]]
        self.assertEqual(self.check(), "gone")
        self.assertEqual(self.fake.writes()[-1][0], "PUT", "asking must not write")
        writes = len(self.fake.writes())
        self.check()
        self.assertEqual(len(self.fake.writes()), writes)

    def test_a_metabase_that_does_not_answer_changes_nothing(self):
        self.make()
        metabase.publish("ops")
        with mock.patch.object(config, "METABASE_URL", "http://127.0.0.1:9"):
            self.assertEqual(metabase.status("ops"), "live")          # still fresh from Go live
            metabase._seen["ops"] = (0, "live")                        # now stale
            self.assertEqual(metabase.status("ops"), "live")
            metabase._seen.clear()
            self.assertEqual(metabase.status("ops"), "unknown")

    def test_the_answer_is_kept_briefly(self):
        self.make()
        metabase.publish("ops")
        metabase._seen.clear()
        metabase.status("ops")
        asked = len(self.fake.calls)
        metabase.status("ops")
        self.assertEqual(len(self.fake.calls), asked)


class DoctorAndPageTest(GoLiveCase):
    def test_doctor(self):
        text = "\n".join(metabase.doctor())
        self.assertIn("Go live is ready.", text)
        self.assertIn('can write to "Our analytics" itself', text)
        self.assertIn("SHOW statement_timeout: 30s", text)
        del self.fake.collections[OURS]
        with mock.patch.object(config, "METABASE_COLLECTION_ID", None):
            self.assertIn("Go live is NOT ready", "\n".join(metabase.doctor()))

    def post(self, path, body):
        conn = http.client.HTTPConnection("127.0.0.1", config.PORT, timeout=20)
        origin = f"http://127.0.0.1:{config.PORT}"
        conn.request("POST", path, body=json.dumps(body), headers={
            "Host": f"127.0.0.1:{config.PORT}", "Content-Type": "application/json", "Origin": origin,
            "Cookie": "studio_session=test-token"})
        response = conn.getresponse()
        text = response.read().decode()
        conn.close()
        return response.status, text

    def test_the_page_routes_and_the_key_stays_on_the_server(self):
        serve(self)
        self.make()
        status, text = self.post("/api/golive/plan", {"slug": "ops"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(text)["plan"]["blockers"], [])
        self.assertIn("not live yet", json.loads(self.post("/api/open", {"slug": "ops"})[1])["error"])
        status, published = self.post("/api/golive", {"slug": "ops"})
        self.assertEqual(json.loads(published)["published"]["mode"], "create")
        state = json.dumps(json.loads(self.post("/api/golive/plan", {"slug": "ops"})[1]))
        for body in (text, published, state, (self.tmp / "dashboards" / "ops" / "metabase.json").read_text()):
            self.assertNotIn(KEY, body)
        with mock.patch("webbrowser.open") as opened:
            self.assertEqual(json.loads(self.post("/api/open", {"slug": "ops"})[1]), {"ok": True})
        opened.assert_called_once_with(f"{self.fake.url}/dashboard/{self.dashboard()['id']}")


if __name__ == "__main__":
    unittest.main()
