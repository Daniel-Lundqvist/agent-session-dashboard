#!/usr/bin/env python3
"""Agent Session Dashboard — entry point."""

import sys
import os

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from src.tray_app import SessionDashboard


def main():
    app = SessionDashboard()
    try:
        app.run()
    except KeyboardInterrupt:
        print("\nShutting down...")
        sys.exit(0)


if __name__ == "__main__":
    main()
