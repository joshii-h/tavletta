"""The mouse editor of the preview (ui/editor.py, ui/canvas.py) in the
window: selecting, dragging with snapping, resizing by the handles, the
keys, aligning and distributing, undo shared with the sidebar, the
sidebar following the selection, and the flowing layout's hint.

Gestures are given to the editor as its GtkGestureDrag gives them (press,
drag by an offset, release, in the canvas's pixels): GTK has no way to
make pointer events in a test."""

from __future__ import annotations

import pytest
from conftest import gtk_available, pump, render_now, toast_titles

pytestmark = pytest.mark.gui
if not gtk_available():
    pytest.skip("no display for GTK", allow_module_level=True)

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402
from test_app_design import element_row  # noqa: E402

from tavletta import freelayout  # noqa: E402
from tavletta.model import Label, ShapeElement, TextElement, element_id  # noqa: E402
from tavletta.ui import canvas, editor, preview, shortcuts  # noqa: E402

SHIFT = Gdk.ModifierType.SHIFT_MASK
CONTROL = Gdk.ModifierType.CONTROL_MASK
ALT = Gdk.ModifierType.ALT_MASK


def box(x, y, width=10.0, height=5.0, **kwargs) -> ShapeElement:
    """A filled rectangle: its box is where it is drawn."""
    return ShapeElement(
        shape="rectangle", fill="solid", x_mm=x, y_mm=y, width_mm=width, height_mm=height, **kwargs
    )


def show(win, label: Label) -> editor.PreviewEditor:
    """The label in a window shown on the screen (the canvas needs its
    size), rendered: the editor ready for gestures."""
    win.present()
    win.set_label(label, None)
    render_now(win)
    assert pump(lambda: win.preview.canvas.view() is not None and win.editor.geometry())
    return win.editor


def free(*elements, **kwargs) -> Label:
    kwargs.setdefault("tape_mm", 25)
    return Label(layout="free", auto_length=False, length_mm=60, elements=list(elements), **kwargs)


def at(win, x_mm, y_mm) -> tuple[float, float]:
    """A point of the label in the canvas's pixels."""
    return win.preview.canvas.view().to_px(x_mm, y_mm)


def px(win, mm: float) -> float:
    return mm * win.preview.canvas.view().sx


def click(win, x_mm, y_mm, state=editor.NO_MODIFIERS) -> None:
    win.editor.press(*at(win, x_mm, y_mm), state)
    win.editor.release()


def drag(win, x_mm, y_mm, dx_mm, dy_mm, state=editor.NO_MODIFIERS, touch=False) -> None:
    """Press at a point, move by dx, dy mm (in two steps, as the pointer
    does), release."""
    ed = win.editor
    ed.press(*at(win, x_mm, y_mm), state, touch)
    ed.drag(px(win, dx_mm) / 2, px(win, dy_mm) / 2, state)
    ed.drag(px(win, dx_mm), px(win, dy_mm), state)
    ed.release()


def key(win, keyval, state=editor.NO_MODIFIERS) -> bool:
    return win.editor._on_key(None, keyval, 0, state)


def positions(win) -> list[tuple[float, float]]:
    return [(e.x_mm, e.y_mm) for e in win.label.elements]


# --- the view: millimetres and pixels -------------------------------------------------


@pytest.mark.parametrize("zoom", [0.5, 1, 2, 4])
def test_the_view_follows_the_zoom(window, zoom):
    show(window, free(box(5, 5)))
    window.preview.set_zoom(zoom)
    pump(
        lambda: window.preview.picture.get_width() == round(60 * preview.PREVIEW_PX_PER_MM * zoom)
    )
    view = window.preview.canvas.view()
    ppm = preview.PREVIEW_PX_PER_MM * zoom
    # the VC-500W's raster is a little taller than the 25 mm tape: the
    # picture fits it in, a little smaller
    assert view.sx == view.sy == pytest.approx(ppm, rel=0.02)
    # the picture is as large as the zoom says, not the image's pixels
    assert window.preview.picture.get_width() == round(60 * ppm)
    x, y = view.to_px(12.5, 7.25)
    assert view.to_mm(x, y) == pytest.approx((12.5, 7.25))
    ok, bounds = window.preview.picture.compute_bounds(window.preview.canvas)
    assert ok and bounds.get_x() <= view.ox < bounds.get_x() + 0.02 * bounds.get_width()


