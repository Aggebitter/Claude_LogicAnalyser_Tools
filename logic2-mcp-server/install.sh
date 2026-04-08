#!/bin/bash
# Install script for logic2-mcp-server
# Installs to /home/agge/claude/logic-analyser/logic2-mcp-server

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[logic2-mcp-server] Installing Python dependencies..."
pip3 install -r "$SCRIPT_DIR/requirements.txt"

echo "[logic2-mcp-server] Installing skill files..."
mkdir -p ~/.claude/skills
cp "$SCRIPT_DIR/../skills/logic2-reverse.md" ~/.claude/skills/
cp "$SCRIPT_DIR/../skills/logic2-debug.md" ~/.claude/skills/

echo ""
echo "[logic2-mcp-server] Done."
echo ""
echo "Next steps:"
echo "  1. Ensure Logic 2 is running before starting a session"
echo "  2. Add to ~/.claude/settings.json:"
echo '     "logic2": { "command": "python3", "args": ["'"$SCRIPT_DIR"'/server.py"] }'
