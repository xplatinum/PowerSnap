"""Input monitoring for PowerSnap - mouse and keyboard tracking"""

import threading
from typing import Optional, Callable, Tuple, Set, List
from enum import Enum
from pynput import mouse, keyboard


class DragState(Enum):
    """Window drag operation states"""
    IDLE = "idle"
    DRAGGING = "dragging"
    RELEASED = "released"


class MouseTracker:
    """Tracks global mouse position and button states"""

    def __init__(self):
        self._position: Tuple[int, int] = (0, 0)
        self._left_pressed = False
        self._right_pressed = False
        self._middle_pressed = False
        self._drag_state = DragState.IDLE
        self._drag_start_pos: Optional[Tuple[int, int]] = None

        # Callbacks
        self._on_position_change: Optional[Callable[[int, int], None]] = None
        self._on_drag_start: Optional[Callable[[int, int], None]] = None
        self._on_drag_move: Optional[Callable[[int, int], None]] = None
        self._on_drag_end: Optional[Callable[[int, int], None]] = None
        self._on_button_press: Optional[Callable[[str], None]] = None
        self._on_button_release: Optional[Callable[[str], None]] = None

        self._listener: Optional[mouse.Listener] = None
        self._running = False

    @property
    def position(self) -> Tuple[int, int]:
        """Get current mouse position"""
        return self._position

    @property
    def is_left_pressed(self) -> bool:
        """Check if left mouse button is pressed"""
        return self._left_pressed

    @property
    def is_right_pressed(self) -> bool:
        """Check if right mouse button is pressed"""
        return self._right_pressed

    @property
    def is_middle_pressed(self) -> bool:
        """Check if middle mouse button is pressed"""
        return self._middle_pressed

    @property
    def is_dragging(self) -> bool:
        """Check if currently dragging"""
        return self._drag_state == DragState.DRAGGING

    @property
    def drag_start_position(self) -> Optional[Tuple[int, int]]:
        """Get position where drag started"""
        return self._drag_start_pos

    def set_on_position_change(self, callback: Callable[[int, int], None]) -> None:
        """Set callback for mouse movement"""
        self._on_position_change = callback

    def set_on_drag_start(self, callback: Callable[[int, int], None]) -> None:
        """Set callback for drag start"""
        self._on_drag_start = callback

    def set_on_drag_move(self, callback: Callable[[int, int], None]) -> None:
        """Set callback for drag movement"""
        self._on_drag_move = callback

    def set_on_drag_end(self, callback: Callable[[int, int], None]) -> None:
        """Set callback for drag end"""
        self._on_drag_end = callback

    def set_on_button_press(self, callback: Callable[[str], None]) -> None:
        """Set callback for button press"""
        self._on_button_press = callback

    def set_on_button_release(self, callback: Callable[[str], None]) -> None:
        """Set callback for button release"""
        self._on_button_release = callback

    def _on_move(self, x: int, y: int):
        """Handle mouse movement"""
        self._position = (x, y)

        # Call position change callback
        if self._on_position_change:
            self._on_position_change(x, y)

        # Handle drag movement
        if self._drag_state == DragState.DRAGGING:
            if self._on_drag_move:
                self._on_drag_move(x, y)

    def _on_click(self, x: int, y: int, button: mouse.Button, pressed: bool):
        """Handle mouse button events"""
        button_name = button.name  # 'left', 'right', 'middle'

        if button == mouse.Button.left:
            self._left_pressed = pressed

            if pressed:
                # Start drag
                self._drag_state = DragState.DRAGGING
                self._drag_start_pos = (x, y)
                if self._on_drag_start:
                    self._on_drag_start(x, y)
            else:
                # End drag
                if self._drag_state == DragState.DRAGGING:
                    self._drag_state = DragState.IDLE
                    if self._on_drag_end:
                        self._on_drag_end(x, y)
                    self._drag_start_pos = None

        elif button == mouse.Button.right:
            self._right_pressed = pressed

        elif button == mouse.Button.middle:
            self._middle_pressed = pressed

        # Call button callbacks
        if pressed and self._on_button_press:
            self._on_button_press(button_name)
        elif not pressed and self._on_button_release:
            self._on_button_release(button_name)

    def _on_scroll(self, x: int, y: int, dx: int, dy: int):
        """Handle mouse scroll events"""
        # Currently not used, but available for future features
        pass

    def start(self) -> bool:
        """Start mouse tracking"""
        if self._running:
            return False

        try:
            self._listener = mouse.Listener(
                on_move=self._on_move,
                on_click=self._on_click,
                on_scroll=self._on_scroll
            )
            self._listener.start()
            self._running = True
            return True

        except Exception as e:
            print(f"Error starting mouse tracker: {e}")
            return False

    def stop(self):
        """Stop mouse tracking"""
        if self._listener:
            self._listener.stop()
            self._listener = None
            self._running = False

        # Reset state
        self._drag_state = DragState.IDLE
        self._drag_start_pos = None

    def is_running(self) -> bool:
        """Check if tracker is running"""
        return self._running

    def wait(self):
        """Wait for listener to finish (blocking)"""
        if self._listener:
            self._listener.join()


