"""What the mouse editor draws over the preview, and the arithmetic of its
pixels: label millimetres to the widget's pixels and back (View), the
handles around a selected element, a box resized by one of them.

The canvas lies over the preview's picture and draws only the overlay
(hover, selection, handles, guides, a rubber band, a dragged element's
pixels): no render per mouse event. Everything is drawn as render nodes
(rectangles, the preview's own texture, Pango text), no Cairo surface the
size of the widget. Coordinates are the widget's logical pixels: on a
display of scale 2 GTK draws them with twice the device pixels.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")

from gi.repository import Gdk, Graphene, Gtk  # noqa: E402

from tavletta.editing import Guide  # noqa: E402
from tavletta.render import MmBox  # noqa: E402

HANDLE_PX = 8  # a handle as drawn
HANDLE_HOVER_PX = 10  # the one under the pointer
HIT_PX = 24  # a handle's target, mouse and pen (WCAG 2.5.8)
TOUCH_HIT_PX = 44  # a finger's
LEAST_MM = 0.1  # a box resized no smaller

# the handles: where on the box, as fractions of its width and height
HANDLES = {
    "nw": (0.0, 0.0),
    "n": (0.5, 0.0),
    "ne": (1.0, 0.0),
    "e": (1.0, 0.5),
    "se": (1.0, 1.0),
    "s": (0.5, 1.0),
    "sw": (0.0, 1.0),
    "w": (0.0, 0.5),
}
CURSORS = {
    "nw": "nwse-resize",
    "se": "nwse-resize",
    "ne": "nesw-resize",
    "sw": "nesw-resize",
    "n": "ns-resize",
    "s": "ns-resize",
    "e": "ew-resize",
    "w": "ew-resize",
}


@dataclass(frozen=True)
class View:
    """Where the label lies in the widget: its top left corner at ox, oy
    pixels, sx and sy pixels per mm (the zoom; both the same but for the
    rounding of the picture to whole pixels)."""

    ox: float
    oy: float
    sx: float
    sy: float

    def to_mm(self, x: float, y: float) -> tuple[float, float]:
        return (x - self.ox) / self.sx, (y - self.oy) / self.sy

    def to_px(self, x: float, y: float) -> tuple[float, float]:
        return self.ox + x * self.sx, self.oy + y * self.sy

    def box_px(self, box: MmBox) -> tuple[float, float, float, float]:
        left, top = self.to_px(box[0], box[1])
        right, bottom = self.to_px(box[2], box[3])
        return left, top, right, bottom

    def mm(self, px: float) -> float:
        """A distance of some pixels in mm (a snapping radius, a slop)."""
        return px / self.sx


def view_of(picture: Gtk.Widget, canvas: Gtk.Widget, size_mm) -> View | None:
    """The view of the label the picture shows (size_mm: its length and
    height), in the canvas's pixels; None while nothing is shown."""
    if not size_mm or min(size_mm) <= 0 or picture.get_paintable() is None:
        return None
    ok, bounds = picture.compute_bounds(canvas)
    if not ok:
        return None
    width, height = bounds.get_width(), bounds.get_height()
    if width <= 0 or height <= 0:
        return None
    return View(bounds.get_x(), bounds.get_y(), width / size_mm[0], height / size_mm[1])


def handle_at(view: View, box: MmBox, x: float, y: float, hit: float) -> str | None:
    """The handle of a box under a point (pixels), if any: within `hit`
    pixels around it. On a box too small for its handles' targets, inside
    the box only the handles as drawn are: the rest of it is the box's
    own (to move it)."""
    left, top, right, bottom = view.box_px(box)
    small = right - left < 2 * hit or bottom - top < 2 * hit
    inside = left < x < right and top < y < bottom
    reach = HANDLE_PX / 2 + 1 if small and inside else hit / 2
    best = None
    for name, (fx, fy) in HANDLES.items():
        hx, hy = left + fx * (right - left), top + fy * (bottom - top)
        distance = max(abs(x - hx), abs(y - hy))
        if distance <= reach and (best is None or distance < best[0]):
            best = (distance, name)
    return best[1] if best else None


