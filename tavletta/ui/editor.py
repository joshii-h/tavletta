"""The mouse editor of the preview: selecting elements by click and rubber
band, moving them by drag or arrow keys with snapping, resizing one by its
handles, aligning and distributing them (the "Arrange" menu), on top of
tavletta.editing (docs/editing.md).

Moving needs the free layout; in the flowing layout the elements can be
selected and highlighted, and a drag or an arrow key says how to switch.

Each edit is one step of the window's undo (editing.Editor on the window's
History, shared with the sidebar's changes): a whole drag, a run of arrow
keys (joined as typing is). While dragging nothing is rendered: the canvas
draws the dragged elements' pixels at their new place (ui/canvas.py); the
preview renders when the drag ends, and until that render is shown the
elements are drawn where they landed.

The selection is kept by the elements' identities (model.element_id), so
it survives undo, reordering and the sidebar being rebuilt; the sidebar
shows it (the last element chosen is the open one, its row expanded).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from tavletta import editing, freelayout  # noqa: E402
from tavletta.model import (  # noqa: E402
    CodeElement,
    ImageElement,
    Label,
    PageElement,
    SymbolElement,
    TextElement,
    element_id,
)
from tavletta.render import MmBox  # noqa: E402
from tavletta.ui import canvas  # noqa: E402
from tavletta.ui import freelayout as free_rows  # noqa: E402
from tavletta.ui.canvas import Scene, View  # noqa: E402
from tavletta.ui.i18n import _  # noqa: E402
from tavletta.ui.texts import element_title  # noqa: E402

if TYPE_CHECKING:
    from tavletta.ui.window import Window

DRAG_THRESHOLD_PX = 4  # a press that moves less is a click
SNAP_PX = 6  # snapping radius on the screen, whatever the zoom
SLOP_PX = 3  # a thin line is hit this close
NUDGE_MM = 0.1  # an arrow key
NUDGE_LARGE_MM = 1.0  # Shift and an arrow key
HINT_SECONDS = 8.0  # the free layout's hint at most once in so long

ARROWS = {
    Gdk.KEY_Left: (-1, 0),
    Gdk.KEY_KP_Left: (-1, 0),
    Gdk.KEY_Right: (1, 0),
    Gdk.KEY_KP_Right: (1, 0),
    Gdk.KEY_Up: (0, -1),
    Gdk.KEY_KP_Up: (0, -1),
    Gdk.KEY_Down: (0, 1),
    Gdk.KEY_KP_Down: (0, 1),
}
CONTROL = Gdk.ModifierType.CONTROL_MASK
SHIFT = Gdk.ModifierType.SHIFT_MASK
ALT = Gdk.ModifierType.ALT_MASK
MODIFIERS = CONTROL | SHIFT | ALT | Gdk.ModifierType.SUPER_MASK
# held while dragging: no snapping (Alt as other editors have it; Ctrl
# too, where the desktop takes Alt and a drag for moving the window)
NO_SNAP = ALT | CONTROL
NO_MODIFIERS = Gdk.ModifierType(0)

# the snapping grid's choices: mm for millimetres, a fraction of an inch for inches
GRIDS_MM = (0.5, 1.0, 2.0, 5.0)
GRIDS_IN = ((1, 32), (1, 16), (1, 8), (1, 4))


def keeps_proportions(element) -> bool:
    """Whether a resize scales the element alike in both directions
    (editing.resize): a text, an image, a symbol, a 2D code."""
    if isinstance(element, CodeElement):
        return freelayout.two_dimensional(element)
    return isinstance(element, (TextElement, ImageElement, SymbolElement))


def resizable(element) -> bool:
    """A page fills the label from its position on: it has no handles."""
    return not isinstance(element, PageElement)


# --- a gesture ----------------------------------------------------------------------


@dataclass
class Press:
    """A press on the canvas, not yet a drag."""

    x: float  # pixels
    y: float
    view: View
    geometry: editing.Geometry
    index: int | None  # the element under it, the topmost
    handle: str | None  # the handle under it
    additive: bool  # Shift or Ctrl: added to the selection


@dataclass
class Move:
    """Elements dragged: by dx, dy mm (snapped) from where they were."""

    press: Press
    indices: list[int]
    start: MmBox  # their bounds
    least: tuple[float, float]  # how far left and up they can go (editing.move)
    dx: float = 0.0
    dy: float = 0.0
    guides: tuple[editing.Guide, ...] = ()

    def update(self, ox: float, oy: float, state, snapping: Snapping) -> None:
        view = self.press.view
        if state & SHIFT:
            ox, oy = canvas.constrained(ox, oy)
        dx, dy = view.mm(ox), view.mm(oy)
        self.guides = ()
        if snapping.on(state):
            snapped = editing.snap(
                editing.moved(self.start, dx, dy),
                self.press.geometry,
                exclude=self.indices,
                tolerance=view.mm(SNAP_PX),
                grid=snapping.grid_mm,
            )
            # an axis Shift holds stays where it is
            dx += snapped.dx if ox else 0.0
            dy += snapped.dy if oy else 0.0
            self.guides = snapped.guides
        # no further left or up than editing.move() goes: a guide beyond is not reached
        self.dx, self.dy = max(dx, -self.least[0]), max(dy, -self.least[1])
        box = editing.moved(self.start, self.dx, self.dy)
        self.guides = tuple(g for g in self.guides if _on_box(g, box))

    def boxes(self) -> dict[int, MmBox]:
        """Where the dragged elements' boxes are now."""
        boxes = self.press.geometry.boxes
        return {i: editing.moved(boxes[i].box, self.dx, self.dy) for i in self.indices}

    def finish(self, label: Label) -> Label:
        return editing.move(label, self.indices, self.dx, self.dy)


