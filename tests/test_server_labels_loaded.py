"""The server warns of other labels loaded than the label's, as the window
and the command line do (job.other_labels()): in the preview and the print
request for a printer of printers.toml, and in the job when it prints."""

from __future__ import annotations

import threading
import time

import pytest
from test_server import ASK, call, wait_for
from test_server_config import write

from tavletta.backends import backends, get_backend
from tavletta.backends.base import PrinterBusy, PrinterInfo
from tavletta.model import Label, TextElement
from tavletta.server import service as service_module
from tavletta.server.config import load_printers
from tavletta.server.service import Service
from tavletta.ui import i18n

CODE = "other_labels_loaded"
CALIBRATE = (
    "The label is 50 \u00d7 25 mm with a gap of 3.0 mm, so a new one starts every 28.0 mm. "
    "The printer measured a label every 20.0 mm. Check the label size and the gap, or "
    "calibrate the printer."
)


class Clock:
    """A monotonic clock the test moves on (Service._monotonic)."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def zpl(monkeypatch, server_env):
    """The ZPL backend answering as a printer set to gap labels that
    measured `info.pitch_mm` (`answer` gives what it answers: replaced by
    tests); what it printed; a 50 x 25 mm label (3 mm gap: a new one every
    28 mm) "zpl" in the labels folder. A printer not known yet is waited for
    long (STATE_WAIT): a warning expected does not depend on how fast the
    machine is."""
    monkeypatch.setattr(service_module, "STATE_WAIT", 30.0)
    backend = get_backend("zpl")
    info = PrinterInfo(
        "GX420d-200dpi", None, None, None, "IDLE", dpi=203.2, pitch_mm=20.0, media_type="gap"
    )
    state: dict = {"info": info, "queries": 0, "printed": []}
    state["answer"] = lambda: state["info"]

    def query(host):
        state["queries"] += 1
        return state["answer"]()

    monkeypatch.setattr(backend, "query", query)
    monkeypatch.setattr(
        backend,
        "print_label",
        lambda image, host, options, progress=None: state["printed"].append(options),
    )
    label = Label(printer="zpl", auto_length=False, orientation="across")
    label.set_media_size(50, 25)
    label.elements = [TextElement(text="x")]
    label.save(server_env.labels / "zpl.tavletta")
    return state


@pytest.fixture
def held(monkeypatch):
    """Jobs wait before they ask their printer until the event is set: the
    answer to the request is read before a job changes its warnings."""
    gate = threading.Event()
    real = Service._for_the_printer

    def waiting(self, job, prepared):
        assert gate.wait(10), "the test never let the job go on"
        return real(self, job, prepared)

    monkeypatch.setattr(Service, "_for_the_printer", waiting)
    yield gate
    gate.set()


def _named(make_server, server_env, settings: str = ""):
    text = '[printers.z]\nbackend = "zpl"\nhost = "zpl.local"\n'
    if settings:
        text += "[printers.z.settings]\n" + settings
    printers = load_printers(write(server_env.tmp / "printers.toml", text))
    return make_server(printers=printers)


def _preview(base, **body):
    status, _r, answer = call(
        base, "POST", "/api/v1/preview", body={"label": "zpl", "format": "json", **body}
    )
    assert status == 200, answer
    return answer["warnings"]


def _codes(warnings) -> list[str]:
    return [w["code"] for w in warnings]


def _print(base, held=None, **body):
    """The answer to a print request and the job once it finished; `held`
    (the fixture) is set once the answer is read."""
    status, _r, job = call(base, "POST", "/api/v1/print", body={"label": "zpl", **body})
    assert status in (200, 202), job
    if held is not None:
        held.set()
    path = "/api/v1/jobs/" + job["id"]
    wait_for(lambda: call(base, "GET", path)[2]["state"] in ("done", "failed"))
    return job, call(base, "GET", path)[2]


def test_preview_warns_of_another_label_distance(make_server, server_env, zpl, monkeypatch):
    monkeypatch.setattr(service_module, "STATUS_SECONDS", 3600.0)  # not asked again
    _server, base = _named(make_server, server_env)
    (warning,) = [w for w in _preview(base, printer="z") if w["code"] == CODE]
    assert warning["message"].replace("\u2068", "").replace("\u2069", "") == CALIBRATE
    status, response, _png = call(base, "POST", "/api/v1/preview", body={"label": "zpl"})
    assert status == 200 and CODE in response.getheader("X-Tavletta-Warnings").split(",")
    assert zpl["queries"] == 1  # the answer of the last seconds counts


def test_the_job_warning_is_in_the_language_of_each_request(
    make_server, server_env, zpl, monkeypatch
):
    _server, base = _named(make_server, server_env)
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
    _server, base = _named(make_server, server_env)
    assert CODE not in _codes(_preview(base))  # the only printer: the default
    job, done = _print(base)
    assert CODE not in _codes(job["warnings"]) and CODE not in _codes(done["warnings"])
    assert done["state"] == "done"


def test_a_print_is_warned_of_and_printed_anyway(make_server, server_env, zpl, held):
    _server, base = _named(make_server, server_env)
    job, done = _print(base, held, printer="z")
    assert CODE in _codes(job["warnings"])  # in the answer to the request
    assert _codes(done["warnings"]).count(CODE) == 1  # and once in the job
    assert done["state"] == "done" and len(zpl["printed"]) == 1


def test_a_continuous_job_on_a_gap_printer_is_not_warned_of(make_server, server_env, zpl):
    """The printer's distance is for gap labels; a job for continuous media
    senses none: nothing to compare."""
    _server, base = _named(make_server, server_env, 'media = "continuous"\n')
    assert CODE not in _codes(_preview(base))
    job, done = _print(base)
    assert CODE not in _codes(job["warnings"]) and CODE not in _codes(done["warnings"])


def test_the_job_warns_of_what_the_printer_reports_when_it_prints(
    make_server, server_env, zpl, held
):
    """Without printers.toml the request is not asked about (any address);
    the job asks the printer before it prints, as the command line does,
    and warns of what it reported."""
    _server, base = make_server(settings={"printers": {"zpl": {"host": "zpl.local"}}})
    job, done = _print(base, held)
    assert CODE not in _codes(job["warnings"]) and zpl["queries"] == 1
    assert CODE in _codes(done["warnings"]) and done["state"] == "done"


@pytest.mark.parametrize(("first", "then"), [(20.0, 28.0), (28.0, 20.0)])
def test_a_roll_changed_before_the_job_prints(
    make_server, server_env, zpl, monkeypatch, held, first, then
):
    """The job's warning is of the labels it printed on: the request is
    told what the printer answered then, the job what it answered when the
    job began."""
    monkeypatch.setattr(service_module, "STATUS_SECONDS", 0)  # asked anew every time
    answers = [first, then]

    def answer():
        zpl["info"].pitch_mm = answers.pop(0)
        return zpl["info"]

    zpl["answer"] = answer
    _server, base = _named(make_server, server_env)
    job, done = _print(base, held)
    assert (CODE in _codes(job["warnings"])) == (first == 20.0)
    assert (CODE in _codes(done["warnings"])) == (then == 20.0)
    assert done["state"] == "done" and not answers


def test_a_printer_that_tells_nothing_of_the_labels_keeps_the_warning(
    make_server, server_env, zpl, monkeypatch, held
):
    """A Zebra still busy with the job before answers without a label
    distance: the job keeps the warning of the request."""
    monkeypatch.setattr(service_module, "STATUS_SECONDS", 0)  # asked anew every time
    busy = PrinterInfo("GX420d-200dpi", None, None, None, "BUSY", dpi=203.2, media_type="gap")
    answers = [zpl["info"], busy]
    zpl["answer"] = lambda: answers.pop(0)
    _server, base = _named(make_server, server_env)
    job, done = _print(base, held)
    assert CODE in _codes(job["warnings"]) and not answers
    assert CODE in _codes(done["warnings"]) and done["state"] == "done"


def test_a_job_asks_anew_after_an_error_of_the_last_seconds(make_server, server_env, zpl):
    """A status request while the printer was busy (an error remembered for
    a few seconds): the job does not take that error, it asks again and
    prints with what the printer reports."""
    answers: list = [PrinterBusy("busy"), zpl["info"]]

    def answer():
        outcome = answers.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    zpl["answer"] = answer
    _server, base = _named(make_server, server_env)
    status, _r, _body = call(base, "GET", "/api/v1/printers/z/status", headers=ASK)
    assert status == 502
    job, done = _print(base)
    assert CODE not in _codes(job["warnings"])  # the error: nothing known for the request
    assert CODE in _codes(done["warnings"]) and zpl["queries"] == 2
    assert zpl["printed"][0].info is zpl["info"]


@pytest.mark.parametrize("hangs", [True, False])
def test_an_unreachable_printer_gives_no_warning_and_no_long_wait(
    make_server, server_env, zpl, monkeypatch, hangs
):
    """A printer that does not answer: the preview waits STATE_WAIT at most
    and warns of nothing; the question goes on without it."""
    monkeypatch.setattr(service_module, "STATE_WAIT", 0.2)
    released = threading.Event()

    def silent():
        if hangs:
            released.wait(30)
        raise OSError("no answer")

    zpl["answer"] = silent
    _server, base = _named(make_server, server_env)
    try:
        started = time.monotonic()
        assert CODE not in _codes(_preview(base, printer="z"))
        assert time.monotonic() - started < 5.0
    finally:
        released.set()


# --- what is known of a printer counts without waiting ---------------------------------


def _hanging(zpl):
    """The printer's next answers wait for the event (a slow printer)."""
    released = threading.Event()
    answer = zpl["answer"]

    def slow():
        released.wait(30)
        return answer()

    zpl["answer"] = slow
    return released


