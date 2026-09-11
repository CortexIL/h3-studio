"""Terminate every pod on the account. The panic button.

Exists because a pod that outlives its owner's attention is the only way this project
loses real money. Anything that leaves one running - a crash, a killed terminal,
`smoketest --keep` - is recoverable with one command.

    python -m app.killpods          list, then terminate everything
    python -m app.killpods --list   list only, terminate nothing
"""
from __future__ import annotations

import argparse
import asyncio
import sys

import httpx

from . import config as config_mod
from .settings import get_settings
from .backends.runpod_pod import API


async def amain() -> int:
    ap = argparse.ArgumentParser(prog="app.killpods")
    ap.add_argument("--list", action="store_true", help="show pods without stopping them")
    args = ap.parse_args()

    cfg = config_mod.Config.from_settings(get_settings())
    key = cfg.runpod.api_key
    if not key or key.startswith("YOUR_"):
        print("No RunPod API key configured.")
        return 1

    async with httpx.AsyncClient(
        timeout=30, headers={"Authorization": f"Bearer {key}"}
    ) as http:
        r = await http.get(f"{API}/pods")
        if r.status_code != 200:
            print(f"Could not list pods: HTTP {r.status_code} {r.text[:200]}")
            return 1
        data = r.json()
        pods = data if isinstance(data, list) else data.get("data", [])

        if not pods:
            print("No pods running. Nothing is being charged.")
            return 0

        print(f"{len(pods)} pod(s) on the account:\n")
        for p in pods:
            print(f"  {p.get('id')}  {p.get('name', '?')}  "
                  f"{p.get('desiredStatus', p.get('status', '?'))}  "
                  f"${float(p.get('costPerHr') or 0):.2f}/hr")

        if args.list:
            print("\n--list given, nothing terminated.")
            return 0

        print()
        failed = 0
        for p in pods:
            pid = p.get("id")
            d = await http.delete(f"{API}/pods/{pid}")
            if d.status_code < 400:
                print(f"  terminated {pid}")
            else:
                failed += 1
                print(f"  FAILED to terminate {pid}: HTTP {d.status_code}")

        if failed:
            print("\nSome pods survived. Stop them at https://console.runpod.io/pods")
            return 1
        print("\nAll pods terminated. Nothing is being charged.")
        return 0


def main() -> None:
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
