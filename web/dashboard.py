#!/usr/bin/env python3
"""Claude Code Session Dashboard - Web-based session monitor.

A single-file web dashboard that shows all active and recent Claude Code
sessions with live stats (cost, tokens, context, tools, agents, etc).

Usage:
    python3 /tmp/claude-pulse/dashboard.py [--port 7685] [--open]
"""

import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
DEFAULT_PORT = 7685
DEFAULT_HOST = "127.0.0.1"

# Cache for active terminal PIDs - refreshed every scan
_active_pids_cache = {"timestamp": 0, "pids": {}}


def get_active_claude_pids():
    """Find running claude processes and map session IDs to PIDs."""
    cache = _active_pids_cache
    now = time.time()
    if now - cache["timestamp"] < 3:
        return cache["pids"]

    pids = {}
    try:
        # Find all claude processes
        result = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True, timeout=3
        )
        for line in result.stdout.splitlines():
            if "claude" not in line.lower():
                continue
            parts = line.split(None, 10)
            if len(parts) < 11:
                continue
            pid = parts[1]
            cmd = parts[10]
            # Try to read /proc/<pid>/cmdline for session info
            try:
                cmdline_path = f"/proc/{pid}/cmdline"
                if os.path.exists(cmdline_path):
                    with open(cmdline_path, "rb") as f:
                        cmdline = f.read().decode("utf-8", errors="replace")
                    # Check for transcript path or session id in cmdline/environ
                    if "claude" in cmdline.lower():
                        pids[pid] = cmdline
            except Exception:
                pass

        # Also check /proc/*/environ for CLAUDE_SESSION_ID
        for pid_dir in Path("/proc").iterdir():
            if not pid_dir.name.isdigit():
                continue
            try:
                environ_path = pid_dir / "environ"
                if environ_path.exists():
                    env_data = environ_path.read_bytes().decode("utf-8", errors="replace")
                    if "CLAUDE_SESSION" in env_data or "claude" in env_data.lower():
                        # Extract cwd
                        try:
                            cwd_link = pid_dir / "cwd"
                            real_cwd = os.readlink(str(cwd_link))
                            pids[pid_dir.name] = {"cwd": real_cwd, "env": env_data}
                        except Exception:
                            pass
            except (PermissionError, OSError):
                continue
    except Exception:
        pass

    cache["pids"] = pids
    cache["timestamp"] = now
    return pids


def session_has_terminal(session_info):
    """Check if a session has an active terminal process."""
    active_pids = get_active_claude_pids()
    session_cwd = session_info.get("cwd", "")
    session_id = session_info.get("fullId", "")

    for pid, data in active_pids.items():
        if isinstance(data, dict):
            pid_cwd = data.get("cwd", "")
            if session_cwd and pid_cwd and os.path.realpath(session_cwd) == os.path.realpath(pid_cwd):
                return True
            env = data.get("env", "")
            if session_id and session_id in env:
                return True
        elif isinstance(data, str):
            if session_id and session_id in data:
                return True
    return False


def detect_launcher(jsonl_path):
    """Detect if the session was launched by AgentZero/Tess or similar."""
    try:
        with open(jsonl_path, "r") as f:
            checked = 0
            for line in f:
                if checked > 10:
                    break
                try:
                    entry = json.loads(line)
                    if entry.get("type") == "user":
                        # Check for programmatic user types
                        user_type = entry.get("userType", "")
                        if user_type == "external":
                            # Check message content for AgentZero/Tess markers
                            msg = entry.get("message", "")
                            if isinstance(msg, list):
                                msg = " ".join(b.get("text", "") for b in msg if isinstance(b, dict))
                            msg_lower = msg.lower() if isinstance(msg, str) else ""
                            if any(k in msg_lower for k in ["agentzero", "agent zero", "tess", "agent_zero"]):
                                return "AgentZero"

                        # Check for headless/programmatic indicators
                        if entry.get("headless") or entry.get("programmatic"):
                            return "Headless"

                    checked += 1
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    return None

# Pricing per million tokens (USD)
PRICING = {
    "claude-opus-4-6": {"input": 15, "output": 75, "cache_read": 1.5, "cache_write": 18.75},
    "claude-opus-4-20250918": {"input": 15, "output": 75, "cache_read": 1.5, "cache_write": 18.75},
    "claude-sonnet-4-5-20250929": {"input": 3, "output": 15, "cache_read": 0.3, "cache_write": 3.75},
    "claude-sonnet-4-20250514": {"input": 3, "output": 15, "cache_read": 0.3, "cache_write": 3.75},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4, "cache_read": 0.08, "cache_write": 1},
}

MODEL_DISPLAY = {
    "claude-opus-4-6": "Opus 4.6",
    "claude-opus-4-20250918": "Opus 4",
    "claude-sonnet-4-5-20250929": "Sonnet 4.5",
    "claude-sonnet-4-20250514": "Sonnet 4",
    "claude-haiku-4-5-20251001": "Haiku 4.5",
}

# Session cache: {(path, mtime, size): parsed_data}
_session_cache = {}


def classify_session(mtime):
    age = time.time() - mtime
    if age < 120:
        return "active"
    elif age < 600:
        return "recent"
    elif age < 3600:
        return "idle"
    else:
        return "completed"


