"""Editing a label with the mouse, without a toolkit: where its elements
are (Geometry), which one lies under a point, snapping a moving box to
guide lines, and the edits themselves as functions from a label to a new
label. The window's editor and the print server's web page share it.

Coordinates are label space in mm, as the preview shows the label: x
along the tape from its left end, y across it from the tape's top edge
(the free layout's x_mm and y_mm). A box is (left, top, right, bottom).

The boxes come from the render (render.Rendered.boxes): what it drew is
where an element is, there is no second layout. While an element is
dragged, its box moves with the mouse (moved()); the next render says
where it landed.

Moving and resizing are for the free layout only: there every element
has its own position. In the flowing layout the elements' places follow
from their order, their sizes and the label's spacing; its boxes can be
shown and hit, but an edit refuses it (NotFree).
"""

from __future__ import annotations

import copy
import math
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass

from tavletta.model import MAX_SIZE_MM, Label, validate_element
from tavletta.render import DEFAULT_SAFE_MM, PRINT_DPI, MmBox, Rendered, render
from tavletta.variables import Values, expand_label

# --- where the elements are -----------------------------------------------------

DIGITS = 2  # mm in to_dict(): a hundredth, finer than any printer's dot


@dataclass(frozen=True)
class ElementBox:
    """Where an element of the label was drawn: its ink (an element without
    any: all of what it drew), turned as it is (a quarter turn at a time,
    so the box is the turned element's)."""

    index: int  # in label.elements
    kind: str
    left: float
    top: float
    right: float
    bottom: float
    rotation: int = 0

    @property
    def box(self) -> MmBox:
        return self.left, self.top, self.right, self.bottom

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    def contains(self, x: float, y: float, slop: float = 0.0) -> bool:
        """Whether the point lies on the box, or within `slop` mm of it (a
        thin line is hard to hit exactly)."""
        return (
            self.left - slop <= x <= self.right + slop
            and self.top - slop <= y <= self.bottom + slop
        )

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "kind": self.kind,
            "box": [round(v, DIGITS) for v in self.box],
            "rotation": self.rotation,
        }


@dataclass(frozen=True)
class Geometry:
    """A rendered label's size and its elements' boxes, in the order of
    label.elements: each one drawn over those before it."""

    width_mm: float  # along the tape: the rendered length
    height_mm: float  # across: the tape or the label's height
    layout: str  # Label.layout
    boxes: tuple[ElementBox, ...]

    @classmethod
    def of(cls, label: Label, rendered: Rendered) -> Geometry:
        """The geometry of a label as rendered (the label the render was
        made of, its variables filled in or not: the same elements)."""
        if len(rendered.boxes) != len(label.elements):
            raise ValueError("the render is not of this label")
        boxes = tuple(
            ElementBox(index, element.kind, *box, rotation=getattr(element, "rotation", 0))
            for index, (element, box) in enumerate(
                zip(label.elements, rendered.boxes, strict=True)
            )
        )
        return cls(rendered.length_mm, label.tape_mm, label.layout, boxes)

    @property
    def free(self) -> bool:
        """Whether its elements can be moved (the free layout)."""
        return self.layout == "free"

    def hits(self, x: float, y: float, slop: float = 0.0) -> list[int]:
        """The elements under a point, the topmost (drawn last) first."""
        return [b.index for b in reversed(self.boxes) if b.contains(x, y, slop)]

    def within(self, rect: MmBox, touching: bool = False) -> list[int]:
        """The elements inside a rectangle (a rubber band, from any corner
        to the opposite one); touching: also those it only overlaps."""
        left, right = sorted((rect[0], rect[2]))
        top, bottom = sorted((rect[1], rect[3]))
        if touching:
            return [
                b.index
                for b in self.boxes
                if b.left <= right and b.right >= left and b.top <= bottom and b.bottom >= top
            ]
        return [
            b.index
            for b in self.boxes
            if left <= b.left and b.right <= right and top <= b.top and b.bottom <= bottom
        ]

    def bounds(self, indices: Iterable[int]) -> MmBox:
        """The box around some of the elements (ValueError: none given)."""
        chosen = [self.boxes[i] for i in indices]
        if not chosen:
            raise ValueError("no elements")
        return (
            min(b.left for b in chosen),
            min(b.top for b in chosen),
            max(b.right for b in chosen),
            max(b.bottom for b in chosen),
        )

    def to_dict(self) -> dict:
        """For JSON (the print server's web page): sizes and boxes in mm."""
        return {
            "width_mm": round(self.width_mm, DIGITS),
            "height_mm": round(self.height_mm, DIGITS),
            "layout": self.layout,
            "elements": [b.to_dict() for b in self.boxes],
        }