def test_a_click_hits_at_any_zoom(window):
    show(window, free(box(5, 5), box(30, 10)))
    for zoom in (0.5, 3):
        window.preview.set_zoom(zoom)
        pump()
        click(window, 34, 12)
        assert window.editor.indices() == [1]
        click(window, 6, 6)
        assert window.editor.indices() == [0]


@pytest.mark.parametrize("orientation", ["along", "across"])
def test_a_die_cut_label_either_way(window, orientation):
    label = free(box(5, 5), box(20, 30))
    label.tape_mm, label.length_mm = 50, 40
    label.orientation = orientation
    show(window, label)
    width, height = window.preview.size_mm
    # label space: x along the preview, never turned
    assert window.editor.geometry().width_mm == pytest.approx(width)
    drag(window, 22, 32, 3, -2)
    assert positions(window)[1] == pytest.approx((23, 28), abs=0.06)


def test_endless_tape(window):
    label = free(box(5, 5))
    label.auto_length = True
    show(window, label)
    assert window.backend.capabilities.continuous
    drag(window, 7, 7, 10, 2)
    assert positions(window)[0] == pytest.approx((15, 7), abs=0.06)


def test_the_view_maps_scaled_pixels():
    """On a display of scale 2 GTK draws the logical pixels with twice the
    device pixels: the view is in logical pixels, the same at any scale."""
    view = canvas.View(30.0, 40.0, 12.0, 12.0)
    assert view.to_px(10, 5) == (150.0, 100.0)
    assert view.to_mm(150, 100) == (10.0, 5.0)
    assert view.mm(6) == 0.5


# --- selecting ------------------------------------------------------------------------


def test_a_click_selects_and_opens_its_row(window):
    ed = show(window, free(box(5, 5), box(30, 10)))
    click(window, 31, 11)
    assert ed.indices() == [1] and window.expand_index == 1
    rows = window.elements_group.rows
    assert rows[1].get_expanded() and not rows[0].get_expanded()
    assert rows[1].has_css_class("selected-element")
    click(window, 50, 20)  # the empty: nothing
    assert ed.indices() == [] and window.expand_index == -1


def test_shift_and_ctrl_click_add_and_take_out(window):
    ed = show(window, free(box(5, 5), box(30, 10), box(45, 15)))
    click(window, 6, 6)
    click(window, 31, 11, SHIFT)
    click(window, 46, 16, CONTROL)
    assert ed.indices() == [0, 1, 2]
    assert window.expand_index == 2  # the one chosen last
    click(window, 31, 11, CONTROL)
    assert ed.indices() == [0, 2]
    assert window.elements_group.rows[0].has_css_class("selected-element")
    assert not window.elements_group.rows[1].has_css_class("selected-element")
    click(window, 50, 2, SHIFT)  # the empty with Shift: the selection stays
    assert ed.indices() == [0, 2]


def test_rubber_band_touching_or_with_alt_inside(window):
    ed = show(window, free(box(5, 5), box(30, 10), box(45, 15)))
    drag(window, 1, 1, 30, 12)  # touches the second one
    assert ed.indices() == [0, 1]
    drag(window, 1, 1, 30, 12, ALT)  # holds only the first
    assert ed.indices() == [0]
    drag(window, 58, 24, -15, -6, SHIFT)  # adds the third
    assert ed.indices() == [0, 2]