class KeyboardTracker:
    """Tracks global keyboard state, especially modifier keys"""

    def __init__(self):
        self._shift_pressed = False
        self._ctrl_pressed = False
        self._alt_pressed = False
        self._super_pressed = False
        self._pressed_keys: Set[keyboard.Key] = set()

        # Callbacks
        self._on_key_press: Optional[Callable[[str], None]] = None
        self._on_key_release: Optional[Callable[[str], None]] = None
        self._on_modifier_change: Optional[Callable[[bool, bool, bool, bool], None]] = None

        self._listener: Optional[keyboard.Listener] = None
        self._running = False

    @property
    def is_shift_pressed(self) -> bool:
        """Check if Shift key is pressed"""
        return self._shift_pressed

    @property
    def is_ctrl_pressed(self) -> bool:
        """Check if Ctrl key is pressed"""
        return self._ctrl_pressed

    @property
    def is_alt_pressed(self) -> bool:
        """Check if Alt key is pressed"""
        return self._alt_pressed

    @property
    def is_super_pressed(self) -> bool:
        """Check if Super/Windows key is pressed"""
        return self._super_pressed

    @property
    def pressed_keys(self) -> Set[keyboard.Key]:
        """Get set of currently pressed keys"""
        return self._pressed_keys.copy()

    def set_on_key_press(self, callback: Callable[[str], None]) -> None:
        """Set callback for key press"""
        self._on_key_press = callback

    def set_on_key_release(self, callback: Callable[[str], None]) -> None:
        """Set callback for key release"""
        self._on_key_release = callback

    def set_on_modifier_change(self, callback: Callable[[bool, bool, bool, bool], None]) -> None:
        """Set callback for modifier key changes (shift, ctrl, alt, super)"""
        self._on_modifier_change = callback

    def _update_modifiers(self):
        """Update modifier key states and call callback"""
        if self._on_modifier_change:
            self._on_modifier_change(
                self._shift_pressed,
                self._ctrl_pressed,
                self._alt_pressed,
                self._super_pressed
            )

    def _on_press(self, key):
        """Handle key press"""
        self._pressed_keys.add(key)

        # Update modifier states
        old_state = (self._shift_pressed, self._ctrl_pressed, self._alt_pressed, self._super_pressed)

        if key in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r):
            self._shift_pressed = True
        elif key in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            self._ctrl_pressed = True
        elif key in (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r, keyboard.Key.alt_gr):
            self._alt_pressed = True
        elif key in (keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r):
            self._super_pressed = True

        new_state = (self._shift_pressed, self._ctrl_pressed, self._alt_pressed, self._super_pressed)

        # Call modifier change callback if state changed
        if old_state != new_state:
            self._update_modifiers()

        # Call key press callback
        if self._on_key_press:
            try:
                key_name = key.char if hasattr(key, 'char') else key.name
            except AttributeError:
                key_name = str(key)
            self._on_key_press(key_name)

    def _on_release(self, key):
        """Handle key release"""
        self._pressed_keys.discard(key)

        # Update modifier states
        old_state = (self._shift_pressed, self._ctrl_pressed, self._alt_pressed, self._super_pressed)

        if key in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r):
            self._shift_pressed = False
        elif key in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            self._ctrl_pressed = False
        elif key in (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r, keyboard.Key.alt_gr):
            self._alt_pressed = False
        elif key in (keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r):
            self._super_pressed = False

        new_state = (self._shift_pressed, self._ctrl_pressed, self._alt_pressed, self._super_pressed)

        # Call modifier change callback if state changed
        if old_state != new_state:
            self._update_modifiers()

        # Call key release callback
        if self._on_key_release:
            try:
                key_name = key.char if hasattr(key, 'char') else key.name
            except AttributeError:
                key_name = str(key)
            self._on_key_release(key_name)

    def start(self) -> bool:
        """Start keyboard tracking"""
        if self._running:
            return False

        try:
            self._listener = keyboard.Listener(
                on_press=self._on_press,
                on_release=self._on_release
            )
            self._listener.start()
            self._running = True
            return True

        except Exception as e:
            print(f"Error starting keyboard tracker: {e}")
            return False

    def stop(self):
        """Stop keyboard tracking"""
        if self._listener:
            self._listener.stop()
            self._listener = None
            self._running = False

        # Reset state
        self._shift_pressed = False
        self._ctrl_pressed = False
        self._alt_pressed = False
        self._super_pressed = False
        self._pressed_keys.clear()

    def is_running(self) -> bool:
        """Check if tracker is running"""
        return self._running

    def wait(self):
        """Wait for listener to finish (blocking)"""
        if self._listener:
            self._listener.join()