@dataclass
class Resize:
    """An element's handle dragged: its new box."""

    press: Press
    index: int
    handle: str
    start: MmBox
    proportional: bool
    box: MmBox = (0.0, 0.0, 0.0, 0.0)
    guides: tuple[editing.Guide, ...] = ()

    def __post_init__(self) -> None:
        self.box = self.start

    def update(self, ox: float, oy: float, state, snapping: Snapping) -> None:
        view = self.press.view
        dx, dy = view.mm(ox), view.mm(oy)
        keep = self.proportional or bool(state & SHIFT)
        self.guides = ()
        if snapping.on(state):
            x, y = self._snapping_edges(dx, dy, keep)
            point = (x or 0.0, y or 0.0, x or 0.0, y or 0.0)  # snaps the dragged edges alone
            snapped = editing.snap(
                point,
                self.press.geometry,
                exclude=[self.index],
                tolerance=view.mm(SNAP_PX),
                grid=snapping.grid_mm,
            )
            dx += snapped.dx if x is not None else 0.0
            dy += snapped.dy if y is not None else 0.0
            axes = {"x"} if x is not None else set()
            axes |= {"y"} if y is not None else set()
            self.guides = tuple(g for g in snapped.guides if g.axis in axes)
        self.box = canvas.resized(self.start, self.handle, dx, dy, keep)
        self.guides = tuple(g for g in self.guides if _on_box(g, self.box))

    def _snapping_edges(self, dx: float, dy: float, keep: bool) -> tuple[float | None, ...]:
        """The dragged edges that snap: in proportion only the one of the
        side that leads (the other follows it, and would jump between)."""
        free = canvas.resized(self.start, self.handle, dx, dy, False)
        x, y = canvas.dragged_edges(free, self.handle)
        if keep and x is not None and y is not None:
            sx = (free[2] - free[0]) / max(self.start[2] - self.start[0], 1e-9)
            sy = (free[3] - free[1]) / max(self.start[3] - self.start[1], 1e-9)
            if abs(math.log(sx)) >= abs(math.log(sy)):
                y = None
            else:
                x = None
        return x, y

    def boxes(self) -> dict[int, MmBox]:
        return {self.index: self.box}

    def finish(self, label: Label) -> Label:
        return editing.resize(label, self.press.geometry, self.index, self.box)


@dataclass
class Band:
    """A rubber band: the elements it touches (with Alt: holds) are selected."""

    press: Press
    base: list[int]  # the selection it adds to (element ids)
    rect: MmBox = (0.0, 0.0, 0.0, 0.0)
    within: list[int] = field(default_factory=list)  # indices
    guides: tuple[editing.Guide, ...] = ()

    def update(self, ox: float, oy: float, state, _snapping: Snapping) -> None:
        view, press = self.press.view, self.press
        x0, y0 = view.to_mm(press.x, press.y)
        x1, y1 = view.to_mm(press.x + ox, press.y + oy)
        self.rect = (x0, y0, x1, y1)
        self.within = press.geometry.within(self.rect, touching=not state & ALT)

    def boxes(self) -> dict[int, MmBox]:
        return {}