def test_the_sidebar_selects(window):
    ed = show(window, free(box(5, 5), box(30, 10)))
    rows = window.elements_group.rows
    rows[1].set_expanded(True)
    assert ed.indices() == [1]
    rows[0].set_expanded(True)
    assert ed.indices() == [0]
    rows[0].set_expanded(False)
    assert ed.indices() == []


def test_selection_by_identity_survives_undo_and_removal(window):
    ed = show(window, free(box(5, 5), box(30, 10), box(45, 15)))
    click(window, 46, 16)
    window.remove_element(0)
    assert ed.indices() == [1]
    window.undo()
    assert ed.indices() == [2]
    window.remove_element(2)
    assert ed.indices() == []


def test_hover_and_cursor(window):
    ed = show(window, free(box(5, 5)))
    ed._on_motion(None, *at(window, 6, 6))
    assert window.preview.canvas.scene.hover is not None
    assert window.preview.canvas.get_cursor().get_name() == "move"
    ed._on_motion(None, *at(window, 40, 20))
    assert window.preview.canvas.scene.hover is None
    assert window.preview.canvas.get_cursor() is None


# --- moving -----------------------------------------------------------------------------


def test_drag_moves_one_step_of_undo(window):
    show(window, free(box(5, 5), box(30, 10)))
    window.settings.set_snap(False)
    drag(window, 7, 7, 3.3, 1.2)
    assert positions(window)[0] == pytest.approx((8.3, 6.2), abs=0.06)
    assert window.editor.indices() == [0]
    window.undo()
    assert positions(window)[0] == (5, 5)
    window.redo()
    assert positions(window)[0] == pytest.approx((8.3, 6.2), abs=0.06)
    window.undo()
    window.undo()  # nothing before the label was set
    assert positions(window)[0] == (5, 5)


def test_a_small_move_is_a_click(window):
    show(window, free(box(5, 5)))
    ed = window.editor
    ed.press(*at(window, 7, 7))
    ed.drag(editor.DRAG_THRESHOLD_PX - 1, 0)
    ed.release()
    assert positions(window)[0] == (5, 5) and ed.indices() == [0]
    assert not window.history.can_undo


def test_dragging_another_element_moves_it_alone(window):
    show(window, free(box(5, 5), box(30, 10)))
    window.settings.set_snap(False)
    click(window, 6, 6)
    drag(window, 31, 11, 2, 0)
    assert positions(window) == [(5, 5), pytest.approx((32, 10), abs=0.06)]
    assert window.editor.indices() == [1]


def test_dragging_the_selection_moves_all_of_it(window):
    show(window, free(box(5, 5), box(30, 10)))
    window.settings.set_snap(False)
    window.editor.select_all()
    drag(window, 31, 11, 2, 3)
    assert positions(window) == [
        pytest.approx((7, 8), abs=0.06),
        pytest.approx((32, 13), abs=0.06),
    ]
    window.undo()  # one step for both
    assert positions(window) == [(5, 5), (30, 10)]


def test_not_beyond_the_top_left_corner(window):
    show(window, free(box(2, 2), box(30, 10)))
    window.editor.select_all()
    drag(window, 3, 3, -10, -10, ALT)
    assert positions(window) == [(0, 0), (28, 8)]


def test_snaps_to_another_element_and_the_label(window):
    show(window, free(box(5, 5), box(30, 10)))
    # the left edge 0.3 mm from the other's: within 6 px at 6 px per mm
    drag(window, 7, 7, 24.7, 0)
    assert positions(window)[0][0] == pytest.approx(30, abs=0.02)
    drag(window, 32, 7, -31.7, 0)  # to the label's left edge
    assert positions(window)[0][0] == pytest.approx(0, abs=0.02)


def test_guides_while_dragging(window):
    show(window, free(box(5, 5), box(30, 10)))
    ed = window.editor
    ed.press(*at(window, 7, 7))
    ed.drag(px(window, 24.8), 0)
    scene = window.preview.canvas.scene
    assert any(g.axis == "x" and g.position == pytest.approx(30, abs=0.01) for g in scene.guides)
    assert scene.ghosts and scene.tag.startswith("X ")
    ed.release()
    assert window.preview.canvas.scene.guides == ()


