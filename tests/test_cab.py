"""The cab backend against FakeCab, on the byte level: the JScript job and the
ESC status requests are checked exactly. Answers are the ones an EOS2/300
with firmware 5.47.7 gave."""

from __future__ import annotations

import base64
import http.server
import io
import json
import re
import socket
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest
from backend_contract import BackendContract
from PIL import Image

from tavletta.backends import cab, mdns, net, usb, zpl
from tavletta.backends.base import (
    Count,
    FoundPrinter,
    HeadOpen,
    LabelNotFound,
    LabelTooWide,
    OutOfLabels,
    PortClosed,
    PrinterBusy,
    PrinterError,
    PrinterInfo,
    PrinterNotFound,
    PrinterOutOfMemory,
    PrinterPaused,
    PrinterStalled,
    PrinterTimeout,
    PrintOptions,
    RibbonError,
    RibbonInDirectMode,
    RibbonOut,
)
from tavletta.backends.cab import Cab
from tavletta.model import Label, TextElement
from tavletta.render import mm_to_px

REAL_CONNECT = cab._connect  # before the autouse fixture replaces it
REAL_FETCH_SETUP = cab._fetch_setup
REAL_FETCH_COUNTERS = cab._fetch_counters
MAX_SLEEPS = 5000


class FakeCab:
    """Answers ESC s, ESC l and qv; counts the labels of a job down, one per
    status request, like a printer that prints one label per poll. With
    `counter` set, its web interface tells the labels printed (cmd=3).
    `per_block`: ESC s tells the labels to print of the A being printed,
    not of the whole job (what docs/cab.md marks as not confirmed)."""

    def __init__(self):
        self.version = "5.47.7 Jan 22, 2026 (EOS2/300)"
        self.sync = "Y0020"
        self.error = "-"
        self.online = True
        self.busy_polls = 0  # status requests that still show another job
        self.jobs: list[bytes] = []
        self.requests: list[bytes] = []
        self.pending = 0
        self.blocks: list[int] = []  # the labels of each A of the job printing
        self.per_block = False
        self.closed = 0
        self.cancelled = 0
        self.silent = False  # never answer
        self.setup: bytes | None = None  # the web interface's setup tree; None: no answer
        self.web: list[str] = []  # hosts whose web interface was asked
        self.counter: int | None = None  # labels printed in its life; None: no web interface
        self.serial = "000000000001"
        self.counted: list[str] = []  # hosts whose counters were asked
        self.alone = False  # printing on without the connection (lose())
        # what the web interface tells of the counter (None: the counter);
        # a counter whose timing is not known: late, in blocks, at the end
        self.view = None
        self.job_start = 0  # the counter when the labels of the last job came
        self.job_total = 0  # and their number

    def counters(self) -> bytes | None:
        if self.counter is None:
            return None
        if self.alone:  # the connection is gone: it prints on, a label a request
            self.print_one()
        shown = self.counter if self.view is None else self.view(self)
        return json.dumps(
            {"printerModel": "EOS2/300", "serialno": self.serial, "labels": [shown, "-"]}
        ).encode()

    def shown_pending(self) -> int:
        """ESC s's labels to print: the job's, or (per_block) its A's."""
        if not self.per_block:
            return self.pending
        left = self.pending
        for block in reversed(self.blocks):
            if left <= block:
                return left
            left -= block
        return left

    def status(self) -> bytes:
        if self.busy_polls:
            self.busy_polls -= 1
            return b"Y-000001Y"
        running = self.pending > 0
        answer = "%s%s%06d%s" % (
            "Y" if self.online else "N",
            self.error,
            self.shown_pending(),
            "Y" if running else "N",
        )
        if running:
            self.print_one()
        return answer.encode()

    def print_one(self) -> None:
        if self.pending:
            self.pending -= 1
            if self.counter is not None:
                self.counter += 1

    def printed(self) -> int:
        return sum(int(n) for job in self.jobs for n in re.findall(rb"\nA (\d+)", job))


class FakeSocket:
    def __init__(self, device: FakeCab):
        self.device = device
        self.out = b""
        self.timeouts: list[float] = []

    def settimeout(self, timeout: float) -> None:
        self.timeouts.append(timeout)

    def sendall(self, data: bytes) -> None:
        device = self.device
        device.requests.append(data)
        if data == b"\x1bs":
            self.out += device.status()
        elif data == b"\x1bl":
            self.out += device.sync.encode()
        elif data == b"qv\r\n":
            self.out += device.version.encode() + b"\r"
        elif data == b"\x1bc":
            device.cancelled += 1
            device.pending = 0
        elif data.startswith(b"m m\r\n"):  # the labels' part of the job
            device.requests.pop()
            device.requests[-1] += data
            device.jobs[-1] = device.requests[-1]
            # all labels of the job: a series' job has an A for each label
            device.blocks = [int(n) for n in re.findall(rb"\nA (\d+)", device.jobs[-1])]
            device.pending = sum(device.blocks)
            device.job_start = device.counter or 0
            device.job_total = device.pending
        else:
            assert data.startswith(b"d PNG;"), data[:40]
            device.jobs.append(data)

    def recv(self, size: int) -> bytes:
        if self.device.silent:
            raise TimeoutError("timed out")
        chunk, self.out = self.out[:size], self.out[size:]
        return chunk

    def close(self) -> None:
        self.device.closed += 1


@pytest.fixture
def printer(monkeypatch):
    device = FakeCab()
    calls = []
    waits = []
    sleeps = []
    now = [1000.0]

    sockets = []

    def connect(host, port, wait=None):
        calls.append((host, port))
        waits.append(wait)
        sockets.append(FakeSocket(device))
        return sockets[-1]

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds
        if len(sleeps) > MAX_SLEEPS:
            raise AssertionError("endless polling")

    def fetch_setup(host):
        device.web.append(host)
        return device.setup

    def fetch_counters(host):
        device.counted.append(host)
        return device.counters()

    monkeypatch.setattr(cab, "_connect", connect)
    monkeypatch.setattr(cab, "_fetch_setup", fetch_setup)
    monkeypatch.setattr(cab, "_fetch_counters", fetch_counters)
    monkeypatch.setattr(cab, "_sleep", sleep)
    monkeypatch.setattr(cab, "_clock", lambda: now[0])
    return SimpleNamespace(
        device=device, calls=calls, waits=waits, sleeps=sleeps, now=now, sockets=sockets
    )


@pytest.fixture
def backend():
    return Cab()


def _label(width=50, height=25, orientation="across", **kwargs) -> Label:
    label = Label(printer="cab", auto_length=False, orientation=orientation, **kwargs)
    label.set_media_size(width, height)
    return label


def _job_parts(job: bytes) -> tuple[Image.Image, list[str]]:
    """The downloaded image and the label commands of a job."""
    text = job.decode()
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")
    lines = text.split("\r\n")[:-1]
    assert lines[:2] == ["d PNG;TAVLETTA", "<BASE64>"] and lines[3] == "</BASE64>"
    image = Image.open(io.BytesIO(base64.b64decode(lines[2])))
    image.load()
    return image, lines[4:]


# --- answers ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Y-000000N", (True, "-", 0, False, "IDLE")),
        ("Y-000003Y", (True, "-", 3, True, "BUSY")),
        ("Y-000000Y", (True, "-", 0, True, "IDLE")),  # interpreter still active, no job
        ("YP000000N", (True, "P", 0, False, "ERROR")),
        ("N-000000N", (False, "-", 0, False, "PAUSED")),
        ("Y-000000N\r", (True, "-", 0, False, "IDLE")),
    ],
)
def test_parse_status(raw, expected):
    status = cab.parse_status(raw)
    assert (status.online, status.error, status.pending, status.running, status.state) == expected
    assert status.idle == (not status.pending and not status.running)


@pytest.mark.parametrize("raw", ["", "Y-00000N", "X-000000N", "Y-0000000N", "hello"])
def test_parse_status_refuses_other_answers(raw):
    with pytest.raises(PrinterError, match="unexpected status"):
        cab.parse_status(raw)


@pytest.mark.parametrize(
    "error, cls",
    [
        ("P", OutOfLabels),
        ("D", HeadOpen),
        ("E", LabelNotFound),
        ("F", RibbonOut),
        ("R", RibbonInDirectMode),
        ("W", PrinterError),
        ("q", PrinterError),  # unknown: still an error
    ],
)
def test_status_errors(error, cls):
    found = cab.status_error(cab.parse_status("Y%s000000N" % error))
    assert type(found) is cls
    assert found.details.startswith("ESC s Y%s000000N" % error)


def test_status_without_error():
    assert cab.status_error(cab.parse_status("Y-000002Y")) is None
    paused = cab.status_error(cab.parse_status("N-000000N"))
    assert isinstance(paused, PrinterPaused) and paused.code == "paused"


@pytest.mark.parametrize(
    "raw, pitch",
    [("Y0020", 20.0), ("Y0152", 152.0), ("Y0000", None), ("N0020", None), ("junk", None)],
)
def test_parse_pitch(raw, pitch):
    assert cab.parse_pitch(raw) == pitch


@pytest.mark.parametrize(
    "raw, firmware, model, dots",
    [
        ("5.47.7 Jan 22, 2026 (EOS2/300)\r", "5.47.7 Jan 22, 2026", "EOS2/300", 12.0),
        ("5.44.2 Mar 04, 2024 (SQUIX 4/300P)", "5.44.2 Mar 04, 2024", "SQUIX 4/300P", 12.0),
        ("5.40 (MACH 4S/200)", "5.40", "MACH 4S/200", 8.0),
        ("5.40 (SQUIX 4/600)", "5.40", "SQUIX 4/600", 23.6),
        ("5.40 (EOS5/400)", "5.40", "EOS5/400", 12.0),  # unknown resolution
        ("5.40 (EOS5)", "5.40", "EOS5", 12.0),
        ("5.40", "5.40", "", 12.0),
    ],
)
def test_parse_version(raw, firmware, model, dots):
    version = cab.parse_version(raw)
    assert (version.firmware, version.model, version.dots_per_mm) == (firmware, model, dots)


