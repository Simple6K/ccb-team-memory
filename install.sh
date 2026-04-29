#!/bin/bash
set -e
echo "Installing ccb-team-memory..."

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Ensure uv
if ! command -v uv &>/dev/null && [ -x "$HOME/.local/bin/uv" ]; then
  export PATH="$HOME/.local/bin:$PATH"
fi

if ! command -v uv &>/dev/null; then
  echo "Error: uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

cd "$SCRIPT_DIR"
if [ ! -d ".venv" ]; then
  uv venv
else
  echo "venv already exists, updating..."
fi
uv pip install -e .

# Symlink to ~/.local/bin so team-memory is globally available
mkdir -p "$HOME/.local/bin"
ln -sf "$(pwd)/.venv/bin/team-memory" "$HOME/.local/bin/team-memory"

echo ""
echo "Done. team-memory is now globally available."
echo "Run: team-memory --version"
