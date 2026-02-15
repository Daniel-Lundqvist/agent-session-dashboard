"""Default configuration."""

# Monitoring
POLL_INTERVAL_SECONDS = 2.0

# Session naming
SESSION_PREFIX = "claude_"

# Claude Code
CLAUDE_COMMAND = "claude --dangerously-skip-permissions"

# UI
TRAY_ICON_NAME = "utilities-terminal"
TRAY_TOOLTIP = "Agent Session Dashboard"
TERMINAL_GEOMETRY = "100x42"

# ttyd
TTYD_BASE_PORT = 7681
TTYD_COMMAND = "ttyd"

# State detection
IDLE_PROMPT_MARKERS = ["\u276f", "$", ">"]
ERROR_KEYWORDS = ["error", "failed", "exception", "traceback"]
PROMPT_KEYWORDS = ["?"]
CHOICE_PREFIXES = ["1.", "2.", "3.", "4.", "\u25cf", "\u25cb", "- ["]
