#!/usr/bin/env python3
"""
PowerSnap Daemon - Background service for window snapping

Runs continuously in the background to:
1. Monitor for modifier+drag window movements (shows zone overlay)
2. Snap windows to zones on release

Note: Zone editor hotkey (Super+Shift+Tab) is registered via native GNOME
keyboard shortcuts during installation (gsettings).

Usage:
    python -m power_snap.daemon [--modifier MODIFIER]

    --modifier: Trigger key for snapping (default: alt)
                Options: shift, ctrl, alt, super
"""

import sys
import os
import signal

from .snapper import WindowSnapper


class PowerSnapDaemon:
    """Background daemon for PowerSnap window snapping"""

    def __init__(self, trigger_modifier: str = 'alt'):
        """
        Initialize daemon

        Args:
            trigger_modifier: Modifier key for snapping (shift/ctrl/alt/super)
        """
        self.trigger_modifier = trigger_modifier

        # Initialize snapper (handles modifier+drag workflow)
        self.snapper = WindowSnapper(trigger_modifier=trigger_modifier)

        print(f"PowerSnap Daemon starting...")
        print(f"Snapping trigger: {trigger_modifier.upper()} + Drag")
        print(f"Zone editor: Super+Shift+Tab (native keyboard shortcut)")

    def _setup_signal_handlers(self):
        """Setup signal handlers for clean shutdown"""
        def signal_handler(signum, frame):
            print(f"\nReceived signal {signum}, shutting down...")
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    def start(self):
        """Start the daemon"""
        try:
            # Setup signal handlers
            self._setup_signal_handlers()

            print("\nPowerSnap daemon is running")
            print("Press Ctrl+C to exit\n")

            # Start snapping workflow (this runs GTK main loop)
            self.snapper.start_snap_workflow()

        except KeyboardInterrupt:
            print("\nShutting down...")
            self.stop()
        except Exception as e:
            print(f"ERROR: Daemon crashed: {e}")
            import traceback
            traceback.print_exc()
            self.stop()
            return False

        return True

    def stop(self):
        """Stop the daemon and cleanup"""
        print("Stopping daemon...")

        # Stop snapper trackers
        if self.snapper:
            if self.snapper.keyboard_tracker:
                self.snapper.keyboard_tracker.stop()
            if self.snapper.mouse_tracker:
                self.snapper.mouse_tracker.stop()

        print("Daemon stopped")


def is_powersnap_running(pid):
    """
    Check if a process is actually the PowerSnap daemon

    Args:
        pid: Process ID to check

    Returns:
        True if the process exists and is PowerSnap, False otherwise
    """
    try:
        # Check if process exists
        os.kill(pid, 0)

        # Verify it's actually PowerSnap by checking command line
        try:
            with open(f'/proc/{pid}/cmdline', 'r') as f:
                cmdline = f.read()
                # cmdline is null-separated, check if it contains power_snap.daemon
                return 'power_snap.daemon' in cmdline or 'power_snap/daemon' in cmdline
        except (FileNotFoundError, PermissionError):
            # Can't read cmdline - process might have just exited or no permission
            # Assume it's not PowerSnap
            return False

    except OSError:
        # Process doesn't exist
        return False


def main():
    """Main entry point for daemon"""
    import argparse

    parser = argparse.ArgumentParser(
        description='PowerSnap Daemon - Background window snapping service'
    )
    parser.add_argument(
        '--modifier',
        choices=['shift', 'ctrl', 'alt', 'super'],
        default='alt',
        help='Modifier key to trigger zone overlay (default: alt)'
    )

    args = parser.parse_args()

    # Check for existing daemon instance
    pid_file = os.path.expanduser('~/.config/powersnap/daemon.pid')
    os.makedirs(os.path.dirname(pid_file), exist_ok=True)

    if os.path.exists(pid_file):
        try:
            with open(pid_file, 'r') as f:
                old_pid = int(f.read().strip())

            # Check if process is still running AND is actually PowerSnap
            if is_powersnap_running(old_pid):
                print(f"ERROR: PowerSnap daemon is already running (PID: {old_pid})")
                print(f"Kill it with: kill {old_pid}")
                sys.exit(1)
            else:
                # Process doesn't exist or is not PowerSnap, remove stale PID file
                print(f"Removing stale PID file (PID {old_pid} is not PowerSnap)")
                os.remove(pid_file)
        except (ValueError, IOError):
            # Invalid PID file, remove it
            try:
                os.remove(pid_file)
            except:
                pass

    # Write our PID
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))

    try:
        # Create and start daemon
        daemon = PowerSnapDaemon(trigger_modifier=args.modifier)
        success = daemon.start()
    finally:
        # Clean up PID file
        try:
            os.remove(pid_file)
        except:
            pass

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
