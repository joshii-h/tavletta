# Editing with the mouse: the core

`tavletta/editing.py` is what a mouse editor needs, without a toolkit:
where the elements of a label are, which one lies under the pointer,
snapping, and the edits with their undo. The window's editor (select,
drag, snap, resize, undo) and the print server's web page (elements
highlighted in the preview) are to share it; neither is built yet.

## Coordinates

Label space in millimetres, as the preview shows the label: x along the
tape from its left end, y across it from the tape's top edge. These are
the free layout's `x_mm` and `y_mm` (see [layout.md](layout.md)). The
preview never turns the label: on die-cut labels whose content runs
across the roll (`orientation` "across") label space is the same. A box
is `(left, top, right, bottom)`.

To go from the preview's pixels to millimetres, divide by the preview's
pixels per millimetre (`PREVIEW_PX_PER_MM` times the zoom). On endless
tape the preview shows the whole tape (`render.whole_tape`), so its top
row is the tape's edge; where the print head is narrower than the tape,
the rounding of the strip in its middle can put it a pixel off.

## Geometry: one layout, from the render

`render.render()` returns, besides the image, where it drew each element:
`Rendered.boxes`, one box per element in the order of `label.elements`,
in mm. A box is the element's ink (alpha at least half, as
`render.ink_box()`), or all of what it drew if it has none (an empty
text). Both layouts fill it from the parts they paste, so the boxes are
exactly what the label shows: turned elements (a quarter turn at a time)
have the turned box, a label scaled down to fit a fixed length the scaled
ones, a black-and-white render those of its black-and-white parts. A
render that fails for an element draws a placeholder; its box is the
placeholder's.

```python
from tavletta import editing

geometry = editing.Geometry.of(label, rendered)  # a render the caller has
geometry = editing.measure(label, values, row)   # or render it now
```

`measure()` fills in the variables (`values`) and a data row's columns
(`row`) first, as printing does: a text with `{Name}` is as wide as the
name printed in it. The window and the server already render the label
for their preview; they take `Geometry.of()` of that render (the label
the render was made of, filled in or not: its elements are the same).

| `Geometry` | |
|---|---|
| `width_mm`, `height_mm` | the rendered length, the tape's (or the label's) height |
| `layout`, `free` | the label's layout; whether its elements can be moved |
| `boxes` | an `ElementBox` per element: `index`, `kind`, `left`, `top`, `right`, `bottom`, `rotation`, `box`, `width`, `height`, `contains(x, y, slop)` |
| `hits(x, y, slop=0)` | the elements under a point, the topmost (drawn last) first |
| `within(rect, touching=False)` | the elements inside a rectangle given by any two corners; `touching`: also those it overlaps |
| `bounds(indices)` | the box around some elements |
| `to_dict()` | for JSON: sizes and boxes in mm to a hundredth |

`slop` makes a thin line easier to hit: a few screen pixels in mm.

`to_dict()` is what the server's web page is to get:

```json
{"width_mm": 40.0, "height_mm": 18.6, "layout": "free",
 "elements": [{"index": 0, "kind": "text", "box": [2.0, 1.5, 21.83, 6.2], "rotation": 0}]}
```

## Snapping

```python
box = editing.moved(start_box, dx, dy)  # the dragged elements' bounds
snapped = editing.snap(box, geometry, exclude=selection, tolerance=1.0, grid=0.0)
box = editing.moved(box, snapped.dx, snapped.dy)
```

On each axis the box snaps to the nearest guide one of its edges or its
middle is within `tolerance` mm of: the label's edges and middle, and the
edges and middles of the elements not in `exclude` (`guides()` lists
them). With `grid` (mm, 0: none) its edges also snap to multiples of it;
a guide wins over the grid at the same distance. `Snap.guides` are the
lines the box lies on afterwards (`Guide`: `axis` "x" for a vertical line
at x = `position`, "y" for a horizontal one; `source` "label", "element"
or "grid"; `index` of the element), to draw while dragging. The
tolerance is in mm: a few screen pixels divided by the zoom's pixels per
mm. For a resize, snap the dragged edge alone: a box of no width (or
height) at that edge.

## Edits