Drag = Move | Resize | Band


@dataclass
class Snapping:
    """Whether dragged elements snap, and to which grid (mm, 0: none)."""

    enabled: bool
    grid_mm: float

    def on(self, state) -> bool:
        """Snapping on, and not turned off for now (Alt or Ctrl held)."""
        return self.enabled and not state & NO_SNAP


def _on_box(guide: editing.Guide, box: MmBox) -> bool:
    """Whether a guide is one of the box's lines (edges or middle)."""
    lines = (
        (box[0], box[2], (box[0] + box[2]) / 2)
        if guide.axis == "x"
        else (box[1], box[3], (box[1] + box[3]) / 2)
    )
    return any(abs(guide.position - v) <= 1e-6 for v in lines)


# --- the editor ---------------------------------------------------------------------


class PreviewEditor:
    """The preview's canvas made an editor of the window's label."""

    def __init__(self, window: Window):
        self.window = window
        self.canvas = window.preview.canvas
        self.selection: list[int] = []  # element ids, the one chosen last at the end
        self._hover: int | None = None  # index
        self._handle: str | None = None  # under the pointer
        self._press: Press | None = None
        self._drag: Drag | None = None
        # edits not yet rendered: element id -> where its box went
        self._landed: dict[int, MmBox] = {}
        self._landed_after = 0  # a render newer than this generation shows them
        self._committing = False
        self._syncing = False  # the sidebar follows the selection
        self._hinted = -HINT_SECONDS
        self._toast: Adw.Toast | None = None  # undo of a removal, while it is the last step
        self._geometry: editing.Geometry | None = None
        self._geometry_of: tuple = ()  # the render, label, elements and landing it is of
        self._install()

    def _install(self) -> None:
        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self._on_begin)
        drag.connect("drag-update", self._on_update)
        drag.connect("drag-end", self._on_end)
        drag.connect("cancel", lambda _g, _s: self._cancel())
        self.canvas.add_controller(drag)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", lambda _c: self._hovered(None, None))
        self.canvas.add_controller(motion)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.canvas.add_controller(keys)
        focus = Gtk.EventControllerFocus()  # the focus ring
        focus.connect("enter", lambda _c: self.canvas.queue_draw())
        focus.connect("leave", lambda _c: self.canvas.queue_draw())
        self.canvas.add_controller(focus)
        self._update_colours()
        style = Adw.StyleManager.get_default()
        for name in ("dark", "high-contrast", "accent-color"):
            if style.find_property(name):  # accent-color: libadwaita 1.6
                style.connect("notify::" + name, lambda *_a: self._update_colours())

    # --- what the window tells ---------------------------------------------------------

    @property
    def free(self) -> bool:
        return self.window.label.layout == "free"

    def geometry(self) -> editing.Geometry | None:
        """The elements' boxes as the preview shows them, those edited since
        where they landed; None while the preview shows other elements (one
        added, removed or moved up or down, not rendered yet)."""
        window = self.window
        ids = tuple(element_id(e) for e in window.label.elements)
        of = (window.rendered, window.label, ids, tuple(self._landed.items()))
        cached = self._geometry_of
        if not cached or cached[0] is not of[0] or cached[1] is not of[1] or cached[2:] != of[2:]:
            self._geometry_of, self._geometry = of, self._measure(ids)
        return self._geometry

    def _shown(self, ids: tuple[int, ...]) -> bool:
        """Whether the preview shows these elements (the render is theirs)."""
        return self.window.rendered is not None and self.window.rendered_ids == ids

    def _measure(self, ids: tuple[int, ...]) -> editing.Geometry | None:
        if not self._shown(ids):
            return None
        rendered, label = self.window.rendered, self.window.label
        assert rendered is not None
        shown = editing.Geometry.of(label, rendered)
        if not self._landed:
            return shown
        boxes = tuple(
            replace(b, left=to[0], top=to[1], right=to[2], bottom=to[3])
            if (to := self._landed.get(ids[b.index]))
            else b
            for b in shown.boxes
        )
        return replace(shown, boxes=boxes)

    def label_changed(self) -> None:
        """The window's label changed (the sidebar, undo, another file):
        the selection keeps the elements still there; where edited ones
        landed is the next render's to say, unless the editor made it."""
        if not self._committing:
            self._landed.clear()
            self._cancel()
        ids = {element_id(e) for e in self.window.label.elements}
        self.selection = [i for i in self.selection if i in ids]
        self._dismiss_toast()
        self.update_actions()
        self._hovered(None, None, force=True)

    def rendered(self, generation: int) -> None:
        """A render is shown: one made after the last edit shows where the
        edited elements are."""
        if generation > self._landed_after:
            self._landed.clear()
        self._update()

    # --- selection ---------------------------------------------------------------------

    def indices(self) -> list[int]:
        """The selected elements' indices, in the label's order."""
        chosen = set(self.selection)
        return [i for i, e in enumerate(self.window.label.elements) if element_id(e) in chosen]

    def primary(self) -> int | None:
        """The element chosen last: the sidebar's open one."""
        ids = [element_id(e) for e in self.window.label.elements]
        for ident in reversed(self.selection):
            if ident in ids:
                return ids.index(ident)
        return None

    def select(self, indices: Iterable[int], sync: bool = True) -> None:
        """Select these elements (the last one the open element)."""
        elements = self.window.label.elements
        self.selection = list(dict.fromkeys(element_id(elements[i]) for i in indices))
        self._selection_changed(sync)

    def _toggle(self, index: int) -> None:
        ident = element_id(self.window.label.elements[index])
        if ident in self.selection:
            self.selection.remove(ident)
        else:
            self.selection.append(ident)
        self._selection_changed()

    def _selection_changed(self, sync: bool = True) -> None:
        if sync:
            self._sync_sidebar()
        self.update_actions()
        self._update()
        self._announce()

    def _sync_sidebar(self) -> None:
        self._syncing = True
        try:
            self.window.elements_group.show_selection(self.indices(), self.primary(), expand=True)
        finally:
            self._syncing = False

    def row_expanded(self, index: int, expanded: bool) -> None:
        """The user opened or closed an element's row in the sidebar: it is
        selected alone, or no longer selected."""
        if self._syncing or not 0 <= index < len(self.window.label.elements):
            return
        ident = element_id(self.window.label.elements[index])
        if expanded:
            self.selection = [ident]
        elif ident in self.selection:
            self.selection.remove(ident)
        else:
            return
        group = self.window.elements_group
        group.show_selection(self.indices())  # the marks only
        self.update_actions()
        self._update()
        self._announce()

    def select_all(self) -> bool:
        if not self.window.label.elements:
            return False
        self.select(range(len(self.window.label.elements)))
        return True

    def select_none(self) -> bool:
        self.select([])
        return True

    # --- the overlay -------------------------------------------------------------------

    def _update(self) -> None:
        self.canvas.set_scene(self._scene())

    def _scene(self) -> Scene:
        size = self.window.preview.size_mm
        geometry = self._drag.press.geometry if self._drag else self.geometry()
        if size is None or geometry is None:
            return Scene(size_mm=size or (0.0, 0.0))
        drag = self._drag
        moving = drag.boxes() if drag else {}
        selected = self._selected_now()
        boxes = {i: moving.get(i, geometry.boxes[i].box) for i in selected}
        handles = self._handles_box(geometry, boxes)
        hover = self._hover if self._hover not in boxes and drag is None else None
        return Scene(
            size_mm=size,
            hover=geometry.boxes[hover].box if hover is not None else None,
            selected=tuple(boxes.values()),
            handles=handles,
            active_handle=drag.handle if isinstance(drag, Resize) else self._handle,
            band=drag.rect if isinstance(drag, Band) else None,
            guides=drag.guides if drag else (),
            grid_mm=self._snapping().grid_mm if isinstance(drag, (Move, Resize)) else 0.0,
            ghosts=self._ghosts(moving),
            tag=self._tag(drag),
            tag_box=self._bounds(moving),
        )

    def _selected_now(self) -> list[int]:
        """The selection, or what a rubber band going on would make it."""
        drag = self._drag
        if not isinstance(drag, Band):
            return self.indices()
        ids = [element_id(e) for e in self.window.label.elements]
        base = [ids.index(i) for i in drag.base if i in ids]
        return sorted(set(base) | set(drag.within))

    def _handles_box(self, geometry: editing.Geometry, boxes: dict[int, MmBox]) -> MmBox | None:
        """The box whose handles show: the one element selected, if it can
        be resized (free layout, not a page), while no band or move goes on."""
        if not self.free or len(boxes) != 1 or isinstance(self._drag, (Move, Band)):
            return None
        index, box = next(iter(boxes.items()))
        return box if resizable(self.window.label.elements[index]) else None

    def _ghosts(self, moving: dict[int, MmBox]) -> tuple[tuple[MmBox, MmBox], ...]:
        """Dragged or edited elements not rendered yet: from where the
        preview shows them to where they are going."""
        rendered, label = self.window.rendered, self.window.label
        ids = [element_id(e) for e in label.elements]
        if rendered is None or not self._shown(tuple(ids)):
            return ()
        targets = {ids.index(i): box for i, box in self._landed.items() if i in ids}
        targets.update(moving)
        return tuple(
            (rendered.boxes[i], box)
            for i, box in sorted(targets.items())
            if not _same(rendered.boxes[i], box)
        )

    @staticmethod
    def _bounds(boxes: dict[int, MmBox]) -> MmBox | None:
        if not boxes:
            return None
        values = list(boxes.values())
        return (
            min(b[0] for b in values),
            min(b[1] for b in values),
            max(b[2] for b in values),
            max(b[3] for b in values),
        )

    def _tag(self, drag: Drag | None) -> str:
        """Where the dragged elements are, or the resized one's size."""
        if isinstance(drag, Resize):
            box = drag.box
            return "%s × %s" % (self._length(box[2] - box[0]), self._length(box[3] - box[1]))
        if isinstance(drag, Move):
            element = self.window.label.elements[drag.indices[0]]
            if len(drag.indices) == 1:
                x, y = element.x_mm + drag.dx, element.y_mm + drag.dy
            else:
                x, y, _right, _bottom = self._bounds(drag.boxes()) or drag.start
            return "X %s   Y %s" % (self._length(x), self._length(y))
        return ""

    def _length(self, mm: float) -> str:
        unit = self.window.unit
        digits = 2 if unit.key == "mm" else 3
        return "%.*f %s" % (digits, unit.to_display(mm, digits), unit.symbol)

    def _update_colours(self) -> None:
        manager = Adw.StyleManager.get_default()
        colours = self.canvas.colours
        if hasattr(manager, "get_accent_color_rgba"):  # libadwaita 1.6
            colours.accent = manager.get_accent_color_rgba()
        colours.guide = canvas.rgba("#ff7b9c" if manager.get_dark() else "#e5254f")
        colours.line = 2.0 if manager.get_high_contrast() else 1.0
        self.canvas.queue_draw()

    # --- pointer -----------------------------------------------------------------------

    def _snapping(self) -> Snapping:
        settings = self.window.settings
        return Snapping(settings.snap, settings.grid_mm)

    def _under(
        self, view: View, geometry: editing.Geometry, x: float, y: float, hit: float
    ) -> tuple[int | None, str | None]:
        """The element and the handle under a point."""
        boxes = {i: geometry.boxes[i].box for i in self.indices()}
        handles = self._handles_box(geometry, boxes)
        if handles is not None:
            handle = canvas.handle_at(view, handles, x, y, hit)
            if handle is not None:
                return None, handle
        hits = geometry.hits(*view.to_mm(x, y), slop=view.mm(SLOP_PX))
        return (hits[0] if hits else None), None

    def _on_motion(self, _controller, x: float, y: float) -> None:
        if self._drag is not None or self._press is not None:
            return
        view, geometry = self.canvas.view(), self.geometry()
        if view is None or geometry is None:
            self._hovered(None, None)
            return
        self._hovered(*self._under(view, geometry, x, y, canvas.HIT_PX))

    def _hovered(self, index: int | None, handle: str | None, force: bool = False) -> None:
        """The element or handle under the pointer: its outline, the cursor
        (force: also when it is the same, the label changed under it)."""
        if (index, handle) == (self._hover, self._handle) and not force:
            return
        self._hover, self._handle = index, handle
        if handle is not None:
            cursor = canvas.CURSORS[handle]
        elif index is not None and self.free:
            cursor = "move"
        else:
            cursor = None
        self.canvas.set_cursor_from_name(cursor)
        self._update()

    def _on_begin(self, gesture: Gtk.GestureDrag, x: float, y: float) -> None:
        state = gesture.get_current_event_state()
        self.press(x, y, state, _touch(gesture.get_device()))
        if self._press is not None:  # the editor's, not the scrolled window's (touch)
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_update(self, gesture: Gtk.GestureDrag, ox: float, oy: float) -> None:
        self.drag(ox, oy, gesture.get_current_event_state())

    def _on_end(self, _gesture, _ox: float, _oy: float) -> None:
        self.release()

    def press(self, x: float, y: float, state=NO_MODIFIERS, touch: bool = False) -> None:
        """A press at x, y (the canvas's pixels): a click or a drag to come."""
        self.canvas.grab_focus()
        self._press = None
        self.window.render_if_stale()  # the boxes of the label as it is now
        view, geometry = self.canvas.view(), self.geometry()
        if view is None or geometry is None:
            return
        hit = canvas.TOUCH_HIT_PX if touch else canvas.HIT_PX
        index, handle = self._under(view, geometry, x, y, hit)
        additive = bool(state & (SHIFT | CONTROL))
        self._press = Press(x, y, view, geometry, index, handle, additive)

    def drag(self, ox: float, oy: float, state=NO_MODIFIERS) -> None:
        """The pointer pressed moved by ox, oy pixels from the press."""
        press = self._press
        if press is None:
            return
        if self._drag is None:
            if math.hypot(ox, oy) < DRAG_THRESHOLD_PX:
                return
            self._drag = self._start(press, state)
            if self._drag is None:  # nothing to drag: the flowing layout
                self._press = None
                return
        self._drag.update(ox, oy, state, self._snapping())
        self._update()

    def _start(self, press: Press, state) -> Drag | None:
        """What a press becomes once it moves: a move, a resize or a band."""
        if press.index is None and press.handle is None:
            base = list(self.selection) if press.additive else []
            return Band(press, base)
        if not self.free:
            self._hint_free()
            return None
        if press.handle is not None:
            index = self.indices()[0]
            element = self.window.label.elements[index]
            box = press.geometry.boxes[index].box
            return Resize(press, index, press.handle, box, keeps_proportions(element))
        assert press.index is not None
        ident = element_id(self.window.label.elements[press.index])
        if ident not in self.selection:  # dragging another element: it is chosen
            self.select([*self.indices(), press.index] if press.additive else [press.index])
        indices = self.indices()
        elements = [self.window.label.elements[i] for i in indices]
        least = (min(e.x_mm for e in elements), min(e.y_mm for e in elements))
        return Move(press, indices, press.geometry.bounds(indices), least)

    def release(self) -> None:
        """The press ends: a click, or the drag done."""
        press, drag = self._press, self._drag
        self._press = self._drag = None
        if press is None:
            return
        if drag is None:
            self._click(press)
        elif isinstance(drag, Band):
            ids = [element_id(e) for e in self.window.label.elements]
            base = [ids.index(i) for i in drag.base if i in ids]
            self.select([*base, *(i for i in drag.within if i not in base)])
        elif isinstance(drag, Move):
            self._edited(drag.finish(self.window.label), drag.indices)
        else:
            self._commit(drag.finish(self.window.label), [drag.index], drag.boxes(), sizes=True)
        self._update()

    def _click(self, press: Press) -> None:
        """A click: the element chosen (Shift, Ctrl: added or taken out),
        none in the empty."""
        if press.handle is not None:
            return
        if press.index is None:
            if not press.additive:
                self.select([])
        elif press.additive:
            self._toggle(press.index)
        else:
            self.select([press.index])

    def _cancel(self) -> bool:
        """Escape while dragging: the elements stay where they were."""
        if self._press is None and self._drag is None:
            return False
        self._press = self._drag = None
        self._update()
        return True

    # --- keys --------------------------------------------------------------------------

    def _on_key(self, _controller, keyval: int, _keycode: int, state) -> bool:
        mods = state & MODIFIERS
        if keyval in ARROWS and not mods & ~SHIFT:
            dx, dy = ARROWS[keyval]
            return self.nudge(dx, dy, bool(mods & SHIFT))
        keys: dict[tuple[int, int], Callable[[], bool]] = {
            (Gdk.KEY_Escape, 0): self.escape,
            (Gdk.KEY_Delete, 0): self.delete,
            (Gdk.KEY_KP_Delete, 0): self.delete,
            (Gdk.KEY_a, int(CONTROL)): self.select_all,
            (Gdk.KEY_a, int(CONTROL | SHIFT)): self.select_none,
        }
        action = keys.get((Gdk.keyval_to_lower(keyval), int(mods)))
        return action() if action else False

    def escape(self) -> bool:
        """Call off a drag, else select nothing."""
        if self._cancel():
            return True
        if self.selection:
            return self.select_none()
        return False

    def nudge(self, dx: int, dy: int, large: bool = False) -> bool:
        """Move the selection by 0.1 mm (large: 1 mm); a run of them is one
        step of undo."""
        indices = self.indices()
        if not indices or self._drag is not None:
            return bool(indices)
        if not self.free:
            self._hint_free()
            return True
        step = NUDGE_LARGE_MM if large else NUDGE_MM
        label = editing.move(self.window.label, indices, dx * step, dy * step)
        key = "nudge:" + ",".join(str(element_id(label.elements[i])) for i in indices)
        self._edited(label, indices, key)
        return True

    def delete(self) -> bool:
        """Remove the selected elements; several at once offer undo."""
        indices = self.indices()
        if not indices:
            return False
        self.window.remove_elements(indices)
        if len(indices) > 1:  # its undo only while no other change came after it
            self._toast = self.window.offer(
                _("Elements removed: %d") % len(indices), _("Undo"), self.window.undo
            )
        return True

    def _dismiss_toast(self) -> None:
        if self._toast is not None:
            toast, self._toast = self._toast, None
            toast.dismiss()

    # --- arranging ---------------------------------------------------------------------

    def align(self, edge: str) -> None:
        self.window.render_if_stale()  # the boxes of the label as it is now
        indices, geometry = self.indices(), self.geometry()
        if indices and geometry is not None and self.free:
            self._edited(editing.align(self.window.label, geometry, indices, edge), indices)

    def distribute(self, axis: str) -> None:
        self.window.render_if_stale()
        indices, geometry = self.indices(), self.geometry()
        if len(indices) >= 3 and geometry is not None and self.free:
            self._edited(editing.distribute(self.window.label, geometry, indices, axis), indices)

    def update_actions(self) -> None:
        count = len(self.indices()) if self.free else 0
        for name, enabled in (("align", count >= 1), ("distribute", count >= 3)):
            action = self.window.lookup_action(name)
            if action is not None:
                action.set_enabled(enabled)

    # --- edits -------------------------------------------------------------------------

    def _edited(self, label: Label, indices: list[int], key: str | None = None) -> None:
        """An edit that moved elements: where their boxes went, from how
        far their positions moved."""
        geometry, old = self.geometry(), self.window.label
        landed = {}
        if geometry is not None:
            for i in indices:
                dx = label.elements[i].x_mm - old.elements[i].x_mm
                dy = label.elements[i].y_mm - old.elements[i].y_mm
                landed[i] = editing.moved(geometry.boxes[i].box, dx, dy)
        if self._commit(label, indices, landed, key):
            self._announce(position=True)

    def _commit(
        self,
        label: Label,
        indices: list[int],
        landed: dict[int, MmBox],
        key: str | None = None,
        sizes: bool = False,
    ) -> bool:
        """An edit of some elements as one step of the window's undo
        (editing.Editor on its History); they are drawn where they landed
        until the next render. sizes: the edit changed their sizes."""
        old = self.window.label.elements
        if all(label.elements[i] == old[i] for i in indices):  # clamped back: no step
            return False
        editor = editing.Editor(self.window.label, self.window.history)
        if not editor.apply(label, key):
            return False
        self._dismiss_toast()
        self._committing = True
        try:
            self.window.edited(editor.label, indices, sizes)
        finally:
            self._committing = False
        for index, box in landed.items():
            self._landed[element_id(label.elements[index])] = box
        self._landed_after = self.window.render_generation
        self._update()
        return True

    def _hint_free(self) -> None:
        """The flowing layout: say how to move elements (not too often)."""
        now = time.monotonic()
        if now - self._hinted < HINT_SECONDS:
            return
        self._hinted = now
        self.window.offer(
            _("Elements can be moved in the free layout"),
            _("Use free layout"),
            lambda: free_rows.set_layout(self.window, "free"),
        )

    # --- accessibility -----------------------------------------------------------------

    def describe(self) -> str:
        """The selection in words, for screen readers."""
        indices, elements = self.indices(), self.window.label.elements
        if not indices:
            return _("No element selected")
        if len(indices) > 1:
            return _("Elements selected: %(n)d of %(total)d") % {
                "n": len(indices),
                "total": len(elements),
            }
        index = indices[0]
        element = elements[index]
        text = _("Element %(n)d of %(total)d: %(name)s") % {
            "n": index + 1,
            "total": len(elements),
            "name": element_title(element),
        }
        if self.free:
            text += ", X %s, Y %s" % (self._length(element.x_mm), self._length(element.y_mm))
        return text

    def _announce(self, position: bool = False) -> None:
        text = self.describe()
        self.canvas.update_property([Gtk.AccessibleProperty.DESCRIPTION], [text])
        if _can_announce(self.canvas):
            priority = Gtk.AccessibleAnnouncementPriority
            self.canvas.announce(text, priority.LOW if position else priority.MEDIUM)