def estimate_cost(tokens, model):
    pricing = None
    for key in PRICING:
        if key in model or model in key:
            pricing = PRICING[key]
            break
    if not pricing:
        # Default to opus pricing
        pricing = PRICING.get("claude-opus-4-6", {"input": 15, "output": 75, "cache_read": 1.5, "cache_write": 18.75})

    cost = (
        tokens.get("input", 0) * pricing["input"] / 1_000_000
        + tokens.get("output", 0) * pricing["output"] / 1_000_000
        + tokens.get("cache_read", 0) * pricing["cache_read"] / 1_000_000
        + tokens.get("cache_write", 0) * pricing["cache_write"] / 1_000_000
    )
    return round(cost, 4)


def get_git_info(cwd):
    if not cwd or not os.path.isdir(cwd):
        return None, False
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd, stderr=subprocess.DEVNULL, timeout=2
        ).decode().strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=cwd, stderr=subprocess.DEVNULL, timeout=2
        ).decode().strip()
        return branch, bool(dirty)
    except Exception:
        return None, False


def format_tokens(n):
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n/1_000:.1f}k"
    return str(n)


def parse_session(jsonl_path):
    stat = os.stat(jsonl_path)
    cache_key = (str(jsonl_path), stat.st_mtime, stat.st_size)
    if cache_key in _session_cache:
        cached = _session_cache[cache_key]
        cached["status"] = classify_session(stat.st_mtime)
        return cached

    info = {
        "id": jsonl_path.stem[:8],
        "fullId": jsonl_path.stem,
        "status": classify_session(stat.st_mtime),
        "cwd": "",
        "project": "",
        "model": "",
        "modelDisplay": "",
        "version": "",
        "gitBranch": None,
        "gitDirty": False,
        "slug": "",
        "firstTimestamp": "",
        "lastTimestamp": "",
        "durationMinutes": 0,
        "tokens": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
        "tokensTotal": 0,
        "cost": 0,
        "tools": 0,
        "agents": 0,
        "agentTypes": [],
        "subagentFiles": 0,
        "messages": 0,
        "linesAdded": 0,
        "linesRemoved": 0,
        "liveState": "unknown",
        "lastTool": "",
        "hasTerminal": False,
        "launcher": None,
    }

    last_type = None
    last_content = []

    try:
        with open(jsonl_path, "r") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                etype = entry.get("type")

                if etype == "user":
                    if not info["cwd"]:
                        info["cwd"] = entry.get("cwd", "")
                        info["version"] = entry.get("version", "")
                        gb = entry.get("gitBranch", "")
                        if gb:
                            info["gitBranch"] = gb
                        sid = entry.get("sessionId", "")
                        if sid:
                            info["fullId"] = sid
                            info["id"] = sid[:8]

                    ts = entry.get("timestamp", "")
                    if ts:
                        if not info["firstTimestamp"]:
                            info["firstTimestamp"] = ts
                        info["lastTimestamp"] = ts

                    if entry.get("userType") == "external":
                        info["messages"] += 1

                elif etype == "assistant":
                    msg = entry.get("message", {})
                    model = msg.get("model", "")
                    if model:
                        info["model"] = model
                        info["modelDisplay"] = MODEL_DISPLAY.get(model, model)

                    slug = entry.get("slug", "")
                    if slug:
                        info["slug"] = slug

                    ts = entry.get("timestamp", "")
                    if ts:
                        if not info["firstTimestamp"]:
                            info["firstTimestamp"] = ts
                        info["lastTimestamp"] = ts

                    usage = msg.get("usage", {})
                    info["tokens"]["input"] += usage.get("input_tokens", 0)
                    info["tokens"]["output"] += usage.get("output_tokens", 0)
                    info["tokens"]["cache_read"] += usage.get("cache_read_input_tokens", 0)
                    info["tokens"]["cache_write"] += usage.get("cache_creation_input_tokens", 0)

                    for block in msg.get("content", []):
                        if block.get("type") == "tool_use":
                            info["tools"] += 1
                            if block.get("name") == "Task":
                                info["agents"] += 1
                                agent_type = block.get("input", {}).get("subagent_type", "")
                                desc = block.get("input", {}).get("description", "")
                                if agent_type:
                                    info["agentTypes"].append({"type": agent_type, "desc": desc})

                # Track last entry for live state
                last_type = etype
                last_content = msg.get("content", []) if etype == "assistant" else []

    except Exception:
        pass

    # Determine live state from last transcript entries
    try:
        if last_type == "user":
            info["liveState"] = "working"
        elif last_type == "assistant":
            # Check if last assistant message has AskUserQuestion
            has_ask = any(
                b.get("type") == "tool_use" and b.get("name") == "AskUserQuestion"
                for b in last_content
            )
            has_task = any(
                b.get("type") == "tool_use" and b.get("name") == "Task"
                for b in last_content
            )
            if has_ask:
                info["liveState"] = "choice"
            elif has_task:
                info["liveState"] = "working"
                running_agent = next(
                    (b.get("input", {}).get("subagent_type", "") for b in last_content
                     if b.get("type") == "tool_use" and b.get("name") == "Task"), ""
                )
                info["lastTool"] = f"Agent: {running_agent}" if running_agent else ""
            else:
                info["liveState"] = "idle"
                last_tool = next(
                    (b.get("name", "") for b in reversed(last_content)
                     if b.get("type") == "tool_use"), ""
                )
                info["lastTool"] = last_tool
        else:
            info["liveState"] = "idle"
    except Exception:
        pass

    # Compute derived fields
    info["tokensTotal"] = sum(info["tokens"].values())
    if info["model"]:
        info["cost"] = estimate_cost(info["tokens"], info["model"])
    if info["cwd"]:
        info["project"] = os.path.basename(info["cwd"])

    # Duration
    if info["firstTimestamp"] and info["lastTimestamp"]:
        try:
            t1 = datetime.fromisoformat(info["firstTimestamp"].replace("Z", "+00:00"))
            t2 = datetime.fromisoformat(info["lastTimestamp"].replace("Z", "+00:00"))
            info["durationMinutes"] = max(1, int((t2 - t1).total_seconds() / 60))
        except Exception:
            pass

    # Count subagent files
    subagent_dir = jsonl_path.parent / jsonl_path.stem / "subagents"
    if subagent_dir.exists():
        info["subagentFiles"] = len(list(subagent_dir.glob("*.jsonl")))

    # Git info (only for active/recent)
    if info["status"] in ("active", "recent") and info["cwd"]:
        branch, dirty = get_git_info(info["cwd"])
        if branch:
            info["gitBranch"] = branch
            info["gitDirty"] = dirty

    # Detect launcher (AgentZero/Tess etc)
    launcher = detect_launcher(jsonl_path)
    if launcher:
        info["launcher"] = launcher

    # Check for active terminal (only for non-completed)
    if info["status"] != "completed":
        info["hasTerminal"] = session_has_terminal(info)

    _session_cache[cache_key] = info
    return info


