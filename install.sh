#!/bin/bash
# PowerSnap Installation Script (Arch Linux / pacman)

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

# Warn before starting a renamed service alongside the old daemon.
if systemctl --user is-active --quiet snapzones.service 2>/dev/null; then
    echo "WARNING: Previous SnapZones service is running."
    echo "Stop it before starting PowerSnap: systemctl --user disable --now snapzones.service"
fi

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

# Check for Arch Linux system dependencies
if ! command -v pacman >/dev/null 2>&1; then
    echo "ERROR: pacman not found. This installer targets Arch Linux."
    exit 1
fi

echo "Checking system dependencies..."
# GTK 3 and PyGObject are supplied by pacman. python-xlib is also
# provided as a native Arch package. pkgconf and cairo are useful for
# Python extension builds if requirements.txt needs them.
SYSTEM_DEPS=(python python-pip python-gobject python-cairo python-xlib python-evdev gtk3 cairo libx11 pkgconf gobject-introspection base-devel)
# xfconf provides xfconf-query, which XFCE uses for application shortcuts.
if [[ "${XDG_CURRENT_DESKTOP:-}" == *XFCE* || "${XDG_CURRENT_DESKTOP:-}" == *Xfce* || "${DESKTOP_SESSION:-}" == *xfce* ]]; then
    SYSTEM_DEPS+=(xfconf)
fi
MISSING_DEPS=()
for pkg in "${SYSTEM_DEPS[@]}"; do
    if ! pacman -Q "$pkg" >/dev/null 2>&1; then
        MISSING_DEPS+=("$pkg")
    fi
done

