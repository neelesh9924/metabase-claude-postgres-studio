import json
import os
import threading
import time
import unittest
from unittest import mock

from support import StudioCase, database, pid_alive, read_json

from app import config
from app.claude import RunRequest, ToolPolicy


def request(timeout=30):
    policy = ToolPolicy(tools=("Read", "Skill"), allow=("Skill", "mcp__studio__list_tables"))
    return RunRequest(prompt="SECRET-PROMPT-TEXT", system="rules", policy=policy,
                      mcp_url="http://127.0.0.1:1/mcp", mcp_token="tok", max_turns=9, timeout=timeout)


class RunnerTest(StudioCase):
    def test_command_environment_and_prompt(self):
        leaked = {"CLAUDE_CONFIG_DIR": r"C:\somewhere\.claude-other", "CLAUDECODE": "1",
                  "CLAUDE_CODE_ENTRYPOINT": "x", "ANTHROPIC_API_KEY": "sk-x"}
        self.databases(database(password="SECRET-MARK-1"))
        with mock.patch.dict(os.environ, leaked), mock.patch.object(config, "METABASE_API_KEY", "SECRET-MARK-2"):
            result = self.runner().run(request())
        self.assertEqual(result.outcome, "ok", result.error)
        seen = json.loads(result.text)
        # Claude gets the machine's own login: no login folder, no API key, and none of the studio's secrets.
        for name in leaked:
            self.assertNotIn(name, seen["env"])
        self.assertEqual(seen["marked"], [])
        self.assertEqual(seen["stdin"], "SECRET-PROMPT-TEXT")
        self.assertNotIn("SECRET-PROMPT-TEXT", " ".join(seen["argv"]))
        argv = seen["argv"]
        for flag, value in [("--permission-mode", "dontAsk"), ("--setting-sources", "project,local"),
                            ("--tools", "Read,Skill"), ("--max-turns", "9"),
                            ("--allowedTools", "Skill,mcp__studio__list_tables")]:
            self.assertEqual(argv[argv.index(flag) + 1], value)
        self.assertIn("--strict-mcp-config", argv)
        self.assertIn("--no-session-persistence", argv)
        self.assertIn("Read(./.env)", argv[argv.index("--disallowedTools") + 1])
        self.assertNotIn("--model", argv)
        self.assertNotIn("Bash", argv[argv.index("--tools") + 1])
        self.assertEqual(os.path.realpath(seen["cwd"]), os.path.realpath(self.tmp))
        self.assertTrue(seen["mcp_config_exists"])
        self.assertFalse(os.path.exists(argv[argv.index("--mcp-config") + 1]), "the token file must be removed")
        self.assertEqual(result.model, "fake-model")

    def test_model_is_passed_only_when_set(self):
        with mock.patch.object(config, "STUDIO_MODEL", "sonnet"):
            argv = json.loads(self.runner().run(request()).text)["argv"]
        self.assertEqual(argv[argv.index("--model") + 1], "sonnet")

    def _hang(self, stop_after=None, timeout=30):
        self.mode("hang")
        pidfile = self.tmp / "pids.json"
        os.environ["FAKE_PIDFILE"] = str(pidfile)
        self.addCleanup(os.environ.pop, "FAKE_PIDFILE", None)
        cancel = threading.Event()
        box = {}
        worker = threading.Thread(target=lambda: box.update(result=self.runner().run(request(timeout), cancel=cancel)))
        worker.start()
        self.wait(pidfile.exists)
        time.sleep(0.2)
        pids = read_json(pidfile)
        self.assertTrue(pid_alive(pids["child"]))
        if stop_after is not None:
            cancel.set()
        worker.join(timeout=30)
        return box["result"], pids

    def test_stop_kills_claude_and_what_it_started(self):
        result, pids = self._hang(stop_after=0)
        self.assertEqual(result.outcome, "cancelled")
        self.assertFalse(pid_alive(pids["parent"]))
        self.assertFalse(pid_alive(pids["child"]))

    def test_timeout_kills_the_tree(self):
        result, pids = self._hang(timeout=2)
        self.assertEqual(result.outcome, "timeout")
        self.assertFalse(pid_alive(pids["parent"]))
        self.assertFalse(pid_alive(pids["child"]))

    def test_turn_limit_is_an_error(self):
        self.mode("limit")
        result = self.runner().run(request())
        self.assertEqual(result.outcome, "error")
        self.assertIn("turn limit", result.error)

    def test_no_answer_is_an_error(self):
        self.mode("mute")
        result = self.runner().run(request())
        self.assertEqual(result.outcome, "error")
        self.assertIn("without an answer", result.error)
        self.assertIn("something went wrong", result.error)

    def test_progress_is_reported(self):
        self.mode("edit")
        seen = []
        self.runner().run(request(), on_event=lambda kind, text, detail: seen.append((kind, text)))
        self.assertIn(("status", "Starting Claude"), seen)
        self.assertIn(("status", "Getting ready"), seen)


if __name__ == "__main__":
    unittest.main()
