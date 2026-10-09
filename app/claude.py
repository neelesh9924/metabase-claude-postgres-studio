"""Run one headless Claude Code call: `claude -p`, prompt on stdin, a fixed tool list.

Claude signs in with the machine's own login: no CLAUDE_CONFIG_DIR is passed on, so
it uses the default one. The command is a list (no shell) and the prompt never
appears in argv. A stop or a timeout kills the whole process tree.
"""
import json
import os
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .winjob import CREATE_NO_WINDOW, Job, kill_tree

MCP_SERVER = "studio"
# Dropped from Claude's environment: anything that would move Claude off the subscription
# login, and the markers of an enclosing Claude session. The studio's own secrets are never
# in the environment at all.
BLOCKED_PREFIXES = ("CLAUDE", "MCP_")
BLOCKED_NAMES = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"}
ALWAYS_DENIED = (
    # data/ holds the settings, the saved secrets, the conversations and the logs.
    "Read(./data/**)", "Read(./.venv/**)", "Read(./.env)",
    # Go live's own record of which Metabase dashboard and cards a folder is published as.
    "Edit(./dashboards/**/metabase.json)", "Write(./dashboards/**/metabase.json)",
)


class ClaudeRefused(RuntimeError):
    """The run was not started."""


@dataclass(frozen=True)
class ToolPolicy:
    tools: tuple          # --tools: the only built-in tools in the session
    allow: tuple          # --allowedTools: run without asking; everything else is refused
    deny: tuple = ALWAYS_DENIED


@dataclass
class RunRequest:
    prompt: str
    system: str
    policy: ToolPolicy
    mcp_url: str
    mcp_token: str
    max_turns: int
    timeout: float
    kind: str = ""


@dataclass
class RunResult:
    outcome: str = "error"          # ok | error | timeout | cancelled
    text: str = ""
    error: str = ""
    exit_code: int | None = None
    duration_ms: int = 0
    num_turns: int | None = None
    cost_usd: float | None = None
    model: str = ""
    denials: list = field(default_factory=list)


def build_env():
    blocked = {name.upper() for name in BLOCKED_NAMES}
    return {
        k: v for k, v in os.environ.items()
        if k.upper() not in blocked and not k.upper().startswith(BLOCKED_PREFIXES)
    }


def build_command(req, mcp_config, prefix=None):
    if prefix:
        cmd = list(prefix)
    else:
        binary = config.claude_bin()
        if not binary:
            raise ClaudeRefused("Claude Code was not found. Give its path in Settings.")
        cmd = [binary]
    cmd += [
        "-p",
        "--output-format", "stream-json", "--verbose",
        "--tools", ",".join(req.policy.tools),
        "--allowedTools", ",".join(req.policy.allow),
        "--disallowedTools", ",".join(req.policy.deny),
        "--permission-mode", "dontAsk",
        "--strict-mcp-config", "--mcp-config", str(mcp_config),
        "--setting-sources", "project,local",
        "--no-session-persistence",
        "--max-turns", str(req.max_turns),
        "--append-system-prompt", req.system,
    ]
    if config.STUDIO_MODEL:
        cmd += ["--model", config.STUDIO_MODEL]
    return cmd


def describe_tool(name, args):
    """One tool call in the user's words: (what, detail)."""
    args = args or {}
    short = name.split("__")[-1]
    target = Path(str(args.get("file_path") or args.get("file") or "")).name
    if short == "list_tables":
        return "Looking for tables", str(args.get("text") or "all")
    if short == "describe_table":
        return f"Looking at {args.get('table', 'a table')}", ""
    if short == "run_query":
        return "Running a query", target or " ".join(str(args.get("sql", "")).split())[:120]
    if short == "check_dashboard":
        return "Checking the dashboard", ""
    if name == "Skill":
        return "Loading the skill", ""
    if name == "Read":
        return f"Reading {target}", ""
    if name in ("Write", "Edit"):
        return f"Writing {target}", ""
    if name in ("Glob", "Grep"):
        return "Searching the project", ""
    return f"Using {short}", ""