def measure(
    label: Label,
    values: Values | None = None,
    columns: Mapping[str, str] | None = None,
    dpi: float = PRINT_DPI,
    safe_mm: float = DEFAULT_SAFE_MM,
    **options,
) -> Geometry:
    """The label rendered (its variables and a data row's columns filled
    in, as printed) and its geometry. options: those of render.render();
    the window and the server have a render already: Geometry.of() it."""
    filled, _warnings = expand_label(label, values, columns)
    return Geometry.of(label, render(filled, dpi=dpi, safe_mm=safe_mm, **options))


def moved(box: MmBox, dx: float, dy: float) -> MmBox:
    """A box moved by dx, dy mm (a dragged element's, until it is rendered)."""
    return box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy


# --- snapping -------------------------------------------------------------------

EPSILON = 1e-6  # mm: a box's line lies on a guide


@dataclass(frozen=True)
class Guide:
    """A line a moving box snaps to: on axis "x" a vertical line at x =
    position, on "y" a horizontal one."""

    axis: str
    position: float
    source: str  # "label" (an edge or its middle), "element" or "grid"
    index: int | None = None  # the element's, for "element"


@dataclass(frozen=True)
class Snap:
    """How far to move a box so it snaps, and the guides it then lies on
    (to draw while dragging)."""

    dx: float
    dy: float
    guides: tuple[Guide, ...]


def guides(geometry: Geometry, exclude: Iterable[int] = ()) -> list[Guide]:
    """The lines to snap to: the label's edges and middle, and the edges and
    middles of its elements but those excluded (the ones being moved)."""
    skip = set(exclude)
    found = [
        Guide(axis, position, "label")
        for axis, size in (("x", geometry.width_mm), ("y", geometry.height_mm))
        for position in (0.0, size / 2, size)
    ]
    for b in geometry.boxes:
        if b.index in skip:
            continue
        for axis, lines in (("x", _lines(b.left, b.right)), ("y", _lines(b.top, b.bottom))):
            found += [Guide(axis, position, "element", b.index) for position in lines]
    return found


def _lines(start: float, end: float) -> tuple[float, float, float]:
    return start, (start + end) / 2, end


def snap(
    box: MmBox,
    geometry: Geometry,
    exclude: Iterable[int] = (),
    tolerance: float = 1.0,
    grid: float = 0.0,
) -> Snap:
    """Where a moving box (the dragged elements' bounds, moved()) snaps:
    on each axis the nearest guide (guides()) one of its edges or its
    middle is within `tolerance` mm of, or a multiple of `grid` mm (0: no
    grid) for one of its edges; a guide wins over the grid at the same
    distance. To snap only one edge (resizing), give a box of no width
    or height at that edge."""
    found = guides(geometry, exclude)
    dx, x_guides = _snap_axis("x", _lines(box[0], box[2]), found, tolerance, grid)
    dy, y_guides = _snap_axis("y", _lines(box[1], box[3]), found, tolerance, grid)
    return Snap(dx, dy, x_guides + y_guides)


def _snap_axis(
    axis: str, lines: tuple[float, float, float], found: list[Guide], tolerance: float, grid: float
) -> tuple[float, tuple[Guide, ...]]:
    """The offset along one axis, and the guides it puts the lines on."""
    candidates = [g for g in found if g.axis == axis]
    edges = (lines[0], lines[2])
    # (distance, a grid line, offset): the nearest first, a guide before the grid
    options = [
        (abs(g.position - line), 0, g.position - line) for g in candidates for line in lines
    ]
    if grid > 0:
        options += [(abs(_on_grid(e, grid) - e), 1, _on_grid(e, grid) - e) for e in edges]
    near = [option for option in options if option[0] <= tolerance]
    if not near:
        return 0.0, ()
    offset = min(near)[2]
    on = [
        g
        for g in candidates
        if any(abs(g.position - (line + offset)) <= EPSILON for line in lines)
    ]
    if grid > 0:
        on += [
            Guide(axis, _on_grid(e + offset, grid), "grid")
            for e in edges
            if abs(_on_grid(e + offset, grid) - (e + offset)) <= EPSILON
        ]
    return offset, tuple(dict.fromkeys(on))


def _on_grid(value: float, grid: float) -> float:
    return round(value / grid) * grid


# --- edits ----------------------------------------------------------------------
#
# Each edit takes a label and gives a new one, the label it was given stays
# as it is (the window's undo keeps copies of labels, tavletta.history); an
# edit that changes nothing gives the label itself. The elements are named
# by their index in label.elements. Every changed element is checked as a
# label file's (model.validate_element): a position is never negative.

POSITION_DIGITS = 2  # positions in mm, rounded as freelayout.adopt() rounds them


class NotFree(ValueError):
    """An edit of the elements' places on a label of the flowing layout."""


