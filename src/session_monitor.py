import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from config.defaults import (
    POLL_INTERVAL_SECONDS,
    IDLE_PROMPT_MARKERS,
    PROMPT_KEYWORDS,
    CHOICE_PREFIXES,
)
from src.session_state import SessionState, SessionInfo

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"


class SessionMonitor:
    """Background thread that polls tmux sessions and detects state changes.

    Primary detection uses JSONL transcripts (same source as web dashboard).
    Falls back to tmux pane output parsing when no transcript is found.
    """

    def __init__(
        self,
        agent_manager,
        on_state_change: Callable[[SessionInfo], None],
        poll_interval: float = POLL_INTERVAL_SECONDS,
    ):
        self._manager = agent_manager
        self._on_state_change = on_state_change
        self._poll_interval = poll_interval
        self._running = False
        self._thread: Optional[threading.Thread] = None
        # Cache: tmux_session_name -> (jsonl_path, last_size)
        self._transcript_cache: dict[str, tuple[Path, int]] = {}

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None

    def _loop(self) -> None:
        while self._running:
            try:
                self._poll_all()
            except Exception as e:
                print(f"Monitor error: {e}")
            time.sleep(self._poll_interval)

    def _poll_all(self) -> None:
        # Discover new sessions created externally (e.g. by Tess/Agent Zero)
        new_sessions = self._manager.sync_sessions()

        # Trigger callback for newly discovered sessions so the UI updates
        if new_sessions:
            for session in self._manager.list_sessions():
                if session.tmux_session_name in new_sessions:
                    self._on_state_change(session)

        for session in self._manager.list_sessions():
            if session.state == SessionState.STOPPED:
                continue

            # Check if tmux session still exists
            if not self._manager.session_exists(session.tmux_session_name):
                if session.state != SessionState.STOPPED:
                    session.state = SessionState.STOPPED
                    self._on_state_change(session)
                continue

            # Try JSONL-based detection first (authoritative)
            new_state = self._detect_state_from_transcript(session)

            # Fall back to tmux output if no transcript found
            if new_state is None:
                output = self._manager.capture_output(session.tmux_session_name)
                new_state = self._detect_state_from_output(output)

            if new_state != session.state:
                session.state = new_state
                self._on_state_change(session)

    def _detect_state_from_transcript(self, session: SessionInfo) -> Optional[SessionState]:
        """Detect state by reading the JSONL transcript (same as web dashboard).

        Returns None if no transcript is found for this session.
        """
        jsonl_path = self._find_transcript(session)
        if not jsonl_path or not jsonl_path.exists():
            return None

        try:
            stat = os.stat(jsonl_path)
            # Read last portion of file
            last_type = None
            last_content = []

            with open(jsonl_path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 50000))
                if f.tell() > 0:
                    f.readline()  # skip partial line
                for line in f:
                    try:
                        entry = json.loads(line)
                        etype = entry.get("type")
                        if etype in ("user", "assistant"):
                            last_type = etype
                            if etype == "assistant":
                                last_content = entry.get("message", {}).get("content", [])
                            else:
                                last_content = []
                    except (json.JSONDecodeError, KeyError):
                        continue

            if last_type == "user":
                return SessionState.WORKING
            elif last_type == "assistant":
                has_ask = any(
                    b.get("type") == "tool_use" and b.get("name") == "AskUserQuestion"
                    for b in last_content
                )
                has_task = any(
                    b.get("type") == "tool_use" and b.get("name") == "Task"
                    for b in last_content
                )
                if has_ask:
                    return SessionState.WAITING_INPUT
                elif has_task:
                    return SessionState.WORKING
                else:
                    return SessionState.IDLE
            else:
                return SessionState.IDLE

        except Exception as e:
            print(f"Transcript read error for {session.name}: {e}")
            return None

    def _find_transcript(self, session: SessionInfo) -> Optional[Path]:
        """Find the JSONL transcript file for a session.

        Matches by working directory and recency.
        """
        cache_key = session.tmux_session_name

        # Use cached path if file still exists and is being written to
        if cache_key in self._transcript_cache:
            cached_path, cached_size = self._transcript_cache[cache_key]
            if cached_path.exists():
                try:
                    current_size = cached_path.stat().st_size
                    if current_size >= cached_size:
                        self._transcript_cache[cache_key] = (cached_path, current_size)
                        return cached_path
                except OSError:
                    pass

        # Find the transcript by matching CWD
        cwd = session.working_directory
        if not cwd or not CLAUDE_PROJECTS.exists():
            return None

        # Also try to find via tmux pane's current CWD
        try:
            pane = self._manager._get_pane(session.tmux_session_name)
            if pane:
                tmux_cwd = pane.pane_current_path
                if tmux_cwd:
                    cwd = tmux_cwd
        except Exception:
            pass

        real_cwd = os.path.realpath(cwd)
        best_path = None
        best_mtime = 0

        for jsonl_file in CLAUDE_PROJECTS.glob("*/*.jsonl"):
            try:
                mtime = jsonl_file.stat().st_mtime
                if mtime <= best_mtime:
                    continue

                # Quick check: read first user entry for CWD
                with open(jsonl_file, "r") as f:
                    for line in f:
                        try:
                            entry = json.loads(line)
                            if entry.get("type") == "user":
                                file_cwd = entry.get("cwd", "")
                                if file_cwd and os.path.realpath(file_cwd) == real_cwd:
                                    best_path = jsonl_file
                                    best_mtime = mtime
                                break
                        except (json.JSONDecodeError, KeyError):
                            continue
            except OSError:
                continue

        if best_path:
            self._transcript_cache[cache_key] = (best_path, best_path.stat().st_size)

        return best_path

    @staticmethod
    def _detect_state_from_output(output: str) -> SessionState:
        """Fallback: detect state from tmux pane output text.

        Used only when no JSONL transcript is found.
        """
        if not output or not output.strip():
            return SessionState.WORKING

        lines = output.strip().split("\n")
        tail = lines[-15:] if len(lines) >= 15 else lines

        # Check bottom-up for idle prompt
        for line in reversed(tail[-8:]):
            stripped = line.strip()
            if not stripped:
                continue
            for marker in IDLE_PROMPT_MARKERS:
                if stripped.endswith(marker):
                    return SessionState.IDLE
            break  # only check last non-empty line

        # Check for interactive prompts / questions
        for line in tail[-8:]:
            stripped = line.strip()
            if any(stripped.startswith(p) for p in CHOICE_PREFIXES):
                return SessionState.WAITING_INPUT
            if any(kw in stripped for kw in PROMPT_KEYWORDS) and stripped.endswith("?"):
                return SessionState.WAITING_INPUT

        # Default: working
        return SessionState.WORKING

    # Keep backward compat alias
    _detect_state = _detect_state_from_output
