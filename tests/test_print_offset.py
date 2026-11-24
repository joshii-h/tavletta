"""The cab's print offset (offset_mm): only the job moves, every preview
(the window's, the REST API's PNG and JSON with its boxes, the web page's
request, the MCP tool, the command line's --output) shows the label as it
is laid out, and they all agree. A label laid out low to make up for a
printer that prints high sends, moved into place and with the offset, the
same job as before."""

from __future__ import annotations

import base64
import io
import json

import pytest
from conftest import gtk_available, ink_bbox, render_now
from PIL import Image

from tavletta.backends import cab, get_backend
from tavletta.backends.base import Distance, PrinterInfo, PrintOptions, shift_along
from tavletta.model import CodeElement, Label, TextElement
from tavletta.render import mm_to_px

DOTS = 12.0  # an EOS2/300: 12 dots per mm


def tag(shift_mm: float = 0.0) -> Label:
    """A 40 x 18.6 mm label across the roll, laid out freely as it was
    taken over from another program: its content `shift_mm` lower than
    centred (the old layout: 1.25)."""
    elements = [
        TextElement(x_mm=2.5, y_mm=2.18, text="Asset", bold=True, size_pt=14.17),
        TextElement(x_mm=2.54, y_mm=6.98, text="Office", size_pt=14.17),
        TextElement(x_mm=2.57, y_mm=13.44, text="{Number}", bold=True, size_pt=12.76),
        CodeElement(x_mm=23.49, y_mm=2.5, data="{Number}", symbology="qr", module_mm=0.67),
    ]
    for element in elements:
        element.y_mm = round(element.y_mm + shift_mm, 2)
    return Label(
        printer="cab",
        tape_mm=18.6,
        length_mm=40,
        auto_length=False,
        gap_mm=1.4,
        orientation="across",
        layout="free",
        elements=elements,
    )


def filled(label: Label, number: str = "100001") -> Label:
    label.elements[2].text = number
    label.elements[3].data = number
    return label


def options(offset=None, **settings) -> PrintOptions:
    if offset is not None:
        settings["offset_mm"] = offset
    return PrintOptions(gap_mm=1.4, settings={"upside_down": True, **settings})


# --- the setting ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "value"),
    [(None, 0.0), (1.25, 1.25), (-2, -2.0), (9, 5.0), (-9, -5.0), (True, 0.0), ("1", 0.0)]
    + [(float("nan"), 0.0), (float("inf"), 0.0), (10**400, 0.0)],
)
def test_a_distance_setting_clamps_and_drops_junk(raw, value):
    assert Distance("offset_mm", -5.0, 5.0).value(raw) == value


def test_the_cab_offers_the_offset():
    assert Distance("offset_mm", -5.0, 5.0) in get_backend("cab").capabilities.settings


@pytest.mark.parametrize("mode", ["1", "L", "RGB"])
def test_shift_along_moves_whole_rows(mode):
    image = Image.new(mode, (4, 20), "white")
    image.putpixel((1, 5), 0)
    image.info["dots_per_mm"] = 12.0
    down = shift_along(image, 0.25, DOTS)  # 3 rows
    assert ink_bbox(down.convert("L"), 255) == (1, 8, 2, 9)
    assert down.info["dots_per_mm"] == 12.0 and down.mode == mode
    assert ink_bbox(shift_along(image, -0.5, DOTS).convert("L"), 255) is None  # cut off
    assert shift_along(image, 0.04, DOTS) is image  # less than half a dot


# --- the job -------------------------------------------------------------------------


def test_the_offset_moves_only_what_is_sent():
    backend = get_backend("cab")
    plain = backend.prepare(filled(tag()), options())
    moved = backend.prepare(filled(tag()), options(1.25))
    assert moved.size == plain.size and moved.info == plain.info
    left, top, right, bottom = ink_bbox(plain, "white")
    assert ink_bbox(moved, "white") == (left, top + 15, right, bottom + 15)


def test_the_offset_moves_along_the_feed_for_labels_along_the_roll():
    backend = get_backend("cab")
    label = filled(tag())
    label.orientation = "along"
    plain = backend.prepare(label, options())
    moved = backend.prepare(label, options(-0.5))
    left, top, right, bottom = ink_bbox(plain, "white")
    assert ink_bbox(moved, "white") == (left, top - 6, right, bottom - 6)


