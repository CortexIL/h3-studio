"""Double-click entry point. Nothing here should ever require typing a command.

Behaviour that matters for a non-terminal workflow:
  * If H3 Studio is already running, this does NOT error - it just opens the browser
    on the existing instance. Double-clicking the icon twice is a normal thing to do.
  * If the configured port is taken by something else entirely, it moves to the next
    free port rather than failing.
  * The browser opens by itself once the server actually answers, not on a guess.
"""
from __future__ import annotations

import argparse
import logging
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

from . import config as config_mod
from . import db
from .main import create_app

BANNER = r"""
  ==============================
      H 3   S T U D I O
  ==============================
"""


def _is_ours(host: str, port: int) -> bool:
    """True when the thing on this port is an H3 Studio, not some other app."""
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/status", timeout=2) as r:
            return b'"policy"' in r.read(4096)
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def _free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _pick_port(host: str, start: int) -> int | None:
    for port in range(start, start + 20):
        if _free(host, port):
            return port
    return None


def _open_when_ready(url: str, timeout: float = 40.0) -> None:
    """Open the browser only once the server answers, so it never lands on a dead tab."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/api/status", timeout=2):
                webbrowser.open(url)
                return
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(0.4)
    webbrowser.open(url)  # last resort: open anyway so the user sees something


def main() -> None:
    ap = argparse.ArgumentParser(prog="H3 Studio")
    ap.add_argument("--mock", action="store_true",
                    help="demo mode: no GPU, no cost")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    cfg = config_mod.load()
    cfg.mock = args.mock
    host = cfg.server.host
    wanted = args.port or cfg.server.port

    print(BANNER)

    # One icon should always work. Without a key there is nothing to run live
    # against, so fall back to demo rather than dying with a stack trace - but say
    # so loudly, both here and with the badge in the page.
    key = cfg.runpod.api_key
    if not cfg.mock and (not key or key.startswith("YOUR_")):
        cfg.mock = True
        print("  No RunPod API key set, so this is running in DEMO mode.")
        print("  Demo generates placeholder videos - it never rents a GPU.")
        print("  To go live: put your key in config.yaml under runpod.api_key.\n")

    # Already running? Just show it. This is the whole point - clicking the icon
    # again should feel like switching to the app, not like an error.
    if not _free(host, wanted) and _is_ours(host, wanted):
        url = f"http://{host}:{wanted}"
        print(f"  H3 Studio is already running at {url}")
        print("  Opening it in your browser.\n")
        if not args.no_browser:
            webbrowser.open(url)
        print("  (You can close this window.)")
        time.sleep(3)
        return

    port = _pick_port(host, wanted)
    if port is None:
        print(f"  Could not find a free port near {wanted}.")
        print("  Something unusual is using this range. Restarting the PC clears it.")
        input("\n  Press Enter to close...")
        raise SystemExit(1)
    if port != wanted:
        print(f"  Port {wanted} was busy, using {port} instead.")

    url = f"http://{host}:{port}"
    mode = "DEMO MODE - no GPU, nothing is charged" if cfg.mock else "LIVE - rents a real GPU"
    print(f"  {mode}")
    print(f"  Open:  {url}")
    print(f"  Output folder:  {cfg.output.folder}")
    print("\n  Leave this window open while you work.")
    print("  Close it, or press Ctrl-C, to stop H3 Studio.")
    if not cfg.mock:
        print("  Stopping also shuts down the rented GPU, so you stop paying.")
    print()

    db.init()
    app = create_app(cfg)
    if not args.no_browser:
        threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()

    # Built explicitly rather than via uvicorn.run() so the running server object can
    # be reached from a request handler - that is what lets the Quit button in the web
    # page stop the app (and with it the GPU) without touching this window.
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    app.state.server = server
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    print("\n  H3 Studio stopped.")
    if not cfg.mock:
        print("  GPU shut down.")
    time.sleep(2)


if __name__ == "__main__":
    main()