# --- the job ---------------------------------------------------------------------


def test_job_places_the_image_on_a_die_cut_label():
    image = Image.new("RGB", (600, 300), "white")
    image.paste((0, 0, 0), (0, 0, 10, 10))
    job = cab.build_job(image, 12.0, 3.0, 2)
    png, commands = _job_parts(job)
    assert commands == [
        "m m",
        "J",
        "S l1;0,0,25,28,50",  # height, height + gap, width
        "I:Label;0,0,0;TAVLETTA",
        "A 2",
    ]
    assert png.mode == "1" and png.size == (600, 300)
    assert png.getpixel((5, 5)) == 0 and png.getpixel((50, 50)) == 255


def test_job_sizes_with_decimals():
    job = cab.build_job(Image.new("RGB", (610, 301), "white"), 12.0, 2.5, 0)
    _png, commands = _job_parts(job)
    assert commands[2] == "S l1;0,0,25.08,27.58,50.83"
    assert commands[-1] == "A 1"  # at least one label


def test_mm_formatting():
    assert [cab._mm(v) for v in (25, 25.0, 25.5, 25.125, 0.004)] == [
        "25",
        "25",
        "25.5",
        "25.12",
        "0",
    ]


# --- backend ---------------------------------------------------------------------


def test_capabilities(backend):
    caps = backend.capabilities
    assert not caps.continuous and caps.tape_widths_mm == ()
    assert caps.color_model == "mono" and caps.cut_modes == ()
    assert caps.label_sizes[0] == cab.LabelSize(50, 25)
    assert caps.max_width_mm == 105.6
    assert backend.raster(25, PrintOptions()).dpi == pytest.approx(304.8)
    assert cab.dpi_for(8.0) == pytest.approx(203.2)


def test_query(backend, printer):
    info = backend.query("cab-eos2.local")
    assert printer.calls == [("cab-eos2.local", 9100)]
    assert printer.device.requests == [b"\x1bs", b"qv\r\n", b"\x1bl"]
    assert (info.model, info.firmware, info.state, info.error) == (
        "EOS2/300",
        "5.47.7 Jan 22, 2026",
        "IDLE",
        "",
    )
    assert info.tape_mm is None and info.pitch_mm == 20.0
    assert printer.device.closed == 1
    assert backend.display_name(info) == "cab EOS2/300"
    assert backend.display_name(None) == backend.name


def test_query_reports_printer_problems(backend, printer):
    printer.device.error = "P"
    info = backend.query("cab-eos2.local")
    assert (info.state, info.error) == ("ERROR", "out_of_labels")


def test_query_learns_the_resolution(backend, printer):
    printer.device.version = "5.40 (MACH 4S/200)"
    printer.device.sync = "N0000"
    info = backend.query("cab-eos2.local")
    assert info.pitch_mm is None
    assert backend.reported["cab-eos2.local"] == 8.0
    assert backend.raster(25, PrintOptions(host="cab-eos2.local")).dpi == pytest.approx(203.2)
    assert info.dpi == pytest.approx(203.2)  # the preview and its warnings at it


def test_prepare_across_the_roll(backend):
    label = _label(50, 25, elements=[TextElement(text="A")])
    image = backend.prepare(label, PrintOptions())
    assert image.size == (600, 300)  # 12 dots per mm, x across the print head
    assert {c for _n, c in image.getcolors()} <= {(0, 0, 0), (255, 255, 255)}


def test_prepare_along_the_roll_turns_the_content(backend, png_b64):
    """Content along the roll: the start of the label comes first off the
    roll (top of the printed image), like on tape."""
    from conftest import ink_bbox

    from tavletta.model import ImageElement

    def label(orientation):
        lab = _label(50, 25, orientation=orientation, align="start", margin_mm=0)
        lab.elements = [ImageElement(data=png_b64((10, 10), "black"))]
        return lab

    across = backend.prepare(label("across"), PrintOptions())
    along = backend.prepare(label("along"), PrintOptions())
    assert along.size == across.size == (600, 300)
    safe = mm_to_px(1.5, 305)
    x0, y0, _x1, _y1 = ink_bbox(across, "white")
    assert (x0, y0) == (safe, safe)  # starts at the left edge
    x0, y0, x1, _y1 = ink_bbox(along, "white")
    assert y0 == safe and x0 > safe and 600 - x1 > safe  # at the top, centred across


def test_prepare_ignores_automatic_length(backend):
    label = _label(50, 25)
    label.auto_length = True  # never set for die-cut labels, but harmless
    assert backend.prepare(label, PrintOptions()).size == (600, 300)
    assert label.auto_length  # the label itself is not changed


def test_print(backend, printer):
    image = backend.prepare(_label(50, 25), PrintOptions())
    events = []
    result = backend.print_label(
        image, "cab-eos2.local", PrintOptions(copies=3, gap_mm=2), events.append
    )
    assert result is None
    (job,) = printer.device.jobs
    _png, commands = _job_parts(job)
    assert commands[2:] == ["S l1;0,0,25,27,50", "I:Label;0,0,0;TAVLETTA", "A 3"]
    assert [e.copy for e in events] == [1, 1, 2, 3, 3] and {e.copies for e in events} == {3}
    assert printer.device.closed == 1
    assert all(s == cab.POLL_INTERVAL for s in printer.sleeps)
    # on USB a print waits longer than a status query for the device
    assert printer.waits == [usb.PRINT_WAIT]
    backend.query("cab-eos2.local")
    assert printer.waits[-1] == usb.BUSY_WAIT


@pytest.mark.parametrize(
    "error, cls",
    [
        (PrinterStalled("USB printer stopped taking data"), PrinterStalled),
        (BrokenPipeError(), PrinterError),
    ],
)
def test_print_cancels_a_job_cut_off_while_sending(backend, printer, monkeypatch, error, cls):
    """Part of the image download reached the printer: ESC c, or the rest
    of it would spoil the next job (on USB nothing closes)."""
    real = FakeSocket.sendall

    def sendall(self, data):
        if data.startswith(b"d PNG;"):
            raise error
        real(self, data)

    monkeypatch.setattr(FakeSocket, "sendall", sendall)
    with pytest.raises(cls):
        backend.print_label(Image.new("RGB", (600, 300), "white"), "h", PrintOptions())
    assert printer.device.requests[-1] == b"\x1bc" and printer.device.cancelled == 1
    # a printer that stalled takes no ESC c either: a short try, a short close
    assert printer.sockets[-1].timeouts[-1] == cab.CANCEL_TIMEOUT


def test_print_send_failure_survives_a_cancel_that_fails_too(backend, printer, monkeypatch):
    def sendall(self, data):
        if data.startswith(b"d PNG;") or data == b"\x1bc":
            raise PrinterStalled("USB printer stopped taking data")
        FakeSocket.real(self, data)

    monkeypatch.setattr(FakeSocket, "real", FakeSocket.sendall, raising=False)
    monkeypatch.setattr(FakeSocket, "sendall", sendall)
    with pytest.raises(PrinterStalled):  # the send's error, not the cancel's
        backend.print_label(Image.new("RGB", (600, 300), "white"), "h", PrintOptions())


def test_print_waits_for_a_running_job(backend, printer):
    printer.device.busy_polls = 3
    backend.print_label(Image.new("RGB", (600, 300), "white"), "h", PrintOptions())
    assert printer.device.printed() == 1
    sent = printer.device.requests.index(printer.device.jobs[0])
    # the status before qv (another job: no problem), then waiting for it
    assert printer.device.requests[:sent] == [b"\x1bs", b"qv\r\n"] + [b"\x1bs"] * 3


def test_print_waits_while_the_interpreter_is_still_active(backend, printer, monkeypatch):
    """After a text command (qv) the printer reports "interpreter active" with
    no labels pending for about a second (EOS2/300, firmware 5.47.7)."""
    states = iter(
        [b"Y-000000N", b"Y-000000Y", b"Y-000000Y", b"Y-000000N", b"Y-000001Y", b"Y-000000N"]
    )
    monkeypatch.setattr(FakeCab, "status", lambda self: next(states))
    backend.print_label(Image.new("RGB", (600, 300), "white"), "h", PrintOptions())
    sent = printer.device.requests.index(printer.device.jobs[0])
    assert sent == 5  # after the status, qv and three status requests


def test_print_gives_up_on_a_printer_that_stays_busy(backend, printer):
    printer.device.busy_polls = 10_000
    with pytest.raises(PrinterBusy):
        backend.print_label(Image.new("RGB", (600, 300), "white"), "h", PrintOptions())
    assert printer.device.jobs == []


def test_print_job_that_never_shows_up(backend, printer, monkeypatch):
    """A job printed between two polls: done after the grace time."""
    monkeypatch.setattr(FakeCab, "status", lambda self: b"Y-000000N")
    backend.print_label(Image.new("RGB", (600, 300), "white"), "h", PrintOptions())
    assert len(printer.device.jobs) == 1
    assert sum(printer.sleeps) >= cab.START_GRACE


def test_print_stuck_job_times_out(backend, printer, monkeypatch):
    def status(self):
        return b"Y-000001Y" if self.jobs else b"Y-000000N"

    monkeypatch.setattr(FakeCab, "status", status)
    with pytest.raises(PrinterTimeout, match="stayed busy"):
        backend.print_label(Image.new("RGB", (600, 300), "white"), "h", PrintOptions())
    assert printer.device.cancelled == 1  # not printed later behind the user's back


def test_print_error_while_printing(backend, printer, monkeypatch):
    states = iter([b"Y-000000N", b"Y-000000N", b"Y-000002Y", b"YP000001N"])
    monkeypatch.setattr(FakeCab, "status", lambda self: next(states))
    with pytest.raises(OutOfLabels):
        backend.print_label(Image.new("RGB", (600, 300), "white"), "h", PrintOptions(copies=2))
    assert printer.device.requests[-1] == b"\x1bc"