def scan_all_sessions(max_hours=24):
    sessions = []
    cutoff = time.time() - (max_hours * 3600)

    if not CLAUDE_PROJECTS.exists():
        return sessions

    for jsonl_file in CLAUDE_PROJECTS.glob("*/*.jsonl"):
        try:
            mtime = os.path.getmtime(jsonl_file)
            if mtime < cutoff:
                continue
            session = parse_session(jsonl_file)
            if session:
                sessions.append(session)
        except Exception:
            continue

    # Sort: active first, then recent, then by last timestamp descending
    status_order = {"active": 0, "recent": 1, "idle": 2, "completed": 3}
    sessions.sort(key=lambda s: (status_order.get(s["status"], 9), -(
        datetime.fromisoformat(s["lastTimestamp"].replace("Z", "+00:00")).timestamp()
        if s["lastTimestamp"] else 0
    )))

    return sessions


def get_plan_usage():
    """Read plan usage from claude-status cache file."""
    cache_path = Path.home() / ".cache" / "claude-status" / "cache.json"
    try:
        with open(cache_path, "r") as f:
            cached = json.load(f)

        usage = cached.get("usage", {})
        plan = cached.get("plan", "")
        cache_age = time.time() - cached.get("timestamp", 0)

        five = usage.get("five_hour", {})
        seven = usage.get("seven_day", {})
        extra = usage.get("extra_usage", {})

        # Format reset times
        session_reset = None
        if five.get("resets_at"):
            try:
                rt = datetime.fromisoformat(five["resets_at"])
                now = datetime.now(timezone.utc)
                secs = max(0, int((rt - now).total_seconds()))
                h, m = secs // 3600, (secs % 3600) // 60
                session_reset = f"{h}h {m:02d}m" if h > 0 else f"{m}m"
            except Exception:
                pass

        weekly_reset = None
        if seven.get("resets_at"):
            try:
                rt = datetime.fromisoformat(seven["resets_at"])
                now = datetime.now(timezone.utc)
                secs = max(0, int((rt - now).total_seconds()))
                if secs >= 86400:
                    d, h = secs // 86400, (secs % 86400) // 3600
                    weekly_reset = f"{d}d {h}h"
                else:
                    h, m = secs // 3600, (secs % 3600) // 60
                    weekly_reset = f"{h}h {m:02d}m" if h > 0 else f"{m}m"
            except Exception:
                pass

        extra_used = (extra.get("used_credits") or 0) / 100
        extra_limit = (extra.get("monthly_limit") or 0) / 100
        extra_enabled = extra.get("is_enabled", False)

        return {
            "plan": plan,
            "session": {
                "pct": five.get("utilization", 0) or 0,
                "reset": session_reset,
            },
            "weekly": {
                "pct": seven.get("utilization", 0) or 0,
                "reset": weekly_reset,
            },
            "extra": {
                "enabled": extra_enabled,
                "used": extra_used,
                "limit": extra_limit,
                "pct": (extra.get("utilization") or 0) if extra_enabled else 0,
            },
            "cacheAge": round(cache_age),
        }
    except Exception:
        return None


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Claude Sessions</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }

body {
    background: #0d1117;
    color: #c9d1d9;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    font-size: 14px;
    min-height: 100vh;
}

.header {
    background: #161b22;
    border-bottom: 1px solid #30363d;
    padding: 16px 24px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
}

