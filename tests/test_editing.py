"""Editing with the mouse, toolkit free (tavletta.editing): geometry and hit
testing, snapping, the edits and their undo."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tavletta import editing
from tavletta.editing import ElementBox, Geometry
from tavletta.model import CodeElement, Label, TextElement
from tavletta.render import render
from tavletta.variables import Values

DPI = 300.0


def _geometry(*boxes: tuple[float, float, float, float], layout: str = "free") -> Geometry:
    return Geometry(
        100.0,
        50.0,
        layout,
        tuple(ElementBox(i, "shape", *box) for i, box in enumerate(boxes)),
    )


def _free_label(*elements) -> Label:
    return Label(tape_mm=24, layout="free", elements=list(elements))


# --- geometry ---------------------------------------------------------------------


def test_geometry_of_a_render_is_its_boxes_and_size():
    label = _free_label(
        TextElement(text="A", size_pt=12, x_mm=3, y_mm=2),
        CodeElement(data="x", rotation=90, module_mm=0.5, x_mm=20, y_mm=4),
    )
    rendered = render(label, dpi=DPI)
    geometry = Geometry.of(label, rendered)
    assert geometry.free
    assert (geometry.width_mm, geometry.height_mm) == (rendered.length_mm, 24)
    assert [b.box for b in geometry.boxes] == rendered.boxes
    assert [(b.index, b.kind, b.rotation) for b in geometry.boxes] == [
        (0, "text", 0),
        (1, "code", 90),
    ]


def test_geometry_of_a_render_of_another_label_is_refused():
    rendered = render(_free_label(TextElement()), dpi=DPI)
    with pytest.raises(ValueError):
        Geometry.of(_free_label(), rendered)


def test_measure_fills_in_columns_and_variables():
    """A text with placeholders is as wide as what is printed in it."""
    label = Label(tape_mm=24, elements=[TextElement(text="{Name}", size_pt=12)])
    short = editing.measure(label, columns={"Name": "Al"}, dpi=DPI)
    long = editing.measure(label, columns={"Name": "Alexandra Longname"}, dpi=DPI)
    assert long.boxes[0].width > 3 * short.boxes[0].width
    assert not short.free  # the flowing layout: shown and hit, not moved

    counter = Label(tape_mm=24, elements=[TextElement(text="{=counter}", size_pt=12)])
    values = Values.for_label(counter, datetime(2026, 1, 1))
    one = editing.measure(counter, values, dpi=DPI)
    raw = editing.measure(counter, dpi=DPI)  # without values: as written
    assert one.boxes[0].width < raw.boxes[0].width


def test_the_geometry_as_json():
    geometry = _geometry((1.004, 2.0, 3.333333, 4.0))
    data = json.loads(json.dumps(geometry.to_dict()))
    assert data == {
        "width_mm": 100.0,
        "height_mm": 50.0,
        "layout": "free",
        "elements": [{"index": 0, "kind": "shape", "box": [1.0, 2.0, 3.33, 4.0], "rotation": 0}],
    }


# --- hit testing ------------------------------------------------------------------


def test_hits_topmost_first():
    geometry = _geometry((0, 0, 10, 10), (5, 5, 15, 15), (20, 20, 30, 30))
    assert geometry.hits(7, 7) == [1, 0]
    assert geometry.hits(2, 2) == [0]
    assert geometry.hits(17, 17) == []
    assert geometry.hits(10, 10) == [1, 0]  # the edge belongs to the box


def test_a_thin_line_is_hit_within_the_slop():
    geometry = _geometry((0, 5, 20, 5.1))
    assert geometry.hits(10, 5.5) == []
    assert geometry.hits(10, 5.5, slop=0.5) == [0]


def test_within_a_rectangle_from_any_corner():
    geometry = _geometry((0, 0, 10, 10), (5, 5, 15, 15), (20, 20, 30, 30))
    assert geometry.within((-1, -1, 16, 16)) == [0, 1]
    assert geometry.within((16, 16, -1, -1)) == [0, 1]  # dragged up and left
    assert geometry.within((8, 8, 25, 25)) == []
    assert geometry.within((8, 8, 25, 25), touching=True) == [0, 1, 2]


def test_bounds_of_some_elements():
    geometry = _geometry((0, 0, 10, 10), (5, 5, 15, 15), (20, 20, 30, 30))
    assert geometry.bounds([2, 0]) == (0, 0, 30, 30)
    with pytest.raises(ValueError):
        geometry.bounds([])


box_strategy = st.tuples(
    st.floats(0, 90), st.floats(0, 40), st.floats(0.1, 10), st.floats(0.1, 10)
).map(lambda t: (t[0], t[1], t[0] + t[2], t[1] + t[3]))


@settings(max_examples=200)
@given(
    st.lists(box_strategy, min_size=1, max_size=12),
    st.floats(0, 100),
    st.floats(0, 50),
)
def test_every_hit_contains_the_point_in_drawing_order(boxes, x, y):
    geometry = _geometry(*boxes)
    found = geometry.hits(x, y)
    assert found == sorted(found, reverse=True)
    assert set(found) == {i for i, b in enumerate(geometry.boxes) if b.contains(x, y)}


def test_moved_box():
    assert editing.moved((1, 2, 3, 4), 1.5, -2) == (2.5, 0, 4.5, 2)


# --- snapping ---------------------------------------------------------------------


def test_snaps_to_the_nearest_element_edge_and_says_which():
    geometry = _geometry((10, 10, 20, 20), (40, 30, 50, 40))
    # dragged box: its left edge 0.4 mm right of element 0's right edge
    snapped = editing.snap((20.4, 30.8, 25.4, 32.8), geometry, exclude=(), tolerance=1)
    assert snapped.dx == pytest.approx(-0.4)
    assert snapped.dy == pytest.approx(-0.8)  # its top to element 1's top
    assert editing.Guide("x", 20, "element", 0) in snapped.guides
    assert editing.Guide("y", 30, "element", 1) in snapped.guides


def test_snaps_to_the_label_edges_and_middle():
    geometry = _geometry()
    assert editing.snap((0.5, 3, 10.5, 8), geometry).dx == pytest.approx(-0.5)
    middle = editing.snap((44.6, 3, 54.6, 8), geometry)  # its middle at 49.6
    assert middle.dx == pytest.approx(0.4)
    assert editing.Guide("x", 50, "label") in middle.guides
    assert editing.snap((89.2, 3, 99.2, 8), geometry).dx == pytest.approx(0.8)


def test_nothing_within_the_tolerance_does_not_snap():
    geometry = _geometry((10, 10, 20, 20))
    snapped = editing.snap((30, 27, 35, 28.5), geometry, tolerance=1)
    assert (snapped.dx, snapped.dy, snapped.guides) == (0.0, 0.0, ())


def test_the_moving_elements_are_no_guides():
    geometry = _geometry((10, 10, 20, 20))
    assert editing.snap((10.3, 10.3, 20.3, 20.3), geometry, exclude=[0]).guides == ()


def test_snaps_to_the_grid_and_a_guide_wins_at_the_same_distance():
    geometry = _geometry((12.5, 0, 13, 1))
    snapped = editing.snap((7.3, 33.2, 9.3, 34.2), geometry, tolerance=1, grid=2.5)
    assert snapped.dx == pytest.approx(0.2)  # 7.3 -> 7.5
    assert snapped.dy == pytest.approx(-0.7)  # 33.2 -> 32.5
    assert editing.Guide("x", 7.5, "grid") in snapped.guides
    # an edge 1 mm from both an element (at 12) and the grid (at 10)
    tie_geometry = _geometry((12, 0, 30, 1))
    tie = editing.snap((11, 33.2, 11, 33.2), tie_geometry, tolerance=1, grid=5)
    assert tie.dx == 1
    assert tie.guides == (editing.Guide("x", 12, "element", 0),)


def test_one_edge_alone_snaps_when_resizing():
    geometry = _geometry((10, 10, 20, 20))
    snapped = editing.snap((19.6, 30, 19.6, 30), geometry, tolerance=1)
    assert snapped.dx == pytest.approx(0.4)


@settings(max_examples=300)
@given(
    st.lists(box_strategy, max_size=8),
    box_strategy,
    st.floats(0.1, 3),
    st.sampled_from([0.0, 1.0, 2.5]),
)
def test_a_snap_moves_within_the_tolerance_onto_its_guides(boxes, box, tolerance, grid):
    geometry = _geometry(*boxes)
    snapped = editing.snap(box, geometry, tolerance=tolerance, grid=grid)
    assert abs(snapped.dx) <= tolerance + 1e-9 and abs(snapped.dy) <= tolerance + 1e-9
    moved = editing.moved(box, snapped.dx, snapped.dy)
    for guide in snapped.guides:
        start, end = (moved[0], moved[2]) if guide.axis == "x" else (moved[1], moved[3])
        assert any(abs(guide.position - v) <= 1e-6 for v in (start, (start + end) / 2, end))
    # it snaps whenever a guide was in reach, so one is drawn
    reach = [
        g
        for g in editing.guides(geometry)
        if g.axis == "x"
        and min(abs(g.position - v) for v in (box[0], (box[0] + box[2]) / 2, box[2])) <= tolerance
    ]
    if reach:
        assert any(g.axis == "x" for g in snapped.guides)