class ClaudeRunner:
    def __init__(self, prefix=None):
        self.prefix = prefix  # tests run a stand-in for claude through the same code

    def run(self, req, cancel=None, on_event=None):
        """Block until Claude finishes. on_event(kind, text, detail) reports progress."""
        cancel = cancel or threading.Event()
        emit = on_event or (lambda *_: None)
        result = RunResult()
        handle, mcp_config = tempfile.mkstemp(prefix="studio-mcp-", suffix=".json")
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump({"mcpServers": {MCP_SERVER: {
                "type": "http", "url": req.mcp_url,
                "headers": {"Authorization": f"Bearer {req.mcp_token}"}}}}, fh)
        try:
            cmd = build_command(req, mcp_config, self.prefix)
            self._run(cmd, req, cancel, emit, result)
        finally:
            try:
                os.unlink(mcp_config)
            except OSError:
                pass
        return result

    def _run(self, cmd, req, cancel, emit, result):
        started = time.monotonic()
        events = []
        stderr = []
        job = Job()
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(config.ROOT), env=build_env(), text=True, encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        job.assign(proc.pid)
        emit("status", "Starting Claude", "")

        def feed():
            try:
                proc.stdin.write(req.prompt)
                proc.stdin.close()
            except OSError:
                pass

        def read_out():
            for line in proc.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                events.append(event)
                kind = event.get("type")
                if kind == "system" and event.get("subtype") == "init":
                    result.model = event.get("model") or ""
                    servers = {s.get("name"): s.get("status") for s in event.get("mcp_servers") or []}
                    if servers.get(MCP_SERVER) not in (None, "connected"):
                        emit("status", "The studio tools did not connect", str(servers.get(MCP_SERVER)))
                    else:
                        emit("status", "Getting ready", "")
                elif kind == "assistant":
                    for block in event.get("message", {}).get("content") or []:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "tool_use":
                            emit("tool", *describe_tool(block.get("name", ""), block.get("input")))
                        elif block.get("type") == "text" and block.get("text", "").strip():
                            emit("say", block["text"].strip(), "")

        def read_err():
            for chunk in proc.stderr:
                if sum(len(c) for c in stderr) < 20000:
                    stderr.append(chunk)

        threads = [threading.Thread(target=f, daemon=True) for f in (feed, read_out, read_err)]
        for thread in threads:
            thread.start()
        try:
            while proc.poll() is None:
                if cancel.is_set():
                    result.outcome = "cancelled"
                    break
                if time.monotonic() - started > req.timeout:
                    result.outcome = "timeout"
                    break
                time.sleep(0.1)
        finally:
            if proc.poll() is None:
                job.terminate()
                kill_tree(proc.pid)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            for thread in threads:
                thread.join(timeout=5)
            for stream in (proc.stdout, proc.stderr):
                try:
                    stream.close()
                except OSError:
                    pass
            job.close()

        result.exit_code = proc.returncode
        result.duration_ms = int((time.monotonic() - started) * 1000)
        if result.outcome == "cancelled":
            result.error = "Stopped."
        elif result.outcome == "timeout":
            result.error = f"Stopped after {int(req.timeout)} s; Claude and everything it started were closed."
        else:
            self._fill(result, events, "".join(stderr))

    @staticmethod
    def _fill(result, events, stderr):
        final = next((e for e in reversed(events) if e.get("type") == "result"), None)
        if final is None:
            tail = [line for line in stderr.strip().splitlines() if line.strip()][-3:]
            result.error = "Claude ended without an answer" + (
                ": " + " / ".join(tail) if tail else f" (exit code {result.exit_code}).")
            return
        result.num_turns = final.get("num_turns")
        result.cost_usd = final.get("total_cost_usd")
        result.denials = final.get("permission_denials") or []
        result.text = final.get("result") or ""
        if final.get("is_error") or final.get("subtype") != "success":
            kind = final.get("subtype")
            result.error = ("Claude reached the turn limit before finishing." if kind == "error_max_turns"
                            else f"Claude reported an error ({kind}). {result.text[:300]}".strip())
            return
        result.outcome = "ok"