.header h1 {
    font-size: 20px;
    font-weight: 600;
    color: #f0f6fc;
}

.header h1 span {
    color: #7c3aed;
}

.header-right {
    display: flex;
    align-items: center;
    gap: 16px;
    font-size: 13px;
    color: #8b949e;
}

.refresh-dot {
    width: 8px;
    height: 8px;
    background: #3fb950;
    border-radius: 50%;
    display: inline-block;
    animation: pulse-dot 2s infinite;
}

@keyframes pulse-dot {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
}

.summary {
    display: flex;
    gap: 24px;
    padding: 16px 24px;
    border-bottom: 1px solid #21262d;
    flex-wrap: wrap;
}

.summary-item {
    display: flex;
    align-items: center;
    gap: 8px;
}

.summary-label {
    color: #8b949e;
    font-size: 13px;
}

.summary-value {
    font-weight: 600;
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 15px;
}

.summary-value.green { color: #3fb950; }
.summary-value.yellow { color: #d29922; }
.summary-value.blue { color: #58a6ff; }
.summary-value.purple { color: #bc8cff; }

.sessions {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(480px, 1fr));
    gap: 16px;
    padding: 24px;
}

.card {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    border-left: 4px solid #30363d;
    padding: 16px 20px;
    transition: border-color 0.3s, box-shadow 0.3s;
}

.card:hover {
    border-color: #58a6ff;
    box-shadow: 0 0 12px rgba(88, 166, 255, 0.1);
}

.card.active {
    border-left-color: #3fb950;
    animation: card-pulse 3s infinite;
}

.live-state {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    padding: 3px 10px;
    border-radius: 10px;
    font-weight: 500;
}

.live-state.working {
    background: #0c2d6b;
    color: #58a6ff;
}

.live-state.idle {
    background: #1c2128;
    color: #8b949e;
}

.live-state.choice {
    background: #3d1f00;
    color: #f0883e;
    animation: choice-pulse 1.5s infinite;
}

@keyframes choice-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
}

.live-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    display: inline-block;
}

.live-dot.working {
    background: #58a6ff;
    animation: spin-dot 1s linear infinite;
}

.live-dot.idle { background: #8b949e; }

.live-dot.choice {
    background: #f0883e;
    animation: choice-pulse 1.5s infinite;
}

@keyframes spin-dot {
    0% { box-shadow: 0 0 0 0 rgba(88, 166, 255, 0.6); }
    100% { box-shadow: 0 0 0 6px rgba(88, 166, 255, 0); }
}

.card.recent { border-left-color: #d29922; }
.card.idle { border-left-color: #484f58; }

@keyframes card-pulse {
    0%, 100% { box-shadow: 0 0 0 rgba(63, 185, 80, 0); }
    50% { box-shadow: 0 0 16px rgba(63, 185, 80, 0.15); }
}

.card-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 12px;
}

.card-title {
    display: flex;
    flex-direction: column;
    gap: 4px;
}

.card-slug {
    font-size: 15px;
    font-weight: 600;
    color: #f0f6fc;
}

.card-project {
    font-size: 13px;
    color: #d29922;
    font-family: 'SF Mono', 'Fira Code', monospace;
}

.card-badge {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    padding: 2px 8px;
    border-radius: 12px;
    letter-spacing: 0.5px;
}

.card-badge.active { background: #0f2d16; color: #3fb950; }
.card-badge.recent { background: #2d2200; color: #d29922; }
.card-badge.idle { background: #1c1e23; color: #8b949e; }

.card-meta {
    display: flex;
    gap: 12px;
    margin-bottom: 12px;
    flex-wrap: wrap;
    font-size: 13px;
    color: #8b949e;
}

.card-meta .model { color: #58a6ff; font-weight: 500; }
.card-meta .branch { color: #bc8cff; }
.card-meta .dirty { color: #f85149; }
.card-meta .clean { color: #3fb950; }

.card-stats {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(100px, 1fr));
    gap: 8px;
    margin-bottom: 12px;
}

.stat {
    display: flex;
    flex-direction: column;
    gap: 2px;
}

.stat-label {
    font-size: 11px;
    color: #6e7681;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

.stat-value {
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 14px;
    font-weight: 500;
}

.stat-value.cost { color: #3fb950; }
.stat-value.tokens { color: #58a6ff; }
.stat-value.tools { color: #d2a8ff; }
.stat-value.agents { color: #f0883e; }
.stat-value.lines-add { color: #3fb950; }
.stat-value.lines-rem { color: #f85149; }
.stat-value.duration { color: #8b949e; }

.context-bar-wrapper {
    margin-top: 8px;
}

.context-bar-label {
    display: flex;
    justify-content: space-between;
    font-size: 11px;
    color: #6e7681;
    margin-bottom: 4px;
}

.context-bar {
    height: 6px;
    background: #21262d;
    border-radius: 3px;
    overflow: hidden;
}

.context-bar-fill {
    height: 100%;
    border-radius: 3px;
    transition: width 0.5s ease;
}

.context-bar-fill.low { background: #3fb950; }
.context-bar-fill.medium { background: #d29922; }
.context-bar-fill.high { background: #f85149; }

.empty-state {
    text-align: center;
    padding: 80px 24px;
    color: #8b949e;
}

.empty-state h2 {
    font-size: 18px;
    margin-bottom: 8px;
    color: #c9d1d9;
}

.footer {
    text-align: center;
    padding: 16px;
    color: #484f58;
    font-size: 12px;
    border-top: 1px solid #21262d;
}

.plan-panel {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 16px 24px;
    margin: 0 24px;
    display: flex;
    align-items: center;
    gap: 32px;
    flex-wrap: wrap;
}

.plan-badge {
    font-size: 13px;
    font-weight: 700;
    color: #f0f6fc;
    background: #7c3aed;
    padding: 4px 12px;
    border-radius: 12px;
    white-space: nowrap;
}

.plan-meters {
    display: flex;
    gap: 28px;
    flex: 1;
    flex-wrap: wrap;
}

.plan-meter {
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 160px;
    flex: 1;
}

.plan-meter-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 12px;
}

.plan-meter-label { color: #8b949e; }
.plan-meter-value {
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-weight: 600;
}
.plan-meter-reset {
    color: #6e7681;
    font-size: 11px;
    font-family: 'SF Mono', 'Fira Code', monospace;
}

.plan-bar {
    height: 8px;
    background: #21262d;
    border-radius: 4px;
    overflow: hidden;
}

.plan-bar-fill {
    height: 100%;
    border-radius: 4px;
    transition: width 0.5s ease;
}

.plan-bar-fill.low { background: #3fb950; }
.plan-bar-fill.medium { background: #d29922; }
.plan-bar-fill.high { background: #f85149; }

.extra-info {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 12px;
    background: #1c1e23;
    border-radius: 6px;
    font-size: 13px;
    font-family: 'SF Mono', 'Fira Code', monospace;
}

.extra-label { color: #8b949e; }
.extra-value { color: #3fb950; font-weight: 600; }

.card { overflow: visible; }
.card-stats { overflow: visible; }
.agent-stat { position: relative; }
.agent-list {
    display: none;
    position: absolute;
    bottom: 100%;
    left: 0;
    z-index: 50;
    background: #2d333b;
    border: 1px solid #444c56;
    border-radius: 8px;
    padding: 8px 0;
    margin-top: 6px;
    min-width: 220px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.4);
}
.agent-list.open { display: block; }
.agent-item {
    padding: 6px 12px;
    display: flex;
    gap: 10px;
    align-items: center;
    font-size: 12px;
}
.agent-item:hover { background: #363d47; }
.agent-type {
    color: #f0883e;
    font-weight: 600;
    font-family: 'SF Mono', 'Fira Code', monospace;
    white-space: nowrap;
}
.agent-desc {
    color: #8b949e;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.agent-toggle:hover { opacity: 0.8; }

[data-tip] {
    position: relative;
    cursor: help;
    border-bottom: 1px dotted #484f58;
}
[data-tip]:hover::after {
    content: attr(data-tip);
    position: absolute;
    bottom: calc(100% + 8px);
    left: 50%;
    transform: translateX(-50%);
    background: #2d333b;
    color: #c9d1d9;
    padding: 8px 12px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 400;
    white-space: nowrap;
    max-width: 320px;
    white-space: normal;
    z-index: 100;
    box-shadow: 0 4px 12px rgba(0,0,0,0.4);
    border: 1px solid #444c56;
    line-height: 1.4;
    pointer-events: none;
}
[data-tip]:hover::before {
    content: '';
    position: absolute;
    bottom: calc(100% + 2px);
    left: 50%;
    transform: translateX(-50%);
    border: 6px solid transparent;
    border-top-color: #2d333b;
    z-index: 101;
    pointer-events: none;
}
.extra-limit { color: #6e7681; }

.filter-bar {
    display: flex;
    gap: 8px;
    padding: 12px 24px;
    flex-wrap: wrap;
    align-items: center;
}

.filter-label {
    color: #6e7681;
    font-size: 12px;
    margin-right: 4px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

.filter-btn {
    background: #21262d;
    color: #8b949e;
    border: 1px solid #30363d;
    border-radius: 16px;
    padding: 4px 14px;
    font-size: 12px;
    cursor: pointer;
    transition: all 0.2s;
    font-family: inherit;
}

.filter-btn:hover {
    background: #30363d;
    color: #c9d1d9;
}

.filter-btn.active {
    background: #388bfd26;
    color: #58a6ff;
    border-color: #388bfd;
}

.filter-count {
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 11px;
    margin-left: 4px;
    opacity: 0.7;
}

@media (max-width: 540px) {
    .sessions {
        grid-template-columns: 1fr;
        padding: 12px;
    }
    .summary { padding: 12px; gap: 12px; }
    .header { padding: 12px; }
}
</style>
</head>
<body>

<div class="header">
    <h1><span>Claude</span> Sessions Dashboard</h1>
    <div class="header-right">
        <span class="refresh-dot"></span>
        <span id="last-update">Updating...</span>
    </div>
</div>

<div class="summary" id="summary"></div>
<div class="plan-panel" id="plan-panel" style="display:none"></div>
<div class="filter-bar" id="filter-bar"></div>
<div class="sessions" id="sessions"></div>
<div class="footer">Claude Code Session Dashboard &middot; Auto-refresh 5s</div>

<script>
const REFRESH_MS = 5000;

function formatTokens(n) {
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
    return String(n);
}

function formatWhen(isoStr) {
    if (!isoStr) return '';
    const d = new Date(isoStr);
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1);
    const dayBefore = new Date(today); dayBefore.setDate(today.getDate() - 2);
    const target = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const time = d.toLocaleTimeString('sv-SE', {hour:'2-digit', minute:'2-digit'});
    if (target.getTime() === today.getTime()) return 'idag ' + time;
    if (target.getTime() === yesterday.getTime()) return 'ig\u00e5r ' + time;
    if (target.getTime() === dayBefore.getTime()) return 'f\u00f6rrg\u00e5r ' + time;
    const months = ['Jan','Feb','Mar','Apr','Maj','Jun','Jul','Aug','Sep','Okt','Nov','Dec'];
    return d.getDate() + ' ' + months[d.getMonth()];
}

function renderSummary(summary) {
    const el = document.getElementById('summary');
    el.innerHTML = `
        <div class="summary-item">
            <span class="summary-label" data-tip="Sessioner med aktivitet senaste 2 minuterna">Active</span>
            <span class="summary-value green">${summary.active}</span>
        </div>
        <div class="summary-item">
            <span class="summary-label" data-tip="Sessioner aktiva 2\u201310 minuter sedan">Recent</span>
            <span class="summary-value yellow">${summary.recent}</span>
        </div>
        <div class="summary-item">
            <span class="summary-label" data-tip="Alla sessioner fr\u00e5n senaste 24 timmarna (active + recent + idle + completed)">Total</span>
            <span class="summary-value blue">${summary.total}</span>
        </div>
        <div class="summary-item">
            <span class="summary-label" data-tip="Totalt antal tokens f\u00f6rbrukade av alla sessioner (in + ut + cache)">Tokens</span>
            <span class="summary-value purple">${formatTokens(summary.totalTokens)}</span>
        </div>
        <div class="summary-item">
            <span class="summary-label" data-tip="Estimerad totalkostnad baserat p\u00e5 tokens och modellpriser">Cost</span>
            <span class="summary-value green">$${summary.totalCost.toFixed(2)}</span>
        </div>
    `;
}

function renderCard(s) {
    const ctxPct = s.tokensTotal > 0
        ? Math.min(100, Math.round((s.tokens.input + s.tokens.output + s.tokens.cache_read + s.tokens.cache_write) / 200000 * 100))
        : 0;
    const ctxClass = ctxPct > 80 ? 'high' : ctxPct > 50 ? 'medium' : 'low';

    const gitInfo = s.gitBranch
        ? `<span class="branch">\u2387 ${s.gitBranch}</span> <span class="${s.gitDirty ? 'dirty' : 'clean'}">${s.gitDirty ? '\u25cf' : '\u2714'}</span>`
        : '';

    const slug = s.slug || s.id;

    return `
    <div class="card ${s.status}">
        <div class="card-header">
            <div class="card-title">
                <div class="card-slug">${slug}</div>
                <div class="card-project">${s.project || 'unknown'}</div>
            </div>
            <div style="display:flex;flex-direction:column;align-items:flex-end;gap:4px">
                <span class="card-badge ${s.status}" data-tip="${s.status === 'active' ? 'Aktivitet senaste 2 min' : s.status === 'recent' ? 'Aktiv 2\u201310 min sedan' : s.status === 'idle' ? 'Vilande, 10\u201360 min sedan' : 'Avslutad, \u00f6ver 1h sedan'}">${s.status}</span>
                ${s.status !== 'active' ? `<span style="color:#6e7681;font-size:11px;font-family:'SF Mono','Fira Code',monospace">${formatWhen(s.lastTimestamp)}</span>` : ''}
            </div>
        </div>
        <div class="card-meta">
            ${s.status === 'active' ? `<span class="live-state ${s.liveState}" data-tip="${
                s.liveState === 'working' ? 'Claude arbetar just nu' + (s.lastTool ? ' (' + s.lastTool + ')' : '') :
                s.liveState === 'choice' ? 'V\u00e4ntar p\u00e5 ditt val (fr\u00e5ga visas i terminalen)' :
                'V\u00e4ntar p\u00e5 ditt meddelande'
            }"><span class="live-dot ${s.liveState}"></span>${
                s.liveState === 'working' ? 'Arbetar...' :
                s.liveState === 'choice' ? 'V\u00e4ntar p\u00e5 val' :
                'V\u00e4ntar'
            }</span>` : ''}
            ${s.hasTerminal ? '<span style="color:#3fb950" data-tip="Aktiv terminal k\u00f6r denna session">\u25cf Terminal</span>' : s.status !== 'completed' ? '<span style="color:#6e7681" data-tip="Ingen terminal hittad f\u00f6r denna session">\u25cb Ingen terminal</span>' : ''}
            ${s.launcher ? `<span style="color:#bc8cff;font-weight:600" data-tip="Startad av ${s.launcher}">\u2692 ${s.launcher}</span>` : ''}
            <span class="model">${s.modelDisplay || s.model || '?'}</span>
            ${gitInfo}
            <span>v${s.version || '?'}</span>
            <span>${s.durationMinutes} min</span>
        </div>
        <div class="card-stats">
            <div class="stat">
                <span class="stat-label" data-tip="Estimerad kostnad baserat p\u00e5 tokens \u00d7 modellpris">Cost</span>
                <span class="stat-value cost">$${s.cost.toFixed(2)}</span>
            </div>
            <div class="stat">
                <span class="stat-label" data-tip="Tokens skickade till modellen (input + cache-l\u00e4sningar)">Tokens In</span>
                <span class="stat-value tokens">${formatTokens(s.tokens.input + s.tokens.cache_read)}</span>
            </div>
            <div class="stat">
                <span class="stat-label" data-tip="Tokens genererade av modellen">Tokens Out</span>
                <span class="stat-value tokens">${formatTokens(s.tokens.output)}</span>
            </div>
            <div class="stat">
                <span class="stat-label" data-tip="Antal verktygsanrop (Read, Edit, Bash, Grep, etc)">Tools</span>
                <span class="stat-value tools">\uD83D\uDD27 ${s.tools}</span>
            </div>
            <div class="stat agent-stat">
                <span class="stat-label" data-tip="Klicka f\u00f6r att visa vilka agenter som anv\u00e4ndes">Agents</span>
                <span class="stat-value agents agent-toggle" onclick="this.parentElement.querySelector('.agent-list').classList.toggle('open')" style="cursor:pointer">\uD83E\uDD16 ${s.agents} calls${s.subagentFiles ? ` (${s.subagentFiles} agents)` : ''} ${s.agentTypes.length ? '\u25BE' : ''}</span>
                ${s.agentTypes.length ? `<div class="agent-list">${s.agentTypes.map(a => `<div class="agent-item"><span class="agent-type">${a.type}</span><span class="agent-desc">${a.desc}</span></div>`).join('')}</div>` : ''}
            </div>
            <div class="stat">
                <span class="stat-label" data-tip="Antal meddelanden fr\u00e5n dig i sessionen">Messages</span>
                <span class="stat-value duration">${s.messages}</span>
            </div>
        </div>
        <div class="context-bar-wrapper">
            <div class="context-bar-label">
                <span data-tip="Andel av context-f\u00f6nstret (200k tokens) som anv\u00e4nds. R\u00f6d = n\u00e4ra fullt.">Context</span>
                <span>${formatTokens(s.tokensTotal)} tokens</span>
            </div>
            <div class="context-bar">
                <div class="context-bar-fill ${ctxClass}" style="width: ${Math.min(ctxPct, 100)}%"></div>
            </div>
        </div>
    </div>`;
}

function renderPlan(plan) {
    const el = document.getElementById('plan-panel');
    if (!plan) { el.style.display = 'none'; return; }
    el.style.display = 'flex';

    const barClass = (pct) => pct >= 80 ? 'high' : pct >= 50 ? 'medium' : 'low';

    let extraHtml = '';
    if (plan.extra && plan.extra.enabled) {
        const currency = '$';
        if (plan.extra.limit > 0) {
            extraHtml = `
            <div class="extra-info">
                <span class="extra-label">Extra</span>
                <span class="extra-value">${currency}${plan.extra.used.toFixed(2)}</span>
                <span class="extra-limit">/ ${currency}${plan.extra.limit.toFixed(0)}</span>
            </div>`;
        } else {
            extraHtml = `
            <div class="extra-info">
                <span class="extra-label">Extra</span>
                <span class="extra-value">${currency}${plan.extra.used.toFixed(2)}</span>
            </div>`;
        }
    }

    el.innerHTML = `
        <span class="plan-badge" data-tip="Din Anthropic-prenumeration">${plan.plan || 'Plan'}</span>
        <div class="plan-meters">
            <div class="plan-meter">
                <div class="plan-meter-header">
                    <span class="plan-meter-label" data-tip="Anv\u00e4ndning i nuvarande 5-timmarsblock. Nollst\u00e4lls automatiskt.">Session (5h)</span>
                    <span class="plan-meter-value" style="color: ${plan.session.pct >= 80 ? '#f85149' : plan.session.pct >= 50 ? '#d29922' : '#3fb950'}">${plan.session.pct.toFixed(0)}%</span>
                </div>
                <div class="plan-bar">
                    <div class="plan-bar-fill ${barClass(plan.session.pct)}" style="width: ${Math.min(plan.session.pct, 100)}%"></div>
                </div>
                ${plan.session.reset ? `<span class="plan-meter-reset">\u23f1 ${plan.session.reset}</span>` : ''}
            </div>
            <div class="plan-meter">
                <div class="plan-meter-header">
                    <span class="plan-meter-label" data-tip="Total anv\u00e4ndning senaste 7 dagarna, alla modeller. Nollst\u00e4lls veckovis.">Weekly</span>
                    <span class="plan-meter-value" style="color: ${plan.weekly.pct >= 80 ? '#f85149' : plan.weekly.pct >= 50 ? '#d29922' : '#3fb950'}">${plan.weekly.pct.toFixed(0)}%</span>
                </div>
                <div class="plan-bar">
                    <div class="plan-bar-fill ${barClass(plan.weekly.pct)}" style="width: ${Math.min(plan.weekly.pct, 100)}%"></div>
                </div>
                ${plan.weekly.reset ? `<span class="plan-meter-reset">\u23f1 ${plan.weekly.reset}</span>` : ''}
            </div>
        </div>
        ${extraHtml}
    `;
}

let _openAgentMenu = null;
let _allSessions = [];
let _currentFilter = 'default';

const FILTERS = {
    'all':       { label: 'Alla',                    fn: () => true },
    'active':    { label: 'Aktiva',                  fn: s => s.status === 'active' },
    'default':   { label: 'Senaste (+1h)',           fn: s => s.status !== 'completed' },
    'today':     { label: 'Idag',                    fn: s => { const d = new Date(s.lastTimestamp); const t = new Date(); return d.toDateString() === t.toDateString(); }},
    'no-empty':  { label: 'Med aktivitet',           fn: s => s.tools > 0 || s.messages > 3 },
    'expensive': { label: 'Dyra (>$5)',              fn: s => s.cost > 5 },
    'terminal': { label: 'Med terminal',            fn: s => s.hasTerminal },
};

function renderFilters() {
    const el = document.getElementById('filter-bar');
    const counts = {};
    for (const [key, f] of Object.entries(FILTERS)) {
        counts[key] = _allSessions.filter(f.fn).length;
    }
    el.innerHTML = '<span class="filter-label">Filter</span>' +
        Object.entries(FILTERS).map(([key, f]) =>
            `<button class="filter-btn ${key === _currentFilter ? 'active' : ''}" onclick="setFilter('${key}')">${f.label}<span class="filter-count">${counts[key]}</span></button>`
        ).join('');
}

function setFilter(key) {
    _currentFilter = key;
    applyFilter();
    renderFilters();
}

function applyFilter() {
    const fn = FILTERS[_currentFilter]?.fn || (() => true);
    const filtered = _allSessions.filter(fn);
    _renderSessionCards(filtered);
}

function renderSessions(sessions) {
    _allSessions = sessions;
    renderFilters();
    applyFilter();
}

function _renderSessionCards(sessions) {
    const el = document.getElementById('sessions');
    // Remember which agent menu was open
    const openMenu = el.querySelector('.agent-list.open');
    if (openMenu) {
        const card = openMenu.closest('.card');
        const slug = card ? card.querySelector('.card-slug')?.textContent : null;
        _openAgentMenu = slug;
    } else if (!document.querySelector('.agent-list.open')) {
        _openAgentMenu = null;
    }

    if (!sessions.length) {
        el.innerHTML = '<div class="empty-state"><h2>Inga sessioner matchar filtret</h2><p>Prova ett annat filter.</p></div>';
        return;
    }
    el.innerHTML = sessions.map(renderCard).join('');

    // Restore open menu
    if (_openAgentMenu) {
        el.querySelectorAll('.card-slug').forEach(slugEl => {
            if (slugEl.textContent === _openAgentMenu) {
                const list = slugEl.closest('.card')?.querySelector('.agent-list');
                if (list) list.classList.add('open');
            }
        });
    }
}

async function refresh() {
    try {
        const res = await fetch('/api/sessions');
        const data = await res.json();
        renderSessions(data.sessions);
        renderSummary(data.summary);
        renderPlan(data.plan);
        document.getElementById('last-update').textContent =
            'Updated ' + new Date().toLocaleTimeString();
    } catch (err) {
        document.getElementById('last-update').textContent = 'Error: ' + err.message;
    }
}

document.addEventListener('click', (e) => {
    if (!e.target.closest('.agent-stat')) {
        document.querySelectorAll('.agent-list.open').forEach(el => el.classList.remove('open'));
    }
});

setInterval(refresh, REFRESH_MS);
refresh();
</script>
</body>
</html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress access logs

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self._serve_html()
        elif path == "/api/sessions":
            params = parse_qs(parsed.query)
            hours = int(params.get("hours", [24])[0])
            self._serve_sessions(hours)
        else:
            self.send_error(404)

    def _serve_html(self):
        content = HTML_TEMPLATE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _serve_sessions(self, hours=24):
        sessions = scan_all_sessions(hours)

        summary = {
            "active": sum(1 for s in sessions if s["status"] == "active"),
            "recent": sum(1 for s in sessions if s["status"] == "recent"),
            "total": len(sessions),
            "totalTokens": sum(s["tokensTotal"] for s in sessions),
            "totalCost": sum(s["cost"] for s in sessions),
        }

        plan_usage = get_plan_usage()
        data = json.dumps({"sessions": sessions, "summary": summary, "plan": plan_usage})
        content = data.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(content)


def main():
    parser = argparse.ArgumentParser(description="Claude Code Session Dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port (default {DEFAULT_PORT})")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Host (default {DEFAULT_HOST})")
    parser.add_argument("--hours", type=int, default=24, help="Show sessions from last N hours (default 24)")
    parser.add_argument("--open", action="store_true", help="Open browser on start")
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), DashboardHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"Claude Sessions Dashboard running at {url}")
    print(f"Scanning: {CLAUDE_PROJECTS}")
    print(f"Press Ctrl+C to stop")

    if args.open:
        import webbrowser
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
