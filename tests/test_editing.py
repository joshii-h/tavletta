"""Editing with the mouse, toolkit free (tavletta.editing): geometry and hit
testing, snapping, the edits and their undo."""

from __future__ import annotations

import copy
import itertools
import json
import math
from datetime import datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tavletta import editing, freelayout
from tavletta.editing import ElementBox, Geometry
from tavletta.history import History
from tavletta.model import (
    CodeElement,
    ImageElement,
    Label,
    PageElement,
    ShapeElement,
    SymbolElement,
    TextElement,
    element_id,
)
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
    with pytest.raises(IndexError):
        geometry.bounds([-1])


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


# --- edits ------------------------------------------------------------------------

DOT = 25.4 / DPI


def _three() -> Label:
    return _free_label(
        ShapeElement(shape="rectangle", width_mm=5, height_mm=4, x_mm=2, y_mm=3),
        ShapeElement(shape="ellipse", width_mm=8, height_mm=6, x_mm=20, y_mm=10, fill="solid"),
        TextElement(text="Tx", size_pt=12, x_mm=40, y_mm=5),
    )


def test_move_gives_a_new_label_and_keeps_the_old_one():
    label = _three()
    before = copy.deepcopy(label)
    moved = editing.move(label, [0, 2], 1.234, -0.5)
    assert label == before  # the label given stays as it was
    assert [(e.x_mm, e.y_mm) for e in moved.elements] == [(3.23, 2.5), (20, 10), (41.23, 4.5)]
    assert [element_id(e) for e in moved.elements] == [element_id(e) for e in label.elements]
    assert moved.to_dict()["elements"][1] == label.to_dict()["elements"][1]


def test_moved_elements_render_where_their_boxes_moved_to():
    label = _three()
    before = Geometry.of(label, render(label, dpi=DPI))
    moved = editing.move(label, [1], 3.5, 2.25)
    after = Geometry.of(moved, render(moved, dpi=DPI))
    want = editing.moved(before.boxes[1].box, 3.5, 2.25)
    assert after.boxes[1].box == pytest.approx(want, abs=DOT)
    assert after.boxes[0].box == before.boxes[0].box


def test_a_move_stops_at_the_label_edge_for_all_together():
    label = _three()
    moved = editing.move(label, [0, 1], -5, -4)
    assert [(e.x_mm, e.y_mm) for e in moved.elements[:2]] == [(0, 0), (18, 7)]


def test_a_move_of_nothing_is_the_label_itself():
    label = _three()
    assert editing.move(label, [], 3, 3) is label
    assert editing.move(label, [0], 0, 0) is label


def test_edits_refuse_the_flowing_layout_wrong_elements_and_numbers():
    label = _three()
    flowing = Label(tape_mm=24, elements=[TextElement()])
    with pytest.raises(editing.NotFree):
        editing.move(flowing, [0], 1, 1)
    with pytest.raises(IndexError):
        editing.move(label, [3], 1, 1)
    with pytest.raises(IndexError):
        editing.move(label, [-1], 1, 1)
    with pytest.raises(ValueError):
        editing.move(label, [0], math.nan, 1)
    geometry = _geometry((0, 0, 1, 1))  # of another label
    with pytest.raises(ValueError):
        editing.align(label, geometry, [0, 1], "left")
    with pytest.raises(ValueError):
        editing.align(label, editing.measure(label, dpi=DPI), [0, 1], "sideways")
    with pytest.raises(ValueError):
        editing.distribute(label, editing.measure(label, dpi=DPI), [0, 1, 2], "z")


@pytest.mark.parametrize(
    ("edge", "side"),
    [("left", 0), ("right", 2), ("top", 1), ("bottom", 3), ("center", None), ("middle", None)],
)
def test_aligned_boxes_line_up_once_rendered(edge, side):
    label = _three()
    geometry = editing.measure(label, dpi=DPI)
    aligned = editing.align(label, geometry, [0, 1, 2], edge)
    after = editing.measure(aligned, dpi=DPI)
    if side is None:
        axis = (0, 2) if edge == "center" else (1, 3)
        values = [(b.box[axis[0]] + b.box[axis[1]]) / 2 for b in after.boxes]
    else:
        values = [b.box[side] for b in after.boxes]
    assert max(values) - min(values) <= 2 * DOT


