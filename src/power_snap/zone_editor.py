#!/usr/bin/env python3
"""
Zone Editor - Fullscreen overlay GUI for creating and editing zone layouts

Provides a transparent fullscreen overlay for:
- Drawing new zones with mouse (see exactly where they'll be on screen)
- Displaying existing zones over actual desktop
- Selecting, moving, and resizing zones in-place
- Applying preset layouts
- Saving/loading zone configurations

Controls:
- Click and drag: Draw new zone
- Click on zone: Select zone
- Drag selected zone: Move it
- Drag resize handles: Resize zone
- Arrow keys: Move selected zone (1px precision)
- Alt + Arrow keys: Resize selected zone (1px precision)
- Delete key: Delete selected zone
- Escape: Exit editor
- H: Toggle help
- D: Toggle dimension display
- S: Save zones
- C: Clear all zones
- N: New layout
- 1-4: Apply presets (1=halves, 2=thirds, 3=quarters, 4=grid3x3)

Layout Manager (always visible):
- Single-click: Select layout
- Double-click: Load layout
- Slow-click or F2: Rename layout
- Buttons: Create New, Rename, Delete, Close Editor
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib
import cairo
import json
import os
import time
from typing import List, Optional, Tuple
from .zone import Zone, create_preset_layout
from .layout_library import LayoutLibrary, Layout
from .settings import load_gap_px, save_gap_px
from .tile_geometry import inset_rectangle


class ZoneEditorOverlay(Gtk.Window):
    """Fullscreen transparent overlay for zone editing"""

    # Visual constants
    ZONE_FILL_COLOR = (0.3, 0.5, 0.8, 0.3)  # Blue with transparency
    ZONE_BORDER_COLOR = (0.2, 0.4, 0.7, 0.8)
    SELECTED_FILL_COLOR = (1.0, 0.5, 0.0, 0.4)  # Orange with transparency for selected
    SELECTED_BORDER_COLOR = (1.0, 0.5, 0.0, 1.0)
    HANDLE_SIZE = 10
    HANDLE_COLOR = (1.0, 1.0, 1.0, 0.9)
    HANDLE_BORDER_COLOR = (0.0, 0.0, 0.0, 1.0)

    # Help text
    HELP_BG_COLOR = (0.1, 0.1, 0.1, 0.85)
    HELP_TEXT_COLOR = (1.0, 1.0, 1.0, 1.0)

    def __init__(self, initial_layout: Optional[str] = None):
        super().__init__()

        self.layout_library = LayoutLibrary()

        # X11 display for workspace detection
        import Xlib.display
        self.x_display = Xlib.display.Display()

        # Get work area (usable screen space excluding panels/docks)
        self.workarea_margins = self._get_workarea_margins()

        # Will be set after workspace detection
        self.current_layout_name: Optional[str] = initial_layout
        self.current_file: Optional[str] = None

        self.zones: List[Zone] = []
        self.selected_zone: Optional[Zone] = None

        # Drawing state
        self.is_drawing = False
        self.draw_start: Optional[Tuple[int, int]] = None
        self.draw_current: Optional[Tuple[int, int]] = None

        # Moving/resizing state
        self.is_moving = False
        self.is_resizing = False
        self.resize_handle: Optional[str] = None  # 'nw', 'ne', 'sw', 'se', 'n', 's', 'e', 'w'
        self.move_start: Optional[Tuple[int, int]] = None
        self.zone_original_rect: Optional[Tuple[int, int, int, int]] = None
        # Shared divider drag: both sides change together, preserving tiling.
        self.divider_drag = None

        # Status message
        self.status_message = f"Editing: {self.current_layout_name} - Draw zones, ESC to exit, H for help"
        self.show_help = False
        self.show_dimensions = True  # Toggle for dimension display (default ON)

        # Layout manager window (always visible)
        self.layout_manager_window = None

        # For slow click rename detection in layout manager
        self.layout_last_click_time = 0
        self.layout_last_click_row = None

        # Setup window
        self._setup_window()
        self._setup_events()

        # Auto-load current layout
        self._load_current_layout()

        # Show layout manager window
        self._create_layout_manager_window()
        # Previewing the layout must not move windows. Reflow requests are
        # emitted only when a zone layout is actually saved or edited.

    def _request_window_reflow(self):
        """Tell the background daemon to reapply absolute tile targets."""
        try:
            path = os.path.expanduser('~/.config/powersnap/.layout-reflow-request')
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='ascii') as stream:
                stream.write(str(time.time_ns()))
        except OSError as exc:
            print(f"Layout reflow notification failed: {exc}", flush=True)

    def _setup_window(self):
        """Setup fullscreen transparent window"""
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)

        self.set_app_paintable(True)
        self.set_decorated(False)
        self.fullscreen()

        # Stay on top but allow click-through when not interacting
        self.set_keep_above(True)
        self.set_accept_focus(True)

    def _setup_events(self):
        """Setup event handlers"""
        self.connect("draw", self.on_draw)
        self.connect("button-press-event", self.on_button_press)
        self.connect("button-release-event", self.on_button_release)
        self.connect("motion-notify-event", self.on_motion)
        self.connect("key-press-event", self.on_key_press)
        self.connect("destroy", Gtk.main_quit)

        self.set_events(
            Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK |
            Gdk.EventMask.KEY_PRESS_MASK
        )

    def _get_workarea_margins(self) -> Tuple[int, int, int, int]:
        """
        Get work area margins (reserved space for panels/docks).

        Returns:
            Tuple of (left, top, right, bottom) margins in pixels
        """
        try:
            root = self.x_display.screen().root
            screen_width = self.x_display.screen().width_in_pixels
            screen_height = self.x_display.screen().height_in_pixels

            # Get _NET_WORKAREA property
            net_workarea = self.x_display.intern_atom('_NET_WORKAREA')
            workarea_prop = root.get_full_property(net_workarea, 0)

            if workarea_prop and len(workarea_prop.value) >= 4:
                # _NET_WORKAREA: x, y, width, height (for first desktop)
                wa_x, wa_y, wa_width, wa_height = workarea_prop.value[:4]

                # Calculate margins
                left = wa_x
                top = wa_y
                right = screen_width - (wa_x + wa_width)
                bottom = screen_height - (wa_y + wa_height)

                return (left, top, right, bottom)
        except Exception as e:
            print(f"Error getting work area margins: {e}")

        # Default: no margins
        return (0, 0, 0, 0)

    def _constrain_to_workarea(self, x: int, y: int, screen_width: int, screen_height: int) -> Tuple[int, int]:
        """
        Constrain coordinates to the usable work area (excluding panels/docks).

        Args:
            x, y: Input coordinates in pixels
            screen_width, screen_height: Total screen dimensions

        Returns:
            Constrained (x, y) coordinates
        """
        left, top, right, bottom = self.workarea_margins

        # Clamp coordinates to work area
        x = max(left, min(x, screen_width - right))
        y = max(top, min(y, screen_height - bottom))

        return (x, y)

    def get_current_workspace(self) -> int:
        """Get the current workspace number from X11"""
        try:
            root = self.x_display.screen().root
            current_desktop = root.get_full_property(
                self.x_display.intern_atom('_NET_CURRENT_DESKTOP'),
                0  # Xlib.X.AnyPropertyType
            )
            if current_desktop:
                return current_desktop.value[0]
            return 0
        except Exception as e:
            print(f"Error getting current workspace: {e}")
            return 0

    def _load_current_layout(self):
        """Load the current layout from the layout library"""
        if not self.current_layout_name:
            # Auto-detect layout for current workspace
            workspace_id = self.get_current_workspace()
            workspace_layout = self.layout_library.get_active_layout(workspace_id)
            self.current_layout_name = workspace_layout or "default"

        layout = self.layout_library.load_layout(self.current_layout_name)
        if layout:
            # Create deep copies of zones to avoid modifying the cached layout
            self.zones = [Zone(z.x, z.y, z.width, z.height, z.name, z.color) for z in layout.zones]
            self.status_message = f"Editing: {self.current_layout_name} ({len(self.zones)} zones)"
        else:
            # Layout doesn't exist yet - start with empty zones
            self.zones = []
            self.status_message = f"Editing: {self.current_layout_name} (new layout)"

    def on_draw(self, widget, cr: cairo.Context):
        """Draw zones, handles, and UI"""
        # Transparent background
        cr.set_source_rgba(0, 0, 0, 0)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        alloc = self.get_allocation()
        width, height = alloc.width, alloc.height

        # Draw zones
        for zone in self.zones:
            is_selected = (zone == self.selected_zone)
            self._draw_zone(cr, zone, is_selected, width, height)

        # Shared separators are visible even before a zone is selected.
        self._draw_shared_dividers(cr, width, height)

        # Draw temporary zone being created
        if self.is_drawing and self.draw_start and self.draw_current:
            x1, y1 = self.draw_start
            x2, y2 = self.draw_current
            x = min(x1, x2)
            y = min(y1, y2)
            w = abs(x2 - x1)
            h = abs(y2 - y1)

            cr.set_source_rgba(*self.ZONE_FILL_COLOR)
            cr.rectangle(x, y, w, h)
            cr.fill_preserve()
            cr.set_source_rgba(*self.ZONE_BORDER_COLOR)
            cr.set_line_width(2)
            cr.stroke()

        # Do not paint a full-width bottom status bar over the zone preview:
        # its opaque last 40 px concealed the bottom of snapped windows.
        # Status remains visible in the separate Layout Manager window.
        if self.show_help:
            self._draw_help(cr, width, height)

        return False

    def _draw_zone(self, cr: cairo.Context, zone: Zone, is_selected: bool,
                   canvas_width: int, canvas_height: int):
        """Draw a single zone with optional selection handles"""
        # Convert relative coordinates to absolute
        x = int(zone.x * canvas_width)
        y = int(zone.y * canvas_height)
        w = int(zone.width * canvas_width)
        h = int(zone.height * canvas_height)

        # Visual tile previews reflect the actual spacing between neighbors.
        draw_x, draw_y, draw_w, draw_h = inset_rectangle(
            zone, self.zones, canvas_width, canvas_height, load_gap_px())

        # Fill
        if is_selected:
            cr.set_source_rgba(*self.SELECTED_FILL_COLOR)
        else:
            cr.set_source_rgba(*self.ZONE_FILL_COLOR)
        cr.rectangle(draw_x, draw_y, draw_w, draw_h)
        cr.fill()

        # Border
        if is_selected:
            cr.set_source_rgba(*self.SELECTED_BORDER_COLOR)
            cr.set_line_width(3)
        else:
            cr.set_source_rgba(*self.ZONE_BORDER_COLOR)
            cr.set_line_width(2)
        cr.rectangle(draw_x, draw_y, draw_w, draw_h)
        cr.stroke()

        # Label
        if zone.name:
            cr.set_source_rgb(1, 1, 1)
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(16)

            text_extents = cr.text_extents(zone.name)
            text_x = x + (w - text_extents.width) / 2
            text_y = y + (h + text_extents.height) / 2

            cr.move_to(text_x, text_y)
            cr.show_text(zone.name)

        # Draw dimensions if enabled
        if self.show_dimensions:
            self._draw_zone_dimensions(cr, x, y, w, h)

        # Draw resize handles if selected
        if is_selected:
            self._draw_handles(cr, x, y, w, h)

    def _draw_handles(self, cr: cairo.Context, x: int, y: int, w: int, h: int):
        """Draw resize handles at corners and edges"""
        handles = [
            (x, y),                    # nw
            (x + w, y),                # ne
            (x, y + h),                # sw
            (x + w, y + h),            # se
            (x + w//2, y),             # n
            (x + w//2, y + h),         # s
            (x, y + h//2),             # w
            (x + w, y + h//2),         # e
        ]

        for hx, hy in handles:
            # White fill
            cr.set_source_rgba(*self.HANDLE_COLOR)
            cr.rectangle(
                hx - self.HANDLE_SIZE // 2,
                hy - self.HANDLE_SIZE // 2,
                self.HANDLE_SIZE,
                self.HANDLE_SIZE
            )
            cr.fill()

            # Black border
            cr.set_source_rgba(*self.HANDLE_BORDER_COLOR)
            cr.set_line_width(2)
            cr.rectangle(
                hx - self.HANDLE_SIZE // 2,
                hy - self.HANDLE_SIZE // 2,
                self.HANDLE_SIZE,
                self.HANDLE_SIZE
            )
            cr.stroke()

    def _draw_zone_dimensions(self, cr: cairo.Context, x: int, y: int, w: int, h: int):
        """Draw zone dimensions and coordinates overlay - CaptiX-inspired styling"""
        cr.select_font_face("Arial", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(12)

        padding = 6
        margin = 10  # Margin from zone edges

        # TOP-LEFT: Position (X: Y: format)
        pos_text = f"X: {x}  Y: {y}"
        pos_extents = cr.text_extents(pos_text)
        pos_bg_width = pos_extents.width + 2 * padding
        pos_bg_height = pos_extents.height + 2 * padding

        pos_bg_x = x + margin
        pos_bg_y = y + margin

        # Draw position background (semi-transparent dark)
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.47)  # ~120/255 alpha
        cr.rectangle(pos_bg_x, pos_bg_y, pos_bg_width, pos_bg_height)
        cr.fill()

        # Draw position text (white)
        cr.set_source_rgb(1.0, 1.0, 1.0)
        cr.move_to(pos_bg_x + padding, pos_bg_y + padding + pos_extents.height)
        cr.show_text(pos_text)

        # BOTTOM-RIGHT: Size (W × H format)
        size_text = f"{w} × {h}"
        size_extents = cr.text_extents(size_text)
        size_bg_width = size_extents.width + 2 * padding
        size_bg_height = size_extents.height + 2 * padding

        # Anchor to bottom-right, inside zone
        size_bg_x = x + w - size_bg_width - margin
        size_bg_y = y + h - size_bg_height - margin

        # Draw size background (semi-transparent dark)
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.47)
        cr.rectangle(size_bg_x, size_bg_y, size_bg_width, size_bg_height)
        cr.fill()

        # Draw size text (white)
        cr.set_source_rgb(1.0, 1.0, 1.0)
        cr.move_to(size_bg_x + padding, size_bg_y + padding + size_extents.height)
        cr.show_text(size_text)

    def _draw_status_bar(self, cr: cairo.Context, width: int, height: int):
        """Draw status bar at bottom of screen"""
        bar_height = 40

        # Semi-transparent background
        cr.set_source_rgba(*self.HELP_BG_COLOR)
        cr.rectangle(0, height - bar_height, width, bar_height)
        cr.fill()

        # Status text
        cr.set_source_rgba(*self.HELP_TEXT_COLOR)
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(14)
        cr.move_to(20, height - bar_height + 25)
        cr.show_text(self.status_message)

    def _draw_help(self, cr: cairo.Context, width: int, height: int):
        """Draw help panel"""
        panel_width = 380
        panel_height = 580
        panel_x = (width - panel_width) // 2
        panel_y = (height - panel_height) // 2

        # Background
        cr.set_source_rgba(*self.HELP_BG_COLOR)
        cr.rectangle(panel_x, panel_y, panel_width, panel_height)
        cr.fill()

        # Border
        cr.set_source_rgba(1, 1, 1, 0.5)
        cr.set_line_width(2)
        cr.rectangle(panel_x, panel_y, panel_width, panel_height)
        cr.stroke()

        # Help text
        cr.set_source_rgba(*self.HELP_TEXT_COLOR)
        cr.select_font_face("Monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(14)

        help_lines = [
            "ZONE EDITOR - HELP",
            "",
            "Drawing & Editing:",
            "  Click & Drag       - Draw new zone",
            "  Click on zone      - Select zone",
            "  Drag selected      - Move zone",
            "  Drag handles       - Resize zone",
            "  F                  - Fullscreen zone",
            "  V                  - Split left/right",
            "  B                  - Split top/bottom",
            "",
            "Precise Movement (1px):",
            "  Arrow Keys         - Move selected zone",
            "  Alt + Arrow Keys   - Resize selected zone",
            "",
            "Keyboard Shortcuts:",
            "  ESC       - Exit editor",
            "  H         - Toggle this help",
            "  D         - Toggle dimension display",
            "  S         - Save zones",
            "  C         - Clear all zones",
            "  N         - New layout",
            "  X/Delete  - Delete selected zone",
            "",
            "Presets:",
            "  1  - Halves     2  - Thirds",
            "  3  - Quarters   4  - Grid 3x3",
            "",
            "Layout Manager (always visible):",
            "  Double-click layout to load",
            "  Slow-click or F2 to rename",
            "",
            "Press H to close help"
        ]

        y_offset = panel_y + 30
        for line in help_lines:
            cr.move_to(panel_x + 20, y_offset)
            cr.show_text(line)
            y_offset += 18

    def on_button_press(self, widget, event):
        """Handle mouse button press"""
        if event.button != 1:  # Only left click
            return False

        x, y = int(event.x), int(event.y)
        alloc = self.get_allocation()

        # Shared dividers take precedence over individual zone-edge handles.
        divider = self._shared_divider_at(x, y, alloc.width, alloc.height)
        if divider is not None:
            self.divider_drag = (divider, x, y,
                                 [(z, z.x, z.y, z.width, z.height) for z in self.zones])
            self.status_message = "Dragging shared divider (resizes both sides)"
            return True

        # Check if clicking on selected zone's handles
        if self.selected_zone:
            handle = self._get_handle_at_position(x, y, alloc.width, alloc.height)
            if handle:
                self.is_resizing = True
                self.resize_handle = handle
                self.move_start = (x, y)
                self.zone_original_rect = (
                    self.selected_zone.x,
                    self.selected_zone.y,
                    self.selected_zone.width,
                    self.selected_zone.height
                )
                return True

        # Check if clicking on a zone
        clicked_zone = self._get_zone_at_position(x, y, alloc.width, alloc.height)
        if clicked_zone:
            self.selected_zone = clicked_zone
            self.is_moving = True
            self.move_start = (x, y)
            self.zone_original_rect = (
                clicked_zone.x,
                clicked_zone.y,
                clicked_zone.width,
                clicked_zone.height
            )
            self.status_message = f"Selected: {clicked_zone.name}"
            self._refresh_split_controls()
            self.queue_draw()
            return True

        # Start drawing new zone
        self.selected_zone = None
        self._refresh_split_controls()
        self.is_drawing = True
        self.draw_start = (x, y)
        self.draw_current = (x, y)
        self.status_message = "Drawing new zone..."
        self.queue_draw()
        return True

    def on_button_release(self, widget, event):
        """Handle mouse button release"""
        if event.button != 1:
            return False

        alloc = self.get_allocation()

        if self.divider_drag is not None:
            self.divider_drag = None
            self._auto_save()
            self.queue_draw()
            return True

        # Finish drawing new zone
        if self.is_drawing and self.draw_start and self.draw_current:
            x1, y1 = self.draw_start
            x2, y2 = self.draw_current

            # Clamp coordinates to work area
            x1, y1 = self._constrain_to_workarea(x1, y1, alloc.width, alloc.height)
            x2, y2 = self._constrain_to_workarea(x2, y2, alloc.width, alloc.height)

            # Create zone if it has reasonable size
            if abs(x2 - x1) > 30 and abs(y2 - y1) > 30:
                # Convert to relative coordinates
                rel_x1 = min(x1, x2) / alloc.width
                rel_y1 = min(y1, y2) / alloc.height
                rel_x2 = max(x1, x2) / alloc.width
                rel_y2 = max(y1, y2) / alloc.height

                # Find first available zone number
                existing_numbers = set()
                for zone in self.zones:
                    if zone.name.startswith("Zone "):
                        try:
                            num = int(zone.name.split()[1])
                            existing_numbers.add(num)
                        except (ValueError, IndexError):
                            pass

                zone_number = 1
                while zone_number in existing_numbers:
                    zone_number += 1

                new_zone = Zone(
                    rel_x1, rel_y1,
                    rel_x2 - rel_x1,
                    rel_y2 - rel_y1,
                    name=f"Zone {zone_number}"
                )
                self.zones.append(new_zone)
                self.selected_zone = new_zone
                self.status_message = f"Created {new_zone.name}"
                self._auto_save()
                self._refresh_layout_manager()

            self.is_drawing = False
            self.draw_start = None
            self.draw_current = None
            self.queue_draw()
            return True

        # Finish moving
        if self.is_moving:
            self.is_moving = False
            self.move_start = None
            self.zone_original_rect = None
            self.status_message = f"Moved {self.selected_zone.name if self.selected_zone else 'zone'}"
            self._auto_save()
            return True

        # Finish resizing
        if self.is_resizing:
            self.is_resizing = False
            self.resize_handle = None
            self.move_start = None
            self.zone_original_rect = None
            self._auto_save()
            self.status_message = f"Resized {self.selected_zone.name if self.selected_zone else 'zone'}"
            return True

        return False

    def on_motion(self, widget, event):
        """Handle mouse motion"""
        x, y = int(event.x), int(event.y)
        alloc = self.get_allocation()

        if self.divider_drag is not None:
            self._move_shared_divider(x, y, alloc.width, alloc.height)
            self.queue_draw()
            return True

        # Update drawing
        if self.is_drawing:
            self.draw_current = (x, y)
            self.queue_draw()
            return True

        # Update moving
        if self.is_moving and self.move_start and self.zone_original_rect:
            dx = (x - self.move_start[0]) / alloc.width
            dy = (y - self.move_start[1]) / alloc.height

            orig_x, orig_y, orig_w, orig_h = self.zone_original_rect

            # Calculate work area bounds in relative coordinates
            left, top, right, bottom = self.workarea_margins
            min_x = left / alloc.width
            min_y = top / alloc.height
            max_x = (alloc.width - right) / alloc.width
            max_y = (alloc.height - bottom) / alloc.height

            # Clamp to work area bounds
            new_x = max(min_x, min(max_x - orig_w, orig_x + dx))
            new_y = max(min_y, min(max_y - orig_h, orig_y + dy))

            self.selected_zone.x = new_x
            self.selected_zone.y = new_y
            self.queue_draw()
            return True

        # Update resizing
        if self.is_resizing and self.move_start and self.zone_original_rect:
            self._handle_resize(x, y, alloc.width, alloc.height)
            self.queue_draw()
            return True

        return False

    def on_key_press(self, widget, event):
        """Handle keyboard shortcuts"""
        keyval = event.keyval
        keyname = Gdk.keyval_name(keyval)

        # ESC - Exit
        if keyname == 'Escape':
            Gtk.main_quit()
            return True

        # H - Toggle help
        if keyname in ('h', 'H'):
            self.show_help = not self.show_help
            self.queue_draw()
            return True

        # Tile layout shortcuts (available in overlay and Layout Manager).
        if keyname in ('f', 'F'):
            self._create_fullscreen_zone()
            return True
        if keyname in ('v', 'V'):
            self._split_selected_zone("vertical")
            return True
        if keyname in ('b', 'B'):
            self._split_selected_zone("horizontal")
            return True

        # D - Toggle dimension display
        if keyname in ('d', 'D'):
            self.show_dimensions = not self.show_dimensions
            self.status_message = f"Dimension display: {'ON' if self.show_dimensions else 'OFF'}"
            self.queue_draw()
            return True

        # S - Save
        if keyname in ('s', 'S'):
            self._save_zones()
            return True

        # C - Clear all zones
        if keyname in ('c', 'C'):
            self.zones.clear()
            self.selected_zone = None
            self._refresh_split_controls()
            self.current_file = None
            self.status_message = "Cleared all zones"
            self._auto_save()
            self._refresh_layout_manager()
            self.queue_draw()
            return True

        # N - New layout
        if keyname in ('n', 'N'):
            self._on_create_layout()
            return True

        # Delete or X - Remove selected zone
        if keyname in ('Delete', 'x', 'X') and self.selected_zone:
            zone_name = self.selected_zone.name
            self.zones.remove(self.selected_zone)
            self.selected_zone = None
            self._refresh_split_controls()
            self._auto_save()
            self._refresh_layout_manager()
            self.status_message = f"Deleted {zone_name}"
            self.queue_draw()
            return True

        # Arrow keys - Move or resize selected zone with 1px precision
        if self.selected_zone and keyname in ('Up', 'Down', 'Left', 'Right'):
            alloc = self.get_allocation()
            canvas_width = alloc.width
            canvas_height = alloc.height

            # Check if Alt is pressed for resize mode
            is_alt = event.state & Gdk.ModifierType.MOD1_MASK

            # Use high precision: convert to pixels and back using exact division
            # This avoids cumulative rounding errors
            if is_alt:
                # Alt + Arrow: Resize by 1px
                # Get current absolute pixel dimensions and position
                abs_x = self.selected_zone.x * canvas_width
                abs_y = self.selected_zone.y * canvas_height
                abs_w = self.selected_zone.width * canvas_width
                abs_h = self.selected_zone.height * canvas_height

                orig_abs_w = int(round(abs_w))
                orig_abs_h = int(round(abs_h))

                if keyname == 'Right':
                    abs_w += 1
                elif keyname == 'Left':
                    abs_w = max(30, abs_w - 1)
                elif keyname == 'Down':
                    abs_h += 1
                elif keyname == 'Up':
                    abs_h = max(30, abs_h - 1)

                # Ensure zone stays within work area
                left, top, right, bottom = self.workarea_margins
                max_width = (canvas_width - right) - abs_x
                max_height = (canvas_height - bottom) - abs_y

                abs_w = min(max_width, abs_w)
                abs_h = min(max_height, abs_h)

                # Convert back to relative coordinates with full precision
                self.selected_zone.width = abs_w / canvas_width
                self.selected_zone.height = abs_h / canvas_height

                new_abs_w = int(round(abs_w))
                new_abs_h = int(round(abs_h))

                if new_abs_w != orig_abs_w or new_abs_h != orig_abs_h:
                    self.status_message = f"Resized {self.selected_zone.name} → {new_abs_w}×{new_abs_h}px"
                else:
                    self.status_message = f"{self.selected_zone.name} at boundary (cannot resize)"
            else:
                # Arrow: Move by 1px
                # Get current absolute pixel positions with full precision
                abs_x = self.selected_zone.x * canvas_width
                abs_y = self.selected_zone.y * canvas_height
                abs_w = self.selected_zone.width * canvas_width
                abs_h = self.selected_zone.height * canvas_height

                orig_abs_x = int(round(abs_x))
                orig_abs_y = int(round(abs_y))

                if keyname == 'Right':
                    abs_x += 1
                elif keyname == 'Left':
                    abs_x -= 1
                elif keyname == 'Down':
                    abs_y += 1
                elif keyname == 'Up':
                    abs_y -= 1

                # Calculate work area bounds in absolute pixels
                left, top, right, bottom = self.workarea_margins
                min_x = left
                min_y = top
                max_x = canvas_width - right - abs_w
                max_y = canvas_height - bottom - abs_h

                # Clamp to work area
                abs_x = max(min_x, min(max_x, abs_x))
                abs_y = max(min_y, min(max_y, abs_y))

                # Convert back to relative coordinates with full precision
                self.selected_zone.x = abs_x / canvas_width
                self.selected_zone.y = abs_y / canvas_height

                new_abs_x = int(round(abs_x))
                new_abs_y = int(round(abs_y))

                if new_abs_x != orig_abs_x or new_abs_y != orig_abs_y:
                    self.status_message = f"Moved {self.selected_zone.name} → X:{new_abs_x} Y:{new_abs_y}"
                else:
                    self.status_message = f"{self.selected_zone.name} at boundary (cannot move)"

            self._auto_save()
            self.queue_draw()
            return True

        # Number keys - Apply presets
        presets = {
            '1': 'halves',
            '2': 'thirds',
            '3': 'quarters',
            '4': 'grid3x3'
        }
        if keyname in presets:
            preset_name = presets[keyname]
            # Get screen dimensions
            screen = self.get_screen()
            screen_width = screen.get_width()
            screen_height = screen.get_height()

            # Calculate work area (usable space)
            left, top, right, bottom = self.workarea_margins
            workarea_x = left
            workarea_y = top
            workarea_width = screen_width - left - right
            workarea_height = screen_height - top - bottom

            # Create preset layout with work area dimensions
            absolute_zones = create_preset_layout(preset_name, workarea_width, workarea_height)

            # Convert to relative coordinates (0.0-1.0) with work area offset
            self.zones = []
            for zone in absolute_zones:
                rel_zone = Zone(
                    x=(zone.x + workarea_x) / screen_width,
                    y=(zone.y + workarea_y) / screen_height,
                    width=zone.width / screen_width,
                    height=zone.height / screen_height,
                    name=zone.name,
                    color=zone.color
                )
                self.zones.append(rel_zone)
            self.selected_zone = None
            self.status_message = f"Applied preset: {preset_name} ({len(self.zones)} zones)"
            self._auto_save()
            self._refresh_layout_manager()
            self.queue_draw()
            return True

        return False

    def _handle_resize(self, x: int, y: int, canvas_width: int, canvas_height: int):
        """Handle zone resizing based on which handle is being dragged"""
        if not self.selected_zone or not self.zone_original_rect or not self.move_start:
            return

        dx = (x - self.move_start[0]) / canvas_width
        dy = (y - self.move_start[1]) / canvas_height

        orig_x, orig_y, orig_w, orig_h = self.zone_original_rect

        new_x, new_y = orig_x, orig_y
        new_w, new_h = orig_w, orig_h

        # Apply resize based on handle
        if 'n' in self.resize_handle:
            new_y = orig_y + dy
            new_h = orig_h - dy
        if 's' in self.resize_handle:
            new_h = orig_h + dy
        if 'w' in self.resize_handle:
            new_x = orig_x + dx
            new_w = orig_w - dx
        if 'e' in self.resize_handle:
            new_w = orig_w + dx

        # Clamp to canvas bounds and minimum size
        min_size = 0.03
        if new_w < min_size:
            if 'w' in self.resize_handle:
                new_x = orig_x + orig_w - min_size
            new_w = min_size
        if new_h < min_size:
            if 'n' in self.resize_handle:
                new_y = orig_y + orig_h - min_size
            new_h = min_size

        # Calculate work area bounds in relative coordinates
        left, top, right, bottom = self.workarea_margins
        min_x = left / canvas_width
        min_y = top / canvas_height
        max_x = (canvas_width - right) / canvas_width
        max_y = (canvas_height - bottom) / canvas_height

        # Clamp to work area bounds
        new_x = max(min_x, min(max_x - new_w, new_x))
        new_y = max(min_y, min(max_y - new_h, new_y))
        new_w = min(max_x - new_x, new_w)
        new_h = min(max_y - new_y, new_h)

        self.selected_zone.x = new_x
        self.selected_zone.y = new_y
        self.selected_zone.width = new_w
        self.selected_zone.height = new_h

    def _shared_dividers(self, width, height):
        """Generate shared edges for tiles, including T-junction segments."""
        result = []
        tolerance = 2  # pixels; tolerates serialized normalized coordinates
        for a in self.zones:
            for b in self.zones:
                if a is b:
                    continue
                ax1, ax2 = a.x * width, (a.x + a.width) * width
                ay1, ay2 = a.y * height, (a.y + a.height) * height
                bx1, bx2 = b.x * width, (b.x + b.width) * width
                by1, by2 = b.y * height, (b.y + b.height) * height
                if abs(ax2 - bx1) <= tolerance:
                    start, end = max(ay1, by1), min(ay2, by2)
                    if end - start >= 12:
                        result.append(('v', (ax2 + bx1) / 2, start, end))
                if abs(ay2 - by1) <= tolerance:
                    start, end = max(ax1, bx1), min(ax2, bx2)
                    if end - start >= 12:
                        result.append(('h', (ay2 + by1) / 2, start, end))
        return result

    def _shared_divider_at(self, x, y, width, height):
        # The divider remains clickable inside the visual spacing gap.
        for direction, position, start, end in self._shared_dividers(width, height):
            if direction == 'v' and abs(x - position) <= 12 and start + 4 <= y <= end - 4:
                return (direction, position, start, end)
            if direction == 'h' and abs(y - position) <= 12 and start + 4 <= x <= end - 4:
                return (direction, position, start, end)
        return None

    def _draw_shared_dividers(self, cr, width, height):
        # Short grab bars centered on shared boundaries (not on outside edges).
        seen = set()
        for direction, pos, start, end in self._shared_dividers(width, height):
            key = (direction, round(pos), round(start), round(end))
            if key in seen:
                continue
            seen.add(key)
            center = (start + end) / 2
            cr.set_source_rgba(1, 1, 1, .92)
            cr.set_line_width(5)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            if direction == 'v':
                cr.move_to(pos, center - 17)
                cr.line_to(pos, center + 17)
            else:
                cr.move_to(center - 17, pos)
                cr.line_to(center + 17, pos)
            cr.stroke()

    def _move_shared_divider(self, x, y, width, height):
        divider, start_x, start_y, originals = self.divider_drag
        direction, position, seg_start, seg_end = divider
        span = width if direction == 'v' else height
        delta = ((x - start_x) if direction == 'v' else (y - start_y)) / span
        base = position / span
        tolerance = 2 / span

        # Move every edge on the same seam, including both branches of a
        # T-junction. Moving only the clicked segment would create an overlap
        # or hole next to the other branch.
        left_side = []
        right_side = []
        for zone, ox, oy, ow, oh in originals:
            low = ox if direction == 'v' else oy
            high = low + (ow if direction == 'v' else oh)
            if abs(high - base) <= tolerance:
                left_side.append((zone, ox, oy, ow, oh))
            if abs(low - base) <= tolerance:
                right_side.append((zone, ox, oy, ow, oh))
        if not left_side or not right_side:
            return

        # Restrict movement so none of the adjacent tiles becomes too small.
        min_width = 60 / span
        allowed_min = max((min_width - (ow if direction == 'v' else oh))
                          for _, ox, oy, ow, oh in left_side)
        allowed_max = min(((ow if direction == 'v' else oh) - min_width)
                          for _, ox, oy, ow, oh in right_side)
        delta = max(allowed_min, min(allowed_max, delta))
        for zone, ox, oy, ow, oh in left_side:
            if direction == 'v': zone.width = ow + delta
            else: zone.height = oh + delta
        for zone, ox, oy, ow, oh in right_side:
            if direction == 'v': zone.x, zone.width = ox + delta, ow - delta
            else: zone.y, zone.height = oy + delta, oh - delta
        self.status_message = "Resizing adjacent tiles"

    def _get_zone_at_position(self, x: int, y: int,
                              canvas_width: int, canvas_height: int) -> Optional[Zone]:
        """Find zone at given position (smallest zone first for overlaps)"""
        rel_x = x / canvas_width
        rel_y = y / canvas_height

        # Find all zones containing the point
        containing_zones = [z for z in self.zones if z.contains_point(rel_x, rel_y)]

        # Return smallest zone (highest priority)
        if containing_zones:
            return min(containing_zones, key=lambda z: z.width * z.height)
        return None

    def _get_handle_at_position(self, x: int, y: int,
                               canvas_width: int, canvas_height: int) -> Optional[str]:
        """Check if position is over a resize handle"""
        if not self.selected_zone:
            return None

        zone = self.selected_zone
        zx = int(zone.x * canvas_width)
        zy = int(zone.y * canvas_height)
        zw = int(zone.width * canvas_width)
        zh = int(zone.height * canvas_height)

        threshold = self.HANDLE_SIZE

        # Check corners first (higher priority)
        if abs(x - zx) < threshold and abs(y - zy) < threshold:
            return 'nw'
        if abs(x - (zx + zw)) < threshold and abs(y - zy) < threshold:
            return 'ne'
        if abs(x - zx) < threshold and abs(y - (zy + zh)) < threshold:
            return 'sw'
        if abs(x - (zx + zw)) < threshold and abs(y - (zy + zh)) < threshold:
            return 'se'

        # Check edges
        if abs(y - zy) < threshold and zx < x < zx + zw:
            return 'n'
        if abs(y - (zy + zh)) < threshold and zx < x < zx + zw:
            return 's'
        if abs(x - zx) < threshold and zy < y < zy + zh:
            return 'w'
        if abs(x - (zx + zw)) < threshold and zy < y < zy + zh:
            return 'e'

        return None

    def _next_zone_name(self) -> str:
        """Produce a unique zone label without depending on list positions."""
        existing = {zone.name for zone in self.zones}
        number = 1
        while f"Zone {number}" in existing:
            number += 1
        return f"Zone {number}"

    def _create_fullscreen_zone(self, *args):
        """Replace the layout with one zone covering the usable work area."""
        screen = self.get_screen()
        width, height = screen.get_width(), screen.get_height()
        left, top, right, bottom = self.workarea_margins
        work_width = width - left - right
        work_height = height - top - bottom
        if work_width <= 0 or work_height <= 0:
            self.status_message = "Could not determine a valid work area"
            self.queue_draw()
            return
        zone = Zone(left / width, top / height,
                    work_width / width, work_height / height,
                    "Fullscreen")
        self.zones = [zone]
        self.selected_zone = zone
        self.status_message = "Created fullscreen zone. Select Split Vertical or Split Horizontal."
        self._auto_save()
        self._refresh_layout_manager()
        self._refresh_split_controls()
        self.queue_draw()

    def _split_selected_zone(self, orientation: str):
        """Split the selected zone without gaps or overlaps; snap spacing is separate."""
        zone = self.selected_zone
        if zone is None or zone not in self.zones:
            self.status_message = "Select a zone to split first"
            self.queue_draw()
            return
        alloc = self.get_allocation()
        width = alloc.width or self.get_screen().get_width()
        height = alloc.height or self.get_screen().get_height()
        # Require two usable tiles; keeps splitting very small zones sensible.
        min_pixels = 60
        if orientation == "vertical":
            if zone.width * width < 2 * min_pixels:
                self.status_message = "Zone is too narrow to split vertically"
                self.queue_draw()
                return
            half = zone.width / 2.0
            first = Zone(zone.x, zone.y, half, zone.height, zone.name, zone.color)
            second = Zone(zone.x + half, zone.y, zone.width - half, zone.height,
                          self._next_zone_name(), zone.color)
        elif orientation == "horizontal":
            if zone.height * height < 2 * min_pixels:
                self.status_message = "Zone is too short to split horizontally"
                self.queue_draw()
                return
            half = zone.height / 2.0
            first = Zone(zone.x, zone.y, zone.width, half, zone.name, zone.color)
            second = Zone(zone.x, zone.y + half, zone.width, zone.height - half,
                          self._next_zone_name(), zone.color)
        else:
            raise ValueError(f"Unknown split orientation: {orientation}")

        index = self.zones.index(zone)
        self.zones[index:index + 1] = [first, second]
        self.selected_zone = second
        self.status_message = f"Split {zone.name} {orientation}ly (50/50); selected {second.name}"
        self._auto_save()
        self._refresh_layout_manager()
        self._refresh_split_controls()
        self.queue_draw()

    def _refresh_split_controls(self):
        """Show selected-zone status in the layout manager."""
        if hasattr(self, "selected_zone_label"):
            name = self.selected_zone.name if self.selected_zone in self.zones else None
            self.selected_zone_label.set_text(
                f"Selected zone: {name}" if name else "Selected zone: none"
            )
        for attr in ("split_vertical_button", "split_horizontal_button"):
            if hasattr(self, attr):
                getattr(self, attr).set_sensitive(self.selected_zone in self.zones)

    def _save_zones(self):
        """Save zones to current layout"""
        if not self.current_layout_name:
            self.current_layout_name = "default"

        try:
            # Check if layout exists
            layout = self.layout_library.load_layout(self.current_layout_name)
            if layout:
                # Update existing layout
                layout.update_zones(self.zones)
                self.layout_library.save_layout(layout)
            else:
                # Create new layout
                self.layout_library.create_layout(self.current_layout_name, self.zones, "")
            self._request_window_reflow()

            self.status_message = f"Saved {len(self.zones)} zones to layout '{self.current_layout_name}'"
        except Exception as e:
            self.status_message = f"Failed to save: {e}"

        self.queue_draw()

    def _auto_save(self):
        """Auto-save zones silently (no status message)"""
        if not self.current_layout_name:
            self.current_layout_name = "default"

        try:
            layout = self.layout_library.load_layout(self.current_layout_name)
            if layout:
                layout.update_zones(self.zones)
                self.layout_library.save_layout(layout)
            else:
                self.layout_library.create_layout(self.current_layout_name, self.zones, "")
            self._request_window_reflow()
        except Exception as e:
            print(f"Auto-save failed: {e}")

    def _create_layout_manager_window(self):
        """Create always-visible layout manager window"""
        if self.layout_manager_window:
            return  # Already created

        window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        window.set_title("Layout Manager")
        window.set_default_size(300, 400)
        window.set_keep_above(True)
        window.set_decorated(True)
        window.set_deletable(False)  # Can't close it
        window.set_type_hint(Gdk.WindowTypeHint.UTILITY)  # Make it a utility window
        window.set_skip_taskbar_hint(True)  # Don't show in taskbar

        # Position offset from center (500px to the left)
        screen = self.get_screen()
        screen_width = screen.get_width()
        screen_height = screen.get_height()
        window_width = 300
        window_height = 400

        x_pos = (screen_width - window_width) // 2 - 500
        y_pos = (screen_height - window_height) // 2

        window.move(x_pos, y_pos)

        self.layout_manager_window = window

        # Main container
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        vbox.set_margin_start(15)
        vbox.set_margin_end(15)
        vbox.set_margin_top(15)
        vbox.set_margin_bottom(15)
        window.add(vbox)

        # Header label
        self.layout_header = Gtk.Label()
        self.layout_header.set_markup(f"<b>Current: {self.current_layout_name} ({len(self.zones)} zones)</b>")
        self.layout_header.set_halign(Gtk.Align.START)
        vbox.pack_start(self.layout_header, False, False, 0)

        # Persistent snap gap (shared with the daemon). Affects new snaps
        # immediately; no need to restart PowerSnap after changing it.
        gap_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        gap_label = Gtk.Label(label="Zone gap / outer margin (px):")
        gap_label.set_halign(Gtk.Align.START)
        gap_row.pack_start(gap_label, True, True, 0)
        gap_spin = Gtk.SpinButton.new_with_range(0, 200, 1)
        gap_spin.set_value(load_gap_px())
        gap_spin.set_tooltip_text("Gap between adjacent zones and margin around outer screen edges")
        def on_gap_changed(spin):
            try:
                save_gap_px(spin.get_value_as_int())
                self.queue_draw()
            except OSError as exc:
                print(f"Could not save window spacing: {exc}", flush=True)
        gap_spin.connect("value-changed", on_gap_changed)
        gap_row.pack_start(gap_spin, False, False, 0)
        vbox.pack_start(gap_row, False, False, 0)

        # Tile-based editing tools: begin with a full work-area zone, then split
        # the selected zone like a tiling editor (vertical = left/right).
        split_heading = Gtk.Label(label="Tile layout")
        split_heading.set_halign(Gtk.Align.START)
        split_heading.get_style_context().add_class("heading")
        vbox.pack_start(split_heading, False, False, 0)

        fullscreen_button = Gtk.Button(label="Start Fullscreen Zone")
        fullscreen_button.set_tooltip_text("Replace all zones with one work-area-sized zone")
        fullscreen_button.connect("clicked", self._create_fullscreen_zone)
        vbox.pack_start(fullscreen_button, False, False, 0)

        self.selected_zone_label = Gtk.Label(label="Selected zone: none")
        self.selected_zone_label.set_halign(Gtk.Align.START)
        self.selected_zone_label.set_line_wrap(True)
        vbox.pack_start(self.selected_zone_label, False, False, 0)

        split_button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self.split_vertical_button = Gtk.Button(label="Split Vertical")
        self.split_vertical_button.set_tooltip_text("Split into equal left and right zones")
        self.split_vertical_button.connect(
            "clicked", lambda button: self._split_selected_zone("vertical"))
        split_button_row.pack_start(self.split_vertical_button, True, True, 0)

        self.split_horizontal_button = Gtk.Button(label="Split Horizontal")
        self.split_horizontal_button.set_tooltip_text("Split into equal top and bottom zones")
        self.split_horizontal_button.connect(
            "clicked", lambda button: self._split_selected_zone("horizontal"))
        split_button_row.pack_start(self.split_horizontal_button, True, True, 0)
        vbox.pack_start(split_button_row, False, False, 0)
        self._refresh_split_controls()

        # Scrollable list of layouts
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        vbox.pack_start(scrolled, True, True, 0)

        # List box for layouts
        self.layout_listbox = Gtk.ListBox()
        self.layout_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.layout_listbox.set_activate_on_single_click(False)  # Only activate on double-click

        # Connect double-click handler
        def on_row_activated(listbox, row):
            """Handle double-click on layout"""
            if hasattr(row, 'layout_name'):
                self.current_layout_name = row.layout_name
                self._load_current_layout()
                self.selected_zone = None
                self._refresh_split_controls()
                self._refresh_layout_manager()
                self.queue_draw()

                # Auto-map current workspace to selected layout
                workspace_id = self.get_current_workspace()
                if self.layout_library.set_active_layout(workspace_id, row.layout_name):
                    print(f"Auto-mapped workspace {workspace_id} → layout '{row.layout_name}'")
                else:
                    print(f"Failed to auto-map workspace {workspace_id} to layout '{row.layout_name}'")

        self.layout_listbox.connect("row-activated", on_row_activated)

        # Connect button press for slow click rename
        self.layout_listbox.connect("button-press-event", self._on_layout_button_press)

        # Connect key press for F2 rename
        window.connect("key-press-event", self._on_layout_manager_key_press)

        scrolled.add(self.layout_listbox)

        # Populate layouts initially
        self._refresh_layout_list()

        # Buttons at the bottom
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        button_box.set_homogeneous(True)
        vbox.pack_start(button_box, False, False, 0)

        # Create New button
        create_btn = Gtk.Button(label="Create New")
        create_btn.connect("clicked", lambda btn: self._on_create_layout())
        button_box.pack_start(create_btn, True, True, 0)

        # Rename button
        rename_btn = Gtk.Button(label="Rename")
        rename_btn.connect("clicked", lambda btn: self._on_rename_layout())
        button_box.pack_start(rename_btn, True, True, 0)

        # Delete button
        delete_btn = Gtk.Button(label="Delete")
        delete_btn.connect("clicked", lambda btn: self._on_delete_layout())
        button_box.pack_start(delete_btn, True, True, 0)

        # Close button (exits entire editor)
        close_btn = Gtk.Button(label="Close Editor")
        close_btn.connect("clicked", lambda btn: Gtk.main_quit())
        vbox.pack_start(close_btn, False, False, 5)

        window.show_all()

    def _refresh_layout_list(self):
        """Refresh the layout list in the manager window"""
        if not self.layout_manager_window or not hasattr(self, 'layout_listbox'):
            return

        # Clear existing rows
        for child in self.layout_listbox.get_children():
            self.layout_listbox.remove(child)

        # Populate layouts
        layouts = self.layout_library.list_layouts()
        for layout_name in sorted(layouts):
            row = Gtk.ListBoxRow()
            row.layout_name = layout_name

            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            hbox.set_margin_start(10)
            hbox.set_margin_end(10)
            hbox.set_margin_top(5)
            hbox.set_margin_bottom(5)

            # Layout name with star if current
            label = Gtk.Label()
            if layout_name == self.current_layout_name:
                label.set_markup(f"<b>{layout_name}</b> ★")
            else:
                label.set_text(layout_name)
            label.set_halign(Gtk.Align.START)
            hbox.pack_start(label, True, True, 0)

            # Zone count
            layout = self.layout_library.load_layout(layout_name)
            if layout:
                count_label = Gtk.Label(label=f"{len(layout.zones)} zones")
                count_label.get_style_context().add_class("dim-label")
                hbox.pack_start(count_label, False, False, 0)

            row.add(hbox)
            self.layout_listbox.add(row)

            # Select current layout
            if layout_name == self.current_layout_name:
                self.layout_listbox.select_row(row)

        self.layout_listbox.show_all()

    def _refresh_layout_manager(self):
        """Update layout manager window to reflect current state"""
        if hasattr(self, 'layout_header'):
            self.layout_header.set_markup(f"<b>Current: {self.current_layout_name} ({len(self.zones)} zones)</b>")
        self._refresh_layout_list()
        self._refresh_split_controls()

    def _on_create_layout(self):
        """Handle Create New layout button"""
        # Prompt for new layout name
        name_dialog = Gtk.MessageDialog(
            transient_for=self.layout_manager_window,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text="Create New Layout"
        )
        name_dialog.format_secondary_text("Enter a name for the new layout:")

        # Add text entry to dialog
        content_area = name_dialog.get_content_area()
        entry = Gtk.Entry()

        # Suggest default name
        base_name = "layout"
        counter = 1
        default_name = base_name
        existing_layouts = self.layout_library.list_layouts()
        while default_name in existing_layouts:
            default_name = f"{base_name}{counter}"
            counter += 1

        entry.set_text(default_name)
        entry.set_activates_default(True)
        content_area.pack_start(entry, False, False, 5)
        name_dialog.show_all()

        name_response = name_dialog.run()
        new_name = entry.get_text().strip()
        name_dialog.destroy()

        if name_response == Gtk.ResponseType.OK and new_name:
            # Sanitize name
            safe_name = "".join(c for c in new_name if c.isalnum() or c in (' ', '-', '_')).strip()

            if safe_name:
                # Check if name already exists
                if safe_name in self.layout_library.list_layouts():
                    error_dialog = Gtk.MessageDialog(
                        transient_for=self.layout_manager_window,
                        modal=True,
                        message_type=Gtk.MessageType.ERROR,
                        buttons=Gtk.ButtonsType.OK,
                        text="Layout already exists"
                    )
                    error_dialog.format_secondary_text(f"A layout named '{safe_name}' already exists. Please choose a different name.")
                    error_dialog.run()
                    error_dialog.destroy()
                else:
                    self.current_layout_name = safe_name
                    # Clear zones for new layout
                    self.zones.clear()
                    self.selected_zone = None
                    self.status_message = f"Created new layout: {safe_name}"
                    # Save the empty layout immediately
                    self._auto_save()
                    self._refresh_layout_manager()
                    self.queue_draw()

                    # Auto-map current workspace to new layout
                    workspace_id = self.get_current_workspace()
                    if self.layout_library.set_active_layout(workspace_id, safe_name):
                        print(f"Auto-mapped workspace {workspace_id} → layout '{safe_name}'")
                    else:
                        print(f"Failed to auto-map workspace {workspace_id} to layout '{safe_name}'")
            else:
                self.status_message = "Invalid layout name"
                self.queue_draw()

    def _on_delete_layout(self):
        """Handle Delete layout button"""
        selected_row = self.layout_listbox.get_selected_row()
        if selected_row and hasattr(selected_row, 'layout_name'):
            layout_to_delete = selected_row.layout_name

            # Confirm deletion
            confirm_dialog = Gtk.MessageDialog(
                transient_for=self.layout_manager_window,
                modal=True,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.YES_NO,
                text=f"Delete layout '{layout_to_delete}'?"
            )
            confirm_dialog.format_secondary_text("This action cannot be undone.")

            confirm_response = confirm_dialog.run()
            confirm_dialog.destroy()

            if confirm_response == Gtk.ResponseType.YES:
                # Delete the layout
                if self.layout_library.delete_layout(layout_to_delete):
                    self.status_message = f"Deleted layout: {layout_to_delete}"

                    # If we deleted the current layout, switch to another or create new
                    if layout_to_delete == self.current_layout_name:
                        remaining = self.layout_library.list_layouts()
                        if remaining:
                            self.current_layout_name = remaining[0]
                            self._load_current_layout()
                        else:
                            # No layouts left, create default
                            self.current_layout_name = "default"
                            self.zones.clear()

                        # Update current workspace to point to the new layout
                        workspace_id = self.get_current_workspace()
                        if self.layout_library.set_active_layout(workspace_id, self.current_layout_name):
                            print(f"Updated workspace {workspace_id} → layout '{self.current_layout_name}' after deletion")
                        else:
                            print(f"Failed to update workspace {workspace_id} mapping after deletion")

                    self.selected_zone = None
                    self._refresh_layout_manager()
                    self.queue_draw()
                else:
                    self.status_message = f"Failed to delete layout: {layout_to_delete}"
                    self.queue_draw()

    def _show_layout_picker(self):
        """Legacy method - now just refreshes the always-visible layout manager"""
        self._refresh_layout_manager()

    def _on_layout_button_press(self, widget, event):
        """Handle button press on layout listbox for slow click rename"""
        import time

        # Get the row at the clicked position
        row = self.layout_listbox.get_row_at_y(int(event.y))

        if not row or not hasattr(row, 'layout_name'):
            return False

        current_time = time.time()

        # Check if this is a slow click (click on already selected row after 0.5s)
        if (row == self.layout_last_click_row and
            row == self.layout_listbox.get_selected_row() and
            current_time - self.layout_last_click_time > 0.5 and
            current_time - self.layout_last_click_time < 1.0):
            # Trigger rename
            self._start_rename_layout(row.layout_name)
            return True

        self.layout_last_click_time = current_time
        self.layout_last_click_row = row
        return False

    def _on_layout_manager_key_press(self, widget, event):
        """Handle keyboard shortcuts in layout manager window"""
        keyval = event.keyval
        keyname = Gdk.keyval_name(keyval)

        # F2 to rename selected layout
        if keyname == 'F2':
            selected_row = self.layout_listbox.get_selected_row()
            if selected_row and hasattr(selected_row, 'layout_name'):
                self._start_rename_layout(selected_row.layout_name)
                return True

        # Forward all other shortcuts to the main overlay's key handler
        # This allows all shortcuts to work even when layout manager has focus
        return self.on_key_press(widget, event)

    def _on_rename_layout(self):
        """Handle Rename button click"""
        selected_row = self.layout_listbox.get_selected_row()
        if selected_row and hasattr(selected_row, 'layout_name'):
            self._start_rename_layout(selected_row.layout_name)

    def _start_rename_layout(self, layout_name: str):
        """Show rename dialog for a layout"""
        dialog = Gtk.MessageDialog(
            transient_for=self.layout_manager_window,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text="Rename Layout"
        )
        dialog.format_secondary_text(f"Enter new name for '{layout_name}':")

        # Add text entry to dialog
        content_area = dialog.get_content_area()
        entry = Gtk.Entry()
        entry.set_text(layout_name)
        entry.set_activates_default(True)
        entry.select_region(0, -1)  # Select all text
        content_area.pack_start(entry, False, False, 5)
        dialog.show_all()

        response = dialog.run()
        new_name = entry.get_text().strip()
        dialog.destroy()

        if response == Gtk.ResponseType.OK and new_name and new_name != layout_name:
            # Sanitize the new name
            safe_name = "".join(c for c in new_name if c.isalnum() or c in (' ', '-', '_')).strip()

            if safe_name:
                # Perform rename
                if self.layout_library.rename_layout(layout_name, safe_name):
                    self.status_message = f"Renamed layout: {layout_name} → {safe_name}"

                    # Update current layout name if we renamed the current one
                    if self.current_layout_name == layout_name:
                        self.current_layout_name = safe_name

                    self._refresh_layout_manager()
                    self.queue_draw()
                else:
                    # Show error
                    error_dialog = Gtk.MessageDialog(
                        transient_for=self.layout_manager_window,
                        modal=True,
                        message_type=Gtk.MessageType.ERROR,
                        buttons=Gtk.ButtonsType.OK,
                        text="Failed to rename layout"
                    )
                    error_dialog.format_secondary_text(
                        f"Could not rename '{layout_name}' to '{safe_name}'. "
                        f"The new name may already exist or be invalid."
                    )
                    error_dialog.run()
                    error_dialog.destroy()
            else:
                error_dialog = Gtk.MessageDialog(
                    transient_for=self.layout_manager_window,
                    modal=True,
                    message_type=Gtk.MessageType.ERROR,
                    buttons=Gtk.ButtonsType.OK,
                    text="Invalid layout name"
                )
                error_dialog.format_secondary_text("Please enter a valid name containing only letters, numbers, spaces, hyphens, and underscores.")
                error_dialog.run()
                error_dialog.destroy()



def main():
    """Run zone editor overlay"""
    window = ZoneEditorOverlay()
    window.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