def test_print_job_not_done_while_the_image_is_read(backend, printer, monkeypatch):
    """Interpreter active, nothing pending, idle once: the job has not run
    yet; it is done only after its labels were counted down."""
    states = iter(
        [b"Y-000000N", b"Y-000000N", b"Y-000000Y", b"Y-000000N", b"Y-000000Y", b"Y-000002Y"]
        + [b"Y-000001Y", b"Y-000000N"]
    )
    monkeypatch.setattr(FakeCab, "status", lambda self: next(states))
    events = []
    backend.print_label(
        Image.new("RGB", (600, 300), "white"), "h", PrintOptions(copies=2), events.append
    )
    assert [e.copy for e in events] == [1, 1, 2, 2]
    assert printer.device.cancelled == 0


def test_print_cancel_survives_a_closed_connection(backend, printer, monkeypatch):
    states = iter([b"Y-000000N", b"Y-000000N", b"YP000001N"])
    monkeypatch.setattr(FakeCab, "status", lambda self: next(states))
    real = FakeSocket.sendall

    def sendall(self, data):
        if data == b"\x1bc":
            raise BrokenPipeError("gone")
        real(self, data)

    monkeypatch.setattr(FakeSocket, "sendall", sendall)
    with pytest.raises(OutOfLabels):
        backend.print_label(Image.new("RGB", (600, 300), "white"), "h", PrintOptions())


def test_print_learns_the_resolution_and_renders_again(backend, printer):
    """Rendered for 12 dots/mm, but the printer turns out to be a /200:
    two thirds would drop dots of bars and lines; rendered again for it,
    and printed once."""
    from tavletta.backends.base import RenderAgain
    from tavletta.job import Job

    image = backend.prepare(_label(50, 25), PrintOptions())
    assert image.info["dots_per_mm"] == 12.0 and image.size == (600, 300)
    printer.device.version = "5.40 (MACH 4S/200)"
    with pytest.raises(RenderAgain):
        backend.print_label(image, "h", PrintOptions())
    assert printer.device.jobs == [] and backend.reported["h"] == 8.0
    assert backend.prepare(_label(50, 25), PrintOptions(host="h")).size == (400, 200)
    fresh = Cab()  # a job does it on its own
    job = Job(fresh, _label(50, 25, elements=[TextElement(text="x")]), PrintOptions())
    job.run("h")
    png, commands = _job_parts(printer.device.jobs[0])
    assert png.size == (400, 200) and commands[2] == "S l1;0,0,25,28,50"
    assert len(printer.device.jobs) == 1 and job.printed == 1


def test_rendered_again_for_the_resolution_reported_not_an_older_answer(printer):
    """The job's answer (options.info) says 12 dots/mm, the printer now
    says /200: the second render is for 8 dots/mm (it failed twice)."""
    from tavletta.backends.base import PrinterInfo
    from tavletta.job import Job

    printer.device.version = "5.40 (MACH 4S/200)"
    old = PrinterInfo("EOS2/300", None, None, None, "IDLE", dpi=cab.dpi_for(12.0))
    label = _label(50, 25, elements=[TextElement(text="x")])
    job = Job(Cab(), label, PrintOptions(info=old))
    job.run("h")
    png, _commands = _job_parts(printer.device.jobs[0])
    assert png.size == (400, 200) and len(printer.device.jobs) == 1 and job.printed == 1


def test_print_renders_again_for_nearly_twice_the_resolution(backend, printer):
    """Rendered for 12 dots/mm on a /600 (23.6 dots/mm): about twice, not
    exactly, which would make lines uneven; rendered again."""
    from tavletta.backends.base import RenderAgain

    image = backend.prepare(_label(50, 25), PrintOptions())
    printer.device.version = "5.40 (SQUIX 4/600)"
    with pytest.raises(RenderAgain):
        backend.print_label(image, "h", PrintOptions())


def test_print_keeps_the_resolution_if_the_printer_does_not_say(backend, printer):
    printer.device.version = "5.40"
    image = backend.prepare(_label(105.6, 25), PrintOptions())
    assert image.width == 1267  # the widest label, not rounded up beyond it
    backend.print_label(image, "h", PrintOptions())
    png, commands = _job_parts(printer.device.jobs[0])
    assert (
        png.size == (1267, 300) and commands[2] == "S l1;0,0,25,28,105.6"
    )  # the label's size, as cablabel sends it
    assert "h" not in backend.reported  # what it was rendered at is not learned as reported


def test_an_answer_without_end_is_not_read_on():
    """Something else on port 9100 that talks without a carriage return:
    refused after MAX_ANSWER bytes, not read for ever."""

    class Endless:
        received = 0

        def sendall(self, data):
            pass

        def settimeout(self, timeout):
            pass

        def recv(self, size):
            self.received += size
            return b"x" * size

    endless = Endless()
    with pytest.raises(PrinterError, match="unexpected answer"):
        cab._Session(endless).version()
    assert endless.received == cab.MAX_ANSWER


def test_an_answer_that_trickles_in_ends_in_time(monkeypatch):
    """Each read has its timeout, the whole answer too: a byte every few
    seconds cannot hold the session."""
    now = [0.0]
    timeouts = []

    class Trickle:
        def sendall(self, data):
            pass

        def settimeout(self, timeout):
            timeouts.append(timeout)

        def recv(self, size):
            now[0] += 3.0
            return b"x"

    monkeypatch.setattr(cab, "_clock", lambda: now[0])
    with pytest.raises(PrinterTimeout, match="not complete in time"):
        cab._Session(Trickle()).version()
    assert now[0] <= cab.SOCKET_TIMEOUT + 3.0
    assert timeouts[1] < timeouts[0] and timeouts[-1] == cab.SOCKET_TIMEOUT


def test_query_keeps_the_status_if_text_commands_time_out(backend, printer, monkeypatch):
    def version(self):
        raise PrinterTimeout("timed out")

    monkeypatch.setattr(cab._Session, "version", version)
    printer.device.error = "D"
    info = backend.query("h")
    assert (info.error, info.model, info.pitch_mm) == ("head_open", "cab", None)
    assert "h" not in backend.reported  # nothing reported, nothing learned


@pytest.mark.parametrize(("error", "cls"), [("D", HeadOpen), ("P", OutOfLabels)])
def test_print_refused_with_the_problem_the_status_tells(
    backend, printer, monkeypatch, error, cls
):
    """A printer with a problem may not answer qv: the status is asked
    first, so the problem is told, not a timeout after 10 s."""

    def version(self):
        raise PrinterTimeout("timed out")

    monkeypatch.setattr(cab._Session, "version", version)
    printer.device.error = error
    with pytest.raises(cls):
        backend.print_label(Image.new("RGB", (600, 300), "white"), "h", PrintOptions())
    assert printer.device.jobs == [] and printer.device.requests == [b"\x1bs"]


def test_print_refuses_labels_wider_than_the_head(backend, printer):
    with pytest.raises(LabelTooWide) as caught:
        backend.print_label(Image.new("RGB", (1300, 300), "white"), "h", PrintOptions())
    assert caught.value.params == {"max_mm": 105.6}
    assert printer.calls == []


# --- connection errors --------------------------------------------------------------


@pytest.mark.parametrize(
    "error, cls",
    [
        (socket.gaierror("Name or service not known"), PrinterNotFound),
        (OSError("cannot resolve x.local (mDNS)"), PrinterNotFound),
        (ConnectionRefusedError("refused"), PortClosed),
        (TimeoutError("timed out"), PrinterTimeout),
        (OSError("No route to host"), PrinterNotFound),
        (ValueError(""), PrinterError),
    ],
)
def test_connection_errors(backend, monkeypatch, error, cls):
    def connect(host, port, wait=None):
        raise error

    monkeypatch.setattr(cab, "_connect", connect)
    with pytest.raises(cls) as caught:
        backend.query("h")
    assert type(caught.value) is cls
    assert caught.value.details


def test_printer_that_does_not_answer(backend, printer):
    printer.device.silent = True
    with pytest.raises(PrinterTimeout):
        backend.query("h")
    assert printer.device.closed == 1


def test_connection_closed_mid_answer(backend, printer, monkeypatch):
    monkeypatch.setattr(FakeSocket, "recv", lambda self, size: b"")
    with pytest.raises(PrinterTimeout, match="connection closed"):
        backend.query("h")


def test_answer_with_garbage(backend, printer, monkeypatch):
    monkeypatch.setattr(FakeCab, "status", lambda self: b"?????????")
    with pytest.raises(PrinterError, match="unexpected status"):
        backend.query("h")


def test_real_connect_resolves_through_mdns(monkeypatch):
    seen = []
    monkeypatch.setattr(mdns, "resolve_host", lambda host: "192.0.2.21")
    monkeypatch.setattr(
        socket, "create_connection", lambda address, timeout: seen.append((address, timeout))
    )
    REAL_CONNECT("cab-eos2.local", 9100)
    assert seen == [(("192.0.2.21", 9100), cab.SOCKET_TIMEOUT)]


# --- discovery --------------------------------------------------------------------


def test_discover(backend, monkeypatch):
    calls = []
    found = [FoundPrinter("cab EOS2/300 (aa:bb:cc)", "cab-eos2.local", "192.0.2.21")]

    def browse(service_type, match, timeout):
        calls.append((service_type, timeout))
        assert match("cab EOS2/300 (aa:bb:cc)") and match("CAB SQUIX 4/300")
        assert not match("HP LaserJet") and not match("Brother VC-500W")
        return found

    monkeypatch.setattr(mdns, "browse", browse)
    assert backend.discover(timeout=1.5) == found
    assert calls == [("_pdl-datastream._tcp", 1.5)]