class InputMonitor:
    """Combined mouse and keyboard monitoring with modifier-aware drag detection"""

    def __init__(self):
        self.mouse = MouseTracker()
        self.keyboard = KeyboardTracker()
        self._on_modifier_drag_start: Optional[Callable[[int, int, bool, bool, bool, bool], None]] = None
        self._on_modifier_drag_move: Optional[Callable[[int, int, bool, bool, bool, bool], None]] = None
        self._on_modifier_drag_end: Optional[Callable[[int, int, bool, bool, bool, bool], None]] = None

        # Convenience callbacks for specific modifier combinations
        self._on_shift_drag_start: Optional[Callable[[int, int], None]] = None
        self._on_shift_drag_move: Optional[Callable[[int, int], None]] = None
        self._on_shift_drag_end: Optional[Callable[[int, int], None]] = None

    def set_on_modifier_drag_start(self, callback: Callable[[int, int, bool, bool, bool, bool], None]) -> None:
        """Set callback for drag start with modifiers (x, y, shift, ctrl, alt, super)"""
        self._on_modifier_drag_start = callback

    def set_on_modifier_drag_move(self, callback: Callable[[int, int, bool, bool, bool, bool], None]) -> None:
        """Set callback for drag move with modifiers (x, y, shift, ctrl, alt, super)"""
        self._on_modifier_drag_move = callback

    def set_on_modifier_drag_end(self, callback: Callable[[int, int, bool, bool, bool, bool], None]) -> None:
        """Set callback for drag end with modifiers (x, y, shift, ctrl, alt, super)"""
        self._on_modifier_drag_end = callback

    def set_on_shift_drag_start(self, callback: Callable[[int, int], None]) -> None:
        """Set callback for Shift+drag start (x, y)"""
        self._on_shift_drag_start = callback

    def set_on_shift_drag_move(self, callback: Callable[[int, int], None]) -> None:
        """Set callback for Shift+drag move (x, y)"""
        self._on_shift_drag_move = callback

    def set_on_shift_drag_end(self, callback: Callable[[int, int], None]) -> None:
        """Set callback for Shift+drag end (x, y)"""
        self._on_shift_drag_end = callback

    def _setup_callbacks(self):
        """Setup internal callbacks to connect mouse and keyboard"""
        def on_drag_start(x, y):
            shift = self.keyboard.is_shift_pressed
            ctrl = self.keyboard.is_ctrl_pressed
            alt = self.keyboard.is_alt_pressed
            super_pressed = self.keyboard.is_super_pressed

            # Call general modifier drag callback
            if self._on_modifier_drag_start:
                self._on_modifier_drag_start(x, y, shift, ctrl, alt, super_pressed)

            # Call Shift-specific callback if Shift is pressed
            if shift and self._on_shift_drag_start:
                self._on_shift_drag_start(x, y)

        def on_drag_move(x, y):
            shift = self.keyboard.is_shift_pressed
            ctrl = self.keyboard.is_ctrl_pressed
            alt = self.keyboard.is_alt_pressed
            super_pressed = self.keyboard.is_super_pressed

            # Call general modifier drag callback
            if self._on_modifier_drag_move:
                self._on_modifier_drag_move(x, y, shift, ctrl, alt, super_pressed)

            # Call Shift-specific callback if Shift is pressed
            if shift and self._on_shift_drag_move:
                self._on_shift_drag_move(x, y)

        def on_drag_end(x, y):
            shift = self.keyboard.is_shift_pressed
            ctrl = self.keyboard.is_ctrl_pressed
            alt = self.keyboard.is_alt_pressed
            super_pressed = self.keyboard.is_super_pressed

            # Call general modifier drag callback
            if self._on_modifier_drag_end:
                self._on_modifier_drag_end(x, y, shift, ctrl, alt, super_pressed)

            # Call Shift-specific callback if Shift is pressed
            if shift and self._on_shift_drag_end:
                self._on_shift_drag_end(x, y)

        self.mouse.set_on_drag_start(on_drag_start)
        self.mouse.set_on_drag_move(on_drag_move)
        self.mouse.set_on_drag_end(on_drag_end)

    def start(self) -> bool:
        """Start monitoring mouse and keyboard"""
        self._setup_callbacks()

        mouse_ok = self.mouse.start()
        keyboard_ok = self.keyboard.start()

        if not (mouse_ok and keyboard_ok):
            self.stop()
            return False

        return True

    def stop(self):
        """Stop monitoring"""
        self.mouse.stop()
        self.keyboard.stop()

    def is_running(self) -> bool:
        """Check if both trackers are running"""
        return self.mouse.is_running() and self.keyboard.is_running()

    def wait(self):
        """Wait for listeners to finish (blocking)"""
        self.mouse.wait()
        self.keyboard.wait()


