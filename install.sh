#!/usr/bin/env bash
set -e

echo "=== Installing emu-stitch CLI ==="

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
BIN_DIR="$HOME/.local/bin"
PKG_DIR="$HOME/.local/share/emu-stitch"

mkdir -p "$BIN_DIR"
mkdir -p "$PKG_DIR"

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

echo ""
echo "=== emu-stitch CLI Installed Successfully! ==="
echo "Run 'emu-stitch setup' to configure autostart and Syncthing background services."
echo "(If 'emu-stitch' is not recognized in your current shell, run: source ~/.zshrc)"