def test_a_label_moved_into_place_sends_the_old_job_with_the_offset():
    """The old layout (content 1.25 mm low, made up for a printer that
    prints that much high) and the label laid out centred with offset
    1.25: the same image, the same S and I lines, dot for dot."""
    backend = get_backend("cab")
    old = backend.prepare(filled(tag(1.25)), options())
    new = backend.prepare(filled(tag()), options(1.25))
    assert old.tobytes() == new.tobytes()
    jobs = [
        cab.build_job(i, DOTS, 1.4, 1, "transfer", 50, True, i.info["size_mm"]) for i in (old, new)
    ]
    assert jobs[0] == jobs[1]
    assert b"S l1;0,0,18.6,20,40\r\nO R\r\nI:Label;0,0,0;TAVLETTA\r\n" in jobs[0]


def test_the_offset_in_settings_json(tmp_path, monkeypatch):
    from tavletta.ui import settings as config

    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    settings = config.Settings({"printers": {"cab": {"offset_mm": 1.25, "host": "c"}}})
    chosen = settings.printer(get_backend("cab")).backend_settings()
    assert chosen["offset_mm"] == 1.25


# --- the server's previews -------------------------------------------------------------


@pytest.fixture
def cab_server(make_server, server_env, monkeypatch):
    from test_server_config import write

    from tavletta.server.config import load_printers

    info = PrinterInfo("EOS2/300", None, None, None, "IDLE", dpi=304.8, pitch_mm=20.0)
    monkeypatch.setattr(get_backend("cab"), "query", lambda host: info)
    tag().save(server_env.labels / "tag.tavletta")
    text = (
        '[printers.moved]\nbackend = "cab"\nhost = "cab.example.com"\n'
        "[printers.moved.settings]\nupside_down = true\noffset_mm = 1.25\n"
        '[printers.plain]\nbackend = "cab"\nhost = "cab2.example.com"\n'
        "[printers.plain.settings]\nupside_down = true\n"
    )
    printers = load_printers(write(server_env.tmp / "printers.toml", text))
    return make_server(printers=printers)


def _png(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGB")


def _post_preview(base, **body):
    from test_server import call

    request = {"label": "tag", "data_rows": [{"Number": "100001"}], **body}
    status, _headers, answer = call(base, "POST", "/api/v1/preview", body=request)
    assert status == 200, answer
    return answer


def test_every_preview_shows_the_label_and_agrees(cab_server, server_env, tmp_path):
    """The REST API's PNG, the web page's request (format json, the same
    body as print.js sends), the MCP tool and the command line's --output
    show the label as laid out: the same image, the offset or not; the
    printer's view is what is sent, moved."""
    server, base = cab_server
    api = _png(_post_preview(base, printer="moved"))
    web = _post_preview(base, printer="moved", format="json")
    assert _png(base64.b64decode(web["png"])).tobytes() == api.tobytes()
    assert _png(_post_preview(base, printer="plain")).tobytes() == api.tobytes()

    from tavletta.server.mcp import Mcp

    answer = Mcp(server.service).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "preview_label",
                "arguments": {
                    "label": "tag",
                    "printer": "moved",
                    "data_rows": [{"Number": "100001"}],
                },
            },
        }
    )
    image = answer["result"]["content"][0]["data"]
    assert _png(base64.b64decode(image)).tobytes() == api.tobytes()

    # the printer's view: the image as sent, moved by 15 dots
    sent = _png(_post_preview(base, printer="moved", view="printer"))
    plain = _png(_post_preview(base, printer="plain", view="printer"))
    left, top, right, bottom = ink_bbox(plain, "white")
    assert ink_bbox(sent, "white") == (left, top + 15, right, bottom + 15)
    assert ink_bbox(api, "white") == ink_bbox(plain, "white")


def test_the_boxes_lie_on_the_ink(cab_server):
    """JSON geometry: each element's box (mm from the label's top left)
    around its ink in the PNG, the offset changing nothing."""
    _server, base = cab_server
    answer = _post_preview(base, printer="moved", format="json")
    image = _png(base64.b64decode(answer["png"]))
    geometry = answer["geometry"]
    scale = geometry["dpi"] / 25.4
    assert [e["index"] for e in geometry["elements"]] == [0, 1, 2, 3]
    for element in geometry["elements"]:
        left, top, right, bottom = element["box"]
        box = (
            round(left * scale),
            round(top * scale),
            round(right * scale),
            round(bottom * scale),
        )
        ink = ink_bbox(image.crop(box), "white")
        assert ink is not None, element  # the element's ink is in its box
        # and nothing of it outside: the box widened by a dot holds the same ink
        wider = (box[0] - 2, box[1] - 2, box[2] + 2, box[3] + 2)
        inside = image.crop(wider)
        cut = inside.copy()
        cut.paste((255, 255, 255), (2, 2, 2 + box[2] - box[0], 2 + box[3] - box[1]))
        assert ink_bbox(cut, "white") is None, element
    assert geometry == _post_preview(base, printer="plain", format="json")["geometry"]


