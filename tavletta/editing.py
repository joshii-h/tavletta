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

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from tavletta.model import Label
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