def test_a_known_answer_counts_while_it_is_asked_anew(make_server, server_env, zpl):
    server, base = _named(make_server, server_env)
    clock = server.service._monotonic = Clock()
    assert CODE in _codes(_preview(base))  # nothing known yet: waited for
    clock.now += 30  # older than STATUS_SECONDS, within KNOWN_SECONDS
    released = _hanging(zpl)
    try:
        started = time.monotonic()
        assert CODE in _codes(_preview(base))  # the last answer, without waiting
        assert time.monotonic() - started < 5.0
        wait_for(lambda: zpl["queries"] == 2)  # asked anew in the background
        assert CODE in _codes(_preview(base))  # one question at a time
        assert zpl["queries"] == 2
    finally:
        released.set()


def test_a_recent_error_counts_as_nothing_known_without_waiting(make_server, server_env, zpl):
    server, base = _named(make_server, server_env)
    clock = server.service._monotonic = Clock()

    def refused():
        raise OSError("refused")

    zpl["answer"] = refused
    assert CODE not in _codes(_preview(base))
    clock.now += 10
    zpl["answer"] = lambda: zpl["info"]
    released = _hanging(zpl)
    try:
        started = time.monotonic()
        assert CODE not in _codes(_preview(base))
        assert time.monotonic() - started < 5.0
    finally:
        released.set()