class Hotkey:
    """Represents a keyboard hotkey combination"""

    def __init__(self, modifiers: Set[str], key: str, callback: Callable[[], None], description: str = ""):
        """
        Create a hotkey

        Args:
            modifiers: Set of modifier keys ('shift', 'ctrl', 'alt', 'super')
            key: The main key (e.g., 'z', 'a', 'f1')
            callback: Function to call when hotkey is triggered
            description: Human-readable description of the hotkey's purpose
        """
        self.modifiers = {m.lower() for m in modifiers}
        self.key = key.lower()
        self.callback = callback
        self.description = description

    def matches(self, shift: bool, ctrl: bool, alt: bool, super_key: bool, pressed_key: str) -> bool:
        """Check if current state matches this hotkey"""
        if pressed_key is None:
            return False

        current_mods = set()
        if shift:
            current_mods.add('shift')
        if ctrl:
            current_mods.add('ctrl')
        if alt:
            current_mods.add('alt')
        if super_key:
            current_mods.add('super')

        return current_mods == self.modifiers and pressed_key.lower() == self.key

    def __repr__(self):
        mod_str = '+'.join(sorted(self.modifiers)).upper() if self.modifiers else ''
        key_str = self.key.upper()
        hotkey_str = f"{mod_str}+{key_str}" if mod_str else key_str
        desc_str = f" ({self.description})" if self.description else ""
        return f"Hotkey[{hotkey_str}]{desc_str}"


