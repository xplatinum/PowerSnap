#!/bin/bash
# PowerSnap Installation Script

set -e

# Parse arguments
NON_INTERACTIVE=false
if [[ "$1" == "-y" ]] || [[ "$1" == "--yes" ]]; then
    NON_INTERACTIVE=true
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
ICONS_DIR="$HOME/.local/share/icons"

echo "========================================="
echo "PowerSnap Installation"
echo "========================================="
echo ""

# Check if running on X11
if [ -z "$DISPLAY" ]; then
    echo "ERROR: DISPLAY variable not set. PowerSnap requires X11."
    exit 1
fi

if [ "$XDG_SESSION_TYPE" = "wayland" ]; then
    echo "WARNING: Wayland detected. PowerSnap requires X11 to function properly."
    echo "Please log out and select an X11 session instead."
    if [ "$NON_INTERACTIVE" = false ]; then
        read -p "Continue anyway? (y/N) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    else
        echo "Running in non-interactive mode - continuing anyway"
    fi
fi

# Check for system dependencies
echo "Checking system dependencies..."
MISSING_DEPS=()

for pkg in python3-gi python3-gi-cairo gir1.2-gtk-3.0 libcairo2-dev python3-xlib libx11-dev python3-dev; do
    if ! dpkg -l | grep -q "^ii  $pkg"; then
        MISSING_DEPS+=($pkg)
    fi
done

