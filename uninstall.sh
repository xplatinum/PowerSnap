#!/bin/bash
# PowerSnap Uninstallation Script

set -e

# Parse arguments
NON_INTERACTIVE=false
if [[ "$1" == "-y" ]] || [[ "$1" == "--yes" ]]; then
    NON_INTERACTIVE=true
fi

BIN_DIR="$HOME/.local/bin"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
AUTOSTART_DIR="$HOME/.config/autostart"
CONFIG_DIR="$HOME/.config/powersnap"
ICONS_DIR="$HOME/.local/share/icons"

echo "========================================="
echo "PowerSnap Uninstallation"
echo "========================================="
echo ""

# Stop and disable systemd service
echo "Stopping PowerSnap daemon..."
systemctl --user stop powersnap.service 2>/dev/null || true
systemctl --user disable powersnap.service 2>/dev/null || true
echo "✓ Daemon stopped and disabled"
echo ""

# Remove systemd service symlink
echo "Removing systemd service..."
rm -f "$SYSTEMD_USER_DIR/powersnap.service"
systemctl --user daemon-reload
echo "✓ Systemd service removed"
echo ""

# Remove script symlinks
echo "Removing installed scripts..."
rm -f "$BIN_DIR/powersnap"
rm -f "$BIN_DIR/powersnap-editor"
echo "✓ Scripts removed"
echo ""

# Remove icon symlink
echo "Removing icon..."
rm -f "$ICONS_DIR/powersnap.png"
echo "✓ Icon removed"
echo ""

# Remove legacy autostart file (from old installation method)
if [ -f "$AUTOSTART_DIR/powersnap.desktop" ]; then
    echo "Removing legacy autostart file..."
    rm -f "$AUTOSTART_DIR/powersnap.desktop"
    echo "✓ Legacy autostart removed"
    echo ""
fi

# Remove legacy PID file
if [ -f "$CONFIG_DIR/daemon.pid" ]; then
    rm -f "$CONFIG_DIR/daemon.pid"
fi

# Remove GNOME keyboard shortcut
echo "Removing keyboard shortcut..."
if command -v gsettings &> /dev/null; then
    current_bindings=$(gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings)

    for slot_num in {0..20}; do
        slot_path="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom${slot_num}/"

        if echo "$current_bindings" | grep -q "$slot_path"; then
            cmd=$(gsettings get org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$slot_path command 2>/dev/null || echo "")
            if echo "$cmd" | grep -q "powersnap-editor"; then
                # Remove this slot from the bindings list
                new_bindings=$(echo "$current_bindings" | sed "s|'$slot_path'||g" | sed "s|\[, |\[|g" | sed "s|, \]|\]|g" | sed "s|, , |, |g")
                gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "$new_bindings"

                # Reset the slot properties
                gsettings reset org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$slot_path name 2>/dev/null || true
                gsettings reset org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$slot_path command 2>/dev/null || true
                gsettings reset org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$slot_path binding 2>/dev/null || true

                echo "✓ Keyboard shortcut removed"
                break
            fi
        fi
    done
else
    echo "⚠ gsettings not found - skipping keyboard shortcut removal"
fi
echo ""

# Ask about config
if [ "$NON_INTERACTIVE" = false ]; then
    read -p "Remove configuration and layouts? (y/N) " -n 1 -r
    echo
    REMOVE_CONFIG=$REPLY
else
    REMOVE_CONFIG="n"
fi

if [[ $REMOVE_CONFIG =~ ^[Yy]$ ]]; then
    rm -rf "$CONFIG_DIR"
    echo "✓ Configuration removed"
else
    echo "Configuration kept at $CONFIG_DIR"
fi
echo ""

# Ask about venv
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "$SCRIPT_DIR/venv" ]; then
    if [ "$NON_INTERACTIVE" = false ]; then
        read -p "Remove Python virtual environment? (y/N) " -n 1 -r
        echo
        REMOVE_VENV=$REPLY
    else
        REMOVE_VENV="n"
    fi

    if [[ $REMOVE_VENV =~ ^[Yy]$ ]]; then
        rm -rf "$SCRIPT_DIR/venv"
        echo "✓ Virtual environment removed"
    else
        echo "Virtual environment kept"
    fi
fi
echo ""

echo "========================================="
echo "Uninstallation Complete!"
echo "========================================="
echo ""
echo "PowerSnap has been removed from your system."
echo "Repository remains at: $SCRIPT_DIR"
echo ""
