"""Pure geometry regression tests; do not require a running X11 desktop."""
import unittest
from types import SimpleNamespace
from power_snap.tile_geometry import inset_rectangle


def zone(x, y, w, h):
    return SimpleNamespace(x=x, y=y, width=w, height=h)


class TileGeometryTests(unittest.TestCase):
    def test_fullscreen_outer_gap(self):
        z = zone(0, 0, 1, 1)
        self.assertEqual(inset_rectangle(z, [z], 1280, 800, 20), (20, 20, 1240, 760))

    def test_zero_gap(self):
        a, b = zone(0, 0, .5, 1), zone(.5, 0, .5, 1)
        self.assertEqual(inset_rectangle(a, [a, b], 1280, 800, 0), (0, 0, 640, 800))
        self.assertEqual(inset_rectangle(b, [a, b], 1280, 800, 0), (640, 0, 640, 800))

    def test_vertical_divider_gap(self):
        a, b = zone(0, 0, .5, 1), zone(.5, 0, .5, 1)
        for gap in (1, 18, 36):
            left = inset_rectangle(a, [a, b], 1280, 800, gap)
            right = inset_rectangle(b, [a, b], 1280, 800, gap)
            self.assertEqual(right[0] - (left[0] + left[2]), gap)
            self.assertEqual(left[0], gap)
            self.assertEqual(1280 - right[0] - right[2], gap)

    def test_horizontal_divider_gap(self):
        a, b = zone(0, 0, 1, .5), zone(0, .5, 1, .5)
        for gap in (0, 20, 36):
            top = inset_rectangle(a, [a, b], 1280, 800, gap)
            bottom = inset_rectangle(b, [a, b], 1280, 800, gap)
            self.assertEqual(bottom[1] - (top[1] + top[3]), gap)
            self.assertEqual(top[1], gap)
            self.assertEqual(800 - bottom[1] - bottom[3], gap)

    def test_three_tile_layout(self):
        a, b, c = zone(0,0,.6,1), zone(.6,0,.4,.5), zone(.6,.5,.4,.5)
        rows = [inset_rectangle(z,[a,b,c],1280,800,21) for z in (a,b,c)]
        self.assertEqual(rows[1][1],21)
        self.assertEqual(rows[2][1] - (rows[1][1] + rows[1][3]),21)
        self.assertEqual(800 - rows[2][1] - rows[2][3],21)
        self.assertEqual(rows[1][0] - (rows[0][0]+rows[0][2]),21)


if __name__ == '__main__':
    unittest.main()
