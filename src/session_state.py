from enum import Enum
from dataclasses import dataclass, field
from typing import Optional
import time


class SessionState(Enum):
    IDLE = "idle"
    WORKING = "working"
    WAITING_INPUT = "waiting_input"
    ERROR = "error"
    STOPPED = "stopped"


STATE_ICONS = {
    SessionState.IDLE: "\U0001f7e1",          # yellow circle
    SessionState.WORKING: "\U0001f535",        # blue circle
    SessionState.WAITING_INPUT: "\U0001f7e0",  # orange circle
    SessionState.ERROR: "\U0001f534",          # red circle
    SessionState.STOPPED: "\u26ab",            # black circle
}

STATE_LABELS = {
    SessionState.IDLE: "v\u00e4ntar p\u00e5 meddelande",
    SessionState.WORKING: "arbetar...",
    SessionState.WAITING_INPUT: "v\u00e4ntar p\u00e5 ditt val",
    SessionState.ERROR: "fel",
    SessionState.STOPPED: "stoppad",
}

# Short labels for Tess/AgentZero API consumers
STATE_LABELS_SHORT = {
    SessionState.IDLE: "idle",
    SessionState.WORKING: "working",
    SessionState.WAITING_INPUT: "needs_input",
    SessionState.ERROR: "error",
    SessionState.STOPPED: "stopped",
}


@dataclass
class SessionInfo:
    name: str
    tmux_session_name: str
    state: SessionState = SessionState.IDLE
    last_output: str = ""
    working_directory: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    ttyd_port: Optional[int] = None
    ttyd_pid: Optional[int] = None

    @property
    def display_name(self) -> str:
        icon = STATE_ICONS.get(self.state, "?")
        label = STATE_LABELS.get(self.state, "")
        return f"{icon} {self.name}  ({label})"

    @property
    def is_alive(self) -> bool:
        return self.state not in (SessionState.STOPPED, SessionState.ERROR)

    @property
    def is_ready_for_input(self) -> bool:
        """True when Claude is idle and ready to receive a new message."""
        return self.state == SessionState.IDLE

    @property
    def needs_user_response(self) -> bool:
        """True when Claude asked a question and waits for user choice."""
        return self.state == SessionState.WAITING_INPUT

    @property
    def is_busy(self) -> bool:
        """True when Claude is actively working (processing or running tools)."""
        return self.state == SessionState.WORKING