def test_one_element_aligns_to_the_label():
    label = _free_label(ShapeElement(shape="rectangle", width_mm=6, height_mm=4, x_mm=2, y_mm=3))
    label.auto_length, label.length_mm = False, 50
    geometry = editing.measure(label, dpi=DPI)
    centred = editing.measure(editing.align(label, geometry, [0], "center"), dpi=DPI)
    box = centred.boxes[0]
    assert (box.left + box.right) / 2 == pytest.approx(25, abs=DOT)
    bottom = editing.measure(editing.align(label, geometry, [0], "bottom"), dpi=DPI)
    assert bottom.boxes[0].bottom == pytest.approx(24, abs=DOT)


@pytest.mark.parametrize("axis", ["x", "y"])
def test_distributed_boxes_have_the_same_gaps(axis):
    label = _free_label(
        *(
            ShapeElement(shape="rectangle", width_mm=w, height_mm=w, x_mm=x, y_mm=x / 4)
            for w, x in ((3, 1), (5, 6), (2, 30), (4, 60))
        )
    )
    geometry = editing.measure(label, dpi=DPI)
    spread = editing.measure(editing.distribute(label, geometry, [3, 1, 0, 2], axis), dpi=DPI)
    start, end = (0, 2) if axis == "x" else (1, 3)
    boxes = sorted(spread.boxes, key=lambda b: b.box[start])
    gaps = [b.box[start] - a.box[end] for a, b in itertools.pairwise(boxes)]
    assert max(gaps) - min(gaps) <= 2 * DOT
    assert boxes[0].box == geometry.boxes[0].box and boxes[-1].box == geometry.boxes[3].box


def test_aligning_nothing_is_the_label_itself():
    label = _three()
    assert editing.align(label, editing.measure(label, dpi=DPI), [], "left") is label


def test_two_elements_are_not_distributed():
    label = _three()
    assert editing.distribute(label, editing.measure(label, dpi=DPI), [0, 1], "x") is label


@settings(max_examples=200)
@given(
    st.lists(st.tuples(st.floats(0, 200), st.floats(0, 200)), min_size=1, max_size=5),
    st.floats(-300, 300),
    st.floats(-300, 300),
)
def test_a_move_keeps_positions_valid_and_their_distances(places, dx, dy):
    label = _free_label(*(ShapeElement(x_mm=x, y_mm=y) for x, y in places))
    rounded = editing.move(label, range(len(places)), 0.001, 0.001)  # to hundredths
    moved = editing.move(rounded, range(len(places)), dx, dy)
    xs = [e.x_mm for e in moved.elements]
    ys = [e.y_mm for e in moved.elements]
    assert min(xs) >= 0 and min(ys) >= 0
    old = rounded.elements
    for a, b in itertools.combinations(range(len(places)), 2):
        assert xs[a] - xs[b] == pytest.approx(old[a].x_mm - old[b].x_mm, abs=0.011)
        assert ys[a] - ys[b] == pytest.approx(old[a].y_mm - old[b].y_mm, abs=0.011)
    back = editing.move(moved, range(len(places)), -(xs[0] - old[0].x_mm), -(ys[0] - old[0].y_mm))
    # element by element: pytest.approx takes no nested tuples (pytest 9 refuses
    # them, older versions compare them exactly)
    for element, before in zip(back.elements, old, strict=True):
        assert (element.x_mm, element.y_mm) == pytest.approx((before.x_mm, before.y_mm), abs=0.011)


# --- resizing -----------------------------------------------------------------------


def _resized(element, box_of) -> tuple[ElementBox, ElementBox, Label]:
    """The element alone on a label, resized to box_of(its box): its box
    before and after, and the new label."""
    label = _free_label(element)
    before = editing.measure(label, dpi=DPI).boxes[0]
    resized = editing.resize(label, editing.measure(label, dpi=DPI), 0, box_of(before))
    return before, editing.measure(resized, dpi=DPI).boxes[0], resized


def test_a_shape_takes_the_new_box():
    shape = ShapeElement(shape="rectangle", width_mm=6, height_mm=4, x_mm=5, y_mm=5)
    _before, after, label = _resized(shape, lambda b: (b.left - 2, b.top, b.right, b.bottom + 3))
    assert after.box == pytest.approx((3, 5, 11, 12), abs=DOT)
    # scaled by how much the box (in whole dots) grew: within a dot
    shape = label.elements[0]
    assert (shape.width_mm, shape.height_mm) == pytest.approx((8, 7), abs=DOT)


