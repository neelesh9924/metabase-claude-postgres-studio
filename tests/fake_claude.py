"""Stands in for claude.exe in tests. FAKE_MODE picks the behaviour.

echo   report argv, stdin and the environment in the answer
plan   list the studio tools over HTTP, then answer with a plan
build  write a dashboard into FAKE_ROOT, test its query through run_query, then answer
edit   answer with one line
hang   start a child that sleeps, record both pids in FAKE_PIDFILE, then sleep
limit  end with the turn-limit error
mute   exit without a result
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

mode = os.environ.get("FAKE_MODE", "echo")
argv = sys.argv[1:]
stdin = sys.stdin.read()


def emit(event):
    print(json.dumps(event), flush=True)


def option(name):
    return argv[argv.index(name) + 1] if name in argv else None


def studio(method, params=None):
    """One call to the studio's tool server, the way Claude Code makes it."""
    server = json.loads(Path(option("--mcp-config")).read_text(encoding="utf-8"))["mcpServers"]["studio"]
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode()
    request = urllib.request.Request(server["url"], data=body, headers={**server["headers"], "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=10) as response:
        return json.loads(response.read())["result"]


def tool(name, args=None):
    emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": args or {}}]}})
    time.sleep(float(os.environ.get("FAKE_DELAY", "0")))


def finish(text, **extra):
    emit({"type": "result", "subtype": "success", "is_error": False, "num_turns": 3, "result": text,
          "total_cost_usd": 0.01, "permission_denials": [], **extra})


emit({"type": "system", "subtype": "init", "model": "fake-model", "mcp_servers": [{"name": "studio", "status": "connected"}]})

if mode == "hang":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    Path(os.environ["FAKE_PIDFILE"]).write_text(json.dumps({"parent": os.getpid(), "child": child.pid}))
    time.sleep(120)
elif mode == "mute":
    sys.stderr.write("something went wrong\n")
    sys.exit(3)
elif mode == "limit":
    emit({"type": "result", "subtype": "error_max_turns", "is_error": True, "num_turns": 150, "result": ""})
elif mode == "echo":
    finish(json.dumps({"argv": argv, "stdin": stdin, "env": sorted(os.environ), "cwd": os.getcwd(),
                       "marked": [k for k, v in os.environ.items() if "SECRET-MARK" in v],
                       "mcp_config_exists": Path(option("--mcp-config")).is_file()}))
elif mode == "plan":
    tool("Skill", {"skill": "dashboard-build"})
    offered = sorted(t["name"] for t in studio("tools/list")["tools"])
    tool("mcp__studio__list_tables", {"text": "order"})
    refused = studio("tools/call", {"name": "run_query", "arguments": {"sql": "select 1"}})
    plan = {"slug": "Daily Ops!", "name": "Daily ops", "description": "Tickets per day.",
            "cards": [{"name": "Tickets today", "display": "scalar", "shows": "count of tickets"}],
            "reads": [{"table": "orders", "size": "~11M rows", "filter": "created_at, last 30 days"}],
            "assumptions": ["offered: " + ",".join(offered), "query in plan refused: " + str(refused["isError"])],
            "left_out": []}
    finish("I will count tickets per day.\n<<<STUDIO_JSON\n" + json.dumps(plan) + "\nSTUDIO_JSON>>>")
elif mode == "build":
    slug = stdin.split("The slug is ")[1].split(" ")[0]
    folder = Path(os.environ["FAKE_ROOT"]) / "dashboards" / slug
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "tickets_today.sql").write_text('select 7 as "Tickets today"\n', encoding="utf-8")
    tool("Write", {"file_path": str(folder / "tickets_today.sql")})
    tool("mcp__studio__run_query", {"file": f"dashboards/{slug}/tickets_today.sql"})
    answer = studio("tools/call", {"name": "run_query", "arguments": {"file": f"dashboards/{slug}/tickets_today.sql"}})
    (folder / "dashboard.json").write_text(json.dumps({"name": "Daily ops", "cards": [
        {"key": "tickets_today", "name": "Tickets today", "display": "scalar", "row": 0, "col": 0, "size_x": 6, "size_y": 3}]}),
        encoding="utf-8")
    finish("Built one card. Query said: " + answer["content"][0]["text"].splitlines()[0])
else:
    finish("Made the chart stacked.")