def move(label: Label, indices: Collection[int], dx: float, dy: float) -> Label:
    """The elements moved by dx, dy mm, together: no further left or up
    than the one nearest the edge can go (a position is never negative),
    so they keep their places to one another."""
    _check_free(label)
    chosen = _chosen(label, indices)
    _check_finite(dx, dy)
    if not chosen:
        return label
    elements = [label.elements[i] for i in chosen]
    dx = _within(dx, [e.x_mm for e in elements])
    dy = _within(dy, [e.y_mm for e in elements])
    return _moved(label, dict.fromkeys(chosen, (dx, dy)))


def _within(delta: float, positions: list[float]) -> float:
    """A move no further than every position can go (0 … MAX_SIZE_MM)."""
    return min(max(delta, -min(positions)), MAX_SIZE_MM - max(positions))


ALIGNMENTS = ("left", "center", "right", "top", "middle", "bottom")


def align(label: Label, geometry: Geometry, indices: Collection[int], edge: str) -> Label:
    """The elements' boxes lined up at one edge or middle (ALIGNMENTS:
    "center" along the label, "middle" across it): of the box around them
    all, or of the label for one element alone."""
    if edge not in ALIGNMENTS:
        raise ValueError("unknown alignment %r" % edge)
    _check_free(label, geometry)
    chosen = _chosen(label, indices)
    if not chosen:
        return label
    target = (
        geometry.bounds(chosen)
        if len(chosen) > 1
        else (0.0, 0.0, geometry.width_mm, geometry.height_mm)
    )
    offsets = {}
    for index in chosen:
        box = geometry.boxes[index]
        offsets[index] = {
            "left": (target[0] - box.left, 0.0),
            "center": ((target[0] + target[2] - box.left - box.right) / 2, 0.0),
            "right": (target[2] - box.right, 0.0),
            "top": (0.0, target[1] - box.top),
            "middle": (0.0, (target[1] + target[3] - box.top - box.bottom) / 2),
            "bottom": (0.0, target[3] - box.bottom),
        }[edge]
    return _moved(label, offsets)


def distribute(label: Label, geometry: Geometry, indices: Collection[int], axis: str) -> Label:
    """The same space between neighbouring boxes, along the label (axis "x")
    or across it ("y"): the first and the last keep their places. Fewer
    than three elements stay as they are."""
    if axis not in ("x", "y"):
        raise ValueError("unknown axis %r" % axis)
    _check_free(label, geometry)
    chosen = _chosen(label, indices)
    if len(chosen) < 3:
        return label
    across = axis == "y"

    def span(index: int) -> tuple[float, float]:
        box = geometry.boxes[index].box
        return (box[1], box[3]) if across else (box[0], box[2])

    order = sorted(chosen, key=lambda i: (span(i)[0], i))
    sizes = sum(span(i)[1] - span(i)[0] for i in order)
    gap = (span(order[-1])[1] - span(order[0])[0] - sizes) / (len(order) - 1)
    offsets, start = {}, span(order[0])[0]
    for index in order:
        shift = start - span(index)[0]
        offsets[index] = (0.0, shift) if across else (shift, 0.0)
        start += span(index)[1] - span(index)[0] + gap
    return _moved(label, offsets)


def _moved(label: Label, offsets: Mapping[int, tuple[float, float]]) -> Label:
    changes = {}
    for index, (dx, dy) in offsets.items():
        element = label.elements[index]
        changes[index] = {
            "x_mm": round(element.x_mm + dx, POSITION_DIGITS),
            "y_mm": round(element.y_mm + dy, POSITION_DIGITS),
        }
    return _changed(label, changes)


def _changed(label: Label, changes: Mapping[int, Mapping[str, object]]) -> Label:
    """A copy of the label with some fields of some elements changed, each
    changed element checked (validate_element); the label itself if
    nothing changes."""
    if all(
        getattr(label.elements[i], name) == value
        for i, fields in changes.items()
        for name, value in fields.items()
    ):
        return label
    new = copy.deepcopy(label)  # the embedded files' text is shared, not copied
    for index, fields in changes.items():
        element = new.elements[index]
        for name, value in fields.items():
            setattr(element, name, value)
        validate_element(element)
    return new


def _check_free(label: Label, geometry: Geometry | None = None) -> None:
    if label.layout != "free":
        raise NotFree("the elements of the flowing layout have no places of their own")
    if geometry is not None and len(geometry.boxes) != len(label.elements):
        raise ValueError("the geometry is not of this label")


def _chosen(label: Label, indices: Collection[int]) -> list[int]:
    """The indices in order, each once (IndexError: no such element)."""
    chosen = sorted(set(indices))
    if chosen and not 0 <= chosen[0] <= chosen[-1] < len(label.elements):
        raise IndexError("no such element")
    return chosen


def _check_finite(*values: float) -> None:
    if not all(math.isfinite(v) for v in values):
        raise ValueError("not a finite number")
