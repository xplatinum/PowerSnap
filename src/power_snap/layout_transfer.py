"""Portable JSON export/import of PowerSnap layouts.

An archive contains the layout objects only, not local workspace mappings,
installed application files, or display-specific runtime state.
"""

import json
import math
import os
import re
from datetime import datetime
from pathlib import Path

from .layout_library import Layout
from .zone import Zone

FORMAT = "powersnap-layouts"
VERSION = 1
MAX_ARCHIVE_BYTES = 4 * 1024 * 1024
MAX_LAYOUTS = 200
MAX_ZONES = 200
_SAFE_NAME = re.compile(r"^[\w -]{1,100}$", re.UNICODE)


def _validated_name(name):
    if not isinstance(name, str) or not name.strip() or not _SAFE_NAME.fullmatch(name):
        raise ValueError("Layout names must contain 1–100 letters, numbers, spaces, hyphens or underscores")
    # The library normalizes filenames. Avoid collisions/path surprises.
    if name != name.strip() or name in {".", ".."}:
        raise ValueError("Invalid layout name")
    return name


def _validate_layout(data):
    if not isinstance(data, dict):
        raise ValueError("Invalid layout entry")
    name = _validated_name(data.get("name"))
    zones = data.get("zones")
    if not isinstance(zones, list) or len(zones) > MAX_ZONES:
        raise ValueError(f"Invalid zone list in '{name}'")
    validated_zones = []
    for item in zones:
        if not isinstance(item, dict):
            raise ValueError(f"Invalid zone in '{name}'")
        dimensions = []
        for key in ("x", "y", "width", "height"):
            value = item.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"Invalid {key} in '{name}'")
            if abs(value) > 1000000:
                raise ValueError(f"Out-of-range {key} in '{name}'")
            dimensions.append(value)
        if dimensions[2] <= 0 or dimensions[3] <= 0:
            raise ValueError(f"Nonpositive zone size in '{name}'")
        zone_name = item.get("name", "")
        color = item.get("color", "#3498db")
        if not isinstance(zone_name, str) or len(zone_name) > 200:
            raise ValueError(f"Invalid zone name in '{name}'")
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise ValueError(f"Invalid zone color in '{name}'")
        validated_zones.append(Zone(*dimensions, zone_name, color))
    description = data.get("description", "")
    if not isinstance(description, str) or len(description) > 2000:
        raise ValueError(f"Invalid description in '{name}'")
    return Layout(name, validated_zones, description)


def export_layouts(library, filepath):
    """Export all library layouts to a portable JSON file atomically."""
    names = library.list_layouts()
    if len(names) > MAX_LAYOUTS:
        raise ValueError("Too many layouts to export")
    layouts = []
    for name in names:
        layout = library.load_layout(name)
        if layout is None:
            raise ValueError(f"Unable to read layout '{name}'")
        _validate_layout(layout.to_dict())
        layouts.append(layout.to_dict())
    payload = {"format": FORMAT, "version": VERSION,
               "exported_at": datetime.now().isoformat(), "layouts": layouts}
    content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if len(content.encode("utf-8")) > MAX_ARCHIVE_BYTES:
        raise ValueError("Layout export exceeds maximum supported size")
    target = Path(filepath)
    temporary = target.with_name(target.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return len(layouts)


def import_layouts(library, filepath):
    """Import layouts without overwriting existing ones. Return (count, renamed)."""
    source = Path(filepath)
    if source.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("Layout file is too large")
    with source.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or payload.get("format") != FORMAT or payload.get("version") != VERSION:
        raise ValueError("Not a supported PowerSnap layout export")
    entries = payload.get("layouts")
    if not isinstance(entries, list) or len(entries) > MAX_LAYOUTS:
        raise ValueError("Invalid layouts collection")
    # Validate the whole file before making any changes.
    layouts = [_validate_layout(entry) for entry in entries]
    existing = set(library.list_layouts())
    plan = []
    renamed = 0
    for layout in layouts:
        requested = layout.name
        candidate = requested
        if candidate in existing:
            n = 2
            while True:
                suffix = f" (imported {n})"
                prefix = requested[:100 - len(suffix)].rstrip()
                candidate = prefix + suffix
                if candidate not in existing:
                    break
                n += 1
            renamed += 1
        layout.name = candidate
        existing.add(candidate)
        plan.append(layout)
    for layout in plan:
        if not library.save_layout(layout):
            raise OSError(f"Unable to save imported layout '{layout.name}'")
    return len(plan), renamed