class TestContract(BackendContract):
    backend_id = "cab"

    @pytest.fixture
    def harness(self, printer, monkeypatch):
        found = [FoundPrinter("cab EOS2/300 (aa:bb:cc)", "cab-eos2.local", "192.0.2.21")]
        monkeypatch.setattr(mdns, "browse", lambda service_type, match, timeout: found)

        def fail():
            printer.device.error = "E"

        return SimpleNamespace(backend=Cab(), fail=fail, printed=printer.device.printed)


def test_a_job_on_tcp_goes_through_net_send_all(monkeypatch):
    """A job larger than the socket buffer arrives whole; the timeout counts
    from the last progress (net.send_all), not for the whole job."""
    calls = []
    real = net.send_all
    monkeypatch.setattr(
        net,
        "send_all",
        lambda sock, data, timeout, rate: (
            calls.append(len(data)) or real(sock, data, timeout, rate)
        ),
    )
    ours, theirs = socket.socketpair()
    got = bytearray()

    def drain():
        while chunk := theirs.recv(65536):
            got.extend(chunk)

    reader = threading.Thread(target=drain)
    reader.start()
    job = bytes(range(256)) * 4000
    cab._Session(ours).send(job)
    ours.shutdown(socket.SHUT_WR)
    reader.join(5)
    ours.close()
    theirs.close()
    assert calls == [len(job)] and bytes(got) == job


# --- print method ------------------------------------------------------------------


def _setup_tree(transfer="Off", speed="50 mm/s", options=("Off", "On")) -> dict:
    """The parts of /cgi-bin/get?cmd=1 that matter, as an EOS2/300 with
    firmware 5.47.7 sends them (radio buttons and lists: the shown texts,
    the values and the selected text)."""

    def radio(selection):
        return {"type": "radio", "selection": selection, "options": list(options)}

    speeds = ["30 mm/s", "40 mm/s", "50 mm/s", "75 mm/s", "100 mm/s", "125 mm/s", "150 mm/s"]
    return {
        "id": "ID_SETUP",
        "submenu": [
            {
                "id": "ID_PRINTING",
                "submenu": [
                    {"id": "ID_HEAT_LEVEL", "editor": {"type": "slider", "value": 0}},
                    {
                        "id": "ID_PRINT_SPEED",
                        "editor": {
                            "type": "selectbox",
                            "selection": speed,
                            "list": speeds,
                            "values": speeds,
                        },
                    },
                ],
            },
            {
                "id": "ID_RIBBON",
                "submenu": [
                    {
                        "id": "ID_TRANSFER_PRINT",
                        "editor": dict(radio(transfer), values=["false", "true"]),
                    }
                ],
            },
        ],
    }


def _setup(**kwargs) -> bytes:
    return json.dumps(_setup_tree(**kwargs)).encode()


def test_print_method_is_a_printer_setting(backend):
    setting, upside_down, _offset, _labels_per_job = backend.settings(None)
    assert setting.key == "method" and setting.choices == ("printer", "transfer", "direct")
    assert upside_down.key == "upside_down" and upside_down.value(None) is False
    # the default keeps the printer's own setting, as before the setting existed
    assert backend.setting(PrintOptions(), "method") == "printer"
    assert backend.setting(PrintOptions(settings={"method": "ribbon"}), "method") == "printer"
    assert backend.setting(PrintOptions(settings={"method": "direct"}), "method") == "direct"


@pytest.mark.parametrize(
    "method, speed, line",
    [("transfer", 50, "H 50,0,T"), ("direct", 100, "H 100,0,D"), ("transfer", 30, "H 30,0,T")],
)
def test_job_with_a_print_method(method, speed, line):
    """H after J, before S (as the manual's examples); heat 0 adds nothing
    to the heat set on the printer."""
    job = cab.build_job(Image.new("RGB", (600, 300), "white"), 12.0, 3.0, 1, method, speed)
    _png, commands = _job_parts(job)
    assert commands == [
        "m m",
        "J",
        line,
        "S l1;0,0,25,28,50",
        "I:Label;0,0,0;TAVLETTA",
        "A 1",
    ]


def test_job_as_set_on_the_printer_sends_no_h():
    image = Image.new("RGB", (600, 300), "white")
    assert cab.build_job(image, 12.0, 3.0, 1, "printer") == cab.build_job(image, 12.0, 3.0, 1)
    _png, commands = _job_parts(cab.build_job(image, 12.0, 3.0, 1, "junk"))
    assert not any(c.startswith("H") for c in commands)


@pytest.mark.parametrize(
    "data, expected",
    [
        (_setup(), cab.Setup("direct", 50)),
        (_setup(transfer="On", speed="125 mm/s"), cab.Setup("transfer", 125)),
        # a printer set to German: the texts differ, the values do not
        (_setup(transfer="Ein", options=("Aus", "Ein")), cab.Setup("transfer", 50)),
        (_setup(speed="2 in/s"), cab.Setup("direct", None)),  # inches: not used
        (_setup(transfer="Maybe"), cab.Setup("", 50)),  # not one of the options
        (None, cab.Setup()),
        (b"", cab.Setup()),
        (b"{", cab.Setup()),
        (b"<html>login</html>", cab.Setup()),
        (b"[1, 2]", cab.Setup()),
        (b'{"submenu": [{"id": "ID_PRINT_SPEED", "editor": 3}, 5]}', cab.Setup()),
        (b'{"submenu": {"id": "x"}}', cab.Setup()),
    ],
)
def test_parse_setup(data, expected):
    assert cab.parse_setup(data) == expected


def test_parse_setup_ignores_broken_entries():
    tree = _setup_tree()
    speed = tree["submenu"][0]["submenu"][1]["editor"]
    speed["values"] = speed["values"][:2]  # the selected one has no value
    radio = tree["submenu"][1]["submenu"][0]["editor"]
    radio["options"] = "Off"
    assert cab.parse_setup(json.dumps(tree).encode()) == cab.Setup()


def test_parse_setup_of_a_tree_nested_too_deep():
    tree: dict = {"id": "ID_TRANSFER_PRINT", "editor": _setup_tree()["submenu"][1]}
    for _ in range(30):
        tree = {"submenu": [tree]}
    assert cab.parse_setup(json.dumps(tree).encode()) == cab.Setup()


def test_query_reports_the_print_method_set_on_the_printer(backend, printer):
    assert backend.query("cab-eos2.local").print_method == ""  # no web interface
    printer.device.setup = _setup()
    assert backend.query("cab-eos2.local").print_method == "direct"
    printer.device.setup = _setup(transfer="On")
    assert backend.query("cab-eos2.local").print_method == "transfer"
    assert printer.device.web == ["cab-eos2.local"] * 3


@pytest.mark.parametrize(
    "setup, line",
    [
        (None, "H 50,0,T"),  # web interface off: cab's default speed
        (_setup(speed="100 mm/s"), "H 100,0,T"),  # the printer's own speed
        (_setup(speed="2 in/s"), "H 50,0,T"),
    ],
)
def test_print_with_thermal_transfer(backend, printer, setup, line):
    printer.device.setup = setup
    image = backend.prepare(_label(50, 25), PrintOptions())
    backend.print_label(image, "h", PrintOptions(settings={"method": "transfer"}))
    _png, commands = _job_parts(printer.device.jobs[0])
    assert commands[2:4] == [line, "S l1;0,0,25,28,50"]
    assert printer.device.web == ["h"]


def test_print_thermal_direct(backend, printer):
    printer.device.setup = _setup(transfer="On", speed="75 mm/s")
    backend.print_label(
        Image.new("RGB", (600, 300), "white"), "h", PrintOptions(settings={"method": "direct"})
    )
    _png, commands = _job_parts(printer.device.jobs[0])
    assert commands[2] == "H 75,0,D"


def test_print_as_set_on_the_printer_asks_no_web_interface(backend, printer):
    printer.device.setup = _setup(transfer="On")
    backend.print_label(Image.new("RGB", (600, 300), "white"), "h", PrintOptions())
    _png, commands = _job_parts(printer.device.jobs[0])
    assert commands[2] == "S l1;0,0,25,28,50"
    assert printer.device.web == []


@pytest.mark.parametrize(
    "error, cls, code",
    [("F", RibbonOut, "ribbon_out"), ("R", RibbonInDirectMode, "ribbon_direct")],
)
def test_ribbon_errors_while_printing(backend, printer, monkeypatch, error, cls, code):
    """Thermal transfer without a ribbon, or thermal direct with one: the
    printer stops, the job is cancelled, the error names the problem."""
    states = iter([b"Y-000000N", b"Y-000000N", b"Y%s000001N" % error.encode()])
    monkeypatch.setattr(FakeCab, "status", lambda self: next(states))
    options = PrintOptions(settings={"method": "transfer"})
    with pytest.raises(cls) as raised:
        backend.print_label(Image.new("RGB", (600, 300), "white"), "h", options)
    assert isinstance(raised.value, RibbonError) and raised.value.code == code
    assert printer.device.requests[-1] == b"\x1bc"


class _SetupHandler(http.server.BaseHTTPRequestHandler):
    status = 200
    body = b""
    paths: list[str] = []

    def do_GET(self):  # noqa: N802 - the name http.server calls
        type(self).paths.append(self.path)
        self.send_response(self.status)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *_args):
        pass


