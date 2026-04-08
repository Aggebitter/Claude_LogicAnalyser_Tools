#!/bin/bash
# Install script for logic2-mcp-server
# Installs to /home/agge/claude/logic-analyser/logic2-mcp-server

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"

echo "[logic2-mcp-server] Creating virtual environment..."
python3 -m venv "$VENV"

echo "[logic2-mcp-server] Installing Python dependencies..."
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"

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
echo '     "logic2": { "command": "'"$VENV"'/bin/python3", "args": ["'"$SCRIPT_DIR"'/server.py"] }'
