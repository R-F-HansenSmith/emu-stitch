#!/usr/bin/env bash
set -e

echo "=== Installing emu-stitch CLI ==="

if ! command -v uv >/dev/null 2>&1; then
    echo "Error: uv is required but was not found in PATH." >&2
    echo "Install it from https://docs.astral.sh/uv/getting-started/installation/ and re-run this script." >&2
    exit 1
fi

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# --force lets this also work as an upgrade over a previous installation.
uv tool install --force "$SCRIPT_DIR"
uv tool update-shell

echo ""
echo "=== emu-stitch CLI Installed Successfully! ==="
echo "Run 'emu-stitch setup' to configure autostart and Syncthing background services."
echo "(If 'emu-stitch' is not recognized in your current shell, restart your shell or run: source ~/.bashrc)"
