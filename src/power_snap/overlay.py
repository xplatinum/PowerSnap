"""GTK-based transparent overlay for zone visualization"""

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GLib
import cairo
from typing import List, Optional, Tuple, Callable

from .zone import Zone


class OverlayWindow(Gtk.Window):
    """Transparent full-screen overlay for displaying snap zones"""

    def __init__(self):
        super().__init__()

        self.zones: List[Zone] = []
        self.highlighted_zone: Optional[Zone] = None
        self.selected_zone: Optional[Zone] = None
        self.mouse_x = 0
        self.mouse_y = 0
        self._mouse_poll_timer = None

        # Callbacks
        self._on_zone_selected_callback: Optional[Callable[[Zone], None]] = None
        self._on_overlay_hidden_callback: Optional[Callable[[], None]] = None

        self._setup_window()
        self._setup_events()

    def _setup_window(self):
        """Configure window properties for transparent overlay"""
        # Set window type to overlay/notification
        self.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)

        # Make window fullscreen
        self.fullscreen()

        # Set window to be above everything
        self.set_keep_above(True)

        # Make window accept input and focus (needed for keyboard events)
        self.set_accept_focus(True)

        # Enable transparency
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)

        # Enable drawing
        self.set_app_paintable(True)

        # Don't show in taskbar/pager
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)

        # Set decorated to False (no title bar)
        self.set_decorated(False)

        # Connect draw signal
        self.connect('draw', self._on_draw)

    def _setup_events(self):
        """Setup mouse and keyboard event handlers"""
        # Enable events
        self.add_events(
            Gdk.EventMask.POINTER_MOTION_MASK |
            Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.KEY_PRESS_MASK
        )

        # Connect event handlers
        self.connect('motion-notify-event', self._on_mouse_move)
        self.connect('button-press-event', self._on_button_press)
        self.connect('key-press-event', self._on_key_press)
        self.connect('delete-event', self._on_delete)

    def _on_draw(self, widget, ctx: cairo.Context):
        """Draw the overlay content"""
        # Clear with transparent background
        ctx.set_source_rgba(0, 0, 0, 0)
        ctx.set_operator(cairo.Operator.SOURCE)
        ctx.paint()

        # Draw semi-transparent dark background
        ctx.set_source_rgba(0, 0, 0, 0.3)
        ctx.set_operator(cairo.Operator.OVER)
        ctx.paint()

        # Get screen dimensions for coordinate conversion
        allocation = self.get_allocation()
        screen_width = allocation.width
        screen_height = allocation.height

        # Draw all zones
        for zone in self.zones:
            self._draw_zone(ctx, zone, screen_width, screen_height)

    def _draw_zone(self, ctx: cairo.Context, zone: Zone, screen_width: int, screen_height: int):
        """Draw a single zone with appropriate styling"""
        # Convert relative coordinates (0.0-1.0) to absolute pixels
        x = int(zone.x * screen_width)
        y = int(zone.y * screen_height)
        w = int(zone.width * screen_width)
        h = int(zone.height * screen_height)

        # Parse hex color
        color = self._parse_color(zone.color)

        # Determine opacity based on zone state
        is_highlighted = zone == self.highlighted_zone
        is_selected = zone == self.selected_zone

        # Draw filled rectangle with orange for highlighted/selected, zone color for inactive
        if is_selected:
            ctx.set_source_rgba(1.0, 0.5, 0.0, 0.4)  # Orange fill for selected
        elif is_highlighted:
            ctx.set_source_rgba(1.0, 0.5, 0.0, 0.3)  # Orange fill for highlighted
        else:
            ctx.set_source_rgba(color[0], color[1], color[2], 0.15)  # Zone color for inactive
        ctx.rectangle(x, y, w, h)
        ctx.fill()

        # Draw border with dashed pattern
        # Orange for highlighted/selected, blue for inactive
        if is_selected:
            ctx.set_source_rgba(1.0, 0.5, 0.0, 0.9)  # Brighter orange for selected
            ctx.set_line_width(3)
        elif is_highlighted:
            ctx.set_source_rgba(1.0, 0.5, 0.0, 0.784)  # Orange for highlighted
            ctx.set_line_width(2)
        else:
            ctx.set_source_rgba(0, 0.588, 1.0, 0.5)  # Dimmer blue for inactive
            ctx.set_line_width(2)

        # Set dashed line pattern (dash-dot style like CaptiX)
        # Pattern: [dash_length, gap, dot, gap]
        ctx.set_dash([10, 5, 2, 5])

        ctx.rectangle(x, y, w, h)
        ctx.stroke()

        # Reset dash pattern for other drawing operations
        ctx.set_dash([])

        # Draw zone name if present
        if zone.name:
            self._draw_zone_label(ctx, x, y, w, h, zone.name, is_highlighted or is_selected)

    def _draw_zone_label(self, ctx: cairo.Context, x: int, y: int, w: int, h: int, name: str, is_active: bool):
        """Draw zone name label"""
        # Set font
        ctx.select_font_face('Sans', cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)

        # Larger font for active zones
        font_size = 24 if is_active else 18
        ctx.set_font_size(font_size)

        # Get text dimensions
        extents = ctx.text_extents(name)
        text_width = extents.width
        text_height = extents.height

        # Calculate center position
        cx = x + w // 2
        cy = y + h // 2
        text_x = cx - text_width / 2
        text_y = cy + text_height / 2

        # Draw text shadow
        ctx.set_source_rgba(0, 0, 0, 0.8)
        ctx.move_to(text_x + 2, text_y + 2)
        ctx.show_text(name)

        # Draw text
        ctx.set_source_rgba(1, 1, 1, 1 if is_active else 0.9)
        ctx.move_to(text_x, text_y)
        ctx.show_text(name)

    def _parse_color(self, hex_color: str) -> Tuple[float, float, float]:
        """Parse hex color string to RGB tuple (0-1 range)"""
        hex_color = hex_color.lstrip('#')
        if len(hex_color) == 6:
            r = int(hex_color[0:2], 16) / 255.0
            g = int(hex_color[2:4], 16) / 255.0
            b = int(hex_color[4:6], 16) / 255.0
            return (r, g, b)
        return (0.2, 0.6, 1.0)  # Default blue

    def _on_mouse_move(self, widget, event: Gdk.EventMotion):
        """Handle mouse movement to update highlighted zone"""
        self.mouse_x = int(event.x)
        self.mouse_y = int(event.y)

        # Find zone under cursor
        new_highlighted = self._get_zone_at_point(self.mouse_x, self.mouse_y)

        # Update highlight if changed
        if new_highlighted != self.highlighted_zone:
            self.highlighted_zone = new_highlighted
            self.queue_draw()

    def _on_button_press(self, widget, event: Gdk.EventButton):
        """Handle mouse button press"""
        if event.button == 1:  # Left click
            # Select zone under cursor
            zone = self._get_zone_at_point(int(event.x), int(event.y))
            if zone:
                self.selected_zone = zone
                self.queue_draw()

                # Trigger callback
                if self._on_zone_selected_callback:
                    self._on_zone_selected_callback(zone)

                # Hide overlay after selection
                self.hide_overlay()

    def _on_key_press(self, widget, event: Gdk.EventKey):
        """Handle keyboard events"""
        if event.keyval == Gdk.KEY_Escape:
            # Cancel and hide
            self.selected_zone = None
            self.hide_overlay()
            return True
        return False

    def _on_delete(self, widget, event):
        """Handle window close event"""
        self.hide_overlay()
        return True

    def _get_zone_at_point(self, x: int, y: int) -> Optional[Zone]:
        """Get the zone at specified point (smallest zone if overlapping)"""
        # Get screen dimensions for coordinate conversion
        allocation = self.get_allocation()
        screen_width = allocation.width
        screen_height = allocation.height

        # Convert absolute mouse coordinates to relative (0.0-1.0)
        rel_x = x / screen_width
        rel_y = y / screen_height

        # Check zones using relative coordinates
        matching_zones = []
        for z in self.zones:
            if (z.x <= rel_x < z.x + z.width and
                z.y <= rel_y < z.y + z.height):
                matching_zones.append(z)

        if not matching_zones:
            return None
        # Return smallest zone (most specific)
        return min(matching_zones, key=lambda z: z.width * z.height)

    def set_zones(self, zones: List[Zone]):
        """Set the zones to display"""
        self.zones = zones
        self.queue_draw()

    def _poll_mouse_position(self):
        """Poll mouse position and update highlighted zone"""
        # Get current mouse position from the display
        display = self.get_display()
        seat = display.get_default_seat()
        pointer = seat.get_pointer()

        screen, x, y = pointer.get_position()

        # Update mouse position
        self.mouse_x = x
        self.mouse_y = y

        # Find zone under cursor
        new_highlighted = self._get_zone_at_point(x, y)

        # Update highlight if changed
        if new_highlighted != self.highlighted_zone:
            self.highlighted_zone = new_highlighted
            self.queue_draw()

        # Continue polling
        return True

    def show_overlay(self):
        """Show the overlay"""
        self.show_all()
        # Grab focus so we can receive keyboard events (especially Escape)
        self.grab_focus()
        self.queue_draw()

        # Start polling mouse position (needed because dragging a window blocks motion events)
        if self._mouse_poll_timer is None:
            self._mouse_poll_timer = GLib.timeout_add(50, self._poll_mouse_position)  # Poll every 50ms

    def hide_overlay(self):
        """Hide the overlay"""
        # Stop mouse polling
        if self._mouse_poll_timer is not None:
            GLib.source_remove(self._mouse_poll_timer)
            self._mouse_poll_timer = None

        self.hide()

        # Trigger callback
        if self._on_overlay_hidden_callback:
            self._on_overlay_hidden_callback()

    def set_on_zone_selected(self, callback: Callable[[Zone], None]):
        """Set callback for zone selection"""
        self._on_zone_selected_callback = callback

    def set_on_overlay_hidden(self, callback: Callable[[], None]):
        """Set callback for overlay hidden"""
        self._on_overlay_hidden_callback = callback

    def get_selected_zone(self) -> Optional[Zone]:
        """Get the currently selected zone"""
        return self.selected_zone

    def get_highlighted_zone(self) -> Optional[Zone]:
        """Get the currently highlighted zone"""
        return self.highlighted_zone


