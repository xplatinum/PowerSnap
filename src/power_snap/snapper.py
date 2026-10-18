"""Core window snapping logic for PowerSnap"""

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GLib
from typing import Optional, Dict, Tuple, List
import threading
import time
import os

from .window_manager import WindowManager
from .zone import Zone
from .overlay import OverlayManager
from .input_monitor import KeyboardTracker, MouseTracker
from .layout_library import LayoutLibrary
from .settings import load_gap_px
from .tile_geometry import inset_rectangle
from .display_geometry import current_root_size
from Xlib import X, display as xdisplay


class WindowSnapper:
    """Manages window snapping to zones"""

    def __init__(self, trigger_modifier: str = 'alt'):
        self.window_manager = WindowManager()
        self.layout_library = LayoutLibrary()
        self.overlay_manager = OverlayManager()
        self.keyboard_tracker = KeyboardTracker()
        self.mouse_tracker = MouseTracker()

        # Configuration
        self.trigger_modifier = trigger_modifier.lower()  # 'shift', 'ctrl', 'alt', 'super'

        # State tracking
        self._active_window_id: Optional[int] = None
        self._original_geometry: Optional[Tuple[int, int, int, int]] = None
        self._is_snapping = False
        self._selected_zone: Optional[Zone] = None
        self._overlay_visible = False
        self._window_is_moving = False
        self._last_snap_time: float = 0  # Timestamp of last snap to prevent immediate re-trigger

        # Workspace support
        self._current_workspace = 0
        # Use a separate X11 connection for querying pointer state from GTK's
        # main thread. pynput may not see releases during an xfwm4 pointer grab.
        self._pointer_display = None
        self._pointer_was_down = False
        # Original geometry keyed by X11 client window ID. Keep it when moving
        # between zones; discard it only after an explicit unsnap/restore.
        self._saved_geometries: Dict[int, Tuple[int, int, int, int]] = {}
        self._restore_drag_window_id: Optional[int] = None
        self._restore_pointer_was_down = False
        self._restore_live_requested = False
        self._restore_pointer_anchor = None
        # Managed snapped windows: client ID -> (workspace, zone geometry).
        # Preserve the original pre-snap dimensions separately for unsnapping.
        self._snapped_assignments: Dict[int, Tuple[int, Tuple[float, float, float, float]]] = {}
        self._applied_gap_by_window: Dict[int, int] = {}
        self._last_live_gap = load_gap_px()
        self._gap_stable_since = time.monotonic()
        self._next_gap_reflow_time = 0.0
        # Pending measured gap reflows. Never modify manual Alt+Drag geometry.
        self._gap_frame_checks = {}
        # Editor and daemon are separate processes. Editor writes a revision
        # token to request absolute reflow of already-assigned windows.
        self._reflow_token_path = os.path.expanduser(
            '~/.config/powersnap/.layout-reflow-request')
        self._last_reflow_token = self._read_reflow_token()
        self._last_layout_zones = {}
        # Detect runtime XRandR changes. Stored zones are normalized, so the
        # same layout can be projected into the new pixel dimensions.
        self._last_root_size = current_root_size(self.window_manager.root)

    def _read_reflow_token(self):
        try:
            with open(self._reflow_token_path, 'r', encoding='ascii') as stream:
                return stream.read().strip()
        except OSError:
            return None

    @staticmethod
    def _nearest_zone_key(old_key, zones):
        """Follow the existing zone when an editor divider changes its size.

        The same exact zone key is preferred; for changed dimensions we use
        center distance and size difference, without depending on object IDs
        from the editor's separate process.
        """
        if not zones:
            return None
        if old_key in [tuple((z.x,z.y,z.width,z.height)) for z in zones]:
            return old_key
        ox, oy, ow, oh = old_key
        def score(z):
            cx = z.x + z.width / 2
            cy = z.y + z.height / 2
            return ((cx - ox - ow / 2)**2 + (cy - oy - oh / 2)**2
                    + 0.08*((z.width-ow)**2 + (z.height-oh)**2))
        zone = min(zones, key=score)
        return (zone.x, zone.y, zone.width, zone.height)


    @staticmethod
    def _zone_key(zone: Zone) -> Tuple[float, float, float, float]:
        return (zone.x, zone.y, zone.width, zone.height)

    def _apply_gap_to_snapped_windows(self):
        """Live gap reflow, using a fixed zone target and measured outer frames.

        xfwm4 applies EWMH changes asynchronously. A request being accepted
        does not mean the outer frame ended up at the requested position.
        Do not use current client geometry as the base for another request.
        """
        gap = load_gap_px()
        now = time.monotonic()
        workspace = self.get_current_workspace()
        # Query the live root rectangle, not Display.screen()'s cached mode.
        # A resolution change invalidates every previously applied snap even
        # if the gap value and normalized zone coordinates are unchanged.
        root_size = current_root_size(self.window_manager.root)
        resolution_changed = root_size != self._last_root_size
        if resolution_changed:
            print(f"[Display] Resolution changed: {self._last_root_size} -> {root_size}; reflow queued", flush=True)
            self._last_root_size = root_size
            self._gap_frame_checks.clear()
            self._next_gap_reflow_time = 0.0
            for wid in self._snapped_assignments:
                self._applied_gap_by_window.pop(wid, None)
        token = self._read_reflow_token()
        request_changed = token != self._last_reflow_token
        self._last_reflow_token = token

        if gap != self._last_live_gap:
            self._last_live_gap = gap
            self._gap_stable_since = now
            self._gap_frame_checks.clear()  # Abandon checks on obsolete targets.
            print(f"[Gap] New value {gap}px; waiting for editor to settle", flush=True)
        if now - self._gap_stable_since < 0.5:
            return True

        if (self._window_is_moving or self._is_snapping or
                self._restore_drag_window_id is not None):
            return True
        if self._pointer_display is not None:
            try:
                if self._pointer_display.screen().root.query_pointer().mask & X.Button1Mask:
                    return True
            except Exception:
                return True

        layout = self.layout_library.get_workspace_layout(workspace)
        if not layout or not layout.zones:
            return True
        zones = list(layout.zones)
        signature = tuple(self._zone_key(z) for z in zones)
        previous = self._last_layout_zones.get(workspace)
        layout_changed = previous is not None and signature != previous
        self._last_layout_zones[workspace] = signature
        if layout_changed or request_changed:
            self._gap_frame_checks.clear()
            for wid, (ws, key) in list(self._snapped_assignments.items()):
                if ws == workspace:
                    updated = self._nearest_zone_key(key, zones)
                    if updated is not None:
                        self._snapped_assignments[wid] = (ws, updated)
                        self._applied_gap_by_window.pop(wid, None)
            print(f"[Layout] Reflow queued: layout={layout_changed} editor={request_changed}", flush=True)

        try:
            atom = self.window_manager.display.get_atom('_NET_CLIENT_LIST')
            prop = self.window_manager.root.get_full_property(atom, X.AnyPropertyType)
            live_ids = set(map(int, prop.value)) if prop else set()
        except Exception as exc:
            print(f"[Gap] Unable to enumerate windows: {exc}", flush=True)
            return True

        # Observe completed requests; never send feedback-loop corrections.
        for wid, pending in list(self._gap_frame_checks.items()):
            if wid not in live_ids or wid not in self._snapped_assignments:
                self._gap_frame_checks.pop(wid, None)
                continue
            if now - pending['at'] < 0.65:
                continue
            actual = self.window_manager.get_outer_frame_geometry(wid)
            desired = pending['visible']
            if actual is None:
                print(f"[Gap] Cannot measure frame for {wid:#x}; no adjustment", flush=True)
                self._gap_frame_checks.pop(wid, None)
                continue
            # GTK's shadow extents live inside its X11 client; compare the
            # visible content rect, not the full shadow-inclusive client.
            try:
                window = self.window_manager.display.create_resource_object('window', wid)
                gtk_atom = self.window_manager.display.get_atom('_GTK_FRAME_EXTENTS', only_if_exists=True)
                gtk_prop = window.get_full_property(gtk_atom, X.AnyPropertyType) if gtk_atom else None
                if gtk_prop is not None and len(gtk_prop.value) >= 4:
                    l, r, t, b = (int(v) for v in gtk_prop.value[:4])
                    if any((l, r, t, b)) and all(0 <= v <= 128 for v in (l, r, t, b)):
                        actual = (actual[0] + l, actual[1] + t,
                                  actual[2] - l - r, actual[3] - t - b)
            except Exception as exc:
                print(f"[Gap] Could not read GTK extents {wid:#x}: {exc}", flush=True)
            error = tuple(desired[i] - actual[i] for i in range(4))
            print(f"[Gap] Frame {wid:#x}: desired={desired}, actual={actual}, delta={error}", flush=True)
            if all(abs(value) <= 3 for value in error):
                self._gap_frame_checks.pop(wid, None)
                continue
            # Do not adjust the geometry again. xfwm4 processes EWMH requests
            # asynchronously, and sending a second, delta-adjusted move can
            # fight with the pending one and cause visible oscillation.
            # Record the discrepancy for diagnosis instead.
            print(f"[Gap] Measured mismatch {wid:#x}; no corrective move "
                  f"(manual snapping remains unchanged)", flush=True)
            self._gap_frame_checks.pop(wid, None)

        if self._gap_frame_checks or now < self._next_gap_reflow_time:
            return True
        for wid, (ws, key) in list(self._snapped_assignments.items()):
            if wid not in live_ids:
                self._snapped_assignments.pop(wid, None)
                self._applied_gap_by_window.pop(wid, None)
                self._saved_geometries.pop(wid, None)
                continue
            if ws != workspace or self._applied_gap_by_window.get(wid) == gap:
                continue
            zone = next((z for z in zones if self._zone_key(z) == key), None)
            if zone is None:
                continue
            screen_width, screen_height = current_root_size(self.window_manager.root)
            visible = inset_rectangle(zone, zones, screen_width,
                                       screen_height, gap)
            base = self._zone_target_geometry(wid, zone)
            # Do not call manual snap; it uses StaticGravity, which anchors
            # decorated clients rather than their outer xfwm4 frames.
            success = self.window_manager.move_resize_window_live_gap(wid, *base)
            self._next_gap_reflow_time = now + 0.35
            if success:
                self._applied_gap_by_window[wid] = gap
                self._gap_frame_checks[wid] = {
                    'visible': visible, 'base': base, 'at': now}
                print(f"[Gap] Reflow requested {wid:#x}: visible={visible} base={base}", flush=True)
            else:
                print(f"[Gap] Reflow FAILED {wid:#x}; will retry", flush=True)
            break
        return True

    def _zone_target_geometry(self, window_id: int, zone: Zone) -> Tuple[int, int, int, int]:
        """Convert the inset visible zone rectangle to a client resize target."""
        screen_width, screen_height = current_root_size(self.window_manager.root)
        layout = self.layout_library.get_workspace_layout(self.get_current_workspace())
        peers = layout.zones if layout else [zone]
        visible_rect = inset_rectangle(zone, peers, screen_width,
                                       screen_height, load_gap_px())
        return self.window_manager.zone_to_client_geometry(window_id, visible_rect)

    def snap_window_to_zone(self, window_id: int, zone: Zone, animate: bool = False) -> bool:
        """
        Snap a window to a zone

        Args:
            window_id: X11 window ID
            zone: Zone to snap to (with relative coordinates 0.0-1.0)
            animate: Whether to animate the transition (optional)

        Returns:
            True if successful, False otherwise
        """
        try:
            # Get window object
            window = self.window_manager.display.create_resource_object('window', window_id)

            # Get current window geometry to store as original
            x, y, width, height = self.window_manager.get_window_geometry(window)

            # Only save the *first* pre-snap geometry for this window.
            # A window moved from Zone 1 to Zone 2 must still restore its
            # genuine original size, not Zone 1's dimensions.
            original_geometry = (x, y, width, height)

            # The zone target already includes the configured inter-zone and
            # outer-screen gap. Use the same frame-aware EWMH request as live
            # gap reflow so xfwm4-decorated windows reach their final visible
            # rectangle on the *first* Alt+Drag snap (no second adjustment).
            # For GTK client-side decorated windows this helper retains the
            # existing normal move/resize path.
            target = self._zone_target_geometry(window_id, zone)
            success = self.window_manager.move_resize_window_live_gap(window_id, *target)
            if success:
                if window_id not in self._saved_geometries:
                    self._saved_geometries[window_id] = original_geometry
                self._snapped_assignments[window_id] = (
                    self.get_current_workspace(), self._zone_key(zone))
                self._applied_gap_by_window[window_id] = load_gap_px()
            return success

        except Exception as e:
            print(f"Error snapping window: {e}")
            return False

    def snap_active_window_to_zone(self, zone: Zone, animate: bool = False) -> bool:
        """
        Snap the active window to a zone

        Args:
            zone: Zone to snap to
            animate: Whether to animate the transition (optional)

        Returns:
            True if successful, False otherwise
        """
        active_window = self.window_manager.get_active_window()
        if not active_window:
            return False

        return self.snap_window_to_zone(active_window.window_id, zone, animate)

    def restore_window_geometry(self, window_id: int, at_current_position: bool = False, pointer_xy=None) -> bool:
        """Restore the per-window pre-snap geometry.

        When unsnapping by dragging, retain the dragged window's *current*
        location (rather than jumping back to its original desktop position).
        """
        saved = self._saved_geometries.get(window_id)
        if saved is None:
            return False
        x, y, w, h = saved
        if at_current_position:
            window = self.window_manager.display.create_resource_object('window', window_id)
            cx, cy, cw, _ch = self.window_manager.get_window_geometry(window)
            if pointer_xy is not None:
                px, py = pointer_xy
                # Preserve relative pointer location horizontally; keep the
                # pointer on the title bar rather than jumping to screen origin.
                offset = max(0.05, min(0.95, (px - cx) / max(cw, 1)))
                x = round(px - offset * w)
                y = round(py - max(0, min(32, py - cy)))
            else:
                x, y = cx, cy
            screen_width, screen_height = current_root_size(self.window_manager.root)
            x = max(0, min(x, max(0, screen_width - min(w, 100))))
            y = max(0, min(y, max(0, screen_height - 50)))
        success = self.window_manager.move_resize_window(window_id, x, y, w, h)
        return success

    def get_current_workspace(self) -> int:
        """
        Get the current workspace/desktop number

        Returns:
            Workspace number (0-indexed)
        """
        try:
            display = self.window_manager.display
            root = display.screen().root

            # Query _NET_CURRENT_DESKTOP
            current_desktop = root.get_full_property(
                display.intern_atom('_NET_CURRENT_DESKTOP'),
                0  # Xlib.X.AnyPropertyType
            )

            if current_desktop:
                return current_desktop.value[0]

            return 0

        except Exception as e:
            print(f"Error getting current workspace: {e}")
            return 0

    def load_workspace_zones(self, workspace: Optional[int] = None) -> List[Zone]:
        """
        Load zones for a specific workspace using layout library

        Args:
            workspace: Workspace number (None = current workspace)

        Returns:
            List of zones for the workspace
        """
        if workspace is None:
            workspace = self.get_current_workspace()

        # Load layout for this workspace
        layout = self.layout_library.get_workspace_layout(workspace)

        if layout:
            print(f"Loaded layout '{layout.name}' for workspace {workspace} ({len(layout.zones)} zones)")
            return list(layout.zones)
        else:
            print(f"No layout found for workspace {workspace}")
            return []

    def show_overlay_at_cursor(self) -> Optional[Zone]:
        """
        Show overlay at cursor position and wait for zone selection

        Returns:
            Selected zone or None if cancelled
        """
        # Load zones for current workspace
        workspace = self.get_current_workspace()
        zones = self.load_workspace_zones(workspace)

        if len(zones) == 0:
            print("No zones configured for current workspace")
            return None

        # Show overlay
        self.overlay_manager.show(zones)

        return None  # Zone selection handled by callback

    def hide_overlay(self):
        """Hide the overlay"""
        self.overlay_manager.hide()

    def _is_trigger_modifier_pressed(self) -> bool:
        """Read X11's *current* modifier state, not a delayed pynput event.

        Under xfwm4 (particularly inside a VM) pynput's keyboard callback
        can lag behind window movement polling. The first movement notification
        used to be discarded, and the overlay sometimes appeared only on a
        later attempt. XQueryPointer includes the real modifier bitmask.
        """
        modifier_masks = {
            'shift': X.ShiftMask,
            'ctrl': X.ControlMask,
            'alt': X.Mod1Mask,
            'super': X.Mod4Mask,
        }
        mask = modifier_masks.get(self.trigger_modifier)
        if mask is None:
            return False
        if self._pointer_display is not None:
            try:
                pointer = self._pointer_display.screen().root.query_pointer()
                return bool(pointer.mask & mask)
            except Exception:
                pass  # Use the keyboard listener if X11 state is unavailable.
        return {
            'shift': self.keyboard_tracker.is_shift_pressed,
            'ctrl': self.keyboard_tracker.is_ctrl_pressed,
            'alt': self.keyboard_tracker.is_alt_pressed,
            'super': self.keyboard_tracker.is_super_pressed,
        }[self.trigger_modifier]

    def start_snap_workflow(self):
        """
        Start the complete snap workflow:
        1. Monitor for window drag with modifier key pressed
        2. Show overlay when drag starts with modifier
        3. Hide overlay when modifier is released
        4. Snap window when zone is selected
        """
        print("Starting snap workflow...")
        print(f"Hold {self.trigger_modifier.upper()} and drag a window to snap it to a zone")
        print(f"Release {self.trigger_modifier.upper()} to cancel")
        print("Click on a zone to snap the window")
        print("Press Escape to cancel")
        print("Press Ctrl+C to exit")

        # Start keyboard and mouse trackers
        self.keyboard_tracker.start()
        self.mouse_tracker.start()

        # Set up mouse button release callback. All GTK and X11 window changes
        # happen on the GTK thread, regardless of which listener detected release.
        def on_button_release(button_name: str):
            if button_name != 'left':
                return
            # pynput's callback is not the GTK main thread.
            GLib.idle_add(finish_drag)
            GLib.idle_add(finish_restore_drag)

        def finish_restore_drag():
            """A regular (no Alt) drag of a snapped window restores its size."""
            window_id = self._restore_drag_window_id
            if window_id is None:
                return False
            self._restore_drag_window_id = None
            self._restore_pointer_was_down = False
            self._restore_live_requested = False
            pointer_anchor = self._restore_pointer_anchor
            self._restore_pointer_anchor = None
            if window_id not in self._saved_geometries:
                return False

            # xfwm4 can hold the window during the pointer grab. Wait until
            # the release has settled before sending the resize request.
            def attempt_restore(attempt=0):
                saved = self._saved_geometries.get(window_id)
                if saved is None:
                    return False
                try:
                    if attempt == 0:
                        requested = self.restore_window_geometry(window_id, at_current_position=True, pointer_xy=pointer_anchor)
                        if not requested:
                            print(f"Restore request FAILED for window {window_id}", flush=True)
                            return False
                    # Read the *current* location so our position check doesn't
                    # treat a user-driven move as a failure.
                    win = self.window_manager.display.create_resource_object('window', window_id)
                    actual = self.window_manager.get_window_geometry(win)
                    if abs(actual[2] - saved[2]) <= 30 and abs(actual[3] - saved[3]) <= 30:
                        self._saved_geometries.pop(window_id, None)
                        self._snapped_assignments.pop(window_id, None)
                        self._applied_gap_by_window.pop(window_id, None)
                        print(f"Restored original size for window {window_id}: {saved[2]}x{saved[3]}", flush=True)
                        return False
                    if attempt < 2:
                        print(f"Restore not yet applied for window {window_id}, retry {attempt + 1}", flush=True)
                        # Reissue only after the first attempt has failed to
                        # take effect; xfwm4 can ignore requests during a grab.
                        self.restore_window_geometry(window_id, at_current_position=True, pointer_xy=pointer_anchor)
                        GLib.timeout_add(170, lambda: attempt_restore(attempt + 1))
                    else:
                        print(f"Restore FAILED for window {window_id}: actual={actual}, "
                              f"original size={saved[2]}x{saved[3]}", flush=True)
                except Exception as exc:
                    print(f"Restore error for window {window_id}: {exc}", flush=True)
                return False

            GLib.timeout_add(130, attempt_restore)
            return False

        def finish_drag():
            if not self._window_is_moving:
                return False
            self._window_is_moving = False
            self._pointer_was_down = False

            if self._overlay_visible:
                # Query the cursor at release; overlay highlighting is refreshed
                # only periodically and may be stale during a window-manager grab.
                overlay = self.overlay_manager.overlay
                window_id = self._active_window_id
                zone = None
                if overlay is not None:
                    try:
                        pointer = self._pointer_display.screen().root.query_pointer()
                        zone = overlay._get_zone_at_point(pointer.root_x, pointer.root_y)
                    except Exception as exc:
                        print(f"Could not query release position: {exc}", flush=True)
                        zone = overlay.get_highlighted_zone()

                print(f"Drag released: window={window_id}, zone={zone}", flush=True)
                # Hide before moving to avoid the fullscreen overlay obstructing
                # the target window and to prevent a stale highlight next time.
                self.overlay_manager.hide()
                if zone is not None and window_id is not None:
                    success = self.snap_window_to_zone(window_id, zone)
                    if success:
                        # The initial snap already uses the correct XFCE frame
                        # gravity. Do NOT retry with move_resize_window() or
                        # fallback_configure(): those use different coordinate
                        # semantics and visibly move the window out of its zone.
                        # X11 requests are asynchronous; this log only confirms
                        # that the request was submitted, not its final geometry.
                        self._last_snap_time = time.time()
                        print(f"Snap request sent for window {window_id}", flush=True)
                    else:
                        print(f"Snap request FAILED for window {window_id}", flush=True)
                elif zone is None:
                    print("No zone under cursor at release", flush=True)
            self._active_window_id = None
            self._overlay_visible = False
            self._is_snapping = False
            return False

        self.mouse_tracker.set_on_button_release(on_button_release)

        # Independently detect release with XQueryPointer, which still exposes
        # modifier/button state when xfwm4 or the VM has grabbed pointer events.
        try:
            self._pointer_display = xdisplay.Display()
        except Exception as exc:
            print(f"X11 pointer polling unavailable: {exc}", flush=True)

        def poll_pointer_release():
            if self._pointer_display is None:
                return True
            try:
                pointer = self._pointer_display.screen().root.query_pointer()
                down = bool(pointer.mask & X.Button1Mask)
                if self._window_is_moving and self._overlay_visible:
                    if down:
                        self._pointer_was_down = True
                    elif self._pointer_was_down:
                        print("X11 poll detected mouse button release", flush=True)
                        finish_drag()
                elif not down:
                    self._pointer_was_down = False
                # Also poll releases of *ordinary* drags, which may be grabbed
                # by xfwm4 and therefore invisible to pynput.
                if self._restore_drag_window_id is not None:
                    if down:
                        self._restore_pointer_was_down = True
                        if not self._restore_live_requested:
                            # One live request only: repeated resize requests
                            # during xfwm4's pointer grab can disrupt its drag.
                            wid = self._restore_drag_window_id
                            self._restore_live_requested = True
                            self._restore_pointer_anchor = (pointer.root_x, pointer.root_y)
                            ok = self.restore_window_geometry(
                                wid, at_current_position=True,
                                pointer_xy=self._restore_pointer_anchor)
                            print(f"Live restore requested for {wid}: {ok}", flush=True)
                    elif self._restore_pointer_was_down:
                        print("X11 poll detected unsnap drag release", flush=True)
                        finish_restore_drag()
            except Exception as exc:
                print(f"Pointer polling error: {exc}", flush=True)
            return True

        GLib.timeout_add(25, poll_pointer_release)
        # The editor writes settings.json atomically. Check it in the daemon
        # periodically so no restart, IPC server or unsnap/resnap is required.
        GLib.timeout_add(200, self._apply_gap_to_snapped_windows)

        # Set up keyboard modifier change callback
        def on_modifier_change(shift: bool, ctrl: bool, alt: bool, super_key: bool):
            """Called when modifier keys change state"""
            modifier_pressed = self._is_trigger_modifier_pressed()

            # If overlay is visible and modifier released, hide overlay
            if self._overlay_visible and not modifier_pressed:
                print(f"{self.trigger_modifier.upper()} released - hiding overlay")

                def hide_overlay_on_main_thread():
                    self.overlay_manager.hide()
                    self._overlay_visible = False
                    self._is_snapping = False
                    # Preserve the drag target until the mouse button is released.
                    # The overlay-hidden callback must not discard it either.
                    if not self._window_is_moving:
                        self._active_window_id = None
                    # Don't clear _window_is_moving here - user might still be dragging
                    return False

                GLib.idle_add(hide_overlay_on_main_thread)

            # If window is moving, modifier pressed, but overlay not visible, show overlay
            # Only show if mouse button is actually pressed (still dragging)
            elif self._window_is_moving and modifier_pressed and not self._overlay_visible and self.mouse_tracker.is_left_pressed:
                print(f"{self.trigger_modifier.upper()} pressed during drag - showing overlay")

                def show_overlay_on_main_thread():
                    if not self._is_trigger_modifier_pressed():
                        return False

                    self._is_snapping = True
                    self._overlay_visible = True

                    # Show overlay
                    workspace = self.get_current_workspace()
                    zones = self.load_workspace_zones(workspace)

                    if len(zones) > 0:
                        self.overlay_manager.show(zones)
                        print(f"Overlay shown with {len(zones)} zones", flush=True)
                    else:
                        print("No zones configured", flush=True)

                    return False

                GLib.idle_add(show_overlay_on_main_thread)

        self.keyboard_tracker.set_on_modifier_change(on_modifier_change)

        # Set up overlay callbacks
        overlay = self.overlay_manager.create_overlay()

        def on_zone_selected(zone: Zone):
            print(f"Zone selected: {zone}")
            self._selected_zone = zone

            # Snap window to zone
            if self._active_window_id:
                success = self.snap_window_to_zone(self._active_window_id, zone)
                if success:
                    print(f"Snapped window {self._active_window_id} to {zone}")
                else:
                    print("Failed to snap window")

        def on_overlay_hidden():
            print("Overlay hidden")
            self._is_snapping = False
            self._overlay_visible = False
            # hide_overlay() runs this callback synchronously. It can run while
            # finish_drag() still needs the target ID, or when Alt is released
            # before the mouse. Only finish_drag() ends the drag and clears it.
            if not self._window_is_moving:
                self._active_window_id = None

        overlay.set_on_zone_selected(on_zone_selected)
        overlay.set_on_overlay_hidden(on_overlay_hidden)

        # Set up window movement callbacks
        def on_move_start(window_id: int, x: int, y: int):
            """Track Alt+drag snaps and normal drags out of snapped zones."""
            if time.time() - self._last_snap_time < 0.3:
                return
            # The monitor calls this from its worker thread. Dispatch restore
            # state changes to GTK's main thread, like normal snap events.
            if not self._is_trigger_modifier_pressed():
                def register_restore_drag():
                    client_id = self.window_manager.resolve_client_window_id(window_id)
                    if client_id in self._saved_geometries and not self._window_is_moving:
                        self._restore_drag_window_id = client_id
                        self._restore_pointer_was_down = True
                        self._restore_live_requested = False
                        self._restore_pointer_anchor = None
                        print(f"Dragging snapped window {client_id} without ALT - restore on release", flush=True)
                    return False
                GLib.idle_add(register_restore_drag)
                return
            if not self.mouse_tracker.is_left_pressed:
                # pynput can miss the press under xfwm4; XQueryPointer is
                # authoritative about the physical button's current state.
                try:
                    pointer = self._pointer_display.screen().root.query_pointer()
                    if not pointer.mask & X.Button1Mask:
                        return
                except Exception:
                    return

            if self._window_is_moving and self._active_window_id is not None:
                return
            client_id = self.window_manager.resolve_client_window_id(window_id)
            if client_id is None:
                return
            self._window_is_moving = True
            self._active_window_id = client_id
            self._pointer_was_down = True
            print(f"Tracking active client {client_id:#x} with {self.trigger_modifier.upper()} at ({x}, {y})", flush=True)

            def show_overlay_on_main_thread():
                if not self._window_is_moving or not self._is_trigger_modifier_pressed():
                    return False
                zones = self.load_workspace_zones(self.get_current_workspace())
                if zones:
                    self._is_snapping = True
                    self._overlay_visible = True
                    self.overlay_manager.show(zones)
                    print(f"Overlay shown with {len(zones)} zones", flush=True)
                return False

            GLib.idle_add(show_overlay_on_main_thread)

        def on_move_update(window_id: int, x: int, y: int):
            """Retry drag recognition throughout the same physical drag.

            Window movement may be detected before pynput has processed the
            first Alt press. Previously a missed on_move_start was never
            reconsidered until the user tried another drag.
            """
            if not self._window_is_moving and self._is_trigger_modifier_pressed():
                on_move_start(window_id, x, y)

        def on_move_end(window_id: int, x: int, y: int):
            """Called when window stops moving (position unchanged for timeout period)"""
            # Don't mark window as not moving here - wait for mouse button release
            if self._active_window_id == window_id:
                print(f"Window {window_id} stopped moving at ({x}, {y}) - waiting for mouse release")

        # Start window movement monitoring in separate thread
        monitor_thread = threading.Thread(
            target=self.window_manager.monitor_window_movements,
            args=(on_move_start, on_move_update, on_move_end),
            daemon=True
        )
        monitor_thread.start()

        # Run GTK main loop
        try:
            Gtk.main()
        except KeyboardInterrupt:
            print("\nExiting...")
            self.keyboard_tracker.stop()
            self.mouse_tracker.stop()
            self.overlay_manager.destroy()
            if self._pointer_display is not None:
                self._pointer_display.close()