def test_a_shape_reaching_down_to_the_bottom_gets_a_height_of_its_own():
    shape = ShapeElement(shape="rectangle", width_mm=6, height_mm=0, x_mm=5, y_mm=4)
    before, after, label = _resized(shape, lambda b: (b.left, b.top, b.right, b.bottom - 10))
    assert before.bottom == pytest.approx(24, abs=DOT)
    assert label.elements[0].height_mm == pytest.approx(10, abs=DOT)
    assert after.bottom == pytest.approx(14, abs=DOT)


@pytest.mark.parametrize("rotation", [0, 90])
def test_a_text_keeps_its_proportions(rotation):
    text = TextElement(text="Hello", size_pt=10, rotation=rotation, x_mm=2, y_mm=2, padding_mm=1)
    before, after, label = _resized(
        text, lambda b: (b.left, b.top, b.left + 2 * b.width, b.top + 1.2 * b.height)
    )
    assert label.elements[0].size_pt == 20  # the side that changed more
    assert label.elements[0].padding_mm == 2
    assert after.width == pytest.approx(2 * before.width, rel=0.05)
    assert after.height == pytest.approx(2 * before.height, rel=0.05)
    assert (after.left, after.top) == pytest.approx((before.left, before.top), abs=2 * DOT)


def test_a_text_of_the_default_size_and_dragging_its_top_left_corner():
    text = TextElement(text="Hi", x_mm=10, y_mm=10)
    before, after, label = _resized(
        text, lambda b: (b.right - 1.5 * b.width, b.bottom - 1.5 * b.height, b.right, b.bottom)
    )
    assert label.elements[0].size_pt == pytest.approx(1.5 * freelayout.mm_to_pt(4), abs=0.01)
    assert (after.right, after.bottom) == pytest.approx((before.right, before.bottom), abs=3 * DOT)


def test_an_image_and_a_symbol_keep_their_proportions(png_b64):
    image = ImageElement(data=png_b64((30, 10), "red"), height_mm=5, x_mm=2, y_mm=2)
    before, after, _label = _resized(image, lambda b: (b.left, b.top, b.right, b.top + 10))
    assert after.width == pytest.approx(2 * before.width, abs=2 * DOT)
    symbol = SymbolElement(path="M0,0H24V24H0Z", x_mm=2, y_mm=2)  # size 0: the default
    _before, after, label = _resized(symbol, lambda b: (b.left, b.top, b.left + 5, b.bottom))
    assert label.elements[0].size_mm == 5
    assert after.height == pytest.approx(5, abs=DOT)


def test_a_two_dimensional_code_by_its_module():
    code = CodeElement(data="x", module_mm=0.5, x_mm=2, y_mm=2)
    _before, _after, label = _resized(
        code, lambda b: (b.left, b.top, b.left + 2 * b.width, b.top + 2 * b.height)
    )
    assert label.elements[0].module_mm == 1.0


@pytest.mark.parametrize("rotation", [0, 90])
def test_a_linear_code_by_its_module_along_and_its_bars_across(rotation):
    code = CodeElement(
        data="12345", symbology="code128", module_mm=0.3, height_mm=5, rotation=rotation
    )
    code.x_mm = code.y_mm = 1

    def wider(b):  # twice as long along the bars' run, as high across
        if rotation:
            return (b.left, b.top, b.right, b.top + 2 * b.height)
        return (b.left, b.top, b.left + 2 * b.width, b.bottom)

    _before, _after, label = _resized(code, wider)
    assert (label.elements[0].module_mm, label.elements[0].height_mm) == (0.6, 5)


def test_a_code_of_default_sizes_gets_sizes_of_its_own():
    code = CodeElement(data="12345", symbology="code128", x_mm=1, y_mm=1)
    _before, _after, label = _resized(code, lambda b: (b.left, b.top, b.right, b.bottom))
    bars = freelayout.FREE_BAR_MM * 10 / 0.3  # as designed for the module
    assert (label.elements[0].module_mm, label.elements[0].height_mm) == (0.3, round(bars, 2))


def test_a_page_is_only_moved(png_b64):
    page = PageElement(data=png_b64((20, 20), "blue"), x_mm=5, y_mm=5)
    label = _free_label(page)
    geometry = editing.measure(label, dpi=DPI)
    box = geometry.boxes[0]
    resized = editing.resize(
        label, geometry, 0, (box.left - 2, box.top - 1, box.right, box.bottom)
    )
    assert (resized.elements[0].x_mm, resized.elements[0].y_mm) == pytest.approx((3, 4), abs=DOT)