@pytest.fixture
def web(monkeypatch):
    """A web interface on 127.0.0.1 that answers with _SetupHandler.body."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _SetupHandler)
    monkeypatch.setattr(cab, "WEB_PORT", server.server_address[1])
    monkeypatch.setattr(_SetupHandler, "paths", [])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield _SetupHandler
    server.shutdown()
    server.server_close()


def test_fetch_setup_reads_the_web_interface(web, monkeypatch):
    monkeypatch.setattr(web, "body", _setup())
    assert REAL_FETCH_SETUP("127.0.0.1") == _setup()
    assert web.paths == ["/cgi-bin/get?cmd=1"]


def test_fetch_setup_behind_a_password(web, monkeypatch):
    monkeypatch.setattr(web, "status", 401)
    monkeypatch.setattr(web, "body", b"login")
    assert REAL_FETCH_SETUP("127.0.0.1") is None


def test_fetch_setup_without_a_web_interface(monkeypatch):
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]
    monkeypatch.setattr(cab, "WEB_PORT", port)  # nothing listens: refused
    assert REAL_FETCH_SETUP("127.0.0.1") is None
    assert REAL_FETCH_SETUP("bad\x00host") is None  # cannot be resolved


def test_fetch_setup_not_on_usb(monkeypatch):
    def resolve(host):
        raise AssertionError("resolved %s" % host)

    monkeypatch.setattr(mdns, "resolve_host", resolve)
    assert REAL_FETCH_SETUP("usb://cab/EOS2%2F300?serial=1") is None


def test_job_upside_down():
    """O R turns the whole label by 180° (as cablabel sends it for an
    EOS2); O directly after S, as the manual asks."""
    image = Image.new("RGB", (480, 223), "white")
    job = cab.build_job(image, 12.0, 4.0, 1, "transfer", 50, upside_down=True)
    _png, commands = _job_parts(job)
    assert commands == [
        "m m",
        "J",
        "H 50,0,T",
        "S l1;0,0,18.58,22.58,40",
        "O R",
        "I:Label;0,0,0;TAVLETTA",
        "A 1",
    ]


@pytest.mark.parametrize("value, sent", [(True, True), (False, False), ("yes", False)])
def test_print_upside_down(backend, printer, value, sent):
    options = PrintOptions(settings={"upside_down": value})
    backend.print_label(Image.new("RGB", (600, 300), "white"), "h", options)
    _png, commands = _job_parts(printer.device.jobs[0])
    assert ("O R" in commands) is sent
    if sent:
        assert commands[commands.index("O R") - 1].startswith("S l1;")


@pytest.mark.parametrize(
    ("chosen", "reported", "expected"),
    [
        ({}, "direct", "direct"),  # "printer", the default: as set on the printer
        ({"method": "printer"}, "transfer", "transfer"),
        ({"method": "printer"}, "", ""),  # not told: unknown
        ({"method": "transfer"}, "direct", "transfer"),  # sent with each job (H ...,T)
        ({"method": "direct"}, "transfer", "direct"),
        ({"method": "transfer"}, None, "transfer"),  # no answer: the job's own all the same
        ({"method": "ribbon"}, "direct", "direct"),  # junk is the default
    ],
)
def test_the_print_method_a_job_prints_with(chosen, reported, expected):
    info = (
        None
        if reported is None
        else PrinterInfo("", None, None, None, "IDLE", print_method=reported)
    )
    assert Cab().job_print_method(chosen, info) == expected
    # ZPL leaves the method to the printer: as it reports it
    assert zpl.Zpl().job_print_method(chosen, info) == (reported or "")


# --- label after label (a series) ------------------------------------------------------


def _blank():
    return Image.new("RGB", (480, 223), "white")


def test_qv_is_asked_once_for_the_labels_of_a_series(backend, printer):
    """qv keeps the interpreter active for about a second, which each
    label would wait for: asked again only after VERSION_SECONDS."""
    for _ in range(3):
        backend.print_label(_blank(), "h", PrintOptions())
    assert printer.device.requests.count(b"qv\r\n") == 1 and len(printer.device.jobs) == 3
    printer.now[0] += cab.VERSION_SECONDS
    backend.print_label(_blank(), "h", PrintOptions())
    assert printer.device.requests.count(b"qv\r\n") == 2
    backend.print_label(_blank(), "other", PrintOptions())  # each printer its own
    assert printer.device.requests.count(b"qv\r\n") == 3


def test_a_job_printed_between_two_polls_is_done_after_two_idle_polls(
    backend, printer, monkeypatch
):
    """Only the interpreter seen active, then idle: done after RUN_GRACE
    (idle at two polls in a row), not START_GRACE."""
    states = iter([b"Y-000000N", b"Y-000000N", b"Y-000000Y", b"Y-000000N", b"Y-000000N"])
    monkeypatch.setattr(FakeCab, "status", lambda self: next(states))
    start = printer.now[0]
    backend.print_label(_blank(), "h", PrintOptions())
    assert printer.now[0] - start == pytest.approx(2 * cab.POLL_INTERVAL)
    assert cab.RUN_GRACE < cab.START_GRACE


def test_a_job_never_seen_still_gets_the_start_grace(backend, printer, monkeypatch):
    monkeypatch.setattr(FakeCab, "status", lambda self: b"Y-000000N")
    start = printer.now[0]
    backend.print_label(_blank(), "h", PrintOptions())
    assert printer.now[0] - start >= cab.START_GRACE


def test_the_next_label_does_not_wait_for_the_interpreters_second(backend, printer, monkeypatch):
    """Counted down to 0 with the interpreter still active: printed. The
    next label of a series is sent at once, not after the interpreter's
    second of activity; later than TAIL_SECONDS it waits for idle again."""
    states = iter(
        [b"Y-000000N", b"Y-000000N", b"Y-000001Y", b"Y-000000Y"]  # first label
        + [b"Y-000000Y", b"Y-000000Y", b"Y-000001Y", b"Y-000000Y"]  # second: sent at once
        + [b"Y-000000Y", b"Y-000000Y", b"Y-000000N"]  # third, later: waits for idle
        + [b"Y-000001Y", b"Y-000000N"]
    )
    monkeypatch.setattr(FakeCab, "status", lambda self: next(states))
    backend.print_label(_blank(), "h", PrintOptions())
    before = len(printer.sleeps)
    backend.print_label(_blank(), "h", PrintOptions())
    assert len(printer.device.jobs) == 2
    # the second waited only for its own label: one poll interval
    assert printer.sleeps[before:] == [cab.POLL_INTERVAL]
    printer.now[0] += cab.TAIL_SECONDS
    before = len(printer.sleeps)
    backend.print_label(_blank(), "h", PrintOptions())
    assert len(printer.device.jobs) == 3
    assert printer.sleeps[before:] == [cab.POLL_INTERVAL] * 2  # idle first, then its label


def test_a_series_stops_at_the_label_the_printer_fails(printer, monkeypatch):
    """Label by label ("labels_per_job" 1): out of labels at the third
    label, rows 1 and 2 printed, the error names the third, nothing after
    it is sent."""
    from tavletta.serial import Series, SeriesError, print_series

    def status(self):
        if len(self.jobs) >= 3:
            return b"YP000000N"
        return FakeCab.real_status(self)

    monkeypatch.setattr(FakeCab, "real_status", FakeCab.status, raising=False)
    monkeypatch.setattr(FakeCab, "status", status)
    label = _label(40, 18.6, elements=[TextElement(text="{Number}")])
    rows = [{"Number": str(n)} for n in range(70000, 70005)]
    options = PrintOptions(settings={"labels_per_job": 1})
    series = Series(label=label, rows=rows, indexes=list(range(5)), options=options)
    with pytest.raises(SeriesError) as failed:
        print_series(Cab(), "h", series)
    assert failed.value.outcome.printed == [1, 2] and failed.value.outcome.labels == 2
    assert len(printer.device.jobs) == 3 and printer.device.cancelled == 1


# --- a series as jobs of "labels_per_job" labels ----------------------------------------


def _series_parts(job: bytes) -> tuple[list[tuple[str, Image.Image]], list[str]]:
    """The images downloaded (name, image) and the label commands of a series' job."""
    lines = job.decode().split("\r\n")[:-1]
    images = []
    while lines[0].startswith("d PNG;"):
        assert lines[1] == "<BASE64>" and lines[3] == "</BASE64>"
        image = Image.open(io.BytesIO(base64.b64decode(lines[2])))
        image.load()
        images.append((lines[0][6:], image))
        lines = lines[4:]
    return images, lines


def test_build_series_job():
    """Every image downloaded first, then one job: the first image placed
    and printed, each further one put into the same field (R) and printed."""
    images = [Image.new("RGB", (480, 223), c) for c in ("white", "black", "white")]
    job = cab.build_series_job(images, 12.0, 1.4, 2, "transfer", 50, True, (40, 18.6))
    downloaded, commands = _series_parts(job)
    assert [name for name, _i in downloaded] == ["TAV00001", "TAV00002", "TAV00003"]
    assert downloaded[1][1].getpixel((0, 0)) == 0
    assert commands == [
        "m m",
        "J",
        "H 50,0,T",
        "S l1;0,0,18.6,20,40",
        "O R",
        "I:Label;0,0,0;TAV00001",
        "A 2",
        "R Label;TAV00002",
        "A 2",
        "R Label;TAV00003",
        "A 2",
    ]
    with pytest.raises(ValueError):
        cab.build_series_job([], 12.0, 1.4, 1)
    with pytest.raises(ValueError):  # more than MAX_LABELS_PER_JOB
        cab.build_series_job(images[:1] * 1001, 12.0, 1.4, 1)


@pytest.mark.parametrize(
    ("raw", "labels"),
    [(None, 250), (1, 1), (3, 3), (1000, 1000), (0, 1), (5000, 1000), ("9", 250), (True, 250)]
    + [(2.6, 3), (float("nan"), 250)],
)
def test_labels_per_job_is_a_setting_with_a_default(raw, labels):
    """A series as jobs by default: 250 labels each, 1 to 1000; junk is the default."""
    options = PrintOptions(settings={} if raw is None else {"labels_per_job": raw})
    assert Cab().series_batch(options) == labels
    assert Count("labels_per_job", 1, 1000, 250) in Cab.capabilities.settings


def _series(n, copies=1, text="{Number}", labels_per_job=None, **label):
    from tavletta.serial import Series

    lab = _label(40, 18.6, elements=[TextElement(text=text)], **label)
    rows = [{"Number": str(70000 + i)} for i in range(n)]
    settings = {} if labels_per_job is None else {"labels_per_job": labels_per_job}
    options = PrintOptions(copies=copies, settings=settings)
    return Series(label=lab, rows=rows, indexes=list(range(n)), options=options)


