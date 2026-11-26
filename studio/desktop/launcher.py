"""Open Journal Typesetting Studio as a desktop application.

The launcher starts the Studio's local web server on 127.0.0.1 in a
background thread and shows it in a native window (pywebview, which uses
Microsoft Edge WebView2 on Windows). If no native window can be opened it
falls back to the default browser and a small "Studio is running" panel, so
the editor is never left without a way in or a way to stop it.

It also does the start-up checks an editor would otherwise meet as a stack
trace: Pandoc (needed to read Word files) and Typst (needed for the PDF).
A missing Pandoc opens a setup page with the steps instead of the Studio.

When the Studio is packaged as a Windows program (PyInstaller, see
`studio/desktop/windows/`), the program folder is read-only, so journal
profiles, indexing criteria and article data live in the user's Documents
folder instead. They are seeded from the bundled copies on first run and never
overwritten afterwards, because an editor may have changed them.
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "Journal Typesetting Studio"
APP_ID = "JournalTypesettingStudio.Desktop"
PREFERRED_PORT = 5070
DESKTOP_DIR = Path(__file__).resolve().parent
PACKAGE_DIR = DESKTOP_DIR.parent
BRAND_DIR = DESKTOP_DIR / "branding"
ICON_ICO = BRAND_DIR / "icon.ico"
ICON_PNG = BRAND_DIR / "icon-256.png"
LOGO_SVG = BRAND_DIR / "logo.svg"

log = logging.getLogger("studio.desktop")


# --------------------------------------------------------------- environment

def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def user_folder() -> Path:
    """Where a packaged Studio keeps the editor's own files."""
    override = os.environ.get("STUDIO_HOME")
    if override:
        return Path(override)
    docs = Path.home() / "Documents"
    return (docs if docs.is_dir() else Path.home()) / APP_NAME


def _seed(src: Path, dest: Path) -> None:
    """Copy bundled defaults into a writable folder, never replacing a file."""
    if not src.is_dir():
        return
    for f in src.rglob("*"):
        target = dest / f.relative_to(src)
        if f.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)


PANDOC_CANDIDATES_WINDOWS = (
    # The Pandoc .msi installs per user by default, and a program that was
    # already running when it was installed does not see the new PATH.
    Path(os.environ.get("LOCALAPPDATA", "")) / "Pandoc" / "pandoc.exe",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Pandoc" / "pandoc.exe",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Pandoc" / "pandoc.exe",
)


def find_pandoc() -> str | None:
    """Pandoc from PANDOC_PATH, a copy shipped beside the program, PATH, or a
    standard install location, in that order."""
    explicit = os.environ.get("PANDOC_PATH")
    if explicit and (Path(explicit).exists() or shutil.which(explicit)):
        return explicit
    exe = "pandoc.exe" if os.name == "nt" else "pandoc"
    bundled = [Path(sys.executable).parent / "pandoc" / exe] if is_frozen() else []
    bundled.append(DESKTOP_DIR / "vendor" / "pandoc" / exe)
    for p in bundled:
        if p.is_file():
            return str(p)
    found = shutil.which("pandoc")
    if found:
        return found
    if os.name == "nt":
        for p in PANDOC_CANDIDATES_WINDOWS:
            if p.is_file():
                return str(p)
    return None


def prepare_environment() -> Path:
    """Point the Studio at writable folders and a Pandoc it can run. Must be
    called before `studio.config` is imported. Returns the data folder."""
    if is_frozen():
        home = user_folder()
        os.environ.setdefault("STUDIO_DATA_DIR", str(home / "data"))
        os.environ.setdefault("STUDIO_PROFILES_DIR", str(home / "profiles"))
        os.environ.setdefault("STUDIO_CRITERIA_DIR", str(home / "criteria"))
        _seed(PACKAGE_DIR / "profiles", Path(os.environ["STUDIO_PROFILES_DIR"]))
        _seed(PACKAGE_DIR / "criteria", Path(os.environ["STUDIO_CRITERIA_DIR"]))
    pandoc = find_pandoc()
    if pandoc:
        os.environ["PANDOC_PATH"] = pandoc
    from studio import config  # noqa: E402  (after the environment is set)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return config.DATA_DIR


def setup_logging(data_dir: Path) -> Path:
    log_file = data_dir / "desktop.log"
    handlers: list[logging.Handler] = [logging.FileHandler(log_file, encoding="utf-8")]
    if sys.stderr is not None and getattr(sys.stderr, "write", None):
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return log_file