def resized(box: MmBox, handle: str, dx: float, dy: float, proportional: bool) -> MmBox:
    """The box with one handle dragged by dx, dy mm: its opposite edge (or
    corner) stays; never smaller than LEAST_MM. proportional: its width and
    height scaled alike, by the side that changed more."""
    left, top, right, bottom = box
    fx, fy = HANDLES[handle]
    if fx == 0.0:
        left = min(left + dx, right - LEAST_MM)
    elif fx == 1.0:
        right = max(right + dx, left + LEAST_MM)
    if fy == 0.0:
        top = min(top + dy, bottom - LEAST_MM)
    elif fy == 1.0:
        bottom = max(bottom + dy, top + LEAST_MM)
    if not proportional:
        return left, top, right, bottom
    return _proportional(box, (left, top, right, bottom), fx, fy)


def _proportional(old: MmBox, new: MmBox, fx: float, fy: float) -> MmBox:
    """The new box scaled to the old one's proportions, from the edge (or
    corner) opposite the handle; an edge's handle keeps the top or left."""
    width, height = old[2] - old[0], old[3] - old[1]
    if width <= 0 or height <= 0:
        return new
    sx, sy = (new[2] - new[0]) / width, (new[3] - new[1]) / height
    s = _scale(sx, sy, fx, fy)
    width, height = width * s, height * s
    left = new[2] - width if fx == 0.0 else new[0]
    top = new[3] - height if fy == 0.0 else new[1]
    return left, top, left + width, top + height


def _scale(sx: float, sy: float, fx: float, fy: float) -> float:
    """A side's handle scales by its own side, a corner's by the side that
    changed more."""
    if fy == 0.5:
        return sx
    if fx == 0.5:
        return sy
    return sx if abs(math.log(sx)) >= abs(math.log(sy)) else sy


def dragged_edges(box: MmBox, handle: str) -> tuple[float | None, float | None]:
    """The edges a handle drags: x of the vertical one, y of the
    horizontal one (None: the handle drags none on that axis)."""
    fx, fy = HANDLES[handle]
    x = box[0] if fx == 0.0 else box[2] if fx == 1.0 else None
    y = box[1] if fy == 0.0 else box[3] if fy == 1.0 else None
    return x, y


def constrained(dx: float, dy: float) -> tuple[float, float]:
    """A drag kept to one axis: the one it went further along."""
    return (dx, 0.0) if abs(dx) >= abs(dy) else (0.0, dy)


# --- the overlay -------------------------------------------------------------------


@dataclass(frozen=True)
class Scene:
    """What the canvas draws over the preview, in label mm."""

    size_mm: tuple[float, float] = (0.0, 0.0)  # the label's
    hover: MmBox | None = None
    selected: tuple[MmBox, ...] = ()
    handles: MmBox | None = None  # the box whose handles show
    active_handle: str | None = None  # under the pointer, or dragged
    band: MmBox | None = None  # a rubber band
    guides: tuple[Guide, ...] = ()
    grid_mm: float = 0.0  # drawn while dragging with the grid on
    # dragged elements: their pixels where the preview shows them, drawn
    # at the box they are going to (moved or resized) until the next render
    ghosts: tuple[tuple[MmBox, MmBox], ...] = ()
    tag: str = ""  # position or size while dragging, next to the box
    tag_box: MmBox | None = None


@dataclass
class Colours:
    accent: Gdk.RGBA = field(default_factory=lambda: rgba("#3584e4"))
    guide: Gdk.RGBA = field(default_factory=lambda: rgba("#e5254f"))
    line: float = 1.0  # line width, px (2 in high contrast)


def rgba(spec: str, alpha: float = 1.0) -> Gdk.RGBA:
    colour = Gdk.RGBA()
    colour.parse(spec)
    colour.alpha *= alpha
    return colour


def _rect(x: float, y: float, width: float, height: float) -> Graphene.Rect:
    return Graphene.Rect().init(x, y, width, height)