def test_alt_or_ctrl_drags_without_snapping(window):
    show(window, free(box(5, 5), box(30, 10)))
    for state in (ALT, CONTROL):
        drag(window, 7, 7, 24.7, 0, state)
        assert positions(window)[0][0] == pytest.approx(29.7, abs=0.06)
        window.undo()


def test_snapping_off_and_the_grid(window):
    show(window, free(box(5, 5), box(30, 10)))
    window.activate_action("win.snap", None)
    assert not window.settings.snap
    drag(window, 7, 7, 24.7, 0)
    assert positions(window)[0][0] == pytest.approx(29.7, abs=0.06)
    window.activate_action("win.snap", None)
    window.activate_action("win.grid", GLib.Variant.new_string("2"))
    assert window.settings.grid_mm == 2
    drag(window, 31, 7, 2.2, 0)  # 31.95 … the grid at 32
    assert positions(window)[0][0] == pytest.approx(32, abs=0.02)


def test_shift_keeps_to_one_axis(window):
    show(window, free(box(5, 5)))
    window.settings.set_snap(False)
    drag(window, 7, 7, 5, 1, SHIFT)
    assert positions(window)[0] == pytest.approx((10, 5), abs=0.06)
    drag(window, 12, 7, 1, 4, SHIFT)
    assert positions(window)[0] == pytest.approx((10, 9), abs=0.06)


def test_escape_calls_off_a_drag(window):
    show(window, free(box(5, 5)))
    ed = window.editor
    ed.press(*at(window, 7, 7))
    ed.drag(px(window, 5), 0)
    assert key(window, Gdk.KEY_Escape)
    ed.drag(px(window, 8), 0)  # the button still down: nothing more
    ed.release()
    assert positions(window)[0] == (5, 5) and not window.history.can_undo
    assert window.preview.canvas.scene.ghosts == ()
    assert ed.indices() == [0]  # the selection stays; a second Escape clears it
    assert key(window, Gdk.KEY_Escape) and ed.indices() == []
    assert not key(window, Gdk.KEY_Escape)


def test_where_it_landed_until_the_render(window):
    show(window, free(box(5, 5)))
    window.settings.set_snap(False)
    drag(window, 7, 7, 10, 0)
    # not rendered yet: the editor knows where it went
    assert window.editor.geometry().boxes[0].left == pytest.approx(15, abs=0.1)
    assert window.preview.canvas.scene.ghosts
    render_now(window)
    pump()
    assert window.editor.geometry().boxes[0].left == pytest.approx(15, abs=0.1)
    assert window.preview.canvas.scene.ghosts == ()


def test_touch_has_larger_handles(window):
    ed = show(window, free(box(10, 10, 20, 10)))
    click(window, 15, 15)
    # 15 px beside the right edge's handle: a finger's, not a mouse's
    x, y = at(window, 30, 15)
    ed.press(x + 15, y, editor.NO_MODIFIERS, True)
    assert ed._press.handle == "e"
    ed.release()
    ed.press(x + 15, y)
    assert ed._press.handle is None
    ed.release()


# --- resizing ---------------------------------------------------------------------------


def test_resize_a_shape_by_its_handles(window):
    ed = show(window, free(box(10, 10, 20, 10)))
    window.settings.set_snap(False)
    click(window, 15, 15)
    assert window.preview.canvas.scene.handles is not None
    drag(window, 30, 15, 5, 0)  # the right edge's handle
    shape = window.label.elements[0]
    assert shape.width_mm == pytest.approx(25, abs=0.2) and shape.height_mm == 10
    drag(window, 10, 10, -2, -3)  # the top left corner's
    assert (shape := window.label.elements[0]).x_mm == pytest.approx(8, abs=0.1)
    assert shape.y_mm == pytest.approx(7, abs=0.1)
    assert shape.height_mm == pytest.approx(13, abs=0.2)
    window.undo()
    assert window.label.elements[0].width_mm == pytest.approx(25, abs=0.2)
    assert ed.indices() == [0]