def test_an_old_answer_is_waited_for_again(make_server, server_env, zpl, monkeypatch):
    server, base = _named(make_server, server_env)
    clock = server.service._monotonic = Clock()
    assert CODE in _codes(_preview(base))
    clock.now += service_module.KNOWN_SECONDS + 1
    monkeypatch.setattr(service_module, "STATE_WAIT", 0.2)
    released = _hanging(zpl)
    try:
        started = time.monotonic()
        assert CODE not in _codes(_preview(base))  # too old, the new one too late
        assert time.monotonic() - started >= 0.2
    finally:
        released.set()


def test_a_printer_printing_a_job_is_not_asked(make_server, server_env, zpl, monkeypatch):
    """While a job of this server prints on the printer, a preview takes
    what is known without asking it (a USB printer would keep the question
    waiting for the job)."""
    server, base = _named(make_server, server_env)
    clock = server.service._monotonic = Clock()
    gate, started = threading.Event(), threading.Event()

    def printing(image, host, options, progress=None):
        started.set()
        assert gate.wait(10)

    monkeypatch.setattr(get_backend("zpl"), "print_label", printing)
    status, _r, job = call(base, "POST", "/api/v1/print", body={"label": "zpl"})
    try:
        assert status == 202 and started.wait(10)
        asked = zpl["queries"]
        clock.now += 30
        assert CODE in _codes(_preview(base))  # the answer known
        clock.now += service_module.KNOWN_SECONDS
        assert CODE not in _codes(_preview(base))  # too old, and not asked
        assert zpl["queries"] == asked
    finally:
        gate.set()
    wait_for(lambda: call(base, "GET", "/api/v1/jobs/" + job["id"])[2]["state"] == "done")


