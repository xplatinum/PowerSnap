"""Persistent user preferences shared by the editor and daemon."""
import json
import os
from pathlib import Path

SETTINGS_PATH = Path.home() / ".config" / "powersnap" / "settings.json"
DEFAULT_GAP_PX = 36  # Preserve the previous user's configuration on upgrade.


def load_gap_px():
    try:
        with SETTINGS_PATH.open(encoding="utf-8") as handle:
            value = json.load(handle).get("snap_gap_px", DEFAULT_GAP_PX)
        return min(200, max(0, int(value)))
    except (OSError, ValueError, TypeError, AttributeError):
        return DEFAULT_GAP_PX


def save_gap_px(value):
    gap = min(200, max(0, int(value)))
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    try:
        with SETTINGS_PATH.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        pass
    data["snap_gap_px"] = gap
    temp = SETTINGS_PATH.with_suffix(".json.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    os.replace(temp, SETTINGS_PATH)
