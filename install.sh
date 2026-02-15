#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Agent Session Dashboard — Installation ==="
echo ""

# Python dependencies
echo "[1/4] Installerar Python-beroenden..."
pip3 install --user --break-system-packages -r "$SCRIPT_DIR/requirements.txt"

# wmctrl
if ! command -v wmctrl &>/dev/null; then
    echo "[2/4] Installerar wmctrl..."
    sudo apt-get install -y wmctrl
else
    echo "[2/4] wmctrl redan installerat."
fi

# ttyd
if ! command -v ttyd &>/dev/null; then
    echo "[3/4] Installerar ttyd..."
    sudo apt-get install -y ttyd 2>/dev/null || {
        echo "  ttyd finns inte i apt. Försöker snap..."
        sudo snap install ttyd 2>/dev/null || {
            echo "  Kunde inte installera ttyd automatiskt."
            echo "  Installera manuellt: https://github.com/tsl0922/ttyd"
        }
    }
else
    echo "[3/4] ttyd redan installerat."
fi

# Autostart
echo "[4/4] Sätter upp autostart..."
mkdir -p ~/.config/autostart

DESKTOP_FILE="$HOME/.config/autostart/agent-session-dashboard.desktop"
cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Type=Application
Name=Agent Session Dashboard
Comment=System tray for Claude Code sessions
Exec=/usr/bin/python3 $SCRIPT_DIR/scripts/start_dashboard.py
Icon=utilities-terminal
Terminal=false
Categories=Utility;Development;
StartupNotify=false
X-GNOME-Autostart-enabled=true
EOF

# Make start script executable
chmod +x "$SCRIPT_DIR/scripts/start_dashboard.py"

echo ""
echo "=== Klart! ==="
echo ""
echo "Starta nu:     python3 $SCRIPT_DIR/scripts/start_dashboard.py"
echo "Autostart:     Aktiv vid nästa inloggning"
echo ""
echo "Från din AI-agent:"
echo "  from src.agent_manager import AgentManager"
echo "  manager = AgentManager()"
echo "  manager.create_session('mitt-projekt', '/home/dalu/projects/mitt-projekt')"
