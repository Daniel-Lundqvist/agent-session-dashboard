import math
import subprocess
import time
from typing import Optional

import gi

gi.require_version("Gdk", "3.0")
from gi.repository import Gdk

from config.defaults import TERMINAL_GEOMETRY


def get_screen_size() -> tuple[int, int]:
    """Return (width, height) of the primary monitor."""
    display = Gdk.Display.get_default()
    if display:
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        if monitor:
            geom = monitor.get_geometry()
            return geom.width, geom.height
    # Fallback
    return 1920, 1080


def calculate_grid(
    count: int, screen_w: int, screen_h: int, padding: int = 0
) -> list[tuple[int, int, int, int]]:
    """Calculate (x, y, w, h) positions for `count` windows in a grid.

    Layout logic:
      1 → fullscreen
      2 → side by side
      3 → 2 top + 1 bottom (full width)
      4 → 2x2
      5-6 → 3x2
      7-9 → 3x3
      etc.
    """
    if count <= 0:
        return []
    if count == 1:
        return [(padding, padding, screen_w - 2 * padding, screen_h - 2 * padding)]

    if count == 2:
        w = screen_w // 2
        return [
            (padding, padding, w - 2 * padding, screen_h - 2 * padding),
            (w + padding, padding, w - 2 * padding, screen_h - 2 * padding),
        ]

    if count == 3:
        w = screen_w // 2
        h = screen_h // 2
        return [
            (padding, padding, w - 2 * padding, h - 2 * padding),
            (w + padding, padding, w - 2 * padding, h - 2 * padding),
            (padding, h + padding, screen_w - 2 * padding, h - 2 * padding),
        ]

    # General grid: cols x rows
    cols = math.ceil(math.sqrt(count))
    rows = math.ceil(count / cols)
    cell_w = screen_w // cols
    cell_h = screen_h // rows

    positions = []
    for i in range(count):
        col = i % cols
        row = i // cols
        x = col * cell_w + padding
        y = row * cell_h + padding
        w = cell_w - 2 * padding
        h = cell_h - 2 * padding
        positions.append((x, y, w, h))

    return positions


def open_split(session_names: list[str]) -> None:
    """Open xfce4-terminal windows attached to tmux sessions, arranged in a grid."""
    if not session_names:
        return

    screen_w, screen_h = get_screen_size()
    positions = calculate_grid(len(session_names), screen_w, screen_h)

    window_titles = []

    for session_name, (x, y, w, h) in zip(session_names, positions):
        title = f"Session: {session_name}"
        window_titles.append((title, x, y, w, h))

        subprocess.Popen([
            "xfce4-terminal",
            f"--title={title}",
            f"--geometry={TERMINAL_GEOMETRY}",
            "--execute", "tmux", "attach-session", "-t", session_name,
        ])

    # Give terminals time to open, then position them
    time.sleep(0.8)

    for title, x, y, w, h in window_titles:
        try:
            subprocess.run(
                ["wmctrl", "-r", title, "-e", f"0,{x},{y},{w},{h}"],
                timeout=3,
            )
        except FileNotFoundError:
            print("wmctrl not found. Install with: sudo apt install wmctrl")
            break
        except subprocess.TimeoutExpired:
            pass