def test_the_question_comes_after_the_render(make_server, server_env, zpl, monkeypatch):
    """An answer changes what the backend renders for the printer (its
    resolution): a request renders before it asks."""
    backend = get_backend("zpl")
    events: list[str] = []
    render = backend.render_label
    monkeypatch.setattr(
        backend, "render_label", lambda *a, **k: events.append("render") or render(*a, **k)
    )
    answer = zpl["answer"]
    zpl["answer"] = lambda: events.append("query") or answer()
    server, base = _named(make_server, server_env)
    assert CODE in _codes(_preview(base))
    assert "render" in events and events[-1] == "query"
    server.service._states.clear()  # nothing known: the print request asks
    server.service._answers.clear()
    events.clear()
    status, _r, job = call(base, "POST", "/api/v1/print", body={"label": "zpl", "wait": 10})
    assert CODE in _codes(job["warnings"])
    assert events.index("render") < events.index("query")


# --- which printers are asked ----------------------------------------------------------


def test_only_printers_that_tell_of_their_labels_are_asked():
    reporting = {b.id for b in backends() if b.reports_labels}
    assert reporting == {"cab", "zpl", "brother_ql_diecut"}


@pytest.mark.parametrize("model", ["dymo", "tspl", "epl", "niimbot"])
def test_die_cut_printers_that_tell_nothing_of_the_labels_are_not_asked(
    make_server, server_env, monkeypatch, model
):
    asked: list[str] = []
    monkeypatch.setattr(get_backend(model), "query", lambda host: asked.append(host))
    label = Label(printer=model, auto_length=False, elements=[TextElement(text="x")])
    label.set_media_size(40, 30)
    label.save(server_env.labels / "die.tavletta")
    text = '[printers.d]\nbackend = "%s"\nhost = "die.example.com"\n' % model
    printers = load_printers(write(server_env.tmp / "printers.toml", text))
    _server, base = make_server(printers=printers)
    status, _r, body = call(base, "POST", "/api/v1/preview", body={"label": "die"})
    assert status == 200, body
    assert asked == []


# --- the question to a printer -----------------------------------------------------------


def test_a_question_without_a_thread_is_answered_with_the_error(server_env, monkeypatch, zpl):
    """No thread to be had (TasksMax, memory): the question ends with the
    error at once, nothing waits for it, the next one asks."""
    text = '[printers.z]\nbackend = "zpl"\nhost = "zpl.local"\n'
    printers = load_printers(write(server_env.tmp / "printers.toml", text))
    service = Service(server_env.labels, printers=printers)
    printer = printers["z"]

    class NoThread(threading.Thread):
        def start(self):
            raise RuntimeError("can't start new thread")

    with monkeypatch.context() as patched:
        patched.setattr(service_module.threading, "Thread", NoThread)
        outcome = service._probe(printer)
    assert isinstance(outcome, RuntimeError) and service._asking == {}
    service._states.clear()
    assert service._probe(printer) is zpl["info"]
