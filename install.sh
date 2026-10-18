#!/usr/bin/env bash
# PowerSnap installer for Arch Linux/XFCE. Installs a standalone user copy.
set -euo pipefail

NON_INTERACTIVE=false
for arg in "$@"; do
    case "$arg" in
        -y|--yes) NON_INTERACTIVE=true ;;
        *) echo "Unknown argument: $arg" >&2; exit 2 ;;
    esac
done

if [[ $EUID -eq 0 ]]; then
    echo "Run this as your normal desktop user, not with sudo." >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/.local/share/powersnap"
BIN_DIR="$HOME/.local/bin"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
ICONS_DIR="$HOME/.local/share/icons"
CONFIG_DIR="$HOME/.config/powersnap"

for path in "$SCRIPT_DIR/src/power_snap" "$SCRIPT_DIR/bin/powersnap" "$SCRIPT_DIR/bin/powersnap-editor" "$SCRIPT_DIR/systemd/powersnap.service" "$SCRIPT_DIR/assets/icon.png" "$SCRIPT_DIR/requirements.txt"; do
    if [[ ! -e "$path" ]]; then
        echo "ERROR: required project file missing: $path" >&2
        exit 1
    fi
done
if [[ -z ${DISPLAY:-} || ${XDG_SESSION_TYPE:-} == wayland ]]; then
    echo "ERROR: Run from an X11 graphical session (DISPLAY required)." >&2
    exit 1
fi
if ! command -v pacman >/dev/null 2>&1; then
    echo "ERROR: pacman is required (Arch Linux)." >&2
    exit 1
fi

printf 'Installing PowerSnap to %s\n' "$INSTALL_DIR"
if systemctl --user is-active --quiet snapzones.service 2>/dev/null; then
    echo "WARNING: Legacy SnapZones daemon is active; disable it before running both."
fi

SYSTEM_DEPS=(python python-pip python-gobject python-cairo python-xlib python-evdev gtk3 cairo libx11 pkgconf gobject-introspection base-devel)
if [[ ${XDG_CURRENT_DESKTOP:-} == *XFCE* || ${XDG_CURRENT_DESKTOP:-} == *Xfce* || ${DESKTOP_SESSION:-} == *xfce* ]]; then
    SYSTEM_DEPS+=(xfconf)
fi
MISSING_DEPS=()
for pkg in "${SYSTEM_DEPS[@]}"; do
    pacman -Q "$pkg" >/dev/null 2>&1 || MISSING_DEPS+=("$pkg")
done
if (( ${#MISSING_DEPS[@]} > 0 )); then
    printf 'Missing packages: %s\n' "${MISSING_DEPS[*]}"
    if [[ $NON_INTERACTIVE == false ]]; then
        read -r -p "Install missing packages with pacman? (y/N) " answer
        [[ $answer =~ ^[Yy]$ ]] || exit 1
        sudo pacman -S --needed "${MISSING_DEPS[@]}"
    else
        sudo pacman -S --needed --noconfirm "${MISSING_DEPS[@]}"
    fi
fi

# Install/update the application independently of the checkout. In particular,
# never copy a development venv, .git directory, or user configuration.
mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$SYSTEMD_USER_DIR" "$ICONS_DIR" "$CONFIG_DIR/layouts"
if [[ "$(realpath -m "$SCRIPT_DIR")" == "$(realpath -m "$INSTALL_DIR")" ]]; then
    echo "ERROR: run installer from the source checkout, not from the installed application." >&2
    exit 1
fi

# Shut down the previous process before replacing application files.
was_active=false
if systemctl --user is-active --quiet powersnap.service 2>/dev/null; then
    was_active=true
    systemctl --user stop powersnap.service
fi
cp -a "$SCRIPT_DIR/src" "$SCRIPT_DIR/bin" "$SCRIPT_DIR/assets" "$INSTALL_DIR/"
cp -a "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/requirements.txt"
for optional in README.md LICENSE uninstall.sh; do
    [[ ! -f "$SCRIPT_DIR/$optional" ]] || cp -a "$SCRIPT_DIR/$optional" "$INSTALL_DIR/$optional"
done

# Bindings built and managed by Arch must not be rebuilt by pip.
python -m venv --system-site-packages "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/python" - <<'PY_IMPORTS'
import gi
import cairo
import Xlib
import evdev
PY_IMPORTS

"$INSTALL_DIR/venv/bin/python" - "$INSTALL_DIR/requirements.txt" "$INSTALL_DIR/venv/requirements-arch.txt" <<'PY_REQ'
import re
import sys
from pathlib import Path
source, dest = map(Path, sys.argv[1:])
provided = {"pygobject", "pycairo", "python-xlib", "evdev"}
lines = []
for line in source.read_text().splitlines(keepends=True):
    entry = line.strip()
    match = re.match(r"^([A-Za-z0-9_.-]+)(?=\s|[<>=!~;\[]|$)", entry)
    if match and re.sub(r"[-_.]+", "-", match.group(1)).lower() in provided:
        continue
    lines.append(line)
dest.write_text("".join(lines))
PY_REQ
"$INSTALL_DIR/venv/bin/python" -m pip install -q -r "$INSTALL_DIR/venv/requirements-arch.txt"
"$INSTALL_DIR/venv/bin/python" - <<'PY_VERIFY'
import gi, cairo, Xlib, evdev, pynput
PY_VERIFY

# Symlinks target the installed copy, NEVER the source checkout.
ln -sfn "$INSTALL_DIR/bin/powersnap" "$BIN_DIR/powersnap"
ln -sfn "$INSTALL_DIR/bin/powersnap-editor" "$BIN_DIR/powersnap-editor"
# Service and icon are regular files, not symlinks to the checkout.
# Remove legacy symlinks before copying: cp onto a symlink would otherwise
# overwrite its target and leave the source-checkout dependency intact.
rm -f -- "$SYSTEMD_USER_DIR/powersnap.service" "$ICONS_DIR/powersnap.png"
cp -f "$SCRIPT_DIR/systemd/powersnap.service" "$SYSTEMD_USER_DIR/powersnap.service"
cp -f "$INSTALL_DIR/assets/icon.png" "$ICONS_DIR/powersnap.png"
systemctl --user daemon-reload

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

if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    printf 'NOTE: Add %s to PATH if commands are not found.\n' "$BIN_DIR"
fi
if [[ ! -f "$CONFIG_DIR/layouts/default.json" ]]; then
    cat > "$CONFIG_DIR/layouts/default.json" <<'JSON'
{
  "name": "default",
  "description": "Default empty layout",
  "zones": [],
  "created": "2025-10-08",
  "modified": "2025-10-08"
}
JSON
fi

if [[ $NON_INTERACTIVE == true ]]; then
    # If an existing daemon was running, resume it after the upgrade.
    if [[ $was_active == true ]]; then
        systemctl --user start powersnap.service
    fi
    echo "Installed. To enable: systemctl --user enable --now powersnap.service"
else
    read -r -p "Enable and start PowerSnap now? (Y/n) " answer
    if [[ ! $answer =~ ^[Nn]$ ]]; then
        systemctl --user enable --now powersnap.service
    elif [[ $was_active == true ]]; then
        systemctl --user start powersnap.service
    fi
fi
printf 'PowerSnap installed in %s. Settings preserved at %s.\n' "$INSTALL_DIR" "$CONFIG_DIR"