def test_a_resize_to_nothing_leaves_the_smallest_size():
    shape = ShapeElement(shape="rectangle", width_mm=6, height_mm=4, x_mm=5, y_mm=5)
    label = _free_label(shape)
    geometry = editing.measure(label, dpi=DPI)
    resized = editing.resize(label, geometry, 0, (5, 5, 5, 5))
    assert (resized.elements[0].width_mm, resized.elements[0].height_mm) == (0.1, 0.1)
    text = _free_label(TextElement(text="x", size_pt=8, x_mm=1, y_mm=1))
    tiny = editing.resize(text, editing.measure(text, dpi=DPI), 0, (1, 1, 1, 1))
    assert tiny.elements[0].size_pt == editing.LEAST_PT


def test_a_resize_is_checked_as_a_file_is():
    text = _free_label(TextElement(text="x", size_pt=200, frame_mm=4, x_mm=1, y_mm=1))
    geometry = editing.measure(text, dpi=DPI)
    box = geometry.boxes[0]
    huge = editing.resize(text, geometry, 0, (box.left, box.top, box.left + 10 * box.width, 99))
    assert (huge.elements[0].size_pt, huge.elements[0].frame_mm) == (300, 5)  # the largest
    with pytest.raises(ValueError):
        editing.resize(text, geometry, 0, (0, 0, math.inf, 1))


# --- undo -----------------------------------------------------------------------------


def _places(label: Label) -> list[tuple[float, float]]:
    return [(e.x_mm, e.y_mm) for e in label.elements]


def test_an_edit_is_one_step_and_undone_and_redone():
    label = _three()
    editor = editing.Editor(label)
    assert editor.apply(editing.move(editor.label, [0], 1, 1))
    assert editor.history.can_undo
    assert _places(editor.undo()) == _places(label)
    assert _places(editor.label) == _places(label)
    assert editor.redo().elements[0].x_mm == 3
    assert editor.undo() is not None and editor.undo() is None  # nothing more


def test_an_edit_that_changes_nothing_is_no_step():
    editor = editing.Editor(_three())
    assert not editor.apply(editing.move(editor.label, [0], 0, 0))
    assert not editor.history.can_undo


def test_a_drag_is_one_step_when_it_ends():
    label = _three()
    editor = editing.Editor(label)
    for step in range(1, 30):
        editor.show(editing.move(label, [1], step / 10, 0))
        assert editor.dragging
    assert not editor.history.can_undo  # nothing recorded while dragging
    assert editor.commit()
    assert not editor.dragging and not editor.commit()
    assert editor.label.elements[1].x_mm == 22.9
    assert _places(editor.undo()) == _places(label)


def test_a_drag_called_off_leaves_no_step():
    label = _three()
    editor = editing.Editor(label)
    editor.show(editing.move(label, [1], 5, 0))
    assert editor.cancel() is label
    assert not editor.history.can_undo and not editor.dragging


def test_undo_while_dragging_calls_the_drag_off_and_undoes_the_step_before():
    label = _three()
    editor = editing.Editor(label)
    editor.apply(editing.move(label, [0], 1, 0))
    editor.show(editing.move(editor.label, [0], 9, 0))
    assert _places(editor.undo()) == _places(label)
    assert not editor.dragging


def test_the_window_history_shared_with_the_sidebar():
    """The window keeps its History; the sidebar records into it as before,
    the editor's steps go between them, and undo takes them back in turn."""
    label = _three()
    history = History(label)
    editor = editing.Editor(label, history)
    # the sidebar: a change in place, recorded (as Window.mark_changed does)
    label.elements[2].text = "Changed"
    history.record(label)
    editor.changed(label)
    editor.apply(editing.move(editor.label, [2], 2, 0))
    assert editor.label.elements[2].text == "Changed"
    first = editor.undo()
    assert (first.elements[2].text, first.elements[2].x_mm) == ("Changed", 40)
    second = editor.undo()
    assert second.elements[2].text == "Tx"
    assert editor.redo().elements[2].text == "Changed"


def test_steps_join_by_their_key_as_typing_does():
    """Nudges with the arrow keys can be one step, as typing is (History's key)."""
    now = [0.0]
    label = _three()
    editor = editing.Editor(label, History(label, clock=lambda: now[0]))
    for _ in range(5):
        editor.apply(editing.move(editor.label, [0], 0.1, 0), key="nudge:0")
        now[0] += 0.2
    assert editor.label.elements[0].x_mm == 2.5
    assert _places(editor.undo()) == _places(label)
    assert not editor.history.can_undo
