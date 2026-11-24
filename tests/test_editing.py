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