Each edit takes a label and returns a new one; the label given stays as it
was (the undo history keeps labels), and an edit that changes nothing
returns the label itself. Elements are named by their index in
`label.elements`; each changed element is checked as an element of a
label file is (`model.validate_element()`): numbers are clamped to their
ranges, a position is never negative. Positions and sizes are rounded to
a hundredth, as `freelayout.adopt()` rounds them.

| Edit | |
|---|---|
| `move(label, indices, dx, dy)` | moved together; no further left or up than the element nearest the edge can go, so they keep their places to one another |
| `align(label, geometry, indices, edge)` | the boxes lined up: `edge` one of `ALIGNMENTS` ("left", "center", "right" along the label, "top", "middle", "bottom" across it), at the box around them all, or at the label for one element alone |
| `distribute(label, geometry, indices, axis)` | the same space between neighbouring boxes along "x" or "y"; the first and the last stay, fewer than three elements too |
| `resize(label, geometry, index, box)` | the element made the size of a new box (its own with an edge or a corner dragged), its top left corner at the box's |

How `resize()` scales each kind:

- a shape: its width and height, each on its own (one of height 0, down to
  the label's edge, gets a height of its own);
- a linear code: its module by the change along its bars' run, the bars'
  height by the change across it (turned codes the other way round);
- a text (its size and every length of it: padding, frame, outline,
  shadow, wrap width), a 2D code (its module), an image and a symbol keep
  their proportions, by the side that changed more;
- a page fills the label from its position on: it is only moved.

An element of size 0 (its default size) gets the size it is drawn at,
scaled; a size never shrinks below `LEAST_PT`, `LEAST_MM` or
`LEAST_MODULE_MM` (0 would be the default size again). The element is as
close to the box as it can be drawn, a code's module in whole printer
dots: the next render says exactly where it is.

### Only the free layout moves

Moving, aligning, distributing and resizing need the free layout; on a
label of the flowing layout they raise `NotFree`. There the elements'
places follow from their order, their sizes, the spacing and the label's
alignment: a drag could at most reorder them, and that is the sidebar's
job (its up and down buttons). Geometry, hit testing and snapping work on
both layouts, so the flowing layout's elements can be shown, selected and
highlighted. To move them, the label is switched to the free layout,
which keeps every element where it was (`freelayout.adopt()`, the
sidebar's "Layout" row).

## Undo

The window keeps its undo as it is: `tavletta.history.History`, a copy of
the label after each step. `Editor` wraps the same history, so undo goes
back over the sidebar's changes and the mouse's in turn:

```python
editor = editing.Editor(window.label, window.history)
editor.apply(editing.align(editor.label, geometry, selection, "left"))  # one step

editor.show(editing.move(start, selection, dx, dy))  # while dragging: no step
editor.commit()  # the drag ended: one step
editor.cancel()  # or called off (Escape): back to the last step

editor.changed(window.label)  # the sidebar changed and recorded the label
editor.undo(), editor.redo()  # a drag going on is called off first
```

`apply()` and `commit()` take History's `key`: nudges with the arrow keys
in quick succession can be one step, as typing is. `apply()` returns
False for an edit that changed nothing; no empty step is recorded.

## Cost

Measured on a label of 50 elements (texts, some turned, QR codes in all
four turns, shapes, symbols), Python 3.14 on an Intel Core i7-1365U:

| | ms |
|---|---|
| render with the boxes, free layout, 300 / 600 dpi (warm caches) | 16 / 30 |
| of that, the boxes | 1.0 / 3.8 |
| render with the boxes, flowing layout, 300 / 600 dpi | 24 / 67 |
| `Geometry.of()` | 0.08 |
| `hits()`, `within()` | 0.01 |
| `snap()` against 49 elements and a grid | 0.65 |
| `move()`, `align()` of 10, `resize()` | 0.6 to 0.8 |
| `to_dict()` | 0.1 |

A drag needs no render per mouse event: the dragged boxes move with the
pointer (`moved()`), `snap()` and `move()` take about 1.3 ms together, well
within a frame at 60 fps (16.7 ms). The preview renders after the drag
(or, throttled, during it), and its boxes are the new geometry.
