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

The installer installs system packages through `pacman`, creates a Python virtual environment with access to those packages, installs the application independently into `~/.local/share/powersnap/` and links launchers into `~/.local/bin`, and registers a desktop editor shortcut where possible. Interactive installation can help adjust XFCE's Alt+drag window-manager conflict; alternatively:

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

Configuration and layouts are stored in `~/.config/powersnap/`; they are not part of this repository. The installed copy lives at `~/.local/share/powersnap/`. Launchers in `~/.local/bin/` point to this installed copy, not the source checkout. The service and icon are copied. You may move or delete the Downloads/source directory after installation. Re-run `bash install.sh -y` from a fresh checkout to update the installed application.

## Standalone installation and removal

Installation copies the required `src/`, `bin/`, and `assets/` files into
`~/.local/share/powersnap/`, creates the Python virtual environment there,
and installs a regular systemd user service file. User settings and layouts
remain in `~/.config/powersnap/` and are preserved during upgrades.

The commands in `~/.local/bin/` are symlinks **to the installed copy** (not to
this Git checkout). Removing the checkout after installation is safe.

To update, download or clone a newer release and run its `bash install.sh -y`.
The installer stops a running PowerSnap daemon before copying files and then
restarts it. In unattended mode a fresh install does not automatically enable
or start the service; run `systemctl --user enable --now powersnap.service`
after your desktop session starts.

To remove PowerSnap while retaining your layouts:

```bash
bash uninstall.sh -y
```

Or use `~/.local/share/powersnap/uninstall.sh` after deleting the checkout.
Interactive uninstallation offers to remove layouts and settings as well.
GNOME shortcut removal is left to the user; XFCE's registered shortcut is
removed only when it points to PowerSnap.

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

### Display resolution changes

Zone rectangles are stored as normalized fractions of the X11 desktop. PowerSnap
reads the **current** X11 root size whenever it calculates snapping geometry,
so layouts scale when you change resolution. The daemon also detects an XRandR
mode change and reflows windows already assigned to zones, without changing
the saved layout. The configured gap remains a fixed number of pixels.

This version scales layouts across the entire X11 desktop, not separately per
physical monitor. Panel/work-area offsets saved in older layouts are still
represented as fractions of the original screen, so a panel's pixel offset may
not remain exactly constant after a substantial resolution change. For a
multi-monitor layout, monitor-relative coordinates would require a separate
future change.

### Firefox Picture-in-Picture on XFCE/X11

PowerSnap recognizes Firefox-family Picture-in-Picture windows by their X11
window role/title and class. PiP may use the dialog window type and may not
become the focused window during Alt+Drag, so PowerSnap can select the PiP
client under the pointer instead. This exception does not enable snapping of
ordinary dialogs or desktop panels. Some video players enforce their aspect
ratio or size hints, in which case the window manager may not honor the exact
zone dimensions.

### Exporting and importing layouts

Open the PowerSnap zone editor (`Super+Shift+Tab`) and use **Export Layouts…**
from Layout Manager to save all named layouts in a portable JSON file. Use
**Import Layouts…** on the same or another machine to restore them. When an
imported name already exists, PowerSnap creates a distinct ` (imported N)`
name rather than overwriting an existing layout. After import, double-click
the desired layout to activate it. Zone geometry, labels and colors are
preserved. Per-workspace layout assignments and application settings (including
window gap) are intentionally not part of the export.