def test_the_command_lines_output_shows_the_label(server_env, tmp_path, monkeypatch):
    """tavletta print --dry-run --output: the label as laid out, as the
    API's preview, whatever the offset."""
    from tavletta import cli
    from tavletta.ui import settings as config

    path = tmp_path / "tag.tavletta"
    tag().save(path)
    data = tmp_path / "numbers.csv"
    data.write_text("Number\n100001\n", encoding="utf-8")
    outputs = []
    for offset in (0.0, 1.25):
        config.Settings({"printers": {"cab": {"host": "", "offset_mm": offset}}}).save()
        out = tmp_path / ("out-%s.png" % offset)
        code = cli.main(
            ["print", str(path), "--data", str(data), "--dry-run", "--output", str(out)]
        )
        assert code == 0
        outputs.append(_png(out.read_bytes()))
    assert outputs[0].tobytes() == outputs[1].tobytes()


# --- the window ------------------------------------------------------------------------


@pytest.mark.gui
@pytest.mark.skipif(not gtk_available(), reason="no display for GTK")
def test_the_windows_preview_agrees_with_the_api(make_window, cab_server, gui_env):
    """The window's preview of the label on a cab with the offset: the
    API's PNG, and its row of the label's top edge 0 (the raster is the
    label)."""
    _server, base = cab_server
    api = _png(_post_preview(base, printer="moved"))
    win = make_window({"printers": {"cab": {"host": "cab.local", "offset_mm": 1.25}}})
    gui_env.query_result = PrinterInfo("EOS2/300", None, None, None, "IDLE", dpi=304.8)
    shown = []
    win.preview.show_image = lambda image, *args: shown.append((image, args))
    win.set_label(filled(tag()), None)
    render_now(win)
    image, (_length, _height, _text, raster) = shown[-1]
    assert raster[1] == 0
    assert image.convert("RGB").tobytes() == api.tobytes()
    assert mm_to_px(18.6, raster[0]) == image.height


def test_printers_toml_takes_the_offset_within_its_range(server_env):
    from test_server_config import write

    from tavletta.server.config import ConfigError, load_printers

    text = '[printers.c]\nbackend = "cab"\nhost = "c"\n[printers.c.settings]\noffset_mm = %s\n'
    path = server_env.tmp / "printers.toml"
    assert load_printers(write(path, text % "-1.5"))["c"].settings["offset_mm"] == -1.5
    for bad in ("6", '"1"', "true"):
        with pytest.raises(ConfigError, match="offset_mm"):
            load_printers(write(path, text % bad))


def test_the_label_file_is_not_changed_by_the_offset(tmp_path):
    path = tmp_path / "tag.tavletta"
    tag().save(path)
    before = json.loads(path.read_text())
    get_backend("cab").prepare(filled(Label.load(path)), options(1.25))
    assert json.loads(path.read_text()) == before


@pytest.mark.gui
@pytest.mark.skipif(not gtk_available(), reason="no display for GTK")
def test_the_window_offers_the_offset_in_the_printer_group(make_window):
    gi = pytest.importorskip("gi")
    gi.require_version("Adw", "1")
    from gi.repository import Adw
    from test_app import one

    win = make_window({"printers": {"cab": {"host": "cab.local", "offset_mm": 1.25}}})
    win.set_label(filled(tag()), None)
    win.rebuild_sidebar()
    row = one(win.sidebar, Adw.SpinRow, title="Print offset")
    assert row.get_value() == 1.25 and "mm" in row.get_subtitle()
    assert row.get_adjustment().get_lower() == -5 and row.get_adjustment().get_upper() == 5
    row.set_value(-0.5)
    assert win.printer_settings.get("offset_mm") == -0.5
    assert not win.dirty  # a printer setting, not the label's
