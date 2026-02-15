import os
import signal
import subprocess
import threading
import time
from typing import Optional

import libtmux

from config.defaults import (
    CLAUDE_COMMAND,
    SESSION_PREFIX,
    TTYD_BASE_PORT,
    TTYD_COMMAND,
)
from src.session_state import SessionInfo, SessionState


class AgentManager:
    """Manages Claude Code sessions in tmux with optional ttyd remote access."""

    def __init__(self):
        self.server = libtmux.Server()
        self._sessions: dict[str, SessionInfo] = {}
        self._lock = threading.Lock()
        self._next_ttyd_port = TTYD_BASE_PORT
        self._discover_existing_sessions()

    # -- Session lifecycle --

    def create_session(
        self, project_name: str, working_dir: str = "~"
    ) -> SessionInfo:
        session_name = f"{SESSION_PREFIX}{project_name}"
        working_dir = os.path.expanduser(working_dir)

        tmux_session = self.server.new_session(
            session_name=session_name,
            start_directory=working_dir,
            attach=False,
            x=100,
            y=42,
        )

        pane = tmux_session.active_window.active_pane
        pane.send_keys("export TERM=xterm-256color", enter=True)
        pane.send_keys(CLAUDE_COMMAND, enter=True)

        info = SessionInfo(
            name=project_name,
            tmux_session_name=session_name,
            state=SessionState.WORKING,
            working_directory=working_dir,
        )

        with self._lock:
            self._sessions[session_name] = info

        return info

    def kill_session(self, session_name: str) -> bool:
        try:
            self.stop_ttyd(session_name)

            tmux_session = self._find_tmux_session(session_name)
            if tmux_session:
                tmux_session.kill()

            with self._lock:
                if session_name in self._sessions:
                    self._sessions[session_name].state = SessionState.STOPPED
                    del self._sessions[session_name]
            return True
        except Exception as e:
            print(f"Error killing session {session_name}: {e}")
            return False

    def kill_all_sessions(self) -> None:
        with self._lock:
            names = list(self._sessions.keys())
        for name in names:
            self.kill_session(name)

    # -- Interaction --

    def send_command(self, session_name: str, command: str) -> bool:
        pane = self._get_pane(session_name)
        if not pane:
            return False
        pane.send_keys(command, enter=True)
        return True

    def send_keys(self, session_name: str, keys: str, enter: bool = False) -> bool:
        pane = self._get_pane(session_name)
        if not pane:
            return False
        pane.send_keys(keys, enter=enter)
        return True

    def capture_output(self, session_name: str) -> str:
        pane = self._get_pane(session_name)
        if not pane:
            return ""
        try:
            lines = pane.capture_pane()
            return "\n".join(lines) if isinstance(lines, list) else str(lines)
        except Exception as e:
            print(f"Error capturing {session_name}: {e}")
            return ""

    # -- Query --

    def list_sessions(self) -> list[SessionInfo]:
        with self._lock:
            return list(self._sessions.values())

    def get_session(self, session_name: str) -> Optional[SessionInfo]:
        with self._lock:
            return self._sessions.get(session_name)

    def session_exists(self, session_name: str) -> bool:
        return self._find_tmux_session(session_name) is not None

    # -- ttyd remote access --

    def start_ttyd(self, session_name: str, port: Optional[int] = None) -> Optional[int]:
        with self._lock:
            info = self._sessions.get(session_name)
            if not info:
                return None
            if info.ttyd_pid:
                return info.ttyd_port

        if port is None:
            port = self._next_ttyd_port
            self._next_ttyd_port += 1

        try:
            proc = subprocess.Popen(
                [
                    TTYD_COMMAND,
                    "--writable",
                    "--port", str(port),
                    "tmux", "attach-session", "-t", session_name,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            with self._lock:
                if session_name in self._sessions:
                    self._sessions[session_name].ttyd_port = port
                    self._sessions[session_name].ttyd_pid = proc.pid

            return port
        except FileNotFoundError:
            print("ttyd not found. Install with: sudo apt install ttyd")
            return None
        except Exception as e:
            print(f"Error starting ttyd for {session_name}: {e}")
            return None

    def stop_ttyd(self, session_name: str) -> None:
        with self._lock:
            info = self._sessions.get(session_name)
            if not info or not info.ttyd_pid:
                return
            pid = info.ttyd_pid

        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

        with self._lock:
            if session_name in self._sessions:
                self._sessions[session_name].ttyd_pid = None
                self._sessions[session_name].ttyd_port = None

    def get_tailscale_ip(self) -> Optional[str]:
        try:
            result = subprocess.run(
                ["tailscale", "ip", "-4"],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout.strip() if result.returncode == 0 else None
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None

    def get_ttyd_url(self, session_name: str) -> Optional[str]:
        with self._lock:
            info = self._sessions.get(session_name)
            if not info or not info.ttyd_port:
                return None

        ts_ip = self.get_tailscale_ip()
        host = ts_ip or "localhost"
        return f"http://{host}:{info.ttyd_port}"

    # -- Discovery --

    def _discover_existing_sessions(self) -> set[str]:
        """Find existing tmux sessions with the claude_ prefix and adopt them.

        Returns set of newly discovered session names.
        """
        new_sessions = set()
        try:
            for tmux_session in self.server.sessions:
                name = tmux_session.name
                if not name.startswith(SESSION_PREFIX):
                    continue
                if name in self._sessions:
                    continue

                project_name = name[len(SESSION_PREFIX):]
                pane = None
                try:
                    pane = tmux_session.active_window.active_pane
                    cwd = pane.pane_current_path or ""
                except Exception:
                    cwd = ""

                # Detect initial state from current tmux output
                try:
                    pane_lines = pane.capture_pane() if pane else []
                    pane_output = "\n".join(pane_lines) if isinstance(pane_lines, list) else str(pane_lines)
                    initial_state = self._detect_initial_state(pane_output)
                except Exception:
                    initial_state = SessionState.WORKING

                info = SessionInfo(
                    name=project_name,
                    tmux_session_name=name,
                    state=initial_state,
                    working_directory=cwd,
                )
                self._sessions[name] = info
                new_sessions.add(name)
        except Exception as e:
            print(f"Discovery error: {e}")
        return new_sessions

    def sync_sessions(self) -> set[str]:
        """Re-scan tmux for new/removed sessions. Called periodically by monitor.

        Returns set of newly discovered session names.
        """
        # Discover new sessions
        new_sessions = self._discover_existing_sessions()

        # Remove sessions whose tmux session is gone
        with self._lock:
            gone = [
                name for name in self._sessions
                if not self._find_tmux_session(name)
            ]
            for name in gone:
                self._sessions[name].state = SessionState.STOPPED
                del self._sessions[name]

        return new_sessions

    # -- State detection for discovery --

    @staticmethod
    def _detect_initial_state(output: str) -> SessionState:
        """Quick state detection used when discovering existing sessions.

        Re-uses the same logic as SessionMonitor._detect_state so that the
        initial border color matches reality.
        """
        from src.session_monitor import SessionMonitor
        return SessionMonitor._detect_state(output)

    # -- Internal helpers --

    def _find_tmux_session(self, session_name: str):
        try:
            for s in self.server.sessions:
                if s.name == session_name:
                    return s
        except Exception:
            pass
        return None

    def _get_pane(self, session_name: str):
        tmux_session = self._find_tmux_session(session_name)
        if not tmux_session:
            return None
        try:
            return tmux_session.active_window.active_pane
        except Exception:
            return None
