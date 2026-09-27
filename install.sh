#!/usr/bin/env bash
set -euo pipefail

echo "=== Installing emu-stitch CLI ==="

if ! command -v uv >/dev/null 2>&1; then
    echo "Error: uv is required but was not found in PATH." >&2
    echo "Install it from https://docs.astral.sh/uv/getting-started/installation/ and re-run this script." >&2
    exit 1
fi

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# --force lets this also work as an upgrade over a previous installation.
uv tool install --force "$SCRIPT_DIR"

# Only touch shell startup files if uv's tool bin dir isn't already on PATH.
if ! command -v emu-stitch >/dev/null 2>&1; then
    echo "Adding uv's tool directory to PATH in your shell startup file (uv tool update-shell)..."
    uv tool update-shell
fi

echo ""
echo "=== emu-stitch CLI Installed Successfully! ==="
echo "Run 'emu-stitch setup' to configure autostart and Syncthing background services."
echo "(If 'emu-stitch' is not recognized in your current shell, restart your shell or run: source ~/.bashrc)"