def test_a_handle_is_hit_beside_it(window):
    show(window, free(box(10, 10, 20, 10)))
    click(window, 15, 15)
    x, y = at(window, 30, 20)
    window.editor.press(x + canvas.HIT_PX / 2 - 1, y + canvas.HIT_PX / 2 - 1)
    assert window.editor._press.handle == "se"
    window.editor.release()


def test_a_text_keeps_its_proportions(window):
    show(window, free(TextElement(text="Ab", size_pt=20, x_mm=5, y_mm=5)))
    window.settings.set_snap(False)
    click(window, 7, 7)
    old = window.editor.geometry().boxes[0]
    drag(window, old.right, old.bottom, old.width / 2, 0)
    assert window.label.elements[0].size_pt == pytest.approx(30, rel=0.05)
    scene = window.preview.canvas.scene
    assert scene.ghosts  # drawn scaled until rendered


def test_shift_resizes_a_shape_in_proportion(window):
    show(window, free(box(10, 10, 20, 10)))
    window.settings.set_snap(False)
    click(window, 15, 15)
    drag(window, 30, 20, 10, 0, SHIFT)
    shape = window.label.elements[0]
    assert (shape.width_mm, shape.height_mm) == pytest.approx((30, 15), abs=0.2)


def test_no_handles_for_several_or_a_page(window):
    show(window, free(box(5, 5), box(30, 10)))
    window.editor.select_all()
    assert window.preview.canvas.scene.handles is None


# --- keys -----------------------------------------------------------------------------------


def test_arrow_keys_nudge_one_step(window):
    show(window, free(box(5, 5)))
    click(window, 6, 6)
    assert key(window, Gdk.KEY_Right)
    assert key(window, Gdk.KEY_Right)
    assert key(window, Gdk.KEY_Down, SHIFT)
    assert positions(window)[0] == pytest.approx((5.2, 6))
    assert window.preview.canvas.has_focus() or not window.is_active()
    window.undo()  # the run of nudges was one step
    assert positions(window)[0] == (5, 5)
    assert not key(window, Gdk.KEY_Left, ALT)  # Alt+arrows: the window's


def test_no_nudge_without_a_selection(window):
    show(window, free(box(5, 5)))
    assert not key(window, Gdk.KEY_Right)


def test_delete_removes_the_selection(window):
    show(window, free(box(5, 5), box(30, 10), box(45, 15)))
    click(window, 6, 6)
    click(window, 46, 16, SHIFT)
    assert key(window, Gdk.KEY_Delete)
    assert len(window.label.elements) == 1 and window.editor.indices() == []
    assert "Elements removed: 2" in toast_titles(window)
    window.undo()
    assert len(window.label.elements) == 3
    assert not key(window, Gdk.KEY_Delete)  # nothing selected: the window's own


def test_select_all_and_none(window):
    show(window, free(box(5, 5), box(30, 10)))
    assert key(window, Gdk.KEY_a, CONTROL)
    assert window.editor.indices() == [0, 1]
    assert key(window, Gdk.KEY_A, CONTROL | SHIFT)
    assert window.editor.indices() == []


# --- arranging -------------------------------------------------------------------------------


def test_align_and_distribute(window):
    show(window, free(box(5, 5), box(20, 8), box(45, 3, width=5)))
    ed = window.editor
    assert not window.lookup_action("align").get_enabled()
    ed.select_all()
    assert window.lookup_action("distribute").get_enabled()
    window.activate_action("win.align", GLib.Variant.new_string("top"))
    assert [y for _x, y in positions(window)] == pytest.approx([3, 3, 3], abs=0.05)
    window.activate_action("win.distribute", GLib.Variant.new_string("x"))
    xs = [x for x, _y in positions(window)]
    assert xs[1] == pytest.approx(25, abs=0.05)  # 5 … 15, 25 … 35, 45 … 50: gaps of 10
    window.undo()
    window.undo()
    assert positions(window) == [(5, 5), (20, 8), (45, 3)]


