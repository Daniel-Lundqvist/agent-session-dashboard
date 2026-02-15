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
    SessionState.IDLE: "\U0001f7e1",
    SessionState.WORKING: "\U0001f535",
    SessionState.WAITING_INPUT: "\U0001f7e0",
    SessionState.ERROR: "\U0001f534",
    SessionState.STOPPED: "\u26ab",
}

STATE_LABELS = {
    SessionState.IDLE: "v\u00e4ntar p\u00e5 kommando",
    SessionState.WORKING: "jobbar...",
    SessionState.WAITING_INPUT: "v\u00e4ntar p\u00e5 svar",
    SessionState.ERROR: "fel",
    SessionState.STOPPED: "stoppad",
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
