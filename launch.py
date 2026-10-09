"""Start the studio: start the server, open an app window, stop when the window closes.

Run by "Launch Metabase Claude Studio.vbs" with .venv\\Scripts\\pythonw.exe (no console). With
--setup (first run, visible console) it creates the venv and installs requirements.txt first.
"""
import html
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
VENV_PY = VENV / "Scripts" / "python.exe"
VENV_PYW = VENV / "Scripts" / "pythonw.exe"
DATA = ROOT / "data"
LOG = DATA / "logs" / "studio.log"
SESSION = DATA / "session.json"
PROFILE = DATA / "browser-profile"
APP = "metabase-claude-postgres-studio"
NAME = "Metabase Claude Studio"
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def setup():
    print(f"{NAME} - first-run setup\n")
    try:
        if sys.version_info < (3, 11):
            raise RuntimeError(f"Python 3.11 or newer is needed; this is {sys.version.split()[0]}.")
        if not VENV_PY.is_file():
            print(f"Creating the virtual environment in {VENV} ...")
            subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
        print("Installing the packages from requirements.txt ...\n")
        subprocess.run([str(VENV_PY), "-m", "pip", "install", "--disable-pip-version-check",
                        "-r", str(ROOT / "requirements.txt")], check=True)
        print(f"\nSetup done. Starting {NAME}; this window closes in a few seconds.")
        subprocess.Popen([str(VENV_PYW), str(ROOT / "launch.py")], cwd=str(ROOT),
                         creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, close_fds=True)
        time.sleep(4)
        return 0
    except Exception as exc:  # this console is the only place the reason can be seen
        print(f"\nSetup failed: {exc}")
        input("Press Enter to close this window.")
        return 1


def log(message):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


def quiet_logs():
    """pythonw has no stdout or stderr; send both to logs/studio.log."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None or sys.stderr is None or "pythonw" in Path(sys.executable).name.lower():
        sys.stdout = sys.stderr = open(LOG, "a", encoding="utf-8", buffering=1)


def http_json(url, timeout=2.0):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None


def port_free(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def registry_app_path(exe):
    import winreg

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as key:
                value, _ = winreg.QueryValueEx(key, "")
                if value and Path(value.strip('"')).is_file():
                    return value.strip('"')
        except OSError:
            continue
    return None


def find_browser(override):
    if override:
        return override if Path(override).is_file() else None
    env = os.environ
    chrome = [Path(env[v]) / "Google" / "Chrome" / "Application" / "chrome.exe"
              for v in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA") if env.get(v)]
    edge = [Path(env[v]) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            for v in ("ProgramFiles(x86)", "ProgramFiles") if env.get(v)]
    for found in (registry_app_path("chrome.exe"), *map(str, chrome),
                  registry_app_path("msedge.exe"), *map(str, edge)):
        if found and Path(found).is_file():
            return found
    return None


def open_window(browser, url):
    return subprocess.Popen([browser, f"--app={url}", f"--user-data-dir={PROFILE}", "--window-size=1500,950",
                             "--no-first-run", "--no-default-browser-check"], cwd=str(ROOT))


ERROR_PAGE = """<!doctype html><html lang="en"><meta charset="utf-8"><title>The studio cannot start</title>
<style>body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px; background: #f9fbfc;
color: #4c5773; font: 15px/1.55 system-ui, "Segoe UI", sans-serif; }} main {{ max-width: 560px; padding: 26px 28px;
border: 1px solid #eeecec; border-radius: 12px; background: #fff; }} h1 {{ margin: 0 0 14px; font-size: 20px; }}
.why {{ margin: 0 0 10px; padding: 11px 14px; border-radius: 8px; background: #fdecea; color: #a52a1c; }}
.next {{ margin: 14px 0 0; color: #696e7b; font-size: 13.5px; }} code {{ overflow-wrap: anywhere; }}</style>
<body><main><h1>The studio cannot start</h1>{items}
<p class="next">Fix the above, close this window and open the studio again. Details are in <code>{log}</code>.</p>
</main></body></html>"""


def fail(reasons, browser_override=""):
    for reason in reasons:
        log(f"cannot start: {reason}")
    DATA.mkdir(parents=True, exist_ok=True)
    page = DATA / "launch-error.html"
    items = "".join(f'<p class="why">{html.escape(r)}</p>' for r in reasons)
    page.write_text(ERROR_PAGE.format(items=items, log=html.escape(str(LOG))), encoding="utf-8")
    browser = find_browser(browser_override)
    if browser:
        subprocess.Popen([browser, f"--app={page.as_uri()}", f"--user-data-dir={PROFILE}",
                          "--no-first-run", "--no-default-browser-check"])
    else:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, "\n\n".join(reasons), f"{NAME} cannot start", 0x10)
    return 1


def main():
    if "--setup" in sys.argv:
        return setup()
    if Path(sys.prefix).resolve() != VENV.resolve():
        return setup()  # started with a system Python: build the venv, then start again inside it
    quiet_logs()
    sys.path.insert(0, str(ROOT))
    from app import config, server, settings, specs
    from app.winjob import kill_tree

    settings.start()
    specs.ensure_sample()
    browser = find_browser(config.STUDIO_BROWSER)
    if not browser:
        return fail(["The browser path in Settings does not point at chrome.exe or msedge.exe."
                     if config.STUDIO_BROWSER else "Neither Chrome nor Edge was found."], config.STUDIO_BROWSER)

    # One instance: if the server already runs, open another window onto it.
    try:
        session = json.loads(SESSION.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        session = {}
    if session.get("port"):
        info = http_json(f"http://127.0.0.1:{session['port']}/health")
        if info and info.get("app") == APP and info.get("instance") == session.get("instance"):
            log("already running; opening another window")
            open_window(browser, f"http://127.0.0.1:{session['port']}/auth?token={session['token']}")
            return 0
    if not port_free(config.PORT):
        return fail([f"Port {config.PORT} on 127.0.0.1 is in use by another program. If the studio was started "
                     'from a terminal, stop it there, or change "port" in data\\settings.json.'], config.STUDIO_BROWSER)

    token = secrets.token_urlsafe(32)
    httpd = server.make_server(token)
    thread = threading.Thread(target=httpd.serve_forever, name="server", daemon=True)
    thread.start()

    base = f"http://127.0.0.1:{config.PORT}"
    info = None
    for _ in range(100):
        info = http_json(base + "/health", timeout=1)
        if (info and info.get("app") == APP) or not thread.is_alive():
            break
        time.sleep(0.2)
    if not info:
        httpd.shutdown()
        return fail([f"The server did not start on {base}."], config.STUDIO_BROWSER)
    DATA.mkdir(parents=True, exist_ok=True)
    SESSION.write_text(json.dumps({"port": config.PORT, "token": token, "instance": info["instance"],
                                   "pid": os.getpid()}), encoding="utf-8")
    log(f"server up on {base} (pid {os.getpid()}); opening {Path(browser).name}")

    window = open_window(browser, f"{base}/auth?token={token}")
    try:
        while True:
            if window.poll() is not None:
                log("window closed; shutting down")
                break
            if not thread.is_alive():
                log("server stopped; closing the window")
                kill_tree(window.pid)
                break
            time.sleep(0.5)
    finally:
        httpd.shutdown()
        thread.join(timeout=10)
        httpd.server_close()
        httpd.studio.close()
        try:
            SESSION.unlink()
        except OSError:
            pass
        log("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
