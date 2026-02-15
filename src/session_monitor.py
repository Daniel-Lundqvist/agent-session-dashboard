import threading
import time
from typing import Callable, Optional

from config.defaults import (
    POLL_INTERVAL_SECONDS,
    IDLE_PROMPT_MARKERS,
    ERROR_KEYWORDS,
    PROMPT_KEYWORDS,
    CHOICE_PREFIXES,
)
from src.session_state import SessionState, SessionInfo


class SessionMonitor:
    """Background thread that polls tmux sessions and detects state changes."""

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

            output = self._manager.capture_output(session.tmux_session_name)
            new_state = self._detect_state(output)

            if new_state != session.state:
                session.state = new_state
                session.last_output = output
                self._on_state_change(session)

    @staticmethod
    def _detect_state(output: str) -> SessionState:
        if not output or not output.strip():
            return SessionState.WORKING

        lines = output.strip().split("\n")
        tail = lines[-15:] if len(lines) >= 15 else lines
        recent = "\n".join(tail)

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