class Canvas(Gtk.Widget):
    """The overlay over the preview's picture: draws a Scene; the editor
    (ui/editor.py) handles its input."""

    def __init__(self, picture: Gtk.Picture):
        super().__init__(
            focusable=True,
            focus_on_click=True,
            hexpand=True,
            vexpand=True,
            accessible_role=Gtk.AccessibleRole.GROUP,
        )
        self.picture = picture
        self.scene = Scene()
        self.colours = Colours()

    def view(self) -> View | None:
        return view_of(self.picture, self, self.scene.size_mm)

    def show(self, scene: Scene) -> None:
        self.scene = scene
        self.queue_draw()

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        view = self.view()
        if view is None:
            return
        scene, colours = self.scene, self.colours
        self._ghosts(snapshot, view, scene)
        if scene.grid_mm > 0 and scene.ghosts:
            self._grid(snapshot, view, scene)
        for guide in scene.guides:
            self._guide(snapshot, view, scene, guide)
        if scene.hover is not None:
            _dashed(snapshot, view.box_px(scene.hover), rgba_alpha(colours.accent, 0.6))
        for box in scene.selected:
            _outline(snapshot, view.box_px(box), colours.accent, colours.line)
        if scene.handles is not None:
            self._handles(snapshot, view, scene)
        if scene.band is not None:
            self._band(snapshot, view, scene.band)
        if scene.tag and scene.tag_box is not None:
            self._tag(snapshot, view, scene)
        if self.has_visible_focus():
            label = view.box_px((0.0, 0.0, *scene.size_mm))
            grown = (label[0] - 4, label[1] - 4, label[2] + 4, label[3] + 4)
            _outline(snapshot, grown, colours.accent, 2.0)

    def _ghosts(self, snapshot: Gtk.Snapshot, view: View, scene: Scene) -> None:
        """Dragged elements: their place dimmed, their pixels at the new box."""
        texture = self.picture.get_paintable()
        if not scene.ghosts or not isinstance(texture, Gdk.Texture):
            return
        label = view.box_px((0.0, 0.0, *scene.size_mm))
        for source, _target in scene.ghosts:
            left, top, right, bottom = view.box_px(source)
            snapshot.append_color(
                rgba("#ffffff", 0.7), _rect(left, top, right - left, bottom - top)
            )
        for source, target in scene.ghosts:
            src, dst = view.box_px(source), view.box_px(target)
            if src[2] <= src[0] or src[3] <= src[1]:
                continue
            kx, ky = (dst[2] - dst[0]) / (src[2] - src[0]), (dst[3] - dst[1]) / (src[3] - src[1])
            whole = _rect(
                dst[0] + (label[0] - src[0]) * kx,
                dst[1] + (label[1] - src[1]) * ky,
                (label[2] - label[0]) * kx,
                (label[3] - label[1]) * ky,
            )
            snapshot.push_clip(_rect(dst[0], dst[1], dst[2] - dst[0], dst[3] - dst[1]))
            snapshot.append_texture(texture, whole)
            snapshot.pop()

    def _grid(self, snapshot: Gtk.Snapshot, view: View, scene: Scene) -> None:
        width, height = scene.size_mm
        step = scene.grid_mm
        if step * view.sx < 4:  # denser than every 4 px: noise
            return
        colour = rgba_alpha(self.colours.guide, 0.15)
        left, top, right, bottom = view.box_px((0.0, 0.0, width, height))
        for i in range(int(width / step) + 1):
            x = round(view.to_px(i * step, 0)[0])
            snapshot.append_color(colour, _rect(x, top, 1, bottom - top))
        for i in range(int(height / step) + 1):
            y = round(view.to_px(0, i * step)[1])
            snapshot.append_color(colour, _rect(left, y, right - left, 1))

    def _guide(self, snapshot: Gtk.Snapshot, view: View, scene: Scene, guide: Guide) -> None:
        """A guide the dragged box lies on, across the whole label and a
        little beyond; the grid's dashed."""
        width = self.colours.line
        left, top, right, bottom = view.box_px((0.0, 0.0, *scene.size_mm))
        line: tuple[float, float, float, float]
        if guide.axis == "x":
            x = round(view.to_px(guide.position, 0)[0])
            line = (x, top - 12, x + width, bottom + 12)
        else:
            y = round(view.to_px(0, guide.position)[1])
            line = (left - 12, y, right + 12, y + width)
        if guide.source == "grid":
            _dashes(snapshot, line, self.colours.guide)
        else:
            snapshot.append_color(
                self.colours.guide, _rect(line[0], line[1], line[2] - line[0], line[3] - line[1])
            )

    def _handles(self, snapshot: Gtk.Snapshot, view: View, scene: Scene) -> None:
        assert scene.handles is not None
        left, top, right, bottom = view.box_px(scene.handles)
        white = rgba("#ffffff")
        for name, (fx, fy) in HANDLES.items():
            size = HANDLE_HOVER_PX if name == scene.active_handle else HANDLE_PX
            x = round(left + fx * (right - left) - size / 2)
            y = round(top + fy * (bottom - top) - size / 2)
            snapshot.append_color(white, _rect(x, y, size, size))
            _outline(snapshot, (x, y, x + size, y + size), self.colours.accent, 1.0)

    def _band(self, snapshot: Gtk.Snapshot, view: View, band: MmBox) -> None:
        left, top, right, bottom = view.box_px(band)
        left, right = sorted((left, right))
        top, bottom = sorted((top, bottom))
        accent = self.colours.accent
        snapshot.append_color(
            rgba_alpha(accent, 0.15), _rect(left, top, right - left, bottom - top)
        )
        _outline(snapshot, (left, top, right, bottom), accent, self.colours.line)

    def _tag(self, snapshot: Gtk.Snapshot, view: View, scene: Scene) -> None:
        """The position or size, under the box (above it at the bottom)."""
        assert scene.tag_box is not None
        layout = self.create_pango_layout(scene.tag)
        width, height = (v / 1024 for v in layout.get_size())  # Pango units
        left, top, _right, bottom = view.box_px(scene.tag_box)
        x = max(0.0, left)
        y = bottom + 6 if bottom + 6 + height + 4 <= self.get_height() else top - height - 10
        point = Graphene.Point()
        point.init(x + 4, y + 2)
        snapshot.append_color(rgba("#000000", 0.75), _rect(x, y, width + 8, height + 4))
        snapshot.save()
        snapshot.translate(point)
        snapshot.append_layout(layout, rgba("#ffffff"))
        snapshot.restore()