def test_align_one_element_to_the_label(window):
    show(window, free(box(5, 5)))
    click(window, 6, 6)
    window.activate_action("win.align", GLib.Variant.new_string("right"))
    assert positions(window)[0][0] == pytest.approx(50, abs=0.02)


def test_the_arrange_menu(window):
    model = editor.arrange_button(window).get_menu_model()
    editor._fill(model, window)  # as opening it does
    actions = [
        model.get_item_link(s, "section").get_item_attribute_value(i, "action").get_string()
        for s in range(model.get_n_items())
        for i in range(model.get_item_link(s, "section").get_n_items())
    ]
    assert "win.align" in actions and "win.distribute" in actions and "win.grid" in actions


def test_grids_in_inches(make_window):
    win = make_window({"units": "in"})
    grids = editor.grids(win)
    assert grids[1] == (pytest.approx(25.4 / 16), "Grid of 1/16″")


# --- undo shared with the sidebar ------------------------------------------------------------


def test_undo_goes_back_over_sidebar_and_preview(window):
    show(window, free(box(5, 5)))
    window.settings.set_snap(False)
    element = window.label.elements[0]
    element.width_mm = 12  # as the sidebar's row does it
    window.mark_changed()
    drag(window, 7, 7, 3, 0)
    window.label.elements[0].height_mm = 7
    window.mark_changed()
    shape = window.label.elements[0]
    assert (shape.x_mm, shape.width_mm, shape.height_mm) == pytest.approx((8, 12, 7), abs=0.06)
    window.undo()
    assert window.label.elements[0].height_mm == 5
    window.undo()
    assert window.label.elements[0].x_mm == 5
    window.undo()
    assert window.label.elements[0].width_mm == 10
    window.redo()
    window.redo()
    assert window.label.elements[0].x_mm == pytest.approx(8, abs=0.06)


def test_the_sidebar_shows_the_new_position(window):
    show(window, free(box(5, 5)))
    window.settings.set_snap(False)
    drag(window, 7, 7, 3, 0)
    assert element_row(window, window.elements_group.rows[0].get_title()).get_expanded()
    assert window.label.elements[0] is not None and window.expand_index == 0


# --- the flowing layout ---------------------------------------------------------------------


def test_flow_layout_selects_but_does_not_move(window):
    label = Label(tape_mm=25, elements=[TextElement(text="A"), TextElement(text="B")])
    ed = show(window, label)
    geometry = ed.geometry()
    b = geometry.boxes[1]
    click(window, (b.left + b.right) / 2, (b.top + b.bottom) / 2)
    assert ed.indices() == [1]
    assert window.preview.canvas.scene.handles is None
    drag(window, (b.left + b.right) / 2, (b.top + b.bottom) / 2, 5, 0)
    assert [e.x_mm for e in window.label.elements] == [0, 0]
    assert "Elements can be moved in the free layout" in toast_titles(window)
    assert key(window, Gdk.KEY_Right)  # the hint again, not too soon
    assert toast_titles(window).count("Elements can be moved in the free layout") == 1
    assert not window.lookup_action("align").get_enabled()
    drag(window, 0.1, 0.1, 40, 20)  # a rubber band selects there too
    assert ed.indices() == [0, 1]


def test_the_hint_switches_to_the_free_layout(window):
    label = Label(tape_mm=25, elements=[TextElement(text="A")])
    ed = show(window, label)
    ed._hint_free()
    toast = window.toast_log[-1]
    toast.emit("button-clicked")
    assert window.label.layout == "free"


# --- accessibility ------------------------------------------------------------------------


