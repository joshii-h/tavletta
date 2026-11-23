"""The server warns of other labels loaded than the label's, as the window
and the command line do (job.other_labels()): in the preview and the print
request for a printer of printers.toml, and in the job when it prints."""

from __future__ import annotations

import threading
import time

import pytest
from test_server import call, wait_for
from test_server_config import write

from tavletta.backends import get_backend
from tavletta.backends.base import PrinterInfo
from tavletta.model import Label, TextElement
from tavletta.server import service as service_module
from tavletta.server.config import load_printers
from tavletta.ui import i18n

CODE = "other_labels_loaded"
CALIBRATE = (
    "The label is 50 × 25 mm with a gap of 3.0 mm, so a new one starts every 28.0 mm. "
    "The printer measured a label every 20.0 mm. Check the label size and the gap, or "
    "calibrate the printer."
)


@pytest.fixture
def zpl(monkeypatch, server_env):
    """The ZPL backend answering as a printer set to gap labels that
    measured `info.pitch_mm`; what it printed; a 50 × 25 mm label (3 mm gap:
    a new one every 28 mm) "zpl" in the labels folder."""
    backend = get_backend("zpl")
    info = PrinterInfo(
        "GX420d-200dpi", None, None, None, "IDLE", dpi=203.2, pitch_mm=20.0, media_type="gap"
    )
    state = {"info": info, "queries": 0}
    printed: list = []

    def query(host):
        state["queries"] += 1
        return state["info"]

    monkeypatch.setattr(backend, "query", query)
    monkeypatch.setattr(
        backend,
        "print_label",
        lambda image, host, options, progress=None: printed.append(options),
    )
    label = Label(printer="zpl", auto_length=False, orientation="across")
    label.set_media_size(50, 25)
    label.elements = [TextElement(text="x")]
    label.save(server_env.labels / "zpl.tavletta")
    state["printed"] = printed
    return state


def _named(make_server, server_env, settings: str = ""):
    text = '[printers.z]\nbackend = "zpl"\nhost = "zpl.local"\n'
    if settings:
        text += "[printers.z.settings]\n" + settings
    printers = load_printers(write(server_env.tmp / "printers.toml", text))
    return make_server(printers=printers)[1]


def _preview(base, **body):
    status, _r, answer = call(
        base, "POST", "/api/v1/preview", body={"label": "zpl", "format": "json", **body}
    )
    assert status == 200, answer
    return answer["warnings"]


def _codes(warnings) -> list[str]:
    return [w["code"] for w in warnings]


def _print(base, **body):
    status, _r, job = call(base, "POST", "/api/v1/print", body={"label": "zpl", **body})
    assert status in (200, 202), job
    path = "/api/v1/jobs/" + job["id"]
    wait_for(lambda: call(base, "GET", path)[2]["state"] in ("done", "failed"))
    return job, call(base, "GET", path)[2]


def test_preview_warns_of_another_label_distance(make_server, server_env, zpl):
    base = _named(make_server, server_env)
    (warning,) = [w for w in _preview(base, printer="z") if w["code"] == CODE]
    assert warning["message"].replace("⁨", "").replace("⁩", "") == CALIBRATE
    status, response, _png = call(base, "POST", "/api/v1/preview", body={"label": "zpl"})
    assert status == 200 and CODE in response.getheader("X-Tavletta-Warnings").split(",")
    assert zpl["queries"] == 1  # the answer of the last seconds counts


def test_the_job_warning_is_in_the_language_of_each_request(
    make_server, server_env, zpl, monkeypatch
):
    base = _named(make_server, server_env)
    job, _done = _print(base)

    class Catalog(i18n.gettext.NullTranslations):
        def gettext(self, message):
            return "DE:" + message

    monkeypatch.setattr(i18n, "translation_for", lambda languages: Catalog())
    _s, _r, german = call(
        base, "GET", "/api/v1/jobs/" + job["id"], headers={"Accept-Language": "de"}
    )
    (message,) = [w["message"] for w in german["warnings"] if w["code"] == CODE]
    assert message.startswith("DE:The label is")


def test_no_warning_for_the_same_labels(make_server, server_env, zpl):
    zpl["info"].pitch_mm = 28.0
    base = _named(make_server, server_env)
    assert CODE not in _codes(_preview(base))  # the only printer: the default
    job, done = _print(base)
    assert CODE not in _codes(job["warnings"]) and CODE not in _codes(done["warnings"])
    assert done["state"] == "done"


def test_a_print_is_warned_of_and_printed_anyway(make_server, server_env, zpl):
    base = _named(make_server, server_env)
    job, done = _print(base, printer="z")
    assert CODE in _codes(job["warnings"])  # in the answer to the request
    assert _codes(done["warnings"]).count(CODE) == 1  # and once in the job
    assert done["state"] == "done" and len(zpl["printed"]) == 1


def test_a_continuous_job_on_a_gap_printer_is_not_warned_of(make_server, server_env, zpl):
    """The printer's distance is for gap labels; a job for continuous media
    senses none: nothing to compare."""
    base = _named(make_server, server_env, 'media = "continuous"\n')
    assert CODE not in _codes(_preview(base))
    job, done = _print(base)
    assert CODE not in _codes(job["warnings"]) and CODE not in _codes(done["warnings"])


def test_the_job_warns_of_what_the_printer_reports_when_it_prints(make_server, server_env, zpl):
    """Without printers.toml the request is not asked about (any address);
    the job asks the printer before it prints, as the command line does,
    and warns of what it reported."""
    _server, base = make_server(settings={"printers": {"zpl": {"host": "zpl.local"}}})
    job, done = _print(base)
    assert CODE not in _codes(job["warnings"])
    assert CODE in _codes(done["warnings"]) and done["state"] == "done"


@pytest.mark.parametrize(("first", "then"), [(20.0, 28.0), (28.0, 20.0)])
def test_a_roll_changed_before_the_job_prints(
    make_server, server_env, zpl, monkeypatch, first, then
):
    """The job's warning is of the labels it printed on: the request is
    told what the printer answered then, the job what it answered when the
    job began."""
    monkeypatch.setattr(service_module, "STATUS_SECONDS", 0)  # asked anew every time
    answers = [first, then]

    def query(host):
        zpl["info"].pitch_mm = answers.pop(0) if answers else then
        return zpl["info"]

    monkeypatch.setattr(get_backend("zpl"), "query", query)
    base = _named(make_server, server_env)
    job, done = _print(base)
    assert (CODE in _codes(job["warnings"])) == (first == 20.0)
    assert (CODE in _codes(done["warnings"])) == (then == 20.0)
    assert done["state"] == "done"


@pytest.mark.parametrize("hangs", [True, False])
def test_an_unreachable_printer_gives_no_warning_and_no_long_wait(
    make_server, server_env, zpl, monkeypatch, hangs
):
    """A printer that does not answer: the preview waits STATE_WAIT at most
    and warns of nothing; the question goes on without it."""
    monkeypatch.setattr(service_module, "STATE_WAIT", 0.2)
    released = threading.Event()

    def silent(host):
        if hangs:
            released.wait(30)
        raise OSError("no answer")

    monkeypatch.setattr(get_backend("zpl"), "query", silent)
    base = _named(make_server, server_env)
    try:
        started = time.monotonic()
        assert CODE not in _codes(_preview(base, printer="z"))
        assert time.monotonic() - started < 5.0
    finally:
        released.set()
