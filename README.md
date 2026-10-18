# PowerSnap

PowerSnap is an XFCE/X11-focused fork of [SnapZones](https://github.com/Mahrkeenerh/SnapZones), a window-zone snapping tool inspired by FancyZones. The original project's MIT copyright and license are preserved in `LICENSE`.

**Compatibility:** Developed and tested interactively on Arch Linux with XFCE/X11. Wayland is not supported. The executables, systemd service, configuration folder, and Python package are now consistently named PowerSnap. Existing SnapZones installations need a one-time migration (see below).

## Features

- Hold **Alt** while dragging a window to reveal zones; release the mouse over a zone to snap.
- Open the editor with **Super+Shift+Tab** (or `powersnap-editor`).
- Begin with a full-screen zone, split zones horizontally or vertically, or drag shared dividers to resize.
- Set a configurable pixel gap between zones and around the desktop perimeter.
- Gap changes update windows already assigned to zones; assignments last until daemon restart.
- Normally drag a snapped window to restore its former dimensions (live restoration depends on xfwm4 pointer handling).
- Handles XFCE title-bar geometry and GTK client-side decorations (e.g., LibreWolf).

## Install on Arch Linux / XFCE

Run in an **XFCE X11 graphical session**, as a regular user (do not run the whole installer with sudo):

```bash
cd ~/Downloads/PowerSnap
bash install.sh
systemctl --user enable --now powersnap.service
```

The installer installs system packages through `pacman`, creates a Python virtual environment with access to those packages, links launchers into `~/.local/bin`, and registers a desktop editor shortcut where possible. Interactive installation can help adjust XFCE's Alt+drag window-manager conflict; alternatively:

```bash
xfconf-query -c xfwm4 -p /general/easy_click -s Super
```

Use `bash install.sh -y` in unattended desktop setup. In noninteractive mode it does **not** silently change XFCE's move-window modifier; configure it yourself if needed.

## Manage

```bash
systemctl --user restart powersnap
systemctl --user status powersnap
journalctl --user -u powersnap -f
powersnap-editor
```

Configuration and layouts are stored in `~/.config/powersnap/`; they are not part of this repository. The installation uses symlinks pointing to this project, so keep the folder in place.

## Test

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

These tests check zone geometry without a running GUI. Snapping itself requires manual testing under XFCE/X11.

## License and attribution

Based on the original [SnapZones](https://github.com/Mahrkeenerh/SnapZones) project by Mahrkeenerh. Licensed under the MIT License; see `LICENSE`.

## Migration from the old installation

The rename changes user-level paths; it does **not** automatically remove an older installation or overwrite old layouts. If you had SnapZones installed previously, stop the old service and copy your settings before installing:

```bash
systemctl --user disable --now snapzones.service 2>/dev/null || true
mkdir -p ~/.config/powersnap
cp -an ~/.config/snapzones/. ~/.config/powersnap/ 2>/dev/null || true
```

Remove old symlinks or the old service unit **only after** the new PowerSnap installation has been tested. Your original `~/.config/snapzones` files remain untouched.