def test_the_selection_in_words(window):
    ed = show(window, free(box(5, 5), TextElement(text="Hi", x_mm=30, y_mm=3)))
    assert ed.describe() == "No element selected"
    ed.select([1])
    assert ed.describe().startswith("Element 2 of 2: ")
    assert ed.describe().endswith("X 30.00 mm, Y 3.00 mm")
    ed.select_all()
    assert ed.describe() == "Elements selected: 2 of 2"
    assert window.preview.canvas.get_accessible_role() == Gtk.AccessibleRole.GROUP


def test_the_shortcuts_list_the_preview(window):
    titles = [title for heading, items in shortcuts.sections() for title, _a in items]
    assert "Move the selection by 0.1 mm" in titles
    assert "Drag: without snapping" in titles


def test_colours_follow_the_style(window):
    ed = show(window, free(box(5, 5)))
    manager = Adw.StyleManager.get_default()
    manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    pump()
    assert window.preview.canvas.colours.guide.to_string() == canvas.rgba("#ff7b9c").to_string()
    manager.set_color_scheme(Adw.ColorScheme.DEFAULT)
    pump()
    ed._update_colours()


def test_identity_of_the_selection(window):
    ed = show(window, free(box(5, 5)))
    ed.select([0])
    assert ed.selection == [element_id(window.label.elements[0])]


def test_an_open_page_shows_the_new_position(window):
    from test_app_design import open_page

    show(window, free(box(5, 5)))
    window.settings.set_snap(False)
    click(window, 6, 6)
    title = window.elements_group.rows[0].get_title()
    page = open_page(window, title, "Position and size")
    rows = [r for r in _walk(page) if isinstance(r, Adw.SpinRow)]
    assert rows[0].get_value() == 5
    drag(window, 7, 7, 3, 0)
    page = window.sidebar_nav.get_visible_page()
    assert page.get_title() == "Position and size"
    rows = [r for r in _walk(page) if isinstance(r, Adw.SpinRow)]
    assert rows[0].get_value() == pytest.approx(8, abs=0.06)
    window.undo()  # the sidebar's own undo shows it too
    page = window.sidebar_nav.get_visible_page()
    rows = [r for r in _walk(page) if isinstance(r, Adw.SpinRow)]
    assert rows[0].get_value() == 5


def test_a_resized_text_shows_its_size(window):
    show(window, free(TextElement(text="Ab", size_pt=20, x_mm=5, y_mm=5), box(30, 10)))
    window.settings.set_snap(False)
    click(window, 7, 7)
    old_rows = list(window.elements_group.rows)
    b = window.editor.geometry().boxes[0]
    drag(window, b.right, b.bottom, b.width / 2, 0)
    rows = window.elements_group.rows
    assert rows[0] is not old_rows[0] and rows[1] is old_rows[1]  # only its own built again
    assert rows[0].get_expanded() and rows[0].has_css_class("selected-element")
    size = next(
        r for r in _walk(rows[0]) if isinstance(r, Adw.SpinRow) and r.get_title() == "Size"
    )
    assert size.get_value() == pytest.approx(
        window.label.elements[0].size_pt * 25.4 / 72, abs=0.01
    )
    # its row's fields still edit the window's element
    size.set_value(5)
    assert window.label.elements[0].size_pt == pytest.approx(5 * 72 / 25.4, abs=0.1)


def _walk(widget):
    child = widget.get_first_child()
    while child:
        yield child
        yield from _walk(child)
        child = child.get_next_sibling()


