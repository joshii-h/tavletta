"""Where render() says each element was drawn (Rendered.boxes): against the
ink each element leaves on the label, in both layouts, turned, on labels
whose content runs across, in black and white, scaled down to fit, on
printers whose head is narrower than the tape, and on a label sheet."""

from __future__ import annotations

from dataclasses import replace

import pytest
from PIL import Image, ImageChops

from tavletta.backends import get_backend
from tavletta.backends.base import PrintOptions
from tavletta.model import (
    CodeElement,
    ImageElement,
    Label,
    ShapeElement,
    SheetChoice,
    SymbolElement,
    TextElement,
)
from tavletta.render import mm_to_px, render

DPI = 300.0
WEDGE = "M0,0H24L0,12Z"  # not the same once turned


def _elements(png_b64):
    return [
        TextElement(text="Hello", size_pt=14),
        TextElement(text="Up", size_pt=12, rotation=90),
        CodeElement(data="https://example.org", symbology="qr"),
        CodeElement(data="96385074", symbology="ean8"),
        SymbolElement(path=WEDGE, size_mm=6, rotation=270),
        ShapeElement(shape="rectangle", width_mm=8, height_mm=5),
        ImageElement(data=png_b64((30, 12), "red"), height_mm=4),
    ]


def _free(elements, positions):
    for element, (x, y) in zip(elements, positions, strict=True):
        element.x_mm, element.y_mm = x, y
    return elements


FREE_PLACES = [(2, 1), (30, 2), (40, 4), (10, 10), (60, 1), (70, 12), (2, 14)]


def _hidden(element):
    """The element in another ink: the same size, so the same place."""
    if isinstance(element, ImageElement):
        return replace(element, invert=not element.invert)
    return replace(element, color="#ffffff")


def _ink_px(label: Label, index: int, **options) -> tuple[int, int, int, int]:
    """Where element `index` leaves ink on the rendered label: what changes
    when only its ink does, at least half of it (as the boxes count ink)."""
    shown = render(label, **options).image
    elements = list(label.elements)
    elements[index] = _hidden(elements[index])
    hidden = render(replace(label, elements=elements), **options).image
    diff = ImageChops.difference(shown.convert("RGB"), hidden.convert("RGB")).convert("L")
    box = diff.point(lambda v: 255 if v >= 100 else 0).getbbox()
    assert box, "element %d left no ink" % index
    return box


def _box_px(label: Label, rendered, index: int, dpi: float) -> tuple[int, int, int, int]:
    top = (rendered.image.height - mm_to_px(label.tape_mm, dpi)) // 2
    left, upper, right, lower = rendered.boxes[index]
    x = [round(v / 25.4 * dpi) for v in (left, right)]
    y = [round(v / 25.4 * dpi) + top for v in (upper, lower)]
    return x[0], y[0], x[1], y[1]


def _assert_boxes_match_ink(label: Label, tolerance: int = 1, **options):
    dpi = options.setdefault("dpi", DPI)
    rendered = render(label, **options)
    # every element drawn (no placeholder); one may reach beyond the label,
    # a code scaled down to fit may have thin modules
    assert {w.key for w in rendered.warnings} <= {"outside_label", "code_thin"}
    assert len(rendered.boxes) == len(label.elements)
    for index in range(len(label.elements)):
        want = _ink_px(label, index, **options)
        got = _box_px(label, rendered, index, dpi)
        assert all(abs(a - b) <= tolerance for a, b in zip(got, want, strict=True)), (
            index,
            got,
            want,
        )


@pytest.mark.parametrize("mono", [False, True])
def test_flow_boxes_are_where_the_elements_ink_is(png_b64, mono):
    label = Label(tape_mm=24, elements=_elements(png_b64))
    _assert_boxes_match_ink(label, mono=mono)


def test_flow_boxes_of_a_label_scaled_down_to_fit(png_b64):
    label = Label(tape_mm=24, auto_length=False, length_mm=40, elements=_elements(png_b64))
    assert render(label, dpi=DPI).overflow
    _assert_boxes_match_ink(label)