def test_a_series_is_one_job_by_default(printer):
    """Without a setting: up to 250 labels in one job, each its own image."""
    from tavletta.serial import print_series

    outcome = print_series(Cab(), "h", _series(5))
    assert outcome.printed == [1, 2, 3, 4, 5] and outcome.labels == 5
    assert len(printer.device.jobs) == 1 and printer.device.printed() == 5
    downloaded, commands = _series_parts(printer.device.jobs[0])
    assert [name for name, _i in downloaded] == ["TAV%05d" % n for n in range(1, 6)]
    assert commands.count("A 1") == 5 and len({i.tobytes() for _n, i in downloaded}) == 5


@pytest.mark.parametrize(
    ("labels_per_job", "rows", "jobs"),
    [(1, 4, [1, 1, 1, 1]), (3, 7, [3, 3, 1]), (250, 251, [250, 1]), (250, 7, [7])],
)
def test_a_series_is_sent_in_jobs_of_labels_per_job(printer, labels_per_job, rows, jobs):
    from tavletta.serial import print_series

    outcome = print_series(Cab(), "h", _series(rows, labels_per_job=labels_per_job))
    assert outcome.printed == list(range(1, rows + 1)) and outcome.labels == rows
    assert printer.device.printed() == rows
    if labels_per_job == 1:  # label by label: the job of a single label
        assert all(job.startswith(b"d PNG;TAVLETTA\r\n") for job in printer.device.jobs)
        assert len(printer.device.jobs) == rows
    else:
        assert [len(_series_parts(job)[0]) for job in printer.device.jobs] == jobs


def test_a_series_as_jobs_of_twenty_labels(printer):
    from tavletta.serial import print_series

    events = []
    series = _series(45, text="{Number} {=counter}", labels_per_job=20)
    outcome = print_series(Cab(), "h", series, events.append)
    assert outcome.printed == list(range(1, 46)) and outcome.labels == 45
    assert len(printer.device.jobs) == 3 and printer.device.printed() == 45
    counts = [len(_series_parts(job)[0]) for job in printer.device.jobs]
    assert counts == [20, 20, 5]
    # each label its own image; the names used again by the next job
    first, _commands = _series_parts(printer.device.jobs[1])
    assert first[0][0] == "TAV00001" and first[0][1].tobytes() != first[1][1].tobytes()
    assert outcome.counter is not None and outcome.counter == 45 + _series(1).label.counter_next
    # progress: the label of the series and its row
    assert {(e.label, e.row) for e in events} >= {(1, 1), (20, 20), (21, 21), (45, 45)}
    assert all(e.labels == 45 for e in events)


def test_a_series_job_that_fails_tells_the_rows_printed(printer, monkeypatch):
    """Out of labels after 7 labels of the second job: rows 1 to 27
    printed, row 28 failed, nothing after it sent; the job cancelled."""
    from tavletta.serial import SeriesError, print_series

    real = FakeCab.status

    def status(self):
        if len(self.jobs) == 2 and self.pending == 13:
            return b"YP000013N"
        return real(self)

    monkeypatch.setattr(FakeCab, "status", status)
    with pytest.raises(SeriesError) as failed:
        print_series(Cab(), "h", _series(45, labels_per_job=20))
    error = failed.value
    assert error.row == 28 and error.printed == list(range(1, 28))
    assert error.outcome.labels == 27 and isinstance(error.error, OutOfLabels)
    # counted for the series, not for its job of 20: as the row it stopped at
    from tavletta.ui.texts import printer_error_text
    from tavletta.units import unit_for

    assert error.error.params == {"printed": 27, "copies": 45}
    assert "27 of 45" in printer_error_text(error.error, unit_for("mm"))
    assert len(printer.device.jobs) == 2 and printer.device.cancelled == 1


def test_a_series_job_with_copies_counts_rows_by_their_copies(printer, monkeypatch):
    from tavletta.serial import SeriesError, print_series

    real = FakeCab.status

    def status(self):
        if self.pending == 3:  # 5 of 8 labels: rows 1 and 2, and one copy of row 3
            return b"YD000003N"
        return real(self)

    monkeypatch.setattr(FakeCab, "status", status)
    with pytest.raises(SeriesError) as failed:
        print_series(Cab(), "h", _series(4, copies=2))
    error = failed.value
    assert (error.row, error.printed, error.copies_printed) == (3, [1, 2], 1)
    assert error.outcome.labels == 5 and isinstance(error.error, HeadOpen)
    assert re.findall(rb"\nA (\d+)", printer.device.jobs[0]) == [b"2"] * 4


@pytest.mark.parametrize("labels_per_job", [3, 20])
def test_a_series_job_stops_between_jobs(printer, labels_per_job):
    """Stop asked for during the first job: that job is printed in full,
    nothing after it is sent."""
    from tavletta.serial import print_series

    stop = threading.Event()
    series = _series(45, labels_per_job=labels_per_job)
    outcome = print_series(Cab(), "h", series, lambda _e: stop.set(), stop)
    assert outcome.stopped and outcome.printed == list(range(1, labels_per_job + 1))
    assert len(printer.device.jobs) == 1 and printer.device.printed() == labels_per_job


def test_a_series_job_out_of_memory_says_to_lower_labels_per_job(printer, monkeypatch):
    """The printer reports out of memory (O) for the second job before it
    printed any of its labels: rows 1 to 3 printed, row 4 to go on from;
    the message names the setting. Printed again from row 4, rows 4 to 7
    come out, each once."""
    from tavletta.serial import SeriesError, print_series
    from tavletta.ui.texts import printer_error_text
    from tavletta.units import unit_for

    real = FakeCab.status

    def status(self):
        if len(self.jobs) == 2 and self.pending == 3:
            return b"YO000003N"
        return real(self)

    monkeypatch.setattr(FakeCab, "status", status)
    with pytest.raises(SeriesError) as failed:
        print_series(Cab(), "h", _series(7, labels_per_job=3))
    error = failed.value
    assert (error.row, error.printed, error.copies_printed) == (4, [1, 2, 3], 0)
    assert error.outcome.labels == 3 and isinstance(error.error, PrinterOutOfMemory)
    assert error.error.code == "out_of_memory" and printer.device.cancelled == 1
    assert "“Labels per job”" in printer_error_text(error.error, unit_for("mm"))

    monkeypatch.setattr(FakeCab, "status", real)
    jobs = len(printer.device.jobs)
    series = replace(_series(7, labels_per_job=3), indexes=list(range(error.row - 1, 7)))
    outcome = print_series(Cab(), "h", series)
    assert outcome.printed == [4, 5, 6, 7] and outcome.labels == 4
    images = [i.tobytes() for job in printer.device.jobs[jobs:] for _n, i in _series_parts(job)[0]]
    assert len(images) == 4 and len(set(images)) == 4


def test_a_series_job_prints_the_rows_before_one_that_cannot_be_drawn(printer, monkeypatch):
    from tavletta.job import Job
    from tavletta.serial import SeriesError, print_series

    real = Job.image

    def image(self, label, reported=False):
        if self.columns and self.columns["Number"] == "70003":
            raise PrinterError("cannot draw") from None
        return real(self, label, reported)

    def failing(self, label, reported=False):
        from tavletta.job import PrepareFailed

        try:
            return image(self, label, reported)
        except PrinterError as e:
            raise PrepareFailed(str(e)) from e

    monkeypatch.setattr(Job, "image", failing)
    with pytest.raises(SeriesError) as failed:
        print_series(Cab(), "h", _series(6), check=False)
    assert failed.value.row == 4 and failed.value.preparing
    assert failed.value.printed == [1, 2, 3] and printer.device.printed() == 3


def test_labels_varying_per_copy_are_printed_one_by_one(printer):
    from tavletta.serial import print_series

    print_series(Cab(), "h", _series(3, copies=2, text="{Number} {=copy}"))
    assert len(printer.device.jobs) == 6  # one job per label: each copy its own image
    assert all(job.startswith(b"d PNG;TAVLETTA\r\n") for job in printer.device.jobs)


def test_a_series_job_holds_its_labels_packed(printer, monkeypatch):
    """Each label of a series' job is packed (PNG) as soon as it is
    rendered: no image of the job is kept until it is sent. The job's
    bytes are those of the images themselves."""
    from tavletta.backends.base import PackedImage
    from tavletta.serial import print_series

    batches = []
    real = Cab.print_batch

    def print_batch(self, images, host, options, progress=None):
        batches.append(list(images))
        return real(self, images, host, options, progress)

    monkeypatch.setattr(Cab, "print_batch", print_batch)
    series = _series(3)
    print_series(Cab(), "h", series)
    (images,) = batches
    assert all(isinstance(image, PackedImage) for image in images)
    rendered = [
        Cab().prepare(_series(1, text=str(70000 + n)).label, PrintOptions(host="h"))
        for n in range(3)
    ]
    gap = PrintOptions().gap_mm
    assert printer.device.jobs[0] == cab.build_series_job(
        rendered, 12.0, gap, 1, size_mm=(40, 18.6)
    )
    assert [image.size for image in images] == [image.size for image in rendered]
    assert images[0].open().info["size_mm"] == (40, 18.6)


def test_a_series_job_takes_fewer_labels_once_their_images_reach_the_budget(printer, monkeypatch):
    """ "Labels per job" is the most a job takes: once its packed images
    hold BATCH_BYTES, the next label goes into the next job (one each
    at least)."""
    from tavletta import serial

    size = len(cab.encode_png(Cab().prepare(_series(1).label, PrintOptions(host="h"))))
    monkeypatch.setattr(serial, "BATCH_BYTES", 3 * size)
    outcome = serial.print_series(Cab(), "h", _series(7, labels_per_job=5))
    assert outcome.printed == list(range(1, 8)) and outcome.labels == 7
    assert [len(_series_parts(job)[0]) for job in printer.device.jobs] == [3, 3, 1]
    monkeypatch.setattr(serial, "BATCH_BYTES", 1)
    serial.print_series(Cab(), "h", _series(2, labels_per_job=5))
    assert [len(_series_parts(job)[0]) for job in printer.device.jobs[3:]] == [1, 1]