class HotkeyManager:
    """Manages global hotkey registration and triggering"""

    def __init__(self):
        self._hotkeys: List[Hotkey] = []
        self._keyboard = KeyboardTracker()
        self._enabled = True
        self._on_hotkey_triggered: Optional[Callable[[Hotkey], None]] = None

    def register(self, modifiers: Set[str], key: str, callback: Callable[[], None], description: str = "") -> Hotkey:
        """
        Register a global hotkey

        Args:
            modifiers: Set of modifier keys ('shift', 'ctrl', 'alt', 'super')
            key: The main key
            callback: Function to call when hotkey is triggered
            description: Description of what the hotkey does

        Returns:
            The registered Hotkey object
        """
        hotkey = Hotkey(modifiers, key, callback, description)
        self._hotkeys.append(hotkey)
        return hotkey

    def unregister(self, hotkey: Hotkey) -> bool:
        """Remove a hotkey registration"""
        if hotkey in self._hotkeys:
            self._hotkeys.remove(hotkey)
            return True
        return False

    def clear_all(self):
        """Remove all registered hotkeys"""
        self._hotkeys.clear()

    def set_on_hotkey_triggered(self, callback: Callable[[Hotkey], None]) -> None:
        """Set callback that's called whenever any hotkey is triggered"""
        self._on_hotkey_triggered = callback

    def enable(self):
        """Enable hotkey processing"""
        self._enabled = True

    def disable(self):
        """Disable hotkey processing"""
        self._enabled = False

    def is_enabled(self) -> bool:
        """Check if hotkey processing is enabled"""
        return self._enabled

    def _on_key_press(self, key_name: str):
        """Handle key press and check for hotkey matches"""
        if not self._enabled:
            return

        # Debug: Print key presses for troubleshooting
        if key_name and key_name.lower() == 'tab':
            print(f"[DEBUG] Tab pressed - Shift:{self._keyboard.is_shift_pressed} Super:{self._keyboard.is_super_pressed} Alt:{self._keyboard.is_alt_pressed}")

        # Check all registered hotkeys
        for hotkey in self._hotkeys:
            if hotkey.matches(
                self._keyboard.is_shift_pressed,
                self._keyboard.is_ctrl_pressed,
                self._keyboard.is_alt_pressed,
                self._keyboard.is_super_pressed,
                key_name
            ):
                # Trigger hotkey callback
                print(f"[HOTKEY] Matched: {hotkey}")
                try:
                    hotkey.callback()
                except Exception as e:
                    print(f"Error in hotkey callback: {e}")

                # Trigger global callback
                if self._on_hotkey_triggered:
                    try:
                        self._on_hotkey_triggered(hotkey)
                    except Exception as e:
                        print(f"Error in hotkey triggered callback: {e}")

                # Only trigger first match
                break

    def start(self) -> bool:
        """Start listening for hotkeys"""
        self._keyboard.set_on_key_press(self._on_key_press)
        return self._keyboard.start()

    def stop(self):
        """Stop listening for hotkeys"""
        self._keyboard.stop()

    def is_running(self) -> bool:
        """Check if hotkey manager is running"""
        return self._keyboard.is_running()

    def wait(self):
        """Wait for listener to finish (blocking)"""
        self._keyboard.wait()

    def get_hotkeys(self) -> List[Hotkey]:
        """Get list of registered hotkeys"""
        return self._hotkeys.copy()


