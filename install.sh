#!/usr/bin/env bash
set -e

echo "=== Installing emu-stitch ==="

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
BIN_DIR="$HOME/.local/bin"
PKG_DIR="$HOME/.local/share/emu-stitch"
AUTOSTART_DIR="$HOME/.config/autostart"

mkdir -p "$BIN_DIR"
mkdir -p "$PKG_DIR"
mkdir -p "$AUTOSTART_DIR"

# Copy emu_stitch Python package to ~/.local/share/emu-stitch
cp -r "$SCRIPT_DIR/emu_stitch" "$PKG_DIR/"

# Create executable wrapper script in ~/.local/bin/emu-stitch
cat << 'WRAPPER_EOF' > "$BIN_DIR/emu-stitch"
#!/usr/bin/env bash
PYTHONPATH="$HOME/.local/share/emu-stitch:$PYTHONPATH" exec python3 -m emu_stitch.cli "$@"
WRAPPER_EOF
chmod +x "$BIN_DIR/emu-stitch"

# Ensure ~/.local/bin is in PATH for shell configuration files (.bashrc / .zshrc)
for shell_rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
    if [ -f "$shell_rc" ]; then
        if ! grep -q '\.local/bin' "$shell_rc"; then
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$shell_rc"
        fi
    fi
done

# Create autostart desktop entry
cat << 'AUTOSTART_EOF' > "$AUTOSTART_DIR/emu_stitch.desktop"
[Desktop Entry]
Type=Application
Name=emu-stitch Save Switcher
Exec=bash -c 'PYTHONPATH="$HOME/.local/share/emu-stitch:$PYTHONPATH" python3 -m emu_stitch.cli switch'
Terminal=false
X-GNOME-Autostart-enabled=true
AUTOSTART_EOF

# Run initial audit
echo "Running initial emu-stitch configuration..."
"$BIN_DIR/emu-stitch" audit

echo ""
echo "=== emu-stitch Installation Complete! ==="
echo "You can now run 'emu-stitch' from anywhere in your terminal."
echo "(If 'emu-stitch' is not recognized immediately, run: source ~/.zshrc or open a new terminal window)."