def test_a_packed_series_is_scaled_for_a_printer_of_twice_the_resolution(printer):
    """Rendered for a /300, printed on a /600 (23.6 dots/mm, about twice:
    rendered again) and on a printer of exactly twice: every dot a block
    of two."""
    from tavletta.backends.base import PackedImage, RenderAgain

    image = Cab().prepare(_label(40, 18.6), PrintOptions())
    packed = PackedImage.of(image)
    backend = Cab()
    printer.device.version = "5.40 (EOS2/600)"  # no such model: 23.6 dots/mm
    backend.reported["h"] = 12.0
    with pytest.raises(RenderAgain):
        backend.print_batch([packed, packed], "h", PrintOptions())
    scaled = cab._for_resolution(packed, 12.0, 24.0)
    assert isinstance(scaled, PackedImage) and scaled.size == (2 * image.width, 2 * image.height)
    assert cab._for_resolution(packed, 12.0, 12.0) is packed


@pytest.mark.gui
def test_the_window_offers_labels_per_job_in_the_printer_group(make_window):
    gi = pytest.importorskip("gi")
    gi.require_version("Adw", "1")
    from conftest import gtk_available
    from gi.repository import Adw
    from test_app import one

    if not gtk_available():
        pytest.skip("no display for GTK")
    win = make_window({"printers": {"cab": {"host": "cab.local"}}})
    win.set_label(_label(40, 18.6, elements=[TextElement(text="x")]), None)
    win.rebuild_sidebar()
    row = one(win.sidebar, Adw.SpinRow, title="Labels per job")
    assert row.get_value() == 250 and "Stop" in row.get_subtitle()
    assert row.get_adjustment().get_lower() == 1 and row.get_adjustment().get_upper() == 1000
    row.set_value(20)
    assert win.printer_settings.get("labels_per_job") == 20
    assert Cab().series_batch(PrintOptions(settings=win.printer_settings.backend_settings())) == 20
    assert not win.dirty  # a printer setting, not the label's


# --- what a job printed: the label counter, else ESC s, and lost connections ---------------


def _lose_after(monkeypatch, device: FakeCab, polls: int, prints_on: bool = True) -> None:
    """The connection breaks at the status request after `polls` of them
    once a job was sent: nothing more reaches the printer (ESC c neither),
    which prints on (`prints_on`: a label for each read of its counter)."""
    real = FakeSocket.sendall
    asked = [0]

    def sendall(self, data):
        if device.alone:
            raise BrokenPipeError("gone")
        if data == b"\x1bs" and device.jobs and b"m m" in device.jobs[-1]:
            asked[0] += 1
            if asked[0] > polls:
                device.alone = prints_on
                device.lost = True
                raise ConnectionResetError("connection reset by peer")
        real(self, data)

    device.lost = False
    monkeypatch.setattr(FakeSocket, "sendall", sendall)


def test_parse_counters():
    data = (
        b'{ "printerModel": "EOS2/300", "serialno": "000000000001", '
        b'"labels": [2686, "-" ], "thermalDirect": ["0.156 m", "-" ] }'
    )
    assert cab.parse_counters(data) == cab.Counters(2686, "000000000001", "EOS2/300")
    for broken in (None, b"", b"[]", b"{", b'{"labels": []}', b'{"labels": ["12", "-"]}'):
        assert cab.parse_counters(broken).labels is None
    assert cab.parse_counters(b'{"labels": [true]}').labels is None
    assert cab.parse_counters(b'{"labels": [-1]}').labels is None


def test_fetch_counters_reads_the_web_interface(web, monkeypatch):
    monkeypatch.setattr(web, "body", b'{"labels": [5, "-"], "serialno": "1"}')
    assert REAL_FETCH_COUNTERS("127.0.0.1") == web.body
    assert web.paths == [cab.COUNTERS_PATH]
    assert REAL_FETCH_COUNTERS("usb://cab/EOS2%2F300?serial=1") is None


def test_a_series_job_without_the_counter_is_done_when_the_printer_is_idle(printer):
    """Without the web interface a series' job is over once the printer is
    idle, not when its labels to print reached 0 with the interpreter
    still active (that may be one A of many)."""
    from tavletta.serial import print_series

    printer.device.per_block = True
    outcome = print_series(Cab(), "h", _series(6, copies=2))
    assert outcome.labels == 12 and printer.device.pending == 0


def test_an_error_in_a_series_job_without_the_counter_is_uncertain(printer, monkeypatch):
    """ESC s counting each A on its own: the labels seen counted down are
    printed for certain, those to print in the answer of the problem (of
    one A only) leave up to the rest. Going on from the first row not
    printed for certain may print rows twice, and says so; none is
    left out."""
    from tavletta.serial import SeriesError, print_series
    from tavletta.ui.texts import printer_error_text, series_stop_text
    from tavletta.units import unit_for

    printer.device.per_block = True
    real = FakeCab.status

    def status(self):
        if self.pending == 3:  # 7 of 10 printed, ESC s tells 1 (of the A printing)
            return b"YP000001N"
        return real(self)

    monkeypatch.setattr(FakeCab, "status", status)
    with pytest.raises(SeriesError) as failed:
        print_series(Cab(), "h", _series(10, text="{Number} {=counter}"))
    error = failed.value
    confirmed, most = error.error.params["printed"], error.error.params["printed_max"]
    assert confirmed <= 7 <= most == 9 and error.uncertain
    assert error.row == confirmed + 1 and error.outcome.labels == confirmed
    assert error.outcome.unsure == most - confirmed
    assert error.outcome.counter == _series(1).label.counter_next + most
    text = printer_error_text(error.error, unit_for("mm"))
    assert "outcome is unknown" in text and "up to 9" in text
    assert "may print them twice" in series_stop_text(error.row, uncertain=True)


def test_a_connection_lost_in_a_series_job_without_the_counter(printer, monkeypatch):
    """The printer prints on: up to every label of the job may be out.
    Printed for certain: those counted before; the counter goes on after
    the whole job, the series goes on from the first row not printed for
    certain."""
    from tavletta.serial import SeriesError, print_series

    _lose_after(monkeypatch, printer.device, 3)
    with pytest.raises(SeriesError) as failed:
        print_series(Cab(), "h", _series(10))
    error = failed.value
    assert error.error.params["printed"] == 2 and error.error.params["printed_max"] == 10
    assert error.uncertain and error.row == 3 and error.printed == [1, 2]
    assert error.outcome.labels == 2 and error.outcome.unsure == 8


def test_a_connection_lost_while_the_images_are_sent_printed_nothing(printer, monkeypatch):
    """Cut off within the image downloads: no A reached the printer."""
    from tavletta.serial import SeriesError, print_series

    real = FakeSocket.sendall

    def sendall(self, data):
        if data.startswith(b"d PNG;"):
            raise ConnectionResetError("connection reset by peer")
        real(self, data)

    monkeypatch.setattr(FakeSocket, "sendall", sendall)
    with pytest.raises(SeriesError) as failed:
        print_series(Cab(), "h", _series(10))
    assert "printed" not in failed.value.error.params and not failed.value.uncertain
    assert failed.value.row == 1


def test_a_connection_lost_while_the_labels_are_sent_may_have_printed(printer, monkeypatch):
    from tavletta.serial import SeriesError, print_series

    real = FakeSocket.sendall

    def sendall(self, data):
        if data.startswith(b"m m"):
            raise BrokenPipeError("gone")
        real(self, data)

    monkeypatch.setattr(FakeSocket, "sendall", sendall)
    with pytest.raises(SeriesError) as failed:
        print_series(Cab(), "h", _series(4))
    error = failed.value.error
    assert error.params == {"printed": 0, "copies": 4, "printed_max": 4}
    assert error.code == "not_found"  # as before: the connection's error, mapped


def test_a_connection_lost_in_a_job_of_copies(backend, printer, monkeypatch):
    """One label and its copies (ESC s counts them, the manual): the copies
    counted are printed; up to all of them may be."""
    from tavletta.job import Job
    from tavletta.ui.texts import printer_error_text
    from tavletta.units import unit_for

    _lose_after(monkeypatch, printer.device, 3)
    label = _label(40, 18.6, elements=[TextElement(text="x")])
    job = Job(backend, label, PrintOptions(copies=5))
    with pytest.raises(PrinterError) as failed:
        job.run("h")
    assert failed.value.params == {"printed": 2, "copies": 5, "printed_max": 5}
    assert job.printed == 2 and job.unsure == 3
    text = printer_error_text(failed.value, unit_for("mm"))
    assert "2 of 5 labels printed for certain, up to 5 may have" in text


def test_an_error_in_a_job_of_copies_counts_the_copies_before_it(backend, printer, monkeypatch):
    """ESC s's labels to print in the answer of the problem: the copies
    before it are printed, as the manual has it (exact)."""
    from tavletta.job import Job

    real = FakeCab.status
    monkeypatch.setattr(
        FakeCab, "status", lambda self: b"YP000002N" if self.pending == 2 else real(self)
    )
    job = Job(backend, _label(40, 18.6, elements=[TextElement(text="x")]), PrintOptions(copies=5))
    with pytest.raises(OutOfLabels) as failed:
        job.run("h")
    assert failed.value.params == {"printed": 3, "copies": 5} and job.unsure == 0


def test_the_counter_of_a_printer_without_web_interface_is_not_asked_each_job(printer):
    for _ in range(3):
        Cab().print_label(_blank(), "h", PrintOptions())
    assert printer.device.counted == ["h"]
    printer.now[0] += cab.SETUP_SECONDS
    Cab().print_label(_blank(), "h", PrintOptions())
    assert printer.device.counted == ["h", "h"]


