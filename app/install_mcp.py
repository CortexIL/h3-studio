"""Register the H3 Studio MCP server with Claude Desktop.

    python -m app.install_mcp            add / update the entry
    python -m app.install_mcp --remove   take it out again
    python -m app.install_mcp --print    just show the JSON, change nothing

The existing config is backed up before any write and merged rather than replaced -
Claude Desktop keeps unrelated settings in the same file, and losing them would be a
much bigger problem than not having this integration.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER_KEY = "h3-studio"


def config_path() -> Path | None:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Claude" / "claude_desktop_config.json"
    elif sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    else:
        return Path.home() / ".config/Claude/claude_desktop_config.json"
    return None


def entry() -> dict[str, object]:
    python = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    return {
        "command": str(python),
        "args": ["-m", "app.mcp_server"],
        "cwd": str(ROOT),
    }


def main() -> int:
    ap = argparse.ArgumentParser(prog="app.install_mcp")
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--print", dest="show", action="store_true")
    args = ap.parse_args()

    if args.show:
        print(json.dumps({"mcpServers": {SERVER_KEY: entry()}}, indent=2))
        return 0

    path = config_path()
    if path is None:
        print("Unsupported platform.")
        return 1
    if not path.parent.exists():
        print(f"Claude Desktop config folder not found:\n  {path.parent}")
        print("Install Claude Desktop and run it once, then try again.")
        return 1

    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"Existing config is not valid JSON ({e}). Refusing to touch it.")
            print("Fix or move the file, then run this again.")
            return 1
        backup = path.with_name(f"{path.stem}.backup-{time.strftime('%Y%m%d-%H%M%S')}.json")
        shutil.copy2(path, backup)
        print(f"Backed up existing config to:\n  {backup}")

    servers = data.setdefault("mcpServers", {})
    if args.remove:
        if servers.pop(SERVER_KEY, None) is None:
            print("H3 Studio was not registered; nothing to remove.")
            return 0
        action = "Removed"
    else:
        servers[SERVER_KEY] = entry()
        action = "Registered"

    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"{action} '{SERVER_KEY}' in:\n  {path}")
    print("\nRestart Claude Desktop for the change to take effect.")
    if not args.remove:
        print("\nThen you can ask Claude things like:")
        print('  "queue three 10-second clips of waves at sunset in draft quality"')
        print('  "use C:\\pics\\logo.png as a reference and animate it"')
        print('  "how much would 40 clips cost?"')
        print("\nH3 Studio itself must be running for those to work.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