# --------------------------------------------------------------- checks

def check_tools() -> list[dict]:
    """What is missing, as items the setup page can show. Empty means ready."""
    problems = []
    if not os.environ.get("PANDOC_PATH"):
        problems.append({
            "name": "Pandoc",
            "why": "Pandoc reads the accepted Word manuscript. Without it nothing can be imported.",
            "steps": [
                "Download the Windows installer (the file ending in -windows-x86_64.msi) from the Pandoc releases page.",
                "Run it and accept the defaults.",
                "Come back here and press Check again.",
            ],
            "link": "https://github.com/jgm/pandoc/releases/latest",
        })
    try:
        import typst  # noqa: F401
    except Exception as exc:  # pragma: no cover - depends on the install
        problems.append({
            "name": "Typst",
            "why": f"Typst writes the PDF and could not be loaded ({exc}).",
            "steps": ["Reinstall the Studio, or run: pip install -r studio\\requirements.txt"],
            "link": "",
        })
    return problems


SETUP_PAGE = """{% extends "base.html" %}
{% block title %}Set up Journal Typesetting Studio{% endblock %}
{% block body %}
<div class="card">
  <h1>Almost ready</h1>
  <p>The Studio needs {{ problems|length }} more {{ 'tool' if problems|length == 1 else 'tools' }} on this computer
     before it can produce proofs.</p>
  {% for p in problems %}
    <h2>{{ p.name }}</h2>
    <p>{{ p.why }}</p>
    <ol>{% for s in p.steps %}<li>{{ s }}</li>{% endfor %}</ol>
    {% if p.link %}<p><a href="{{ p.link }}" target="_blank" rel="noopener">Open the {{ p.name }} download page</a></p>{% endif %}
  {% endfor %}
  <form method="get" action="{{ url_for('desktop_setup') }}" style="display:inline"><button>Check again</button></form>
  <a href="{{ url_for('index') }}" style="margin-left:1rem">Continue without it</a>
  <p class="muted small">Pandoc can also be pointed to directly by setting the PANDOC_PATH environment variable
     to the full path of pandoc.exe.</p>
</div>
{% endblock %}"""


def add_desktop_routes(app) -> None:
    from flask import redirect, render_template_string, url_for

    def desktop_setup():
        pandoc = find_pandoc()
        if pandoc:
            os.environ["PANDOC_PATH"] = pandoc
        problems = check_tools()
        if not problems:
            return redirect(url_for("index"))
        return render_template_string(SETUP_PAGE, problems=problems)

    app.add_url_rule("/desktop/setup", "desktop_setup", desktop_setup)


# --------------------------------------------------------------- server

