"""Live X11 desktop dimensions for normalized zone layouts.

python-xlib's Display.screen().width_in_pixels is populated at connection
creation and is stale after XRandR mode switches. Root geometry is queried
from the server for each request instead.
"""


def current_root_size(root):
    """Return current X11 root (width, height), never a cached screen size."""
    geometry = root.get_geometry()
    width, height = int(geometry.width), int(geometry.height)
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid X11 root dimensions: {width}x{height}")
    return width, height