def _can_announce(widget: Gtk.Widget) -> bool:
    """Whether the widget can speak to a screen reader (GTK 4.14): not
    without accessibility (GTK_A11Y=none), nor with the test context of a
    session without an accessibility bus, where GTK 4.22 crashes on it."""
    if not hasattr(widget, "announce"):
        return False
    context = widget.get_at_context()
    return context is not None and type(context).__gtype__.name != "GtkTestATContext"


def _same(a: MmBox, b: MmBox) -> bool:
    return all(abs(x - y) < 1e-6 for x, y in zip(a, b, strict=True))


def _touch(device: Gdk.Device | None) -> bool:
    """A finger or a pen: larger targets."""
    if device is None:
        return False
    return device.get_source() in (Gdk.InputSource.TOUCHSCREEN, Gdk.InputSource.PEN)


# --- the "Arrange" menu ---------------------------------------------------------------


def arrange_button(window: Window) -> Gtk.MenuButton:
    """The header bar's menu of aligning, distributing and snapping."""
    menu = Gio.Menu()
    button = Gtk.MenuButton(
        label=_("Arrange"),
        tooltip_text=_("Align, distribute and snap the selected elements"),
        menu_model=menu,
    )

    def opened(_button, _pspec) -> None:  # filled in the unit of now
        if button.get_active():
            _fill(menu, window)

    button.connect("notify::active", opened)
    return button