if [ ${#MISSING_DEPS[@]} -gt 0 ]; then
    echo "Missing system packages: ${MISSING_DEPS[@]}"
    echo ""
    echo "Install with:"
    echo "  sudo apt install ${MISSING_DEPS[@]}"
    echo ""
    if [ "$NON_INTERACTIVE" = true ]; then
        echo "Please install missing dependencies and run this script again."
        exit 1
    fi
    read -p "Install now? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        sudo apt install -y "${MISSING_DEPS[@]}"
    else
        echo "Please install missing dependencies and run this script again."
        exit 1
    fi
fi

echo "✓ System dependencies satisfied"
echo ""

# Install Python dependencies
echo "Installing Python dependencies..."
cd "$SCRIPT_DIR"

if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo "✓ Python dependencies installed"
echo ""

# Create installation directories
echo "Setting up installation directories..."
mkdir -p "$BIN_DIR"
mkdir -p "$SYSTEMD_USER_DIR"
mkdir -p "$ICONS_DIR"
mkdir -p "$HOME/.config/powersnap/layouts"

echo "✓ Directories created"
echo ""

# Symlink icon
echo "Installing icon..."
ln -sf "$SCRIPT_DIR/assets/icon.png" "$ICONS_DIR/powersnap.png"
echo "✓ Icon symlinked to $ICONS_DIR/"
echo ""

# Symlink wrapper scripts
echo "Installing PowerSnap scripts..."
ln -sf "$SCRIPT_DIR/bin/powersnap" "$BIN_DIR/powersnap"
ln -sf "$SCRIPT_DIR/bin/powersnap-editor" "$BIN_DIR/powersnap-editor"
echo "✓ Scripts symlinked to $BIN_DIR/"
echo ""

# Symlink systemd user service
echo "Installing systemd user service..."
ln -sf "$SCRIPT_DIR/systemd/powersnap.service" "$SYSTEMD_USER_DIR/powersnap.service"
systemctl --user daemon-reload
echo "✓ Systemd service installed"
echo ""

# Register native GNOME keyboard shortcut for editor
echo "Registering keyboard shortcut (Super+Shift+Tab)..."

register_gnome_shortcut() {
    local name="PowerSnap Editor"
    local command="$BIN_DIR/powersnap-editor"
    local binding="<Super><Shift>Tab"

    # Get current custom keybindings list
    current_bindings=$(gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings)

    # Check if PowerSnap shortcut already exists
    for slot_num in {0..20}; do
        slot_path="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom${slot_num}/"
        if echo "$current_bindings" | grep -q "$slot_path"; then
            cmd=$(gsettings get org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$slot_path command 2>/dev/null || echo "")
            if echo "$cmd" | grep -q "powersnap-editor"; then
                # Already registered, update the command path
                gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$slot_path command "$command"
                echo "✓ Keyboard shortcut updated: Super+Shift+Tab → powersnap-editor"
                return
            fi
        fi
    done

    # Find next available custom keybinding slot
    slot_num=0
    while true; do
        slot_path="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom${slot_num}/"
        if echo "$current_bindings" | grep -q "$slot_path"; then
            ((slot_num++))
        else
            break
        fi
    done

    # Add our new slot to the list
    new_bindings=$(python3 << EOF
current = """$current_bindings"""
slot = """$slot_path"""

if current in ["@as []", "[]"]:
    print(f"['{slot}']")
else:
    items = current.strip("[]").split(",")
    items = [item.strip().strip("'\"") for item in items if item.strip()]
    items.append(slot)
    formatted = ", ".join(f"'{item}'" for item in items)
    print(f"[{formatted}]")
EOF
)

    # Set the new bindings list
    gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "$new_bindings"

    # Set keybinding properties
    gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$slot_path name "$name"
    gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$slot_path command "$command"
    gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$slot_path binding "$binding"

    echo "✓ Keyboard shortcut registered: Super+Shift+Tab → powersnap-editor"
}

if command -v gsettings &> /dev/null; then
    register_gnome_shortcut
else
    echo "⚠ gsettings not found - keyboard shortcut not registered"
    echo "  You can manually add a shortcut in System Settings → Keyboard"
    echo "  Command: $BIN_DIR/powersnap-editor"
    echo "  Shortcut: Super+Shift+Tab"
fi

echo ""

# Check if ~/.local/bin is in PATH
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo "WARNING: $HOME/.local/bin is not in your PATH"
    echo "Add this line to your ~/.bashrc or ~/.zshrc:"
    echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo ""
fi

# Create default layout if none exists
if [ ! -f "$HOME/.config/powersnap/layouts/default.json" ]; then
    echo "Creating default layout..."
    cat > "$HOME/.config/powersnap/layouts/default.json" << 'EOF'
{
  "name": "default",
  "description": "Default empty layout",
  "zones": [],
  "created": "2025-10-08",
  "modified": "2025-10-08"
}
EOF
    echo "✓ Default layout created"
    echo ""
fi

echo "========================================="
echo "Installation Complete!"
echo "========================================="
echo ""
echo "PowerSnap has been installed successfully."
echo ""
echo "Usage:"
echo "  systemctl --user start powersnap   - Start daemon"
echo "  systemctl --user stop powersnap    - Stop daemon"
echo "  systemctl --user restart powersnap - Restart daemon"
echo "  systemctl --user status powersnap  - Check status"
echo "  journalctl --user -u powersnap     - View logs"
echo "  powersnap-editor                   - Open zone editor"
echo ""
echo "Default shortcuts:"
echo "  Alt + Drag Window      - Show zone overlay and snap"
echo "  Super+Shift+Tab        - Open zone editor"
echo ""

if [ "$NON_INTERACTIVE" = false ]; then
    read -p "Enable and start PowerSnap daemon now? (Y/n) " -n 1 -r
    echo
    START_DAEMON=$REPLY
else
    START_DAEMON="n"
fi

if [[ ! $START_DAEMON =~ ^[Nn]$ ]]; then
    echo "Enabling and starting daemon..."
    systemctl --user enable powersnap.service
    systemctl --user start powersnap.service
    echo ""
    systemctl --user status powersnap.service --no-pager
    echo ""
    echo "Press Super+Shift+Tab to open the zone editor and create your first layout!"
else
    echo "Run 'systemctl --user enable --now powersnap' to start the daemon."
fi

echo ""
echo "Enjoy PowerSnap!"
