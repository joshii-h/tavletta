"""The print method and how the labels are found, in a printer's status: as
set on the printer (media.print_method, media.media_type) and as the
server's jobs do (media.job_print_method, media.job_media_type), which a
printer's "method" and "media" settings choose for each job."""

from __future__ import annotations

import pytest
from test_server import ASK, call
from test_server_config import write

from tavletta.backends import get_backend
from tavletta.backends.base import PrinterInfo
from tavletta.server.config import load_printers


@pytest.fixture
def answering(monkeypatch, server_env):
    """cab and ZPL printers that are set up for thermal direct and gap labels."""
    info = PrinterInfo(
        "Printer", None, None, None, "IDLE", print_method="direct", media_type="gap"
    )
    for backend in ("cab", "zpl"):
        monkeypatch.setattr(get_backend(backend), "query", lambda host: info)
    return info


def _printers(make_server, server_env, text: str):
    return make_server(printers=load_printers(write(server_env.tmp / "printers.toml", text)))[1]


def _methods(status: dict) -> tuple:
    media = status["media"]
    return media["print_method"], media["job_print_method"]


def _media(status: dict) -> tuple:
    media = status["media"]
    return media["media_type"], media["job_media_type"]


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ('method = "transfer"\n', ("direct", "transfer")),  # sent with each job
        ('method = "printer"\n', ("direct", "direct")),
        ("", ("direct", "direct")),  # the default: as set on the printer
    ],
)
def test_a_cab_of_printers_toml(make_server, server_env, answering, settings, expected):
    text = '[printers.c]\nbackend = "cab"\nhost = "cab.example.com"\n'
    if settings:
        text += "[printers.c.settings]\n" + settings
    base = _printers(make_server, server_env, text)
    status, _r, body = call(base, "GET", "/api/v1/printers/c/status", headers=ASK)
    assert status == 200 and _methods(body) == expected
    _s, _r, listed = call(base, "GET", "/api/v1/printers", headers=ASK)
    assert _methods(listed["printers"][0]["status"]) == expected


def test_a_zpl_printer_prints_as_set_on_it(make_server, server_env, answering):
    """ZPL jobs do not choose a print method: the one set on the printer."""
    base = _printers(
        make_server, server_env, '[printers.z]\nbackend = "zpl"\nhost = "zpl.example.com"\n'
    )
    _s, _r, body = call(base, "GET", "/api/v1/printers/z/status", headers=ASK)
    assert _methods(body) == ("direct", "direct")
    assert _media(body) == ("gap", "gap")


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ('media = "mark"\n', ("gap", "mark")),  # ^MN with each job
        ('media = "printer"\n', ("gap", "gap")),
        ("", ("gap", "gap")),  # the default: as set on the printer
    ],
)
def test_how_the_jobs_of_a_zpl_printer_find_the_labels(
    make_server, server_env, answering, settings, expected
):
    text = '[printers.z]\nbackend = "zpl"\nhost = "zpl.example.com"\n'
    if settings:
        text += "[printers.z.settings]\n" + settings
    base = _printers(make_server, server_env, text)
    _s, _r, body = call(base, "GET", "/api/v1/printers/z/status", headers=ASK)
    assert _media(body) == expected


def test_without_printers_toml_the_apps_setting_counts(make_server, answering):
    """Jobs to an address print with the app's settings of the model."""
    _server, base = make_server(
        settings={"printers": {"cab": {"host": "cab.example.com", "method": "transfer"}}}
    )
    _s, _r, body = call(base, "GET", "/api/v1/printers/cab/status", headers=ASK)
    assert _methods(body) == ("direct", "transfer")


def test_unknown_when_the_printer_does_not_tell(make_server, server_env, answering):
    answering.print_method = answering.media_type = ""
    _server, base = make_server(settings={"printers": {"cab": {"host": "cab.example.com"}}})
    _s, _r, body = call(base, "GET", "/api/v1/printers/cab/status", headers=ASK)
    assert _methods(body) == (None, None) and _media(body) == (None, None)