def _fill(menu: Gio.Menu, window: Window) -> None:
    menu.remove_all()
    align = Gio.Menu()
    for edge, title in (
        ("left", _("Left")),
        ("center", _("Centre")),
        ("right", _("Right")),
        ("top", _("Top")),
        ("middle", _("Middle")),
        ("bottom", _("Bottom")),
    ):
        align.append(title, "win.align::" + edge)
    menu.append_section(_("Align"), align)
    distribute = Gio.Menu()
    distribute.append(_("Horizontally"), "win.distribute::x")
    distribute.append(_("Vertically"), "win.distribute::y")
    menu.append_section(_("Distribute"), distribute)
    snapping = Gio.Menu()
    snapping.append(_("Snap to guides"), "win.snap")
    snapping.append(_("No grid"), "win.grid::0")
    for mm, title in grids(window):
        snapping.append(title, "win.grid::%g" % mm)
    menu.append_section(_("Snapping"), snapping)


def grids(window: Window) -> list[tuple[float, str]]:
    """The grids to choose from in the window's unit: (mm, title)."""
    if window.unit.key == "in":
        return [(25.4 * a / b, _("Grid of %s") % ("%d/%d″" % (a, b))) for a, b in GRIDS_IN]
    return [(mm, _("Grid of %s") % ("%g mm" % mm)) for mm in GRIDS_MM]


