"""No X server required: verify live root size and proportional zone projection."""
import unittest
from types import SimpleNamespace
from power_snap.display_geometry import current_root_size
from power_snap.tile_geometry import inset_rectangle

class FakeRoot:
    def __init__(self):
        self.width, self.height = 1280, 800
    def get_geometry(self):
        return SimpleNamespace(width=self.width, height=self.height)

class DisplayGeometryTests(unittest.TestCase):
    def test_resolution_is_queried_each_time(self):
        root = FakeRoot()
        self.assertEqual(current_root_size(root), (1280, 800))
        root.width, root.height = 1920, 1080
        self.assertEqual(current_root_size(root), (1920, 1080))

    def test_normalized_right_half_scales_and_gap_stays_pixels(self):
        a = SimpleNamespace(x=0, y=0, width=.5, height=1)
        b = SimpleNamespace(x=.5, y=0, width=.5, height=1)
        for w, h in ((1280, 800), (1920, 1080), (3440, 1440)):
            left = inset_rectangle(a, [a,b], w, h, 20)
            right = inset_rectangle(b, [a,b], w, h, 20)
            self.assertEqual(right[0] - (left[0]+left[2]), 20)
            self.assertEqual(w - (right[0]+right[2]), 20)
            self.assertEqual(right[1], 20)
            self.assertEqual(h - (right[1]+right[3]), 20)

if __name__ == '__main__':
    unittest.main()
