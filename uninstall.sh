#!/usr/bin/env bash
# Uninstall the standalone PowerSnap application; keep settings by default.
set -euo pipefail

NON_INTERACTIVE=false
for arg in "$@"; do
    case "$arg" in
        -y|--yes) NON_INTERACTIVE=true ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done
if [[ $EUID -eq 0 ]]; then
    echo "Run as the desktop user, not with sudo." >&2
    exit 1
fi

INSTALL_DIR="$HOME/.local/share/powersnap"
CONFIG_DIR="$HOME/.config/powersnap"
BIN_DIR="$HOME/.local/bin"
SERVICE="$HOME/.config/systemd/user/powersnap.service"
ICON="$HOME/.local/share/icons/powersnap.png"

systemctl --user disable --now powersnap.service 2>/dev/null || true

# Don't remove unrelated user files if these names have been repurposed.
for executable in powersnap powersnap-editor; do
    path="$BIN_DIR/$executable"
    if [[ -L $path && $(readlink -f "$path") == "$INSTALL_DIR/bin/$executable" ]]; then
        rm -f -- "$path"
    fi
done
if [[ -f $SERVICE ]] && grep -Fq '%h/.local/bin/powersnap' "$SERVICE"; then
    rm -f -- "$SERVICE"
fi
systemctl --user daemon-reload
if [[ -f "$ICON" && -f "$INSTALL_DIR/assets/icon.png" ]] && cmp -s "$ICON" "$INSTALL_DIR/assets/icon.png"; then
    rm -f -- "$ICON"
fi

# Remove XFCE shortcut only when it still invokes PowerSnap.
if command -v xfconf-query >/dev/null 2>&1; then
    shortcut='/commands/custom/<Super><Shift>Tab'
    current=$(xfconf-query -c xfce4-keyboard-shortcuts -p "$shortcut" 2>/dev/null || true)
    if [[ $current == "$BIN_DIR/powersnap-editor" ]]; then
        xfconf-query -c xfce4-keyboard-shortcuts -p "$shortcut" -r || true
    fi
fi
# GNOME custom binding removal is intentionally manual to avoid affecting
# other custom shortcuts or mutating the user's settings by mistake.

if [[ -d $INSTALL_DIR ]]; then
    rm -rf -- "$INSTALL_DIR"
fi

remove_config=false
if [[ $NON_INTERACTIVE == false ]]; then
    read -r -p "Remove saved settings and layouts at $CONFIG_DIR? (y/N) " answer
    [[ $answer =~ ^[Yy]$ ]] && remove_config=true
fi
if [[ $remove_config == true ]]; then
    rm -rf -- "$CONFIG_DIR"
    echo "PowerSnap settings removed."
else
    echo "Saved settings kept: $CONFIG_DIR"
fi
printf 'PowerSnap removed. Your source repository is untouched.\n'
