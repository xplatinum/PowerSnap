"""Window management utilities for PowerSnap using X11"""

import sys
from typing import List, Optional, Tuple, Dict
from Xlib import X, display, protocol
from Xlib.error import XError


def is_firefox_pip_identity(role: str, title: str, wm_class: str) -> bool:
    """Recognize Firefox-family PiP without admitting arbitrary dialogs.

    Mozilla uses Toolkit:PictureInPicture as the X11 WM_WINDOW_ROLE for the
    floating video player; the visible title can vary by translation.
    """
    role = (role or '').lower().replace('-', '').replace('_', '')
    title = (title or '').lower().replace('-', '').replace('_', '')
    wm_class = (wm_class or '').lower()
    firefox_family = any(name in wm_class for name in (
        'firefox', 'librewolf', 'waterfox', 'floorp', 'zen', 'icecat'))
    return firefox_family and ('pictureinpicture' in role or 'pictureinpicture' in title)


class WindowInfo:
    """Represents information about a window"""

    def __init__(self, window_id: int, title: str, x: int, y: int, width: int, height: int):
        self.window_id = window_id
        self.title = title
        self.x = x
        self.y = y
        self.width = width
        self.height = height

    def __repr__(self):
        return f"WindowInfo(id={hex(self.window_id)}, title='{self.title}', geometry=({self.x}, {self.y}, {self.width}, {self.height}))"