def test_the_canvas_draws_every_part(window):
    """Everything an overlay has, drawn (as render nodes; no Cairo)."""
    show(window, free(box(5, 5), box(30, 10)))
    cv = window.preview.canvas
    guides = (
        canvas.Guide("x", 30.0, "element", 1),
        canvas.Guide("y", 5.0, "grid"),
    )
    scene = canvas.Scene(
        size_mm=window.preview.size_mm,
        hover=(30, 10, 40, 15),
        selected=((5, 5, 15, 10),),
        handles=(5, 5, 15, 10),
        active_handle="se",
        band=(20, 20, 1, 1),
        guides=guides,
        grid_mm=1.0,
        ghosts=(((5, 5, 15, 10), (8, 6, 20, 12)),),
        tag="X 8.00 mm   Y 6.00 mm",
        tag_box=(8, 6, 20, 12),
    )
    cv.show(scene)
    cv.grab_focus()
    snapshot = Gtk.Snapshot()
    cv.do_snapshot(snapshot)
    assert snapshot.to_node() is not None
    cv.show(canvas.Scene())  # nothing shown: nothing drawn
    snapshot = Gtk.Snapshot()
    cv.do_snapshot(snapshot)
    assert snapshot.to_node() is None


def test_a_resized_edge_snaps(window):
    show(window, free(box(5, 5), box(30, 10)))
    click(window, 6, 6)
    drag(window, 15, 7.5, 14.7, 0)  # the right edge 0.3 mm before the other's left
    assert window.label.elements[0].width_mm == pytest.approx(25, abs=0.05)
    assert window.label.elements[0].height_mm == 5


def test_a_handle_shows_its_cursor(window):
    ed = show(window, free(box(10, 10, 20, 10)))
    click(window, 15, 15)
    ed._on_motion(None, *at(window, 30, 15))
    assert window.preview.canvas.get_cursor().get_name() == "ew-resize"
    assert window.preview.canvas.scene.active_handle == "e"


def test_a_qr_code_keeps_its_proportions(window):
    from tavletta.model import CodeElement

    show(window, free(CodeElement(data="1", x_mm=5, y_mm=5, module_mm=0.5)))
    window.settings.set_snap(False)
    click(window, 7, 7)
    assert editor.keeps_proportions(window.label.elements[0])
    b = window.editor.geometry().boxes[0]
    drag(window, b.right, b.bottom, b.width, 0)
    assert window.label.elements[0].module_mm == pytest.approx(1.0, abs=0.1)


def test_no_geometry_until_reordered_elements_are_rendered(window):
    ed = show(window, free(box(5, 5), box(30, 10)))
    window.move_element(0, 1)  # in place: the preview still shows the old order
    assert ed.geometry() is None
    click(window, 6, 6)  # nothing to hit meanwhile
    assert ed.indices() == []
    render_now(window)
    pump()
    assert ed.geometry().boxes[1].left == pytest.approx(5, abs=0.05)


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_a_turned_element(window, rotation):
    """A turned element's box is the turned one: hit, moved and resized as drawn."""
    from tavletta.model import CodeElement

    code = CodeElement(symbology="code128", data="12", x_mm=5, y_mm=3, rotation=rotation)
    show(window, free(code))
    window.settings.set_snap(False)
    b = window.editor.geometry().boxes[0]
    tall = b.height > b.width
    assert tall == (rotation in (90, 270))
    drag(window, (b.left + b.right) / 2, (b.top + b.bottom) / 2, 4, 2)
    assert positions(window)[0] == pytest.approx((9, 5), abs=0.06)
    render_now(window)
    pump()
    b = window.editor.geometry().boxes[0]
    # the edge across the bars: longer bars (from their default), the module as drawn
    handle = (b.right, (b.top + b.bottom) / 2) if tall else ((b.left + b.right) / 2, b.bottom)
    long_side = b.width if tall else b.height
    drag(window, *handle, *((3, 0) if tall else (0, 3)))
    new = window.label.elements[0]
    assert new.module_mm == freelayout.default_module_mm(new)
    render_now(window)
    pump()
    b = window.editor.geometry().boxes[0]
    # longer by about as much (editing.resize scales the bars by the box,
    # which holds the digits under them too: a little less)
    assert long_side + 2 < (b.width if tall else b.height) <= long_side + 3.1
