"""Zone data structures and management for PowerSnap"""

import json
import os
from typing import List, Optional, Dict, Tuple
from dataclasses import dataclass, asdict


@dataclass
class Zone:
    """Represents a window snap zone"""

    x: int
    y: int
    width: int
    height: int
    name: str = ""
    color: str = "#3498db"  # Default blue color

    def __post_init__(self):
        """Validate zone dimensions"""
        if self.width <= 0 or self.height <= 0:
            raise ValueError(f"Zone dimensions must be positive: width={self.width}, height={self.height}")

    @property
    def x2(self) -> int:
        """Right edge of zone"""
        return self.x + self.width

    @property
    def y2(self) -> int:
        """Bottom edge of zone"""
        return self.y + self.height

    @property
    def center(self) -> Tuple[int, int]:
        """Center point of zone"""
        return (self.x + self.width // 2, self.y + self.height // 2)

    @property
    def area(self) -> int:
        """Area of zone in pixels"""
        return self.width * self.height

    def contains_point(self, x: int, y: int) -> bool:
        """Check if a point is inside this zone"""
        return (self.x <= x < self.x2 and
                self.y <= y < self.y2)

    def overlaps(self, other: 'Zone') -> bool:
        """Check if this zone overlaps with another zone"""
        return not (self.x2 <= other.x or other.x2 <= self.x or
                   self.y2 <= other.y or other.y2 <= self.y)

    def overlap_area(self, other: 'Zone') -> int:
        """Calculate the overlapping area with another zone"""
        if not self.overlaps(other):
            return 0

        overlap_x = max(0, min(self.x2, other.x2) - max(self.x, other.x))
        overlap_y = max(0, min(self.y2, other.y2) - max(self.y, other.y))
        return overlap_x * overlap_y

    def to_dict(self) -> Dict:
        """Convert zone to dictionary for JSON serialization"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> 'Zone':
        """Create zone from dictionary"""
        return cls(**data)

    def __repr__(self) -> str:
        name_str = f" '{self.name}'" if self.name else ""
        return f"Zone{name_str}({self.x}, {self.y}, {self.width}, {self.height})"


def create_preset_layout(preset_name: str, screen_width: int, screen_height: int) -> List[Zone]:
    """
    Create a preset zone layout

    Args:
        preset_name: Name of preset ('halves', 'thirds', 'quarters', 'grid3x3')
        screen_width: Width of screen
        screen_height: Height of screen

    Returns:
        List of zones for the preset
    """
    zones = []

    if preset_name == "halves":
        # Two vertical halves
        zones.append(Zone(0, 0, screen_width // 2, screen_height, "Left Half", "#3498db"))
        zones.append(Zone(screen_width // 2, 0, screen_width // 2, screen_height, "Right Half", "#e74c3c"))

    elif preset_name == "thirds":
        # Three vertical thirds
        third_width = screen_width // 3
        zones.append(Zone(0, 0, third_width, screen_height, "Left Third", "#3498db"))
        zones.append(Zone(third_width, 0, third_width, screen_height, "Center Third", "#2ecc71"))
        zones.append(Zone(third_width * 2, 0, screen_width - (third_width * 2),
                         screen_height, "Right Third", "#e74c3c"))

    elif preset_name == "quarters":
        # Four quadrants
        half_w = screen_width // 2
        half_h = screen_height // 2
        zones.append(Zone(0, 0, half_w, half_h, "Top Left", "#3498db"))
        zones.append(Zone(half_w, 0, half_w, half_h, "Top Right", "#e74c3c"))
        zones.append(Zone(0, half_h, half_w, half_h, "Bottom Left", "#2ecc71"))
        zones.append(Zone(half_w, half_h, half_w, half_h, "Bottom Right", "#f39c12"))

    elif preset_name == "grid3x3":
        # 3x3 grid
        cell_w = screen_width // 3
        cell_h = screen_height // 3
        colors = ["#3498db", "#e74c3c", "#2ecc71", "#f39c12", "#9b59b6",
                 "#1abc9c", "#34495e", "#e67e22", "#95a5a6"]

        for row in range(3):
            for col in range(3):
                x = col * cell_w
                y = row * cell_h
                w = cell_w if col < 2 else screen_width - (cell_w * 2)
                h = cell_h if row < 2 else screen_height - (cell_h * 2)
                zones.append(Zone(x, y, w, h, f"Cell {row},{col}", colors[row * 3 + col]))

    else:
        raise ValueError(f"Unknown preset: {preset_name}")

    return zones
