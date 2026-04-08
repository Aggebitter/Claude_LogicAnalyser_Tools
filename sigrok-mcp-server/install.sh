#!/bin/bash
# Install script for sigrok-mcp-server
# Installs sigrok-cli, libsigrokdecode, and Python dependencies

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[sigrok-mcp-server] Installing sigrok tools..."
sudo apt-get update -qq
sudo apt-get install -y sigrok-cli libsigrokdecode-dev pulseview

echo "[sigrok-mcp-server] Installing Python dependencies..."
pip3 install -r "$SCRIPT_DIR/requirements.txt"

echo "[sigrok-mcp-server] Installing skill files..."
mkdir -p ~/.claude/skills
cp "$SCRIPT_DIR/../skills/sigrok-reverse.md" ~/.claude/skills/
cp "$SCRIPT_DIR/../skills/sigrok-debug.md" ~/.claude/skills/

echo ""
echo "[sigrok-mcp-server] Done."
echo ""
echo "Installed:"
sigrok-cli --version 2>/dev/null | head -1 || echo "  sigrok-cli: check PATH"
echo ""
echo "Supported devices:"
sigrok-cli --list-supported 2>/dev/null | grep "^Driver" | wc -l | xargs echo "  drivers:"
echo ""
echo "Next steps:"
echo "  1. Flash LogicAnalyzer firmware to dedicated Pico: https://github.com/gusmanb/logicanalyzer"
echo "  2. Add to ~/.claude/settings.json:"
echo '     "sigrok": { "command": "python3", "args": ["'"$SCRIPT_DIR"'/server.py"] }'
