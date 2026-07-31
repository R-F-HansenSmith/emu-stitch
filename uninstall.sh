#!/usr/bin/env bash
set -e

echo "=== Uninstalling emu-stitch CLI ==="

DESKTOP_FILE="$HOME/.config/autostart/emu_stitch.desktop"

# Remove the uv-managed tool install (isolated venv + shim)
if command -v uv >/dev/null 2>&1; then
    if uv tool list 2>/dev/null | grep -q '^emu-stitch '; then
        uv tool uninstall emu-stitch
        echo "  Uninstalled emu-stitch uv tool"
    fi
else
    echo "  Warning: uv not found in PATH; skipping 'uv tool uninstall emu-stitch'." >&2
fi

# Clean up leftovers from the pre-uv install method (a copied package
# directory and a plain wrapper script, rather than a uv-managed symlink).
LEGACY_BIN="$HOME/.local/bin/emu-stitch"
LEGACY_PKG_DIR="$HOME/.local/share/emu-stitch"
if [ -d "$LEGACY_PKG_DIR" ]; then
    rm -rf "$LEGACY_PKG_DIR"
    echo "  Removed: $LEGACY_PKG_DIR"
fi
if [ -f "$LEGACY_BIN" ] && [ ! -L "$LEGACY_BIN" ]; then
    rm -f "$LEGACY_BIN"
    echo "  Removed: $LEGACY_BIN"
fi

# Remove desktop autostart entry (if created by setup)
if [ -f "$DESKTOP_FILE" ]; then
    rm -f "$DESKTOP_FILE"
    echo "  Removed: $DESKTOP_FILE"
fi

# Stop and remove systemd user watcher units (if created by setup)
if systemctl --user is-active --quiet emu-stitch-watcher.path 2>/dev/null; then
    systemctl --user disable --now emu-stitch-watcher.path 2>/dev/null || true
fi
for unit_file in "$HOME/.config/systemd/user/emu-stitch-watcher.path" "$HOME/.config/systemd/user/emu-stitch-watcher.service"; do
    if [ -f "$unit_file" ]; then
        rm -f "$unit_file"
        echo "  Removed: $unit_file"
    fi
done
systemctl --user daemon-reload 2>/dev/null || true

echo ""
echo "=== emu-stitch uninstalled successfully ==="
echo "Note: Your save profiles in ~/Emulation/saves_by_user/ were not removed."
echo "Note: Syncthing service configuration was not changed."