def install_actions(window: Window, editor: PreviewEditor) -> None:
    """win.align::EDGE, win.distribute::AXIS, win.snap and win.grid::MM."""
    string = GLib.VariantType.new("s")
    align = Gio.SimpleAction.new("align", string)
    align.connect("activate", lambda _a, edge: editor.align(edge.get_string()))
    distribute = Gio.SimpleAction.new("distribute", string)
    distribute.connect("activate", lambda _a, axis: editor.distribute(axis.get_string()))
    snap = Gio.SimpleAction.new_stateful(
        "snap", None, GLib.Variant.new_boolean(window.settings.snap)
    )
    snap.connect("activate", lambda a, _v: _set_snap(window, a))
    grid = Gio.SimpleAction.new_stateful(
        "grid", string, GLib.Variant.new_string("%g" % window.settings.grid_mm)
    )
    grid.connect("activate", lambda a, v: _set_grid(window, a, v))
    for action in (align, distribute, snap, grid):
        window.add_action(action)
    editor.update_actions()


def _set_snap(window: Window, action: Gio.SimpleAction) -> None:
    on = not action.get_state().get_boolean()
    action.set_state(GLib.Variant.new_boolean(on))
    window.settings.set_snap(on)


def _set_grid(window: Window, action: Gio.SimpleAction, value: GLib.Variant) -> None:
    try:
        mm = float(value.get_string())
    except ValueError:
        return
    action.set_state(value)
    window.settings.set_grid_mm(mm)
