"""The printers found on USB and the network, first in the window's model
list under "Found", the models under "All models"; and the search of all
models behind it (backends.find_all)."""

from __future__ import annotations

import threading
import time

import pytest
from conftest import gtk_available, pump

from tavletta import backends as registry
from tavletta.backends import find_all, get_backend
from tavletta.backends.base import FoundPrinter

ZEBRA = FoundPrinter(
    "Zebra Technologies ZTC GX420d (USB)",
    "usb://Zebra%20Technologies/ZTC%20GX420d?serial=1",
    "/dev/usb/lp0",
)
CAB = FoundPrinter("cab EOS2/300 (00:11:22)", "cab-office.local", "192.0.2.7")


# --- the search of all models -------------------------------------------------------


def test_find_all_searches_every_model_side_by_side(monkeypatch):
    started, release = [], threading.Event()

    def searching(found):
        def discover(timeout=3.0):
            started.append(found)
            assert release.wait(5)
            return found

        return discover

    def broken(timeout=3.0):
        raise OSError("no D-Bus")

    for backend in registry.backends():
        monkeypatch.setattr(backend, "discover", searching([]))
    monkeypatch.setattr(get_backend("zpl"), "discover", searching([ZEBRA]))
    monkeypatch.setattr(get_backend("cab"), "discover", searching([CAB]))
    monkeypatch.setattr(get_backend("system"), "discover", broken)
    result = []
    thread = threading.Thread(target=lambda: result.append(find_all()))
    thread.start()
    deadline = time.monotonic() + 5
    while len(started) < len(registry.backends()) - 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    # every model's search runs at the same time, none waits for another
    assert len(started) == len(registry.backends()) - 1
    release.set()
    thread.join(5)
    order = [b.id for b in registry.backends()]
    assert [(b.id, p) for b, p in result[0]] == sorted(
        [("cab", CAB), ("zpl", ZEBRA)], key=lambda item: order.index(item[0])
    )


# --- the window -----------------------------------------------------------------------

pytestmark_gui = pytest.mark.skipif(not gtk_available(), reason="no display for GTK")


def _model_row(win):
    gi = pytest.importorskip("gi")
    gi.require_version("Adw", "1")
    from gi.repository import Adw
    from test_app import one

    return one(win.sidebar, Adw.ComboRow, title="Printer model")


def _items(row) -> list[str]:
    model = row.get_model()
    return [model.get_item(i).get_string() for i in range(model.get_n_items())]


@pytestmark_gui
@pytest.mark.gui
def test_without_printers_found_the_list_is_the_models(make_window, gui_env):
    win = make_window()
    row = _model_row(win)
    names = [registry.backends()[i].name for i in range(len(registry.backends()))]
    assert len(_items(row)) == len(names)
    assert row.get_header_factory() is None  # a plain list, no headings
    assert gui_env.finds == 1  # searched once, when the window opened


@pytestmark_gui
@pytest.mark.gui
def test_printers_found_come_first_under_found(make_window, gui_env):
    zpl, cab = get_backend("zpl"), get_backend("cab")
    gui_env.found_all = [(cab, CAB), (zpl, ZEBRA)]
    win = make_window()
    row = _model_row(win)
    assert pump(lambda: len(_items(row)) == len(registry.backends()) + 2)
    items = _items(row)
    assert items[:2] == [
        "cab EOS2/300 (00:11:22) · cab EOS2, EOS5, SQUIX 4, MACH 4S",
        "Zebra Technologies ZTC GX420d (USB) · Zebra ZPL",
    ]
    assert row.get_header_factory() is not None
    # the headings: "Found" over the printers, "All models" over the models
    model = row.get_model()
    assert [tuple(model.get_section(i)) for i in (0, 2)] == [(0, 2), (2, len(items))]
    # the model of the label is chosen (no printer found at its address)
    assert items[row.get_selected()] == "Brother VC-500W"


@pytestmark_gui
@pytest.mark.gui
def test_choosing_a_printer_found_takes_its_model_and_address(make_window, gui_env):
    gui_env.found_all = [(get_backend("zpl"), ZEBRA)]
    win = make_window()
    row = _model_row(win)
    assert pump(lambda: _items(row)[0].startswith("Zebra"))
    row.set_selected(0)
    assert win.label.printer == "zpl"
    assert win.printer_settings.host == ZEBRA.host
    assert pump(lambda: ZEBRA.host in gui_env.queries)
    # the rebuilt list shows the printer as the one chosen
    row = _model_row(win)
    assert _items(row)[row.get_selected()].startswith("Zebra Technologies ZTC GX420d")
    # and a model of "All models": that model, its own address
    row.set_selected(_items(row).index("cab EOS2, EOS5, SQUIX 4, MACH 4S"))
    assert win.label.printer == "cab"


@pytestmark_gui
@pytest.mark.gui
def test_the_search_button_searches_every_model_again(make_window, gui_env):
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    from test_app import one

    win = make_window()
    assert gui_env.finds == 1
    gui_env.found_all = [(get_backend("zpl"), ZEBRA)]
    discovers = gui_env.discovers
    one(win.sidebar, Gtk.Button, tooltip="Search for printers").emit("clicked")
    assert pump(lambda: gui_env.finds == 2 and gui_env.discovers == discovers + 1)
    row = _model_row(win)
    assert pump(lambda: _items(row)[0].startswith("Zebra"))


@pytestmark_gui
@pytest.mark.gui
def test_a_new_window_takes_the_last_minutes_search(make_window, gui_env):
    gui_env.found_all = [(get_backend("zpl"), ZEBRA)]
    first = make_window()
    assert pump(lambda: first.printer.found_all)
    second = make_window()
    assert gui_env.finds == 1
    assert _items(_model_row(second))[0].startswith("Zebra")


@pytestmark_gui
@pytest.mark.gui
def test_a_search_of_the_model_adds_to_found(make_window, gui_env):
    """What the search of the label's model finds is in "Found" too."""
    gui_env.found = [FoundPrinter("Brother VC-500W (Office)", "VC.local", "192.0.2.9")]
    win = make_window()  # no address: the window searches the model
    office = "Brother VC-500W (Office) · Brother VC-500W"
    assert pump(lambda: _items(_model_row(win))[0] == office)
    row = _model_row(win)  # the chosen printer: its address taken
    assert win.printer_settings.host == "VC.local"
    assert _items(row)[row.get_selected()] == office