@pytest.mark.parametrize("valign", ["top", "center", "bottom"])
def test_flow_boxes_follow_the_alignment_across(valign):
    label = Label(tape_mm=24, elements=[TextElement(text="ab", size_pt=10, valign=valign)])
    _assert_boxes_match_ink(label)


@pytest.mark.parametrize("mono", [False, True])
def test_free_boxes_are_where_the_elements_ink_is(png_b64, mono):
    elements = _free(_elements(png_b64), FREE_PLACES)
    elements[2] = replace(elements[2], rotation=90, module_mm=0.4)
    label = Label(tape_mm=24, layout="free", elements=elements)
    _assert_boxes_match_ink(label, mono=mono)


def test_free_boxes_on_a_label_whose_content_runs_across(png_b64):
    label = Label.new_for("cab")
    label.orientation = "across"
    label.set_media_size(80.0, 24.0)
    label.layout = "free"
    label.elements = _free(_elements(png_b64), FREE_PLACES)
    _assert_boxes_match_ink(label, dpi=12 * 25.4)
    # along the roll: the same label space, so the same boxes
    along = replace(label)
    along.set_orientation("along")
    assert render(along, dpi=DPI).boxes == render(label, dpi=DPI).boxes


def test_a_text_box_starts_at_its_free_position():
    """A text's position is the top left corner of its ink: its box too."""
    label = Label(
        tape_mm=24, layout="free", elements=[TextElement(text="Hx", size_pt=20, x_mm=5, y_mm=3)]
    )
    left, top, _right, _bottom = render(label, dpi=DPI).boxes[0]
    dot = 25.4 / DPI
    assert abs(left - 5) <= dot and abs(top - 3) <= dot


def test_an_element_without_ink_has_its_whole_part_as_box():
    label = Label(tape_mm=24, layout="free", elements=[TextElement(text="", x_mm=5, y_mm=3)])
    left, top, right, bottom = render(label, dpi=DPI).boxes[0]
    assert right > left and bottom > top


def test_boxes_from_the_nominal_tape_edge_on_a_narrower_print_head():
    """A P-touch prints a strip in the middle of the tape: the boxes count
    from the tape's edge, as the free layout's positions do."""
    backend = get_backend("ptouch")
    label = Label(
        printer="ptouch",
        tape_mm=24,
        layout="free",
        elements=[ShapeElement(shape="rectangle", width_mm=5, height_mm=5, x_mm=3, y_mm=4)],
    )
    rendered = backend.render_label(label, PrintOptions(), bleed=False)
    assert rendered.image.height < mm_to_px(24, backend.capabilities.dpi)
    left, top, _right, _bottom = rendered.boxes[0]
    dot = 25.4 / backend.capabilities.dpi
    assert abs(left - 3) <= dot and abs(top - 4) <= dot


def test_boxes_of_a_label_on_a_sheet():
    backend = get_backend("system")
    label = Label.new_for("system")
    label.sheet = SheetChoice("Avery L7160")
    label.use_sheet_size()
    label.elements = [TextElement(text="On a sheet", size_pt=10)]
    rendered = backend.render_label(label, PrintOptions(), bleed=False, dpi=DPI)
    left, top, right, bottom = rendered.boxes[0]
    length, height = rendered.length_mm, label.tape_mm
    assert 0 < left < right < length and 0 < top < bottom < height
    image = rendered.image.convert("L").point(lambda v: 255 if v < 128 else 0)
    ink = image.getbbox()
    assert ink
    assert abs(ink[0] - round(left / 25.4 * DPI)) <= 1


def test_no_elements_no_boxes():
    assert render(Label(tape_mm=24), dpi=DPI).boxes == []
    assert render(Label(tape_mm=24, layout="free"), dpi=DPI).boxes == []


def test_a_placeholder_is_its_whole_square(png_b64):
    broken = ImageElement(data="not an image")
    rendered = render(Label(tape_mm=24, elements=[broken]), dpi=DPI)
    left, top, right, bottom = rendered.boxes[0]
    assert right - left == pytest.approx(bottom - top)
    assert isinstance(rendered.image, Image.Image)