class OverlayManager:
    """Manages the overlay window lifecycle"""

    def __init__(self):
        self.overlay: Optional[OverlayWindow] = None
        self._is_visible = False

    def create_overlay(self) -> OverlayWindow:
        """Create and return overlay window"""
        if self.overlay is None:
            self.overlay = OverlayWindow()
        return self.overlay

    def show(self, zones: List[Zone]):
        """Show overlay with specified zones"""
        if self.overlay is None:
            self.create_overlay()

        self.overlay.set_zones(zones)
        self.overlay.show_overlay()
        self._is_visible = True

    def hide(self):
        """Hide overlay"""
        if self.overlay:
            self.overlay.hide_overlay()
        self._is_visible = False

    def is_visible(self) -> bool:
        """Check if overlay is visible"""
        return self._is_visible

    def toggle(self, zones: List[Zone]):
        """Toggle overlay visibility"""
        if self._is_visible:
            self.hide()
        else:
            self.show(zones)

    def destroy(self):
        """Destroy overlay window"""
        if self.overlay:
            self.overlay.destroy()
            self.overlay = None
        self._is_visible = False


def main():
    """Command-line interface for testing overlay"""
    import argparse
    import sys
    import os

    # Add parent directory to path to import zone module
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from power_snap.zone import create_preset_layout

    parser = argparse.ArgumentParser(description='PowerSnap Overlay Renderer')
    parser.add_argument('--show', action='store_true', help='Show overlay with test zones')
    parser.add_argument('--preset', choices=['halves', 'thirds', 'quarters', 'grid3x3'],
                       help='Use preset layout (default: quarters)')
    parser.add_argument('--screen-width', type=int, default=3440, help='Screen width')
    parser.add_argument('--screen-height', type=int, default=1440, help='Screen height')
    parser.add_argument('--duration', type=int, default=10, help='Duration to show overlay (seconds)')

    args = parser.parse_args()

    if not args.show:
        parser.print_help()
        return

    # Create zones from preset
    if args.preset:
        zones = create_preset_layout(args.preset, args.screen_width, args.screen_height)
        print(f"Created {args.preset} layout with {len(zones)} zones")
    else:
        # Create default test zones (quarters)
        zones = create_preset_layout('quarters', args.screen_width, args.screen_height)
        print(f"Created test layout with {len(zones)} zones")

    # Create overlay manager
    overlay_manager = OverlayManager()
    overlay = overlay_manager.create_overlay()

    # Set callbacks
    def on_zone_selected(zone: Zone):
        print(f"Zone selected: {zone}")

    def on_overlay_hidden():
        print("Overlay hidden")
        Gtk.main_quit()

    overlay.set_on_zone_selected(on_zone_selected)
    overlay.set_on_overlay_hidden(on_overlay_hidden)

    # Show overlay
    print(f"Showing overlay for {args.duration} seconds...")
    print("Click on a zone to select it (will hide overlay)")
    print("Press Escape to cancel and hide overlay")
    overlay_manager.show(zones)

    # Auto-hide after duration
    def auto_hide():
        print("Duration expired, hiding overlay")
        overlay_manager.hide()
        return False

    GLib.timeout_add_seconds(args.duration, auto_hide)

    # Run GTK main loop
    try:
        Gtk.main()
    except KeyboardInterrupt:
        print("\nInterrupted")
        overlay_manager.destroy()


if __name__ == '__main__':
    main()