def main():
    """Command-line interface for testing input monitoring"""
    import argparse
    import time

    parser = argparse.ArgumentParser(description='PowerSnap Input Monitor')
    parser.add_argument('--monitor', action='store_true', help='Monitor mouse position')
    parser.add_argument('--track-drag', action='store_true', help='Track drag operations')
    parser.add_argument('--track-buttons', action='store_true', help='Track button presses')
    parser.add_argument('--track-modifiers', action='store_true', help='Track modifier keys')
    parser.add_argument('--track-shift-drag', action='store_true',
                       help='Track Shift+drag operations (combined monitor)')
    parser.add_argument('--test-hotkeys', action='store_true',
                       help='Test global hotkey system')
    parser.add_argument('--duration', type=int, default=10,
                       help='Duration to monitor (seconds, default: 10)')

    args = parser.parse_args()

    # Use hotkey manager for hotkey test
    if args.test_hotkeys:
        hotkey_mgr = HotkeyManager()
        use_hotkeys = True
        use_combined = False
    # Use combined monitor for shift+drag
    elif args.track_shift_drag:
        monitor = InputMonitor()
        use_combined = True
        use_hotkeys = False
    else:
        tracker = MouseTracker()
        kbd_tracker = KeyboardTracker() if args.track_modifiers else None
        use_combined = False
        use_hotkeys = False

    if args.monitor and not use_combined:
        print("Monitoring mouse position...")
        print("Move your mouse around for {} seconds".format(args.duration))
        print("-" * 80)

        last_print_time = 0

        def on_move(x, y):
            nonlocal last_print_time
            current_time = time.time()
            # Print position every 100ms to avoid flooding
            if current_time - last_print_time > 0.1:
                print(f"\rPosition: ({x:5d}, {y:5d})", end='', flush=True)
                last_print_time = current_time

        tracker.set_on_position_change(on_move)

    if args.track_drag and not use_combined:
        print("Tracking drag operations...")
        print("Click and drag the mouse for {} seconds".format(args.duration))
        print("-" * 80)

        def on_drag_start(x, y):
            print(f"\n[DRAG START] at ({x}, {y})")

        def on_drag_move(x, y):
            start_x, start_y = tracker.drag_start_position
            dx = x - start_x
            dy = y - start_y
            distance = (dx**2 + dy**2) ** 0.5
            print(f"\r[DRAGGING] ({x}, {y}) - offset: ({dx:+4d}, {dy:+4d}) - distance: {distance:.1f}px",
                  end='', flush=True)

        def on_drag_end(x, y):
            start_x, start_y = tracker.drag_start_position
            dx = x - start_x
            dy = y - start_y
            print(f"\n[DRAG END] at ({x}, {y}) - total movement: ({dx:+d}, {dy:+d})")

        tracker.set_on_drag_start(on_drag_start)
        tracker.set_on_drag_move(on_drag_move)
        tracker.set_on_drag_end(on_drag_end)

    if args.track_buttons and not use_combined:
        print("Tracking button presses...")
        print("Click mouse buttons for {} seconds".format(args.duration))
        print("-" * 80)

        def on_press(button_name):
            print(f"[BUTTON PRESS] {button_name}")

        def on_release(button_name):
            print(f"[BUTTON RELEASE] {button_name}")

        tracker.set_on_button_press(on_press)
        tracker.set_on_button_release(on_release)

    if args.track_modifiers and not use_combined:
        print("Tracking modifier keys...")
        print("Press Shift, Ctrl, Alt, or Super keys for {} seconds".format(args.duration))
        print("-" * 80)

        def on_modifier_change(shift, ctrl, alt, super_key):
            modifiers = []
            if shift:
                modifiers.append("SHIFT")
            if ctrl:
                modifiers.append("CTRL")
            if alt:
                modifiers.append("ALT")
            if super_key:
                modifiers.append("SUPER")

            if modifiers:
                print(f"\r[MODIFIERS] {'+'.join(modifiers)}", end='', flush=True)
            else:
                print(f"\r[MODIFIERS] (none)     ", end='', flush=True)

        kbd_tracker.set_on_modifier_change(on_modifier_change)

    if args.track_shift_drag:
        print("Tracking Shift+drag operations...")
        print("Try dragging with and without Shift pressed for {} seconds".format(args.duration))
        print("-" * 80)

        def on_modifier_drag_start(x, y, shift, ctrl, alt, super_key):
            modifiers = []
            if shift:
                modifiers.append("SHIFT")
            if ctrl:
                modifiers.append("CTRL")
            if alt:
                modifiers.append("ALT")
            if super_key:
                modifiers.append("SUPER")

            mod_str = '+'.join(modifiers) if modifiers else "NO MODIFIERS"
            print(f"\n[{mod_str}+DRAG START] at ({x}, {y})")

            if shift:
                print("*** SHIFT DETECTED! ***")

        def on_modifier_drag_move(x, y, shift, ctrl, alt, super_key):
            modifiers = []
            if shift:
                modifiers.append("S")
            if ctrl:
                modifiers.append("C")
            if alt:
                modifiers.append("A")
            if super_key:
                modifiers.append("W")

            mod_str = '+'.join(modifiers) if modifiers else "-"
            print(f"\r[{mod_str}] ({x:4d}, {y:4d})", end='', flush=True)

        def on_modifier_drag_end(x, y, shift, ctrl, alt, super_key):
            modifiers = []
            if shift:
                modifiers.append("SHIFT")
            if ctrl:
                modifiers.append("CTRL")
            if alt:
                modifiers.append("ALT")
            if super_key:
                modifiers.append("SUPER")

            mod_str = '+'.join(modifiers) if modifiers else "NO MODIFIERS"
            print(f"\n[{mod_str}+DRAG END] at ({x}, {y})")

        monitor.set_on_modifier_drag_start(on_modifier_drag_start)
        monitor.set_on_modifier_drag_move(on_modifier_drag_move)
        monitor.set_on_modifier_drag_end(on_modifier_drag_end)

    if args.test_hotkeys:
        print("Testing global hotkey system...")
        print("Registered hotkeys:")
        print("-" * 80)

        # Register test hotkeys
        def on_hotkey1():
            print("\n*** HOTKEY 1 TRIGGERED: Super+Alt+Z ***")

        def on_hotkey2():
            print("\n*** HOTKEY 2 TRIGGERED: Ctrl+Shift+A ***")

        def on_hotkey3():
            print("\n*** HOTKEY 3 TRIGGERED: Alt+F ***")

        hk1 = hotkey_mgr.register({'super', 'alt'}, 'z', on_hotkey1, "Toggle zones overlay")
        hk2 = hotkey_mgr.register({'ctrl', 'shift'}, 'a', on_hotkey2, "Show all windows")
        hk3 = hotkey_mgr.register({'alt'}, 'f', on_hotkey3, "Focus mode")

        for i, hk in enumerate(hotkey_mgr.get_hotkeys(), 1):
            print(f"{i}. {hk}")

        print("\nPress the registered hotkeys for {} seconds...".format(args.duration))
        print("-" * 80)

        # Set global callback
        def on_any_hotkey(hotkey):
            print(f"[HOTKEY DETECTED] {hotkey}")

        hotkey_mgr.set_on_hotkey_triggered(on_any_hotkey)

    # Start tracking
    if use_hotkeys:
        success = hotkey_mgr.start()
        tracker_obj = hotkey_mgr
    elif use_combined:
        success = monitor.start()
        tracker_obj = monitor
    else:
        success = tracker.start()
        if kbd_tracker:
            success = success and kbd_tracker.start()
        tracker_obj = tracker

    if success:
        print("\nInput monitor started. Press Ctrl+C to stop.\n")

        try:
            # Run for specified duration
            time.sleep(args.duration)
            print("\n\nStopping monitor...")
            tracker_obj.stop()
            if not use_combined and not use_hotkeys:
                if kbd_tracker:
                    kbd_tracker.stop()

            if not use_combined and not use_hotkeys:
                print(f"\nFinal mouse position: {tracker.position}")
                print(f"Buttons - Left: {tracker.is_left_pressed}, "
                      f"Right: {tracker.is_right_pressed}, "
                      f"Middle: {tracker.is_middle_pressed}")
                if kbd_tracker:
                    print(f"Modifiers - Shift: {kbd_tracker.is_shift_pressed}, "
                          f"Ctrl: {kbd_tracker.is_ctrl_pressed}, "
                          f"Alt: {kbd_tracker.is_alt_pressed}, "
                          f"Super: {kbd_tracker.is_super_pressed}")

        except KeyboardInterrupt:
            print("\n\nInterrupted by user")
            tracker_obj.stop()
            if not use_combined and not use_hotkeys:
                if kbd_tracker:
                    kbd_tracker.stop()
    else:
        print("Failed to start input monitor")


if __name__ == '__main__':
    main()
