"""The mouse editor's arithmetic of pixels (ui/canvas.py): the view, the
handles, a box resized by one of them, a drag kept to one axis; and the
settings of snapping."""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")

from tavletta.ui import canvas  # noqa: E402
from tavletta.ui.settings import Settings  # noqa: E402

VIEW = canvas.View(20.0, 30.0, 10.0, 10.0)  # 10 px per mm, the label at 20, 30
BOX = (2.0, 3.0, 12.0, 8.0)  # 100 x 50 px at 40, 60


def test_view_round_trip():
    assert VIEW.to_px(2, 3) == (40, 60)
    assert VIEW.to_mm(40, 60) == (2, 3)
    assert VIEW.box_px(BOX) == (40, 60, 140, 110)
    assert VIEW.mm(25) == 2.5


@pytest.mark.parametrize(
    ("x", "y", "handle"),
    [
        (40, 60, "nw"),
        (90, 60, "n"),
        (140, 60, "ne"),
        (140, 85, "e"),
        (140, 110, "se"),
        (90, 110, "s"),
        (40, 110, "sw"),
        (40, 85, "w"),
        (51, 71, "nw"),  # within the 24 px target
        (90, 85, None),  # the middle: the box's own
        (200, 200, None),
    ],
)
def test_handle_at(x, y, handle):
    assert canvas.handle_at(VIEW, BOX, x, y, canvas.HIT_PX) == handle


def test_a_small_box_is_moved_from_inside():
    small = (2.0, 3.0, 5.0, 4.0)  # 30 x 10 px at 40, 60
    assert canvas.handle_at(VIEW, small, 47, 64, canvas.HIT_PX) is None  # inside
    assert canvas.handle_at(VIEW, small, 69, 65, canvas.HIT_PX) == "e"  # its handle as drawn
    assert canvas.handle_at(VIEW, small, 30, 52, canvas.HIT_PX) == "nw"  # outside, a target


def test_resized_each_handle():
    assert canvas.resized(BOX, "e", 2, 9, False) == (2, 3, 14, 8)
    assert canvas.resized(BOX, "w", 1, 9, False) == (3, 3, 12, 8)
    assert canvas.resized(BOX, "n", 9, -1, False) == (2, 2, 12, 8)
    assert canvas.resized(BOX, "s", 9, 2, False) == (2, 3, 12, 10)
    assert canvas.resized(BOX, "nw", -1, -1, False) == (1, 2, 12, 8)
    assert canvas.resized(BOX, "se", 1, 1, False) == (2, 3, 13, 9)


def test_resized_never_inside_out():
    left, _top, right, _bottom = canvas.resized(BOX, "e", -50, 0, False)
    assert right - left == pytest.approx(canvas.LEAST_MM)
    _left, top, _right, bottom = canvas.resized(BOX, "n", 0, 50, False)
    assert bottom - top == pytest.approx(canvas.LEAST_MM)


def test_resized_in_proportion():
    # the right edge 10 -> 20 mm wide: twice the height too, from the top
    assert canvas.resized(BOX, "e", 10, 0, True) == pytest.approx((2, 3, 22, 13))
    # the bottom edge: by the height, from the left
    assert canvas.resized(BOX, "s", 0, 5, True) == pytest.approx((2, 3, 22, 13))
    # a corner by the side that changed more, from the opposite corner
    assert canvas.resized(BOX, "nw", -10, -1, True) == pytest.approx((-8, -2, 12, 8))
    assert canvas.resized(BOX, "se", 1, 5, True) == pytest.approx((2, 3, 22, 13))


def test_dragged_edges():
    assert canvas.dragged_edges(BOX, "nw") == (2, 3)
    assert canvas.dragged_edges(BOX, "e") == (12, None)
    assert canvas.dragged_edges(BOX, "s") == (None, 8)


def test_constrained():
    assert canvas.constrained(5, 2) == (5, 0)
    assert canvas.constrained(-1, 3) == (0, 3)


def test_snapping_settings():
    settings = Settings()
    settings.save = lambda: None
    assert settings.snap and settings.grid_mm == 0
    settings.set_snap(False)
    settings.set_grid_mm(2.5)
    assert not settings.snap and settings.grid_mm == 2.5
    for junk in ("1", True, -1, 0, 999, None):
        settings.data["grid_mm"] = junk
        assert settings.grid_mm == 0
    settings.data["snap"] = "no"  # anything but false: on
    assert settings.snap


class _Bounds:
    def __init__(self, x, y, width, height):
        self.values = (x, y, width, height)

    def get_x(self):
        return self.values[0]

    def get_y(self):
        return self.values[1]

    def get_width(self):
        return self.values[2]

    def get_height(self):
        return self.values[3]


class _Picture:
    """A Gtk.Picture's part view_of() asks: its image's size, its place."""

    def __init__(self, image, bounds):
        self.image, self.bounds = image, bounds

    def get_paintable(self):
        if self.image is None:
            return None
        width, height = self.image
        return type(
            "P",
            (),
            {"get_intrinsic_width": lambda s: width, "get_intrinsic_height": lambda s: height},
        )()

    def compute_bounds(self, _canvas):
        return True, _Bounds(*self.bounds)


def test_view_of_a_picture_that_fits_its_image():
    # 254 dpi: 10 px per mm; drawn at half size at 30, 40
    view = canvas.view_of(_Picture((400, 200), (30, 40, 200, 100)), None, canvas.Raster(254))
    assert view == canvas.View(30, 40, 5, 5)


def test_view_of_a_letterboxed_image_and_its_top_row():
    # the image taller than the picture's proportions: drawn 100 px high,
    # 200 px wide, centred in 300; the label's top 20 rows down the image
    raster = canvas.Raster(254, top_px=20)
    view = canvas.view_of(_Picture((400, 200), (0, 0, 300, 100)), None, raster)
    assert view.ox == 50 and view.oy == 10 and view.sx == view.sy == 5


def test_no_view_without_an_image():
    assert canvas.view_of(_Picture(None, (0, 0, 10, 10)), None, canvas.Raster(254)) is None
    assert canvas.view_of(_Picture((10, 10), (0, 0, 10, 10)), None, None) is None
    assert canvas.view_of(_Picture((10, 10), (0, 0, 0, 0)), None, canvas.Raster(254)) is None