class WindowManager:
    """Manages X11 window operations"""

    def __init__(self):
        try:
            self.display = display.Display()
            self.screen = self.display.screen()
            self.root = self.screen.root
        except Exception as e:
            print(f"Error connecting to X11 display: {e}", file=sys.stderr)
            raise

        # Detect Window Calls extension (once at startup)
        self.has_window_calls = self._detect_window_calls_extension()

        # Cache for is_normal_window results (window types don't change at runtime)
        self._normal_window_cache: Dict[int, bool] = {}

        if self.has_window_calls:
            print("Window Calls extension detected - using for all window operations")
        else:
            print("Window Calls extension not found - using X11 protocol")

    def get_window_title(self, window) -> str:
        """Get the title of a window"""
        try:
            # Try _NET_WM_NAME first (UTF-8)
            net_wm_name = self.display.get_atom('_NET_WM_NAME')
            utf8_string = self.display.get_atom('UTF8_STRING')
            prop = window.get_full_property(net_wm_name, utf8_string)
            if prop and prop.value:
                return prop.value.decode('utf-8', errors='replace')

            # Fallback to WM_NAME
            wm_name = window.get_wm_name()
            if wm_name:
                return wm_name

            return "<Untitled>"
        except Exception:
            return "<Unknown>"

    def get_window_frame_extents(self, window) -> Tuple[int, int, int, int]:
        """
        Get frame extents (window decorations) for a window

        Returns:
            Tuple of (left, right, top, bottom) border sizes in pixels
        """
        try:
            # First try _GTK_FRAME_EXTENTS for GTK apps with client-side decorations
            # This property describes invisible borders/shadows around GTK windows
            gtk_frame_extents = self.display.get_atom('_GTK_FRAME_EXTENTS')
            gtk_extents_prop = window.get_full_property(gtk_frame_extents, X.AnyPropertyType)

            if gtk_extents_prop and len(gtk_extents_prop.value) >= 4:
                left, right, top, bottom = gtk_extents_prop.value[:4]
                return (left, right, top, bottom)
        except Exception:
            pass

        try:
            # Try to get _NET_FRAME_EXTENTS property (standard window manager decorations)
            net_frame_extents = self.display.get_atom('_NET_FRAME_EXTENTS')
            extents_prop = window.get_full_property(net_frame_extents, X.AnyPropertyType)

            if extents_prop and len(extents_prop.value) >= 4:
                left, right, top, bottom = extents_prop.value[:4]
                return (left, right, top, bottom)
        except Exception:
            pass

        # Fallback: No frame extents detected
        return (0, 0, 0, 0)

    def zone_to_client_geometry(self, window_id: int, zone_geometry: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
        """Convert a desired *visible outer window rectangle* to client geometry.

        Under xfwm4, the EWMH requested x/y targets the managed outer frame.
        Do not add decoration insets to the position; subtract them only
        from the client size.
        GTK CSD shadows instead live inside the client rectangle.
        """
        client_id = self.resolve_client_window_id(window_id)
        if client_id is None:
            return zone_geometry
        try:
            window = self.display.create_resource_object('window', client_id)
            x, y, w, h = zone_geometry

            def get_extents(name):
                atom = self.display.get_atom(name, only_if_exists=True)
                if not atom:
                    return None
                prop = window.get_full_property(atom, X.AnyPropertyType)
                if prop is None or len(prop.value) < 4:
                    return None
                values = tuple(int(v) for v in prop.value[:4])
                if any(v < 0 or v > 128 for v in values):
                    return None
                return values

            # CSD shadows are drawn by the app *within* the client.
            gtk = get_extents('_GTK_FRAME_EXTENTS')
            if gtk and any(gtk):
                left, right, top, bottom = gtk
                target = (x - left, y - top, w + left + right, h + top + bottom)
                print(f"[X11] GTK client-side shadow correction: "
                      f"visible={zone_geometry} extents={gtk} target={target}", flush=True)
                return target

            # Ordinary XFCE decorations are drawn *outside* the client.
            frame = get_extents('_NET_FRAME_EXTENTS')
            if frame and any(frame):
                left, right, top, bottom = frame
                # EWMH x/y refers to the outer frame under xfwm4.
                # Adding left/top insets shifts the visible window away from
                # the desired zone; only dimensions need correction.
                target = (x, y,
                          max(1, w - left - right), max(1, h - top - bottom))
                print(f"[X11] XFWM decoration correction: "
                      f"visible={zone_geometry} extents={frame} target={target}", flush=True)
                return target
            return zone_geometry
        except Exception as exc:
            print(f"[X11] Could not convert frame geometry: {exc}", flush=True)
            return zone_geometry

    def get_outer_frame_geometry(self, window_id: int) -> Optional[Tuple[int, int, int, int]]:
        """Actual visible X11 frame bounds, not client dimensions.

        This is needed for XFCE because EWMH move/resize is asynchronous,
        and client geometry is not interchangeable with decorated bounds.
        """
        client_id = self.resolve_client_window_id(window_id)
        if client_id is None:
            return None
        try:
            node = self.display.create_resource_object('window', client_id)
            # xfwm4 reparents the managed client inside its decoration frame.
            # Ascend to the root's direct child (the decorated outer frame).
            for _ in range(12):
                parent = node.query_tree().parent
                if not parent or parent.id == self.root.id:
                    break
                node = parent
            geom = node.get_geometry()
            trans = self.root.translate_coords(node, 0, 0)
            return (int(trans.x), int(trans.y), int(geom.width), int(geom.height))
        except Exception as exc:
            print(f"[Gap] Could not read frame of {window_id:#x}: {exc}", flush=True)
            return None

    def get_window_geometry(self, window) -> Tuple[int, int, int, int]:
        """Get window geometry (x, y, width, height)"""
        try:
            geom = window.get_geometry()
            # Translate coordinates to root window
            # Translate the window's local origin into root coordinates.
            # XTranslateCoordinates(source=window, destination=root).
            trans = window.translate_coords(self.root, 0, 0)
            return (trans.x, trans.y, geom.width, geom.height)
        except XError:
            return (0, 0, 0, 0)

    def is_normal_window(self, window, use_cache: bool = True) -> bool:
        """Check if window is a normal application window (not desktop, dock, etc.)

        Args:
            window: X11 window object
            use_cache: Whether to use cached result (default True for performance)
        """
        window_id = window.id

        # Check cache first (window types don't change at runtime)
        if use_cache and window_id in self._normal_window_cache:
            return self._normal_window_cache[window_id]

        try:
            # Check window type
            net_wm_window_type = self.display.get_atom('_NET_WM_WINDOW_TYPE')
            window_type = window.get_full_property(net_wm_window_type, X.AnyPropertyType)

            if window_type:
                type_atoms = [self.display.get_atom_name(atom) for atom in window_type.value]
                # Skip desktop, dock, and other non-normal windows
                skip_types = ['_NET_WM_WINDOW_TYPE_DESKTOP', '_NET_WM_WINDOW_TYPE_DOCK',
                             '_NET_WM_WINDOW_TYPE_TOOLBAR', '_NET_WM_WINDOW_TYPE_MENU',
                             '_NET_WM_WINDOW_TYPE_SPLASH', '_NET_WM_WINDOW_TYPE_NOTIFICATION']
                if any(t in skip_types for t in type_atoms):
                    self._normal_window_cache[window_id] = False
                    return False

            # Check if window is mapped (visible)
            attrs = window.get_attributes()
            if attrs.map_state != X.IsViewable:
                # Don't cache unmapped state - window might become visible later
                return False

            self._normal_window_cache[window_id] = True
            return True
        except (XError, Exception):
            return False

    def get_all_windows(self) -> List[WindowInfo]:
        """Get information about all normal application windows"""
        windows = []

        try:
            # Get list of all client windows from _NET_CLIENT_LIST
            net_client_list = self.display.get_atom('_NET_CLIENT_LIST')
            client_list_prop = self.root.get_full_property(net_client_list, X.AnyPropertyType)

            if not client_list_prop:
                return windows

            for window_id in client_list_prop.value:
                try:
                    window = self.display.create_resource_object('window', window_id)

                    if not self.is_normal_window(window):
                        continue

                    title = self.get_window_title(window)
                    x, y, width, height = self.get_window_geometry(window)

                    # Skip windows with invalid geometry
                    if width > 0 and height > 0:
                        windows.append(WindowInfo(window_id, title, x, y, width, height))

                except (XError, Exception):
                    continue

        except Exception as e:
            print(f"Error getting window list: {e}", file=sys.stderr)

        return windows

    def get_active_window(self) -> Optional[WindowInfo]:
        """Get the currently active (focused) window"""
        try:
            net_active_window = self.display.get_atom('_NET_ACTIVE_WINDOW')
            active_prop = self.root.get_full_property(net_active_window, X.AnyPropertyType)

            if not active_prop or not active_prop.value:
                return None

            window_id = active_prop.value[0]
            if window_id == 0:
                return None

            window = self.display.create_resource_object('window', window_id)

            if not self.is_normal_window(window):
                return None

            title = self.get_window_title(window)
            x, y, width, height = self.get_window_geometry(window)

            return WindowInfo(window_id, title, x, y, width, height)

        except Exception as e:
            print(f"Error getting active window: {e}", file=sys.stderr)
            return None

    def get_window_at_position(self, x: int, y: int) -> Optional[WindowInfo]:
        """Get the window at the specified screen coordinates"""
        try:
            # Query the window at position
            pointer_window = self.root.query_pointer()
            child = pointer_window.child

            if child == 0:
                return None

            # Walk up the window tree to find the top-level window
            window = child
            while True:
                parent = window.query_tree().parent
                if parent == self.root or parent == 0:
                    break
                window = parent

            if not self.is_normal_window(window):
                return None

            window_id = window.id
            title = self.get_window_title(window)
            wx, wy, width, height = self.get_window_geometry(window)

            return WindowInfo(window_id, title, wx, wy, width, height)

        except Exception as e:
            print(f"Error getting window at position: {e}", file=sys.stderr)
            return None

    def move_resize_window(self, window_id: int, x: int, y: int,
                           width: int, height: int) -> bool:
        """
        Move and resize window.

        Uses Window Calls extension if available, otherwise falls back to X11.
        Detection happens once at startup, not per-call.

        Args:
            window_id: Window ID
            x, y: Target position
            width, height: Target size

        Returns:
            True if successful
        """

        # Use Window Calls if available (detected at startup)
        if self.has_window_calls:
            return self._move_resize_via_window_calls(window_id, x, y, width, height)

        # Otherwise use existing X11 implementation
        return self._move_resize_via_x11(window_id, x, y, width, height)

    def resolve_client_window_id(self, window_id: int) -> Optional[int]:
        """Convert an xfwm4 frame/child ID into an EWMH client window ID.

        _NET_MOVERESIZE_WINDOW is only meant for managed client windows; a
        ConfigureNotify event can identify the WM frame instead.
        """
        try:
            atom = self.display.get_atom('_NET_CLIENT_LIST')
            prop = self.root.get_full_property(atom, X.AnyPropertyType)
            clients = set(map(int, prop.value)) if prop is not None else set()
            if window_id in clients:
                return window_id
            candidate = self.display.create_resource_object('window', window_id)
            # If the supplied ID is a frame, find its managed descendant.
            queue = [candidate]
            visited = set()
            while queue and len(visited) < 128:
                node = queue.pop(0)
                if node.id in visited:
                    continue
                visited.add(node.id)
                if node.id in clients:
                    return node.id
                queue.extend(node.query_tree().children)
            # If the event came from a child of the client, walk upwards.
            node = candidate
            while node.id != self.root.id:
                parent = node.query_tree().parent
                if not parent or parent.id == node.id:
                    break
                if parent.id in clients:
                    return parent.id
                node = parent
            print(f"[X11] {window_id:#x} is not an EWMH client window", flush=True)
        except Exception as exc:
            print(f"[X11] Unable to resolve client window {window_id:#x}: {exc}", flush=True)
        return None

    def _move_resize_via_x11(self, window_id: int, x: int, y: int, width: int, height: int, gravity: int = 10) -> bool:
        """Request a move/resize from the EWMH window manager.

        Sending a ClientMessage does not mean the window manager accepted it.
        The caller checks the resulting geometry after a short delay.
        """
        client_id = self.resolve_client_window_id(window_id)
        if client_id is None:
            return False
        try:
            window = self.display.create_resource_object('window', client_id)
            print(f"[X11] Requesting '{self.get_window_title(window)}' "
                  f"({client_id:#x}) -> ({x}, {y}, {width}, {height})", flush=True)
            self._unmaximize_window(window)
            # EWMH gravity: StaticGravity=10 (existing drag behavior),
            # NorthWestGravity=1 (outer-frame anchored, live gap only).
            flags = (1 << 12) | (15 << 8) | gravity
            values = [flags, x & 0xffffffff, y & 0xffffffff,
                      max(1, width), max(1, height)]
            event = protocol.event.ClientMessage(
                window=window,
                client_type=self.display.get_atom('_NET_MOVERESIZE_WINDOW'),
                data=(32, values),
            )
            self.root.send_event(event,
                                 event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
            self.display.flush()
            return True
        except Exception as exc:
            print(f"[X11] Move/resize request failed: {exc}", flush=True)
            return False

    def move_resize_window_live_gap(self, window_id: int, x: int, y: int,
                                    width: int, height: int) -> bool:
        """Reflow a snapped window without changing the manual drag path.

        For server-side (xfwm4) decorated windows, x/y are intended outer
        frame coordinates. StaticGravity anchors the X11 client instead,
        displacing the visible frame by its decoration insets. Request
        NorthWestGravity to anchor the *outer frame* to the tile origin.
        GTK client-side decoration windows retain the existing path.
        """
        client_id = self.resolve_client_window_id(window_id)
        if client_id is None:
            return False
        try:
            window = self.display.create_resource_object('window', client_id)
            gtk_atom = self.display.get_atom('_GTK_FRAME_EXTENTS', only_if_exists=True)
            gtk_prop = window.get_full_property(gtk_atom, X.AnyPropertyType) if gtk_atom else None
            gtk_extents = tuple(map(int, gtk_prop.value[:4])) if gtk_prop is not None and len(gtk_prop.value) >= 4 else ()
            frame_atom = self.display.get_atom('_NET_FRAME_EXTENTS', only_if_exists=True)
            frame_prop = window.get_full_property(frame_atom, X.AnyPropertyType) if frame_atom else None
            frame_extents = tuple(map(int, frame_prop.value[:4])) if frame_prop is not None and len(frame_prop.value) >= 4 else ()
            if frame_extents and any(frame_extents) and not any(gtk_extents):
                print(f"[Gap] XFWM frame gravity=NorthWest for {client_id:#x} extents={frame_extents}", flush=True)
                return self._move_resize_via_x11(client_id, x, y, width, height, gravity=1)
        except Exception as exc:
            print(f"[Gap] Frame gravity detection failed for {client_id:#x}: {exc}", flush=True)
        # Leave existing GTK/CSD and non-XFCE behavior unchanged.
        return self.move_resize_window(client_id, x, y, width, height)

    def verify_geometry(self, window_id: int, requested: Tuple[int, int, int, int]) -> bool:
        """Check that the client was really resized and approximately positioned."""
        client_id = self.resolve_client_window_id(window_id)
        if client_id is None:
            return False
        window = self.display.create_resource_object('window', client_id)
        actual = self.get_window_geometry(window)
        x, y, w, h = requested
        # WM decorations and size increments can cause small differences.
        close = (abs(actual[0] - x) <= 35 and abs(actual[1] - y) <= 55
                 and abs(actual[2] - w) <= 30 and abs(actual[3] - h) <= 30)
        print(f"[X11] Geometry verification: requested={requested}, actual={actual}, "
              f"matched={close}", flush=True)
        return close

    def fallback_configure(self, window_id: int, x: int, y: int, width: int, height: int) -> bool:
        """Last-resort XConfigureWindow attempt for WMs ignoring EWMH."""
        client_id = self.resolve_client_window_id(window_id)
        if client_id is None:
            return False
        try:
            window = self.display.create_resource_object('window', client_id)
            window.configure(x=x, y=y, width=max(1, width), height=max(1, height))
            self.display.flush()
            print(f"[X11] Fallback configure sent to {client_id:#x}", flush=True)
            return True
        except Exception as exc:
            print(f"[X11] Fallback configure failed: {exc}", flush=True)
            return False

    def _get_window_calls_id(self, x11_window_id: int) -> Optional[int]:
        """
        Map X11 window ID to Window Calls window ID.

        Uses PID and window title to match windows between the two systems.
        Handles frame windows by checking their children for properties.

        Args:
            x11_window_id: X11 window ID

        Returns:
            Window Calls window ID if found, None otherwise
        """
        import subprocess
        import json

        try:
            # Get X11 window info
            window = self.display.create_resource_object('window', x11_window_id)

            # Get PID from X11 window
            net_wm_pid = self.display.get_atom('_NET_WM_PID')
            pid_prop = window.get_full_property(net_wm_pid, X.AnyPropertyType)

            # If no PID on parent, check first child (common for frame windows)
            if not pid_prop:
                try:
                    tree = window.query_tree()
                    if tree.children and len(tree.children) > 0:
                        child_window = tree.children[0]
                        pid_prop = child_window.get_full_property(net_wm_pid, X.AnyPropertyType)
                        if pid_prop:
                            # Use child window for getting properties
                            window = child_window
                except Exception:
                    pass

            if not pid_prop:
                return None
            x11_pid = pid_prop.value[0]

            # Get title from X11 window
            x11_title = self.get_window_title(window)

            # Get Window Calls window list
            result = subprocess.run([
                'gdbus', 'call', '--session',
                '--dest', 'org.gnome.Shell',
                '--object-path', '/org/gnome/Shell/Extensions/Windows',
                '--method', 'org.gnome.Shell.Extensions.Windows.List'
            ],
            capture_output=True,
            text=True,
            timeout=1.0
            )

            if result.returncode != 0:
                return None

            # Parse Window Calls output - it's a tuple with JSON string: ('...',)
            output = result.stdout.strip()
            if output.startswith("('") and output.endswith("',)"):
                json_str = output[2:-3]  # Remove ("' and "',)
                windows = json.loads(json_str)

                # First try to match by PID only (for efficiency)
                candidates = [w for w in windows if w.get('pid') == x11_pid]

                if len(candidates) == 1:
                    # Only one window with this PID, use it
                    return candidates[0]['id']
                elif len(candidates) > 1:
                    # Multiple windows with same PID
                    # Use the focused/active window from the candidates
                    print(f"Multiple windows found with PID {x11_pid} - using focused window",
                          file=sys.stderr)

                    # Try to find the focused window among candidates
                    focused = [w for w in candidates if w.get('focus') is True]
                    if len(focused) == 1:
                        print(f"  Found focused window: id={focused[0]['id']}", file=sys.stderr)
                        return focused[0]['id']

                    # If no focused window or multiple focused (shouldn't happen), fall back to X11
                    print(f"  Could not determine focused window - falling back to X11", file=sys.stderr)
                    return None
                elif len(candidates) == 0:
                    # PID matching failed (common for Flatpak apps with sandbox PIDs)
                    # Try matching by WM_CLASS instead
                    print(f"No windows found with PID {x11_pid} - trying WM_CLASS fallback for Flatpak apps",
                          file=sys.stderr)

                    # Get WM_CLASS from X11 window
                    try:
                        wm_class = self.display.get_atom('WM_CLASS')
                        wm_class_prop = window.get_full_property(wm_class, X.AnyPropertyType)
                        if wm_class_prop and wm_class_prop.value:
                            # WM_CLASS contains instance\0class\0
                            wm_class_str = wm_class_prop.value.decode('utf-8', errors='replace')
                            # Split by null byte and get the class name (second element)
                            wm_class_parts = wm_class_str.split('\0')
                            if len(wm_class_parts) >= 2:
                                x11_wm_class = wm_class_parts[1]
                                print(f"  X11 WM_CLASS: {x11_wm_class}", file=sys.stderr)

                                # Match by WM_CLASS in Window Calls
                                wm_class_candidates = [w for w in windows if w.get('wm_class', '').lower() == x11_wm_class.lower()]

                                if len(wm_class_candidates) == 1:
                                    print(f"  Found window by WM_CLASS: id={wm_class_candidates[0]['id']}", file=sys.stderr)
                                    return wm_class_candidates[0]['id']
                                elif len(wm_class_candidates) > 1:
                                    # Multiple windows with same WM_CLASS, use focused one
                                    print(f"  Multiple windows with WM_CLASS '{x11_wm_class}' - using focused window", file=sys.stderr)
                                    focused = [w for w in wm_class_candidates if w.get('focus') is True]
                                    if len(focused) >= 1:
                                        print(f"  Found focused window: id={focused[0]['id']}", file=sys.stderr)
                                        return focused[0]['id']
                    except Exception as e:
                        print(f"  WM_CLASS fallback failed: {e}", file=sys.stderr)

            return None

        except Exception as e:
            print(f"Error mapping window ID: {e}", file=sys.stderr)
            return None

    def _detect_window_calls_extension(self) -> bool:
        """
        Detect if Window Calls GNOME extension is available.
        Called once at startup.

        Returns:
            True if extension is available and responding
        """
        try:
            import subprocess

            result = subprocess.run([
                'gdbus', 'call', '--session',
                '--dest', 'org.gnome.Shell',
                '--object-path', '/org/gnome/Shell/Extensions/Windows',
                '--method', 'org.gnome.Shell.Extensions.Windows.List'
            ],
            capture_output=True,
            timeout=1.0  # Quick timeout - should respond immediately
            )

            return result.returncode == 0

        except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
            return False

    def _move_resize_via_window_calls(self, window_id: int, x: int, y: int,
                                       width: int, height: int) -> bool:
        """
        Move and resize window using Window Calls GNOME extension.
        Falls back to X11 if Window Calls mapping or execution fails.

        Args:
            window_id: X11 window ID (as integer)
            x, y: Target position
            width, height: Target size

        Returns:
            True if successful, False otherwise
        """
        import subprocess

        try:
            # Map X11 window ID to Window Calls ID
            wc_window_id = self._get_window_calls_id(window_id)
            if wc_window_id is None:
                print(f"Could not map X11 window {window_id:#x} to Window Calls ID, falling back to X11",
                      file=sys.stderr)
                return self._move_resize_via_x11(window_id, x, y, width, height)

            # Use separate Resize and Move calls instead of MoveResize
            # MoveResize has issues with some windows (e.g., Steam) where it only resizes but doesn't move

            # First resize
            resize_result = subprocess.run([
                'gdbus', 'call', '--session',
                '--dest', 'org.gnome.Shell',
                '--object-path', '/org/gnome/Shell/Extensions/Windows',
                '--method', 'org.gnome.Shell.Extensions.Windows.Resize',
                str(wc_window_id),
                str(width),
                str(height)
            ],
            capture_output=True,
            timeout=2.0
            )

            if resize_result.returncode != 0:
                print(f"Window Calls Resize failed: {resize_result.stderr.decode('utf-8', errors='replace')}, falling back to X11",
                      file=sys.stderr)
                return self._move_resize_via_x11(window_id, x, y, width, height)

            # Then move
            move_result = subprocess.run([
                'gdbus', 'call', '--session',
                '--dest', 'org.gnome.Shell',
                '--object-path', '/org/gnome/Shell/Extensions/Windows',
                '--method', 'org.gnome.Shell.Extensions.Windows.Move',
                str(wc_window_id),
                str(x),
                str(y)
            ],
            capture_output=True,
            timeout=2.0
            )

            if move_result.returncode == 0:
                return True
            else:
                # Window Calls failed, fall back to X11
                print(f"Window Calls Move failed: {move_result.stderr.decode('utf-8', errors='replace')}, falling back to X11",
                      file=sys.stderr)
                return self._move_resize_via_x11(window_id, x, y, width, height)

        except subprocess.TimeoutExpired:
            print(f"Window Calls timeout for window {window_id:#x}, falling back to X11", file=sys.stderr)
            return self._move_resize_via_x11(window_id, x, y, width, height)
        except Exception as e:
            print(f"Window Calls error: {e}, falling back to X11", file=sys.stderr)
            return self._move_resize_via_x11(window_id, x, y, width, height)

    def _unmaximize_window(self, window):
        """Remove maximized state from a window"""
        try:
            net_wm_state = self.display.get_atom('_NET_WM_STATE')
            net_wm_state_maximized_vert = self.display.get_atom('_NET_WM_STATE_MAXIMIZED_VERT')
            net_wm_state_maximized_horz = self.display.get_atom('_NET_WM_STATE_MAXIMIZED_HORZ')

            # Send event to remove maximized state (0 = remove)
            for atom in [net_wm_state_maximized_vert, net_wm_state_maximized_horz]:
                event = protocol.event.ClientMessage(
                    window=window,
                    client_type=net_wm_state,
                    data=(32, [0, atom, 0, 1])  # 0=remove, source=1 (application)
                )
                mask = X.SubstructureRedirectMask | X.SubstructureNotifyMask
                self.root.send_event(event, event_mask=mask)

            self.display.flush()

        except Exception:
            pass  # Silently fail if window doesn't support this

    def get_window_by_id(self, window_id: int) -> Optional[WindowInfo]:
        """Get window information by window ID"""
        try:
            window = self.display.create_resource_object('window', window_id)

            if not self.is_normal_window(window):
                return None

            title = self.get_window_title(window)
            x, y, width, height = self.get_window_geometry(window)

            return WindowInfo(window_id, title, x, y, width, height)

        except Exception as e:
            print(f"Error getting window by ID: {e}", file=sys.stderr)
            return None

    def monitor_window_movements(self, on_move_start=None, on_move_update=None, on_move_end=None):
        """Track the focused *client* while Button1 is down, not arbitrary
        ConfigureNotify messages from panels, frames, or the overlay.

        xfwm4 produces ConfigureNotify events for many windows during a drag;
        the old global event handler could select xfce4-panel as the target.
        Use a dedicated X connection since this runs on a worker thread.
        """
        import time

        monitor_display = display.Display()
        root = monitor_display.screen().root
        active_atom = monitor_display.get_atom('_NET_ACTIVE_WINDOW')
        type_atom = monitor_display.get_atom('_NET_WM_WINDOW_TYPE')
        rejected_types = {
            monitor_display.get_atom(name) for name in (
                '_NET_WM_WINDOW_TYPE_DESKTOP', '_NET_WM_WINDOW_TYPE_DOCK',
                '_NET_WM_WINDOW_TYPE_TOOLBAR', '_NET_WM_WINDOW_TYPE_MENU',
                '_NET_WM_WINDOW_TYPE_SPLASH', '_NET_WM_WINDOW_TYPE_NOTIFICATION',
                '_NET_WM_WINDOW_TYPE_DIALOG', '_NET_WM_WINDOW_TYPE_UTILITY',
            )
        }
        client_atom = monitor_display.get_atom('_NET_CLIENT_LIST')
        role_atom = monitor_display.get_atom('WM_WINDOW_ROLE')
        name_atom = monitor_display.get_atom('_NET_WM_NAME')
        class_atom = monitor_display.get_atom('WM_CLASS')

        def property_text(window, atom):
            prop = window.get_full_property(atom, X.AnyPropertyType)
            if prop is None or prop.value is None:
                return ''
            value = prop.value
            if isinstance(value, bytes):
                return value.decode('utf-8', errors='replace').replace('\0', ' ')
            if hasattr(value, 'tobytes'):
                return value.tobytes().decode('utf-8', errors='replace').replace('\0', ' ')
            return str(value)

        def is_pip(window):
            return is_firefox_pip_identity(
                property_text(window, role_atom),
                property_text(window, name_atom),
                property_text(window, class_atom))

        def pip_under_pointer(pointer, clients):
            """Locate PiP below pointer even when it is not the active window.

            An XFCE decoration frame may be the direct child of root; the
            actual EWMH client is a descendant of that frame.
            """
            child = pointer.child
            if not child or child.id == X.NONE:
                return None
            queue = [child]
            seen = set()
            while queue and len(seen) < 128:
                node = queue.pop(0)
                if node.id in seen:
                    continue
                seen.add(node.id)
                if node.id in clients and is_pip(node):
                    return node.id
                queue.extend(node.query_tree().children)
            return None

        last_window = None
        last_geometry = None
        drag_is_pip = False
        moving_window = None
        last_change = 0.0
        print('Monitoring active client window movements (X11 polling)...', flush=True)

        try:
            while True:
                try:
                    pointer = root.query_pointer()
                    if not pointer.mask & X.Button1Mask:
                        if moving_window is not None and last_geometry is not None and on_move_end:
                            on_move_end(moving_window, last_geometry[0], last_geometry[1])
                        last_window = None
                        last_geometry = None
                        drag_is_pip = False
                        moving_window = None
                        time.sleep(0.025)
                        continue

                    # Only EWMH-managed clients may be snap targets.
                    clients_prop = root.get_full_property(client_atom, X.AnyPropertyType)
                    if clients_prop is None:
                        time.sleep(0.025)
                        continue
                    clients = set(map(int, clients_prop.value))
                    prop = root.get_full_property(active_atom, X.AnyPropertyType)
                    active_id = int(prop.value[0]) if prop is not None and len(prop.value) else 0

                    # Prefer a PiP window actually under the dragged pointer.
                    # Firefox PiP may be marked as a dialog and may not take
                    # _NET_ACTIVE_WINDOW focus while being moved.
                    pointer_pip = pip_under_pointer(pointer, clients) if not drag_is_pip else None
                    window_id = (last_window if drag_is_pip and last_window in clients
                                 else pointer_pip or active_id)
                    if window_id not in clients:
                        time.sleep(0.025)
                        continue

                    window = monitor_display.create_resource_object('window', window_id)
                    attrs = window.get_attributes()
                    if attrs.map_state != X.IsViewable:
                        time.sleep(0.025)
                        continue
                    types = window.get_full_property(type_atom, X.AnyPropertyType)
                    if types is not None and any(t in rejected_types for t in types.value):
                        # PiP is frequently DIALOG/UTILITY. Keep the explicit
                        # exclusion of all other dialogs, docks and panels.
                        window_types = set(map(int, types.value))
                        pip_allowed = {monitor_display.get_atom('_NET_WM_WINDOW_TYPE_DIALOG'),
                                       monitor_display.get_atom('_NET_WM_WINDOW_TYPE_UTILITY')}
                        if not (is_pip(window) and window_types <= pip_allowed):
                            time.sleep(0.025)
                            continue

                    geom = window.get_geometry()
                    trans = window.translate_coords(root, 0, 0)
                    current = (trans.x, trans.y, geom.width, geom.height)
                    now = time.monotonic()

                    if window_id != last_window:
                        if moving_window is not None and last_geometry is not None and on_move_end:
                            on_move_end(moving_window, last_geometry[0], last_geometry[1])
                        last_window = window_id
                        drag_is_pip = is_pip(window)
                        if drag_is_pip:
                            print(f'[X11] Tracking Firefox PiP client {window_id:#x}', flush=True)
                        last_geometry = current
                        moving_window = None
                    elif current[:2] != last_geometry[:2]:
                        if moving_window is None:
                            moving_window = window_id
                            if on_move_start:
                                on_move_start(window_id, current[0], current[1])
                        elif on_move_update:
                            on_move_update(window_id, current[0], current[1])
                        last_geometry = current
                        last_change = now
                    elif moving_window is not None and now - last_change > 0.15:
                        if on_move_end:
                            on_move_end(moving_window, current[0], current[1])
                        moving_window = None
                except Exception as exc:
                    print(f'[X11] Movement polling error: {exc}', flush=True)
                time.sleep(0.025)
        finally:
            monitor_display.close()

    def close(self):
        """Clean up display connection"""
        if self.display:
            self.display.close()


def main():
    """Command-line interface for testing window operations"""
    import argparse

    parser = argparse.ArgumentParser(description='PowerSnap Window Manager')
    parser.add_argument('--list', action='store_true', help='List all windows')
    parser.add_argument('--active', action='store_true', help='Show active window')
    parser.add_argument('--at-cursor', action='store_true', help='Show window under cursor')
    parser.add_argument('--move-active', nargs=4, metavar=('X', 'Y', 'WIDTH', 'HEIGHT'),
                       type=int, help='Move/resize active window to X Y WIDTH HEIGHT')

    args = parser.parse_args()

    wm = WindowManager()

    try:
        if args.list:
            print("All windows:")
            print("-" * 80)
            windows = wm.get_all_windows()
            for i, win in enumerate(windows, 1):
                print(f"{i}. {win}")
            print(f"\nTotal: {len(windows)} windows")

        elif args.active:
            active = wm.get_active_window()
            if active:
                print("Active window:")
                print(active)
            else:
                print("No active window")

        elif args.at_cursor:
            print("Move your cursor over a window...")
            import time
            time.sleep(2)
            win = wm.get_window_at_position(0, 0)  # Position is ignored, uses current pointer
            if win:
                print("Window under cursor:")
                print(win)
            else:
                print("No window under cursor")

        elif args.move_active:
            x, y, width, height = args.move_active
            active = wm.get_active_window()
            if active:
                print(f"Moving window '{active.title}' to ({x}, {y}, {width}, {height})...")
                success = wm.move_resize_window(active.window_id, x, y, width, height)
                if success:
                    print("Window moved successfully!")
                    import time
                    time.sleep(0.5)  # Give window manager time to update
                    updated = wm.get_window_by_id(active.window_id)
                    if updated:
                        print(f"New geometry: ({updated.x}, {updated.y}, {updated.width}, {updated.height})")
                else:
                    print("Failed to move window")
            else:
                print("No active window")

        else:
            parser.print_help()

    finally:
        wm.close()


if __name__ == '__main__':
    main()