def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def pick_port(preferred: int = PREFERRED_PORT) -> int:
    if preferred and port_is_free(preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def studio_answers(url: str, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(url + "/desktop/ping", timeout=timeout) as r:
            return r.read().decode("utf-8", "replace").strip() == APP_ID
    except Exception:
        return False


class Server:
    """The Studio's Flask app on a werkzeug server in a daemon thread."""

    def __init__(self, port: int):
        from werkzeug.serving import make_server

        from studio.web import create_app

        self.app = create_app()
        add_desktop_routes(self.app)
        self.app.add_url_rule("/desktop/ping", "desktop_ping", lambda: APP_ID)
        self._server = make_server("127.0.0.1", port, self.app, threaded=True)
        self.port = self._server.server_port
        self.url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._server.serve_forever, name="studio-server", daemon=True)

    def start(self, wait: float = 15.0) -> None:
        self._thread.start()
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if studio_answers(self.url, timeout=0.5):
                return
            time.sleep(0.1)
        raise RuntimeError(f"the Studio server did not answer on {self.url}")

    def stop(self) -> None:
        self._server.shutdown()


# --------------------------------------------------------------- windows

def set_windows_app_id() -> None:
    """Group the window under the Studio's own taskbar icon on Windows rather
    than under python.exe."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def set_window_icon_windows(title: str) -> None:
    """pywebview sets no icon on Windows (that is left to a bundler), so a
    Studio started from source would show Python's. Set it on the window."""
    if os.name != "nt" or not ICON_ICO.is_file():
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.FindWindowW.restype = ctypes.c_void_p
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        for _ in range(50):
            hwnd = user32.FindWindowW(None, title)
            if hwnd:
                break
            time.sleep(0.1)
        else:
            return
        IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON = 1, 0x10, 0x80
        big = user32.LoadImageW(None, str(ICON_ICO), IMAGE_ICON, 256, 256, LR_LOADFROMFILE)
        small = user32.LoadImageW(None, str(ICON_ICO), IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
        if big:
            user32.SendMessageW(hwnd, WM_SETICON, 1, big)
        if small:
            user32.SendMessageW(hwnd, WM_SETICON, 0, small)
    except Exception:
        log.exception("could not set the window icon")


def open_native_window(url: str, data_dir: Path) -> bool:
    """Show the Studio in its own window and block until it is closed.
    Returns False when no native window could be opened."""
    try:
        import webview
    except ImportError:
        log.info("pywebview is not installed; using the browser")
        return False
    try:
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        webview.settings["ALLOW_DOWNLOADS"] = True
    except Exception:
        pass
    try:
        webview.create_window(APP_NAME, url, width=1320, height=900, min_size=(900, 600))
        storage = data_dir / "webview"
        storage.mkdir(exist_ok=True)
        webview.start(set_window_icon_windows, (APP_NAME,), icon=str(ICON_PNG),
                      private_mode=False, storage_path=str(storage))
        return True
    except Exception:
        log.exception("could not open a native window; using the browser")
        return False


def run_browser_fallback(url: str, server: Server | None) -> None:
    """Open the default browser and keep the server alive behind a small panel
    with Open and Quit buttons; without Tk, until interrupted."""
    webbrowser.open(url)
    try:
        import tkinter as tk
    except Exception:
        tk = None
    if tk is None:
        print(f"{APP_NAME} is running at {url}. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return
    root = tk.Tk()
    root.title(APP_NAME)
    root.resizable(False, False)
    try:
        if os.name == "nt" and ICON_ICO.is_file():
            root.iconbitmap(str(ICON_ICO))
        elif ICON_PNG.is_file():
            root.iconphoto(True, tk.PhotoImage(file=str(ICON_PNG)))
    except Exception:
        pass
    frame = tk.Frame(root, padx=18, pady=14)
    frame.pack()
    tk.Label(frame, text=f"{APP_NAME} is running in your browser.", font=("Segoe UI", 11, "bold")).pack(anchor="w")
    tk.Label(frame, text=url, fg="#1f3b63").pack(anchor="w", pady=(2, 10))
    row = tk.Frame(frame)
    row.pack(anchor="e")
    tk.Button(row, text="Open in browser", command=lambda: webbrowser.open(url)).pack(side="left", padx=4)
    tk.Button(row, text="Quit Studio", command=root.destroy).pack(side="left", padx=4)
    root.mainloop()


# --------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m studio.desktop", description=f"Open {APP_NAME}")
    ap.add_argument("--browser", action="store_true", help="use the default browser instead of a window")
    ap.add_argument("--port", type=int, default=0, help=f"port on 127.0.0.1 (default {PREFERRED_PORT} if free)")
    ap.add_argument("--check", action="store_true", help="report the start-up checks and exit")
    a = ap.parse_args(argv)

    data_dir = prepare_environment()
    log_file = setup_logging(data_dir)
    log.info("starting %s (frozen=%s), data in %s", APP_NAME, is_frozen(), data_dir)
    problems = check_tools()

    if a.check:
        print(f"Data folder:   {data_dir}")
        print(f"Pandoc:        {os.environ.get('PANDOC_PATH') or 'NOT FOUND'}")
        print(f"Log:           {log_file}")
        for p in problems:
            print(f"Missing {p['name']}: {p['why']}")
        print("Ready." if not problems else "Not ready.")
        return 0 if not problems else 2

    # A second click on the icon while the Studio is open reuses it.
    existing = f"http://127.0.0.1:{a.port or PREFERRED_PORT}"
    if studio_answers(existing):
        log.info("already running at %s", existing)
        webbrowser.open(existing)
        return 0

    set_windows_app_id()
    server = Server(a.port or pick_port())
    try:
        server.start()
    except Exception:
        log.exception("server failed to start")
        raise
    log.info("serving on %s", server.url)
    start_url = server.url + ("/desktop/setup" if problems else "/")

    try:
        if a.browser or not open_native_window(start_url, data_dir):
            run_browser_fallback(start_url, server)
    finally:
        server.stop()
        log.info("stopped")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