if (( ${#MISSING_DEPS[@]} > 0 )); then
    echo "Missing system packages: ${MISSING_DEPS[*]}"
    echo "Install with: sudo pacman -S --needed ${MISSING_DEPS[*]}"
    if [ "$NON_INTERACTIVE" = true ]; then
        sudo pacman -S --needed --noconfirm "${MISSING_DEPS[@]}"
    else
        read -r -p "Install now? (y/N) " REPLY
        if [[ "$REPLY" =~ ^[Yy]$ ]]; then
            sudo pacman -S --needed "${MISSING_DEPS[@]}"
        else
            echo "Please install missing dependencies and run this script again."
            exit 1
        fi
    fi
fi

echo "✓ System dependencies satisfied"
echo ""

# Install Python dependencies
echo "Installing Python dependencies..."
cd "$SCRIPT_DIR"

# Use --system-site-packages so the venv can access pacman-installed
# gi (PyGObject), cairo and Xlib modules. Re-running also updates an
# existing venv's pyvenv.cfg without clearing installed packages.
echo "Creating/updating virtual environment (with Arch system packages)..."
python -m venv --system-site-packages venv

source venv/bin/activate

# Arch provides GTK Python bindings as binary pacman packages. Installing
# PyGObject/pycairo through pip can try compiling incompatible versions.
# Ensure this venv can import the system-provided bindings first.
if ! python - <<'PY_CHECK_IMPORTS'
import gi
import cairo
import Xlib
import evdev
PY_CHECK_IMPORTS
then
    echo "ERROR: Cannot import Arch's Python GTK/Xlib bindings inside the venv."
    echo "Check that python-gobject, python-cairo, and python-xlib are installed."
    echo "Try removing the project's venv and rerunning this installer."
    exit 1
fi

# Exclude dependencies already installed through pacman from pip requirements.
# Keep requirements.txt unchanged in the project directory.
FILTERED_REQUIREMENTS="$SCRIPT_DIR/venv/requirements-arch.txt"
python - "$SCRIPT_DIR/requirements.txt" "$FILTERED_REQUIREMENTS" <<'PY_FILTER_REQS'
import re
import sys
from pathlib import Path

source = Path(sys.argv[1])
dest = Path(sys.argv[2])
arch_provided = {"pygobject", "pycairo", "python-xlib", "evdev"}
filtered = []
for line in source.read_text().splitlines(keepends=True):
    entry = line.strip()
    match = re.match(r"^([A-Za-z0-9_.-]+)(?=\s|[<>=!~;\[]|$)", entry)
    if match and re.sub(r"[-_.]+", "-", match.group(1)).lower() in arch_provided:
        print(f"Using pacman package instead of pip: {entry}")
    else:
        filtered.append(line)
dest.write_text("".join(filtered))
PY_FILTER_REQS

python -m pip install -q -r "$FILTERED_REQUIREMENTS"

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
            slot_num=$((slot_num + 1))
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

register_xfce_shortcut() {
    local shortcut='/commands/custom/<Super><Shift>Tab'
    local command="$BIN_DIR/powersnap-editor"
    local existing=''
    existing=$(xfconf-query -c xfce4-keyboard-shortcuts -p "$shortcut" 2>/dev/null || true)

    if [[ -n "$existing" && "$existing" != "$command" ]]; then
        echo "WARNING: XFCE shortcut Super+Shift+Tab already runs: $existing"
        echo "  Leaving your existing shortcut unchanged."
        echo "  Add PowerSnap manually in Settings → Keyboard → Application Shortcuts."
        return 0
    fi
    if [[ -n "$existing" ]]; then
        echo "✓ XFCE shortcut already configured: Super+Shift+Tab"
    else
        xfconf-query -c xfce4-keyboard-shortcuts -p "$shortcut" -n -t string -s "$command"
        echo "✓ XFCE shortcut registered: Super+Shift+Tab → powersnap-editor"
    fi
}

configure_xfce_mouse_modifier() {
    # xfwm4 normally uses Alt+drag to move windows, which prevents
    # PowerSnap from receiving its own Alt+drag gesture.
    local current=''
    current=$(xfconf-query -c xfwm4 -p /general/easy_click 2>/dev/null || true)
    if [[ "${current,,}" == 'alt' || "${current,,}" == 'true' ]]; then
        echo ""
        echo "WARNING: XFCE's window manager uses Alt+drag for moving windows."
        echo "  This conflicts with PowerSnap' Alt+drag activation."
        if [[ "$NON_INTERACTIVE" == false ]]; then
            read -r -p "Change XFCE's move-window modifier to Super? (y/N) " REPLY
            if [[ "$REPLY" =~ ^[Yy]$ ]]; then
                xfconf-query -c xfwm4 -p /general/easy_click -s Super
                echo "✓ XFCE move-window modifier changed to Super+drag"
            else
                echo "  PowerSnap Alt+drag may not work until you change it."
            fi
        else
            echo "  No XFCE settings changed automatically in --yes mode."
            echo "  Fix manually: xfconf-query -c xfwm4 -p /general/easy_click -s Super"
        fi
    fi
}

if [[ "${XDG_CURRENT_DESKTOP:-}" == *XFCE* || "${XDG_CURRENT_DESKTOP:-}" == *Xfce* || "${DESKTOP_SESSION:-}" == *xfce* ]]; then
    if command -v xfconf-query >/dev/null 2>&1; then
        register_xfce_shortcut
        configure_xfce_mouse_modifier
    else
        echo "WARNING: xfconf-query not found - XFCE shortcut not registered"
        echo "  Install xfconf and set shortcut in Settings → Keyboard → Application Shortcuts"
    fi
elif [[ "${XDG_CURRENT_DESKTOP:-}" == *GNOME* || "${XDG_CURRENT_DESKTOP:-}" == *gnome* ]]; then
    if command -v gsettings >/dev/null 2>&1; then
        register_gnome_shortcut
    else
        echo "WARNING: gsettings not found - GNOME shortcut not registered"
    fi
else
    echo "Desktop not recognized; skipping desktop-specific shortcut registration."
    echo "  Command: $BIN_DIR/powersnap-editor"
    echo "  Suggested shortcut: Super+Shift+Tab"
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