def test_no_web_interface_on_usb(printer):
    host = "usb://cab/EOS2%2F300?serial=1"
    printer.device.counter = 0
    Cab().print_label(_blank(), host, PrintOptions(copies=2))
    assert printer.device.counted == [] and printer.device.printed() == 2


def test_another_printer_at_the_address_is_asked_its_model_again(backend, printer):
    """The web interface names another serial number: what qv answered for
    the printer before (its resolution) is asked again, not kept for
    VERSION_SECONDS."""
    printer.device.counter = 0
    backend.print_label(_blank(), "h", PrintOptions())
    backend.print_label(_blank(), "h", PrintOptions())
    assert printer.device.requests.count(b"qv\r\n") == 1
    printer.device.serial = "000000000002"
    backend.print_label(_blank(), "h", PrintOptions())
    assert printer.device.requests.count(b"qv\r\n") == 2


def test_a_connection_lost_label_by_label_goes_on_after_the_label(printer, monkeypatch):
    """Label by label ("labels_per_job" 1): the label sent may be out; the
    counter goes on after it, the series from it."""
    from tavletta.serial import SeriesError, print_series

    _lose_after(monkeypatch, printer.device, 0)
    with pytest.raises(SeriesError) as failed:
        print_series(Cab(), "h", _series(3, text="{Number} {=counter}", labels_per_job=1))
    error = failed.value
    assert error.row == 1 and error.uncertain and error.outcome.labels == 0
    assert error.outcome.unsure == 1
    assert error.outcome.counter == _series(1).label.counter_next + 1


def test_a_stuck_series_job_without_the_counter_gets_time_for_each_label(printer, monkeypatch):
    """ESC s may not show a series' labels printed (counted for each A on
    its own): such a job counts as stuck only after PRINT_TIMEOUT and
    LABEL_SECONDS for each label."""
    from tavletta.serial import SeriesError, print_series

    monkeypatch.setattr(
        FakeCab,
        "status",
        lambda self: b"Y-000001Y" if b"m m" in (self.jobs or [b""])[-1] else b"Y-000000N",
    )
    start = printer.now[0]
    with pytest.raises(SeriesError) as failed:
        print_series(Cab(), "h", _series(4))
    assert isinstance(failed.value.error, PrinterTimeout)
    waited = printer.now[0] - start
    assert waited >= cab.PRINT_TIMEOUT + 4 * cab.LABEL_SECONDS
    assert failed.value.error.params["printed_max"] == 4


def test_a_series_job_tells_its_labels_printed_while_it_prints(printer):
    """SeriesProgress.printed rises label by label within a job of
    several labels (the server's job tells it), not only per job."""
    from tavletta.serial import print_series

    events = []
    print_series(Cab(), "h", _series(12, labels_per_job=5), events.append)
    printed = [e.printed for e in events]
    assert printed == sorted(printed) and set(printed) >= {1, 2, 3, 4, 6, 7, 11}


def test_a_series_job_is_not_over_at_a_moment_idle_between_its_labels(printer, monkeypatch):
    """Without the label counter a series' job ends only when the printer
    is idle at two polls in a row: idle once between two A is not its end."""
    from tavletta.backends.base import PackedImage

    states = iter(
        [b"Y-000000N", b"Y-000000N"]  # before: the status, and idle
        + [b"Y-000001Y", b"Y-000000N", b"Y-000001Y", b"Y-000000N", b"Y-000000N"]
    )
    monkeypatch.setattr(FakeCab, "status", lambda self: next(states))
    image = PackedImage.of(Cab().prepare(_label(40, 18.6), PrintOptions()))
    Cab().print_batch([image] * 2, "h", PrintOptions())
    assert next(states, None) is None  # every answer was asked for


# the label counter, whose timing is not known: as printed, a label late, in
# blocks of 5, only at the job's end, reset at the job's start
COUNTER_VIEWS = {
    "as printed": None,
    "a label late": lambda d: (
        d.counter - 1 if d.counter - d.job_start < d.job_total else d.counter
    ),
    "in blocks": lambda d: d.job_start + (d.counter - d.job_start) // 5 * 5,
    "at the end": lambda d: d.job_start if d.counter - d.job_start < d.job_total else d.counter,
    "reset": lambda d: d.counter - d.job_start,
}


@pytest.mark.parametrize("view", COUNTER_VIEWS)
@pytest.mark.parametrize("per_block", [False, True])
def test_a_series_job_ends_by_esc_s_whatever_the_counter_tells(printer, view, per_block):
    """The counter decides nothing about a job's end: a series' job is over
    when the printer is idle, also with a counter that is late or tells
    nothing until the end (or ESC s counting each A on its own)."""
    from tavletta.serial import print_series

    printer.device.counter, printer.device.view = 100, COUNTER_VIEWS[view]
    printer.device.per_block = per_block
    outcome = print_series(Cab(), "h", _series(6, copies=2))
    assert outcome.labels == 12 and printer.device.counter == 112
    assert printer.device.pending == 0 and printer.device.counted == ["h"]


def _fail_at(monkeypatch, pending: int) -> None:
    """Out of labels with `pending` labels of the job still to print."""
    real = FakeCab.status

    def status(self):
        if self.pending == pending and self.jobs and b"m m" in self.jobs[-1]:
            return b"YP%06dN" % self.shown_pending()
        return real(self)

    monkeypatch.setattr(FakeCab, "status", status)


def _bounds_hold(error, device, total):
    """printed is for certain, printed_max at most: the labels that came out
    lie between them, and no number rests on the counter alone."""
    actual = device.counter - device.job_start
    printed = error.params.get("printed", 0)
    most = error.params.get("printed_max", printed)
    assert printed <= actual <= most <= total, (printed, actual, most)
    return printed, most


@pytest.mark.parametrize("view", COUNTER_VIEWS)
@pytest.mark.parametrize("per_block", [False, True])
def test_a_problem_in_a_series_job_is_bounded_whatever_the_counter_tells(
    printer, monkeypatch, view, per_block
):
    from tavletta.backends.base import PackedImage

    printer.device.counter, printer.device.view = 100, COUNTER_VIEWS[view]
    printer.device.per_block = per_block
    _fail_at(monkeypatch, 4)  # 6 of 10 printed
    image = PackedImage.of(Cab().prepare(_label(40, 18.6), PrintOptions()))
    with pytest.raises(OutOfLabels) as failed:
        Cab().print_batch([image] * 10, "h", PrintOptions())
    printed, most = _bounds_hold(failed.value, printer.device, 10)
    if view == "as printed" and not per_block:  # ESC s and the counter agree
        assert printed == most == 6


@pytest.mark.parametrize("view", COUNTER_VIEWS)
def test_a_problem_in_a_job_of_copies_is_bounded_whatever_the_counter_tells(
    backend, printer, monkeypatch, view
):
    """ESC s counts the copies of one label (the manual); a counter that
    tells another number makes it a range, never a number of its own."""
    printer.device.counter, printer.device.view = 100, COUNTER_VIEWS[view]
    _fail_at(monkeypatch, 4)
    with pytest.raises(OutOfLabels) as failed:
        backend.print_label(_blank(), "h", PrintOptions(copies=10))
    printed, most = _bounds_hold(failed.value, printer.device, 10)
    exact = view in ("as printed", "reset")  # agreeing, or telling nothing
    assert (printed == most == 6) is exact


@pytest.mark.parametrize("view", COUNTER_VIEWS)
@pytest.mark.parametrize("single", [False, True])
def test_a_lost_connection_is_bounded_whatever_the_counter_tells(
    printer, monkeypatch, view, single
):
    """Up to every label of the job may print on; printed for certain only
    what both ESC s and the counter confirm."""
    from tavletta.backends.base import PackedImage

    printer.device.counter, printer.device.view = 100, COUNTER_VIEWS[view]
    _lose_after(monkeypatch, printer.device, 4, prints_on=False)
    image = PackedImage.of(Cab().prepare(_label(40, 18.6), PrintOptions()))
    with pytest.raises(PrinterError) as failed:
        if single:
            Cab().print_label(image.open(), "h", PrintOptions(copies=10))
        else:
            Cab().print_batch([image] * 10, "h", PrintOptions())
    printed, most = _bounds_hold(failed.value, printer.device, 10)
    assert most == 10 and printed <= 3


def test_one_failed_read_of_the_counter_is_not_its_end(printer, monkeypatch):
    """A read that fails (a busy web server) is tried again: the counter
    still checks ESC s's count."""
    printer.device.counter = 100
    printer.device.view = lambda d: d.counter - (d.counter > 100)  # a label short: a range
    _fail_at(monkeypatch, 4)
    real = cab._fetch_counters
    reads = []

    def flaky(host):
        reads.append(host)
        return None if len(reads) == 2 else real(host)  # the read after the problem

    monkeypatch.setattr(cab, "_fetch_counters", flaky)
    with pytest.raises(OutOfLabels) as failed:
        Cab().print_label(_blank(), "h", PrintOptions(copies=10))
    assert failed.value.params == {"printed": 5, "copies": 10, "printed_max": 6}
    assert len(reads) == 3


def test_the_counter_is_read_only_with_the_printer_idle(printer, monkeypatch):
    """Its start for a job is read when the printer is idle: right after a
    label of the job before (the interpreter still active), its last label
    may not be counted yet, and is not read then."""
    states = iter(
        [b"Y-000000N", b"Y-000000N", b"Y-000001Y", b"Y-000000Y"]  # first label: tail
        + [b"Y-000000Y", b"Y-000000Y", b"Y-000001Y", b"Y-000000N"]  # second: no read
    )
    monkeypatch.setattr(FakeCab, "status", lambda self: next(states))
    printer.device.counter = 0
    backend = Cab()
    backend.print_label(_blank(), "h", PrintOptions())
    assert printer.device.counted == ["h"]
    backend.print_label(_blank(), "h", PrintOptions())
    assert printer.device.counted == ["h"]
