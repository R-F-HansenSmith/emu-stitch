#!/usr/bin/env bash
set -e

echo "=== Uninstalling emu-stitch CLI ==="

BIN_DIR="$HOME/.local/bin"
PKG_DIR="$HOME/.local/share/emu-stitch"
DESKTOP_FILE="$HOME/.config/autostart/emu_stitch.desktop"
PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'

# Remove installed package and binary
if [ -d "$PKG_DIR" ]; then
    rm -rf "$PKG_DIR"
    echo "  Removed: $PKG_DIR"
fi

if [ -f "$BIN_DIR/emu-stitch" ]; then
    rm -f "$BIN_DIR/emu-stitch"
    echo "  Removed: $BIN_DIR/emu-stitch"
fi

# Remove desktop autostart entry (if created by setup)
if [ -f "$DESKTOP_FILE" ]; then
    rm -f "$DESKTOP_FILE"
    echo "  Removed: $DESKTOP_FILE"
fi

# Remove the PATH export line added to shell rc files
for shell_rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
    if [ -f "$shell_rc" ] && grep -qF "$PATH_LINE" "$shell_rc"; then
        # Use a temp file to avoid in-place edit issues on all platforms
        grep -vF "$PATH_LINE" "$shell_rc" > "$shell_rc.tmp" && mv "$shell_rc.tmp" "$shell_rc"
        echo "  Removed PATH entry from: $shell_rc"
    fi
done

echo ""
echo "=== emu-stitch uninstalled successfully ==="
echo "Note: Your save profiles in ~/Emulation/saves_by_user/ were not removed."
echo "Note: Syncthing service configuration was not changed."