def rgba_alpha(colour: Gdk.RGBA, alpha: float) -> Gdk.RGBA:
    copy = colour.copy()
    copy.alpha = colour.alpha * alpha
    return copy


def _outline(snapshot: Gtk.Snapshot, box, colour: Gdk.RGBA, width: float) -> None:
    """A box's outline, its lines on whole pixels, drawn around it."""
    left, top = math.floor(box[0]) - width, math.floor(box[1]) - width
    right, bottom = math.ceil(box[2]), math.ceil(box[3])
    for x, y, w, h in (
        (left, top, right - left + width, width),
        (left, bottom, right - left + width, width),
        (left, top, width, bottom - top),
        (right, top, width, bottom - top + width),
    ):
        snapshot.append_color(colour, _rect(x, y, w, h))


def _dashed(snapshot: Gtk.Snapshot, box, colour: Gdk.RGBA) -> None:
    """A dashed outline (the hover's: not told from the selection by colour alone)."""
    left, top = math.floor(box[0]) - 1, math.floor(box[1]) - 1
    right, bottom = math.ceil(box[2]), math.ceil(box[3])
    for line in (
        (left, top, right + 1, top + 1),
        (left, bottom, right + 1, bottom + 1),
        (left, top, left + 1, bottom + 1),
        (right, top, right + 1, bottom + 1),
    ):
        _dashes(snapshot, line, colour)


DASH_PX = 4


def _dashes(snapshot: Gtk.Snapshot, line, colour: Gdk.RGBA) -> None:
    """A dashed line: a thin box (left, top, right, bottom) in dashes along
    its longer side."""
    left, top, right, bottom = line
    along = right - left >= bottom - top
    length = (right - left) if along else (bottom - top)
    start = 0.0
    while start < length:
        dash = min(DASH_PX, length - start)
        if along:
            rect = _rect(left + start, top, dash, bottom - top)
        else:
            rect = _rect(left, top + start, right - left, dash)
        snapshot.append_color(colour, rect)
        start += 2 * DASH_PX
