#!/usr/bin/env python3
"""CLI for querying the Claude Sessions Dashboard.

Quick access for scripts, Tess/AgentZero, or terminal use.

Usage:
    python3 cli.py                    # Summary of all sessions
    python3 cli.py status             # One-line status per active session
    python3 cli.py json               # Full JSON (same as API)
    python3 cli.py wait <slug|id>     # Block until session is idle
    python3 cli.py open               # Open dashboard in browser
"""
import json
import sys
import time
import subprocess
import urllib.request

DASHBOARD_URL = "http://127.0.0.1:7685"
API_URL = f"{DASHBOARD_URL}/api/sessions"


def _fetch():
    try:
        with urllib.request.urlopen(API_URL, timeout=3) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"Error: Dashboard not running? ({e})", file=sys.stderr)
        print(f"Start with: python3 web/dashboard.py --port 7685 &", file=sys.stderr)
        sys.exit(1)


def cmd_summary():
    data = _fetch()
    s = data["summary"]
    plan = data.get("plan")

    print(f"Claude Sessions Dashboard")
    print(f"{'─' * 45}")
    print(f"  Active: {s['active']}   Recent: {s['recent']}   Total: {s['total']}")
    print(f"  Tokens: {s['totalTokens']:,}   Cost: ${s['totalCost']:.2f}")

    if plan:
        print(f"\n  Plan: {plan.get('plan', '?')}")
        print(f"  Session (5h): {plan['session']['pct']:.0f}%   Weekly: {plan['weekly']['pct']:.0f}%")

    active = [x for x in data["sessions"] if x.get("hasTerminal") or x["status"] == "active"]
    if active:
        print(f"\n  Active sessions:")
        for x in active:
            name = x.get("slug") or x["id"]
            state = x.get("liveState", "?")
            tmux = f" [tmux: {x['tmuxSession']}]" if x.get("tmuxSession") else ""
            state_icon = {"working": "🔵", "idle": "🟡", "choice": "🟠"}.get(state, "⚪")
            state_label = {"working": "arbetar", "idle": "väntar", "choice": "väntar på val"}.get(state, state)
            print(f"    {state_icon} {name:30s} {state_label}{tmux}")


def cmd_status():
    data = _fetch()
    for x in data["sessions"]:
        if not (x.get("hasTerminal") or x["status"] == "active"):
            continue
        name = x.get("slug") or x["id"]
        state = x.get("liveState", "unknown")
        tmux = x.get("tmuxSession") or ""
        print(f"{name}\t{state}\t{x['status']}\t{tmux}\t{x.get('project','')}")


def cmd_json():
    data = _fetch()
    print(json.dumps(data, indent=2))


def cmd_wait(target):
    """Block until the target session reaches idle state."""
    print(f"Waiting for '{target}' to become idle...", file=sys.stderr)
    while True:
        data = _fetch()
        for x in data["sessions"]:
            name = x.get("slug") or x["id"]
            if target in name or target in x.get("fullId", ""):
                state = x.get("liveState", "unknown")
                if state == "idle":
                    print(f"idle", end="")
                    return
                elif state == "choice":
                    print(f"needs_input", end="")
                    return
        time.sleep(3)


def cmd_open():
    subprocess.Popen(["xdg-open", DASHBOARD_URL],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"Opened {DASHBOARD_URL}")


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "summary"

    if cmd in ("summary", "s"):
        cmd_summary()
    elif cmd in ("status", "st"):
        cmd_status()
    elif cmd in ("json", "j"):
        cmd_json()
    elif cmd in ("wait", "w"):
        if len(args) < 2:
            print("Usage: cli.py wait <session-name-or-id>", file=sys.stderr)
            sys.exit(1)
        cmd_wait(args[1])
    elif cmd in ("open", "o"):
        cmd_open()
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
