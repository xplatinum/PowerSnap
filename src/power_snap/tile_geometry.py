"""Compute the visible zone rectangles and a single uniform inter-zone gap.

Zones are stored as independent normalized floats, and manually drawn or
split zones can have a few pixels of seam mismatch. Account for that mismatch
rather than only recognizing perfectly coincident borders.
"""


def inset_rectangle(zone, zones, width, height, gap):
    """Return (x, y, w, h) for visible window *outer* bounds.

    Adjacent zones receive exactly one ``gap`` between their visible rectangles,
    even if their saved boundaries overlap or separate slightly. Exposed edges
    receive a full outer margin. Rounding is done on absolute boundaries to
    prevent accumulated off-by-one errors in split layouts.
    """
    width, height = int(width), int(height)
    g = max(0, int(gap))
    x1, x2 = round(zone.x * width), round((zone.x + zone.width) * width)
    y1, y2 = round(zone.y * height), round((zone.y + zone.height) * height)

    # left, right, top, bottom: exposed edges have a full-width outer inset.
    margins = [g, g, g, g]
    found = [False] * 4
    # Allow slight mismatch due to manual resizing, saved floating-point
    # coordinates, and editor handle movement. Don't connect distant zones.
    tolerance = max(2, min(24, g))

    for other in zones:
        if other is zone or other == zone:
            continue
        ox1 = round(other.x * width)
        ox2 = round((other.x + other.width) * width)
        oy1 = round(other.y * height)
        oy2 = round((other.y + other.height) * height)
        vertical_overlap = min(y2, oy2) - max(y1, oy1)
        horizontal_overlap = min(x2, ox2) - max(x1, ox1)

        if vertical_overlap > 1:
            # d is the *existing* separation between the raw zone edges;
            # negative d means the zones overlap slightly.
            for index, distance, is_start in ((0, x1 - ox2, True),
                                              (1, ox1 - x2, False)):
                if -tolerance <= distance <= g:
                    required = max(0, g - distance)
                    # Ensure each partner's half sums to the exact desired gap.
                    inset = (required + 1) // 2 if is_start else required // 2
                    if not found[index] or inset > margins[index]:
                        margins[index] = inset
                    found[index] = True

        if horizontal_overlap > 1:
            for index, distance, is_start in ((2, y1 - oy2, True),
                                              (3, oy1 - y2, False)):
                if -tolerance <= distance <= g:
                    required = max(0, g - distance)
                    inset = (required + 1) // 2 if is_start else required // 2
                    if not found[index] or inset > margins[index]:
                        margins[index] = inset
                    found[index] = True

    left, right, top, bottom = margins
    # Keep narrow tiles valid and always inside their allocated raw rectangle.
    left = min(left, max(0, x2 - x1 - 1))
    right = min(right, max(0, x2 - x1 - left - 1))
    top = min(top, max(0, y2 - y1 - 1))
    bottom = min(bottom, max(0, y2 - y1 - top - 1))
    return (x1 + left, y1 + top,
            max(1, x2 - x1 - left - right),
            max(1, y2 - y1 - top - bottom))


def boundary_insets(zone, zones, width, height, gap):
    """Retained for callers of the older API."""
    x, y, w, h = inset_rectangle(zone, zones, width, height, gap)
    x1, x2 = round(zone.x * width), round((zone.x + zone.width) * width)
    y1, y2 = round(zone.y * height), round((zone.y + zone.height) * height)
    return x - x1, x2 - (x + w), y - y1, y2 - (y + h)
