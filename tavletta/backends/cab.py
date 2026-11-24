"""cab label printers with a 4 inch print head (EOS2, EOS5, SQUIX 4, MACH 4S):
JScript on TCP port 9100 or USB (see docs/cab.md).

JScript is cab's own, publicly documented printer language ("JScript
Programming Manual"). A job downloads the rendered label as a PNG in base 64
and places it on a die-cut label of the given size. The print method
(thermal transfer with a ribbon, or thermal direct) is the printer's own
setting unless the "method" setting chooses one for each job;
"upside_down" turns the label by 180° (O R); "offset_mm" moves the print
along the feed, for a printer that prints off the label's place;
"labels_per_job" is how many labels of a series go as one job, which the
printer runs through without stopping between them; heat,
backfeed and tear-off position stay as set on the printer. Status comes from the ESC
commands, which the printer answers on the same connection; the setup of
the web interface, if it answers, tells the print method and speed set on
the printer, its status the labels a job printed (the label counter;
without it see _Tally).
"""

from __future__ import annotations

import base64
import http.client
import json
import math
import re
import socket
import threading
import time
from collections.abc import Sequence
from contextlib import AbstractContextManager, suppress
from dataclasses import dataclass, replace

from PIL import Image

from tavletta.backends import mdns, net, usb
from tavletta.backends.base import (
    Backend,
    Capabilities,
    Choice,
    Count,
    Distance,
    FoundPrinter,
    HeadOpen,
    HostMemory,
    LabelNotFound,
    LabelSize,
    OutOfLabels,
    PackedImage,
    PrinterBusy,
    PrinterError,
    PrinterInfo,
    PrinterOutOfMemory,
    PrinterPaused,
    PrinterTimeout,
    PrintMode,
    PrintOptions,
    PrintResult,
    ProgressCallback,
    Raster,
    RibbonInDirectMode,
    RibbonOut,
    Toggle,
    check_width,
    for_resolution,
    map_error,
    open_session,
    report_progress,
    shift_along,
)
from tavletta.model import Label
from tavletta.render import DEFAULT_SAFE_MM

DEFAULT_PORT = 9100
# seconds for connecting and for each answer; also without progress while
# sending a job (net.send_all, usb.Connection.sendall)
SOCKET_TIMEOUT = 10.0
CANCEL_TIMEOUT = 1.0  # seconds for ESC c after a problem, and for closing then
# an answer up to a carriage return (qv's is about 40 characters): longer is
# not a cab printer answering, and is not read on
MAX_ANSWER = 256
MIN_RATE = 8_000  # bytes a second: a job that trickles in slower counts as stalled
PRINT_TIMEOUT = 180.0  # seconds without progress while waiting or printing
# a series' job without the web interface's label counter: seconds more
# for each label still to print before it counts as stuck (_Tally.allowance())
LABEL_SECONDS = 10.0
POLL_INTERVAL = 0.5  # the manual: status requests not more often than every 0.5 s
START_GRACE = 3.0  # a job that never shows up as running counts as done after this
# a job seen only as the interpreter active (printed between two polls)
# counts as done when the printer stayed idle this long: idle at two polls
# in a row, so an idle moment between reading the image and printing is
# not taken for the end
RUN_GRACE = POLL_INTERVAL
# the interpreter stays active about a second after a job (docs/cab.md): a
# label sent this soon after the last label of the one before need not wait
# for that
TAIL_SECONDS = 2.0
IMAGE_NAME = "TAVLETTA"  # at most 8 characters; each job replaces the previous image
# a series as jobs of "labels_per_job" labels (print_batch()): their images
# TAV00001, TAV00002, … (8 characters), each job of a series using the same
# names again, so the RAM holds at most that many of them; the labels after
# the first replace the image of the one field (R), confirmed on an
# EOS2/300 (docs/cab.md). An image of a label is a few KB: 250 of 40 ×
# 18.6 mm take about 0.5 MB of the printer's RAM
SERIES_IMAGE = "TAV%05d"
LABELS_PER_JOB = 250
MAX_LABELS_PER_JOB = 1000
CR = b"\r\n"
ESC = b"\x1b"

# dots per mm by the resolution in the model name ("EOS2/300"); the print head
# of an EOS2/300 reports itself as "106 mm 12 dots/mm"
DOTS_PER_MM = {200: 8.0, 300: 12.0, 600: 23.6}
DEFAULT_DOTS_PER_MM = DOTS_PER_MM[300]
# widest label: the print width of a 4 inch head (manual); the EOS2/300's head
# reports "106 mm", but the printer refuses S ...,106 as a syntax error
MAX_WIDTH_MM = 105.6
# how far the print may be moved along the feed ("offset_mm"), either way
MAX_OFFSET_MM = 5.0

# the "method" setting: as set on the printer (no H command), thermal
# transfer with a ribbon (H ...,T) or thermal direct (H ...,D); the first is
# the default
METHODS = ("printer", "transfer", "direct")
METHOD_CODES = {"transfer": "T", "direct": "D"}
# H needs a speed. Sent as set on the printer if its web interface tells it,
# else this one: the default of cab's setup, and the slower the cleaner the
# print (docs/cab.md)
DEFAULT_SPEED = 50  # mm/s
WEB_PORT = 80
WEB_TIMEOUT = 2.0  # seconds for the web interface's setup, an optional hint
SETUP_PATH = "/cgi-bin/get?cmd=1"  # the setup as JSON, read without a password
MAX_SETUP_BYTES = 1 << 20
# the status as JSON (model, serial number, counters), read without a
# password: its label counter is compared with what ESC s tells of a job
# that failed (_Tally.failed(); when it counts a label is not known)
COUNTERS_PATH = "/cgi-bin/get?cmd=3"
MAX_COUNTERS_BYTES = 64 << 10
# reads of the counter when a job failed before it counts as telling
# nothing (one may fail on a busy web server), POLL_INTERVAL apart
COUNTER_TRIES = 3
# the setup read is kept this long per printer: a job of many labels, each
# sent on its own (a counter), asks once, not once per label (up to
# WEB_TIMEOUT each where port 80 is filtered)
SETUP_SECONDS = 30.0
# a printer's model and resolution (qv) is asked again after this: a
# series prints label after label without asking (qv keeps the interpreter
# active for about a second, which each label would wait for)
VERSION_SECONDS = 30.0

# common die-cut sizes (width × height); the first one is the default
LABEL_SIZES = (
    LabelSize(50, 25),
    LabelSize(38, 25),
    LabelSize(57, 32),
    LabelSize(76, 51),
    LabelSize(100, 50),
    LabelSize(100, 100),
    LabelSize(100, 150),
)

# error character of ESC s (position 2) -> error class; text for bug reports
STATUS_ERRORS: dict[str, tuple[type[PrinterError], str]] = {
    "P": (OutOfLabels, "out of paper"),
    "D": (HeadOpen, "printhead or pinch roller open"),
    "E": (LabelNotFound, "synchronization error (no label found)"),
    "F": (RibbonOut, "out of ribbon"),
    "R": (RibbonInDirectMode, "ribbon detected in thermal direct mode"),
    "B": (PrinterError, "protocol error / invalid barcode data"),
    "H": (PrinterError, "heating voltage problem"),
    "O": (PrinterOutOfMemory, "out of memory"),
    "V": (PrinterError, "input buffer overflow"),
    "W": (PrinterError, "print head overheated"),
    "Y": (PrinterError, "printhead error"),
    "Z": (PrinterError, "printhead damaged"),
}

_sleep = time.sleep  # replaced in tests
_clock = time.monotonic


def dpi_for(dots_per_mm: float) -> float:
    """Exact, not rounded: 12 dots/mm is 304.8 dpi, and a 100 mm label must
    come out as 1200 dots, not 1201."""
    return dots_per_mm * 25.4


# --- answers ----------------------------------------------------------------------


@dataclass
class Status:
    """ESC s: "XYNNNNNNZ" - online, error, labels still to print, job running."""

    online: bool = True
    error: str = "-"  # "-" = no error
    pending: int = 0
    running: bool = False
    raw: str = ""

    @property
    def state(self) -> str:
        """BUSY only with labels to print: after a text command such as qv the
        interpreter stays active for about a second without a job."""
        if self.error != "-":
            return "ERROR"
        if not self.online:
            return "PAUSED"
        return "BUSY" if self.pending else "IDLE"

    @property
    def idle(self) -> bool:
        """Ready for a new job: nothing to print, interpreter done."""
        return not self.pending and not self.running


def parse_status(raw: str) -> Status:
    """PrinterError for an answer that is not a status at all."""
    match = re.fullmatch(r"([YN])(.)(\d{6})([YN])", raw.strip())
    if not match:
        raise PrinterError("unexpected status answer %r" % raw)
    online, error, pending, running = match.groups()
    return Status(online == "Y", error, int(pending), running == "Y", raw.strip())


def status_error(status: Status) -> PrinterError | None:
    """What keeps the printer from printing, or None."""
    if status.error != "-":
        cls, text = STATUS_ERRORS.get(status.error, (PrinterError, "error"))
        return cls("ESC s %s: %s" % (status.raw, text))
    if not status.online:
        return PrinterPaused("ESC s %s: printer is offline (paused)" % status.raw)
    return None


def parse_pitch(raw: str) -> float | None:
    """ESC l: "YNNNN" - synchronised, label distance in mm (0000 = unknown)."""
    match = re.fullmatch(r"([YN])(\d{4})", raw.strip())
    if not match or match.group(1) != "Y" or not int(match.group(2)):
        return None
    return float(int(match.group(2)))


@dataclass
class Version:
    firmware: str = ""
    model: str = ""

    @property
    def dots_per_mm(self) -> float:
        match = re.search(r"/(\d+)", self.model)
        return DOTS_PER_MM.get(int(match.group(1)) if match else 0, DEFAULT_DOTS_PER_MM)


def parse_version(raw: str) -> Version:
    """qv: "5.47.7 Jan 22, 2026 (EOS2/300)"."""
    text = raw.strip()
    match = re.fullmatch(r"(.*?)\s*\(([^()]*)\)", text)
    if not match:
        return Version(firmware=text)
    return Version(firmware=match.group(1), model=match.group(2))


@dataclass(frozen=True)
class Setup:
    """What the web interface's setup tells: the print method set on the
    printer ("transfer", "direct", "" = unknown) and its print speed."""

    method: str = ""
    speed: int | None = None  # mm/s


def parse_setup(data: bytes | None) -> Setup:
    """The setup tree of /cgi-bin/get?cmd=1 (firmware 5.47); anything else
    tells nothing. The shown texts depend on the printer's language: the
    choice is read by its position in the list of values."""
    try:
        tree = json.loads(data or b"")
    except ValueError:
        return Setup()
    editors = dict(_editors(tree))
    method = {"true": "transfer", "false": "direct"}.get(
        _selected(editors.get("ID_TRANSFER_PRINT"), "options") or "", ""
    )
    speed = None
    match = re.fullmatch(r"(\d+) ?mm/s", str(_selected(editors.get("ID_PRINT_SPEED"), "list")))
    if match:
        speed = int(match.group(1))
    return Setup(method, speed)


def _editors(node, depth: int = 0):
    """(id, editor) of every entry of the setup tree."""
    if not isinstance(node, dict) or depth > 20:
        return
    if isinstance(node.get("editor"), dict):
        yield node.get("id"), node["editor"]
    for child in node.get("submenu") or ():
        yield from _editors(child, depth + 1)


def _selected(editor, shown: str) -> str | None:
    """The value of a radio button or list: the one at the position of the
    selected text."""
    if not isinstance(editor, dict):
        return None
    texts, values = editor.get(shown), editor.get("values")
    selection = editor.get("selection")
    if not isinstance(texts, list) or not isinstance(values, list) or selection not in texts:
        return None
    index = texts.index(selection)
    return str(values[index]) if index < len(values) else None


def _fetch_setup(host: str) -> bytes | None:
    """The setup tree from the printer's web interface; None on USB or if
    it does not answer (switched off, behind a password). Replaced in tests."""
    return _fetch(host, SETUP_PATH, MAX_SETUP_BYTES)


def _fetch_counters(host: str) -> bytes | None:
    """The status of the web interface (model, serial number, counters);
    None on USB or if it does not answer. Replaced in tests."""
    return _fetch(host, COUNTERS_PATH, MAX_COUNTERS_BYTES)


def _fetch(host: str, path: str, limit: int) -> bytes | None:
    if usb.is_usb(host):
        return None
    try:
        connection = http.client.HTTPConnection(
            mdns.resolve_host(host), WEB_PORT, timeout=WEB_TIMEOUT
        )
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            return response.read(limit) if response.status == 200 else None
        finally:
            connection.close()
    except Exception:  # noqa: BLE001 - only a hint: any failure tells nothing
        return None


_setups: HostMemory[tuple[float, Setup]] = HostMemory()  # host -> (when read, setup)
_setups_lock = threading.Lock()


def web_setup(host: str, fresh: bool = False) -> Setup:
    """The setup of the printer's web interface, read again after
    SETUP_SECONDS (fresh: now); what told nothing (no answer) is kept as
    long."""
    now = _clock()
    with _setups_lock:
        cached = _setups.get(host)
    if not fresh and cached and 0 <= now - cached[0] < SETUP_SECONDS:
        return cached[1]
    setup = parse_setup(_fetch_setup(host))
    with _setups_lock:
        _setups[host] = (now, setup)
    return setup


@dataclass(frozen=True)
class Counters:
    """What the web interface's status tells: the labels the printer has
    printed in its life, its serial number and model ("" = unknown)."""

    labels: int | None = None
    serial: str = ""
    model: str = ""


def parse_counters(data: bytes | None) -> Counters:
    """The status of /cgi-bin/get?cmd=3 (firmware 5.47: "labels": [2686,
    "-"], "serialno", "printerModel"); anything else tells nothing."""
    try:
        tree = json.loads(data or b"")
    except ValueError:
        return Counters()
    if not isinstance(tree, dict):
        return Counters()
    labels = tree.get("labels")
    count = labels[0] if isinstance(labels, list) and labels else None
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        count = None
    serial, model = tree.get("serialno"), tree.get("printerModel")
    return Counters(
        count, serial if isinstance(serial, str) else "", model if isinstance(model, str) else ""
    )


# printers whose web interface did not tell the label counter, by address:
# when; not asked again for SETUP_SECONDS (each job would wait up to
# WEB_TIMEOUT where port 80 is filtered)
_no_counters: HostMemory[float] = HostMemory()


def web_counters(host: str) -> Counters:
    """The web interface's status now; nothing on USB, or for a printer
    whose web interface did not tell it in the last SETUP_SECONDS."""
    missed = _no_counters.get(host)
    if usb.is_usb(host) or (missed is not None and 0 <= _clock() - missed < SETUP_SECONDS):
        return Counters()
    counters = parse_counters(_fetch_counters(host))
    if counters.labels is None:
        _no_counters[host] = _clock()
    else:
        _no_counters.pop(host, None)
    return counters


class _LabelCounter:
    """The label counter of the web interface, read with the printer idle
    just before a job: the labels counted since, compared with what ESC s
    tells (docs/cab.md, "What a job printed")."""

    def __init__(self, host: str, start: Counters):
        assert start.labels is not None
        self.host = host
        self.start = start.labels
        self.serial = start.serial

    def printed(self) -> int | None:
        """The labels counted since the job's start (negative if it was
        reset); None if the counter does not tell now (or another printer
        answers there)."""
        counters = parse_counters(_fetch_counters(self.host))
        if counters.labels is None or counters.serial != self.serial:
            return None
        return counters.labels - self.start


# --- the job --------------------------------------------------------------------


def _mm(value: float) -> str:
    return ("%.2f" % value).rstrip("0").rstrip(".")


def encode_png(image: Image.Image | PackedImage) -> bytes:
    """Black and white (a packed image is already)."""
    return PackedImage.of(image).png


def build_job(
    image: Image.Image | PackedImage,
    dots_per_mm: float,
    gap_mm: float,
    copies: int,
    method: str = "printer",
    speed: int = DEFAULT_SPEED,
    upside_down: bool = False,
    size_mm: tuple[float, float] | None = None,
) -> bytes:
    """Download the image and print it on die-cut labels of its size.

    The image is in printer orientation: x across the print head, y along the
    feed, one pixel per dot. size_mm: the label's size (width across, height
    along), sent as it is where the image agrees with it within a dot (an
    18.6 mm label is 18.6, not the 18.58 of its 223 dots), else the image's.
    A print method other than "printer" is sent with the speed (H needs
    one) and heat 0, which adds nothing to the heat set on the printer.
    upside_down: O R, the whole label turned by 180° (O must directly
    follow S)."""
    width = _size(image.width, dots_per_mm, size_mm[0] if size_mm else None)
    height = _size(image.height, dots_per_mm, size_mm[1] if size_mm else None)
    data = base64.b64encode(encode_png(image))
    lines = [
        b"d PNG;" + IMAGE_NAME.encode(),
        b"<BASE64>",
        data,
        b"</BASE64>",
        b"m m",
        b"J",
    ]
    if method in METHOD_CODES:
        lines.append(("H %d,0,%s" % (speed, METHOD_CODES[method])).encode())
    lines += [
        # l1: die-cut labels, the gap sensor finds each one
        ("S l1;0,0,%s,%s,%s" % (_mm(height), _mm(height + gap_mm), _mm(width))).encode(),
    ]
    if upside_down:
        lines.append(b"O R")
    lines += [
        ("I:Label;0,0,0;%s" % IMAGE_NAME).encode(),
        b"A %d" % max(1, copies),
    ]
    return CR.join(lines) + CR


def build_series_job(
    images: Sequence[Image.Image | PackedImage],
    dots_per_mm: float,
    gap_mm: float,
    copies: int,
    method: str = "printer",
    speed: int = DEFAULT_SPEED,
    upside_down: bool = False,
    size_mm: tuple[float, float] | None = None,
) -> bytes:
    """Several labels of the same size as one job (build_job()): every
    image downloaded first (TAV00001, …), then one label whose image field
    shows the first, printed `copies` times, and for each further label its
    image put into that field (R) and printed. One job: the printer runs
    the labels through without the tear-off feed and backfeed between
    jobs."""
    if not 0 < len(images) <= MAX_LABELS_PER_JOB:
        raise ValueError("1 to %d images" % MAX_LABELS_PER_JOB)
    first = images[0]
    width = _size(first.width, dots_per_mm, size_mm[0] if size_mm else None)
    height = _size(first.height, dots_per_mm, size_mm[1] if size_mm else None)
    names = [SERIES_IMAGE % n for n in range(1, len(images) + 1)]
    lines: list[bytes] = []
    for name, image in zip(names, images, strict=True):
        lines += [
            b"d PNG;" + name.encode(),
            b"<BASE64>",
            base64.b64encode(encode_png(image)),
            b"</BASE64>",
        ]
    lines += [b"m m", b"J"]
    if method in METHOD_CODES:
        lines.append(("H %d,0,%s" % (speed, METHOD_CODES[method])).encode())
    lines.append(("S l1;0,0,%s,%s,%s" % (_mm(height), _mm(height + gap_mm), _mm(width))).encode())
    if upside_down:
        lines.append(b"O R")
    lines.append(("I:Label;0,0,0;%s" % names[0]).encode())
    lines.append(b"A %d" % max(1, copies))
    for name in names[1:]:
        lines += [("R Label;%s" % name).encode(), b"A %d" % max(1, copies)]
    return CR.join(lines) + CR


def _size(pixels: int, dots_per_mm: float, mm: float | None) -> float:
    """The label's size in mm if the pixels make it within a dot, else
    the pixels' size."""
    if mm is not None and abs(mm * dots_per_mm - pixels) <= 1:
        return mm
    return pixels / dots_per_mm


# --- connection -----------------------------------------------------------------


def _connect(host: str, port: int, wait: float = usb.BUSY_WAIT):
    """TCP to the printer's port, or its USB device: the same language
    (net.connect). Replaced in tests."""
    return net.connect(host, port, SOCKET_TIMEOUT, wait, MIN_RATE)


class _Session:
    def __init__(self, sock):
        self.sock = sock

    def _read(self, length: int | None = None) -> str:
        """An answer: `length` bytes, or up to a carriage return (at most
        MAX_ANSWER bytes). All of it within SOCKET_TIMEOUT: a timeout for
        each read alone would let something that sends a byte now and then
        hold the session (and on USB the printer) for ever."""
        data = b""
        deadline = _clock() + SOCKET_TIMEOUT
        try:
            while True:
                remaining = deadline - _clock()
                if remaining <= 0:
                    raise PrinterTimeout("answer not complete in time: %r" % data[:40])
                self.sock.settimeout(remaining)
                chunk = self.sock.recv(1 if length is None else length - len(data))
                if not chunk:
                    raise PrinterTimeout("connection closed after %r" % data[:40])
                data += chunk
                if length is not None and len(data) >= length:
                    return data.decode("ascii", "replace")
                if length is None and data.endswith(b"\r"):
                    return data.decode("ascii", "replace")
                if len(data) >= MAX_ANSWER:
                    raise PrinterError("unexpected answer: %r" % data[:40])
        finally:
            self.sock.settimeout(SOCKET_TIMEOUT)

    def status(self) -> Status:
        self.sock.sendall(ESC + b"s")
        return parse_status(self._read(9))

    def pitch(self) -> float | None:
        self.sock.sendall(ESC + b"l")
        return parse_pitch(self._read(5))

    def version(self) -> Version:
        self.sock.sendall(b"qv" + CR)
        return parse_version(self._read())

    def send(self, job: bytes) -> None:
        """The job; on TCP the timeout restarts with each bit of progress
        (net.send_all), usb.Connection.sendall does the same on USB."""
        if isinstance(self.sock, socket.socket):
            net.send_all(self.sock, job, SOCKET_TIMEOUT, MIN_RATE)
        else:
            self.sock.sendall(job)

    def cancel(self) -> None:
        """ESC c: drop the running job, so that "try again" does not print
        its remaining labels a second time once the problem is solved."""
        # the connection is gone or the printer takes nothing: nothing more to
        # do. A printer that stalled will not take it either: a short try,
        # and closing (which waits for the last write on USB) is short too
        with suppress(OSError, PrinterError):
            self.sock.settimeout(CANCEL_TIMEOUT)
            self.sock.sendall(ESC + b"c")


def session(
    host: str, port: int = DEFAULT_PORT, wait: float = usb.BUSY_WAIT
) -> AbstractContextManager[_Session]:
    return open_session(lambda: _connect(host, port, wait), _Session, map_error)


# --- the backend ------------------------------------------------------------------


class Cab(Backend):
    query_before_render = True  # the resolution (dots per mm) the printer reports
    reports_labels = True  # the label distance it measured
    id = "cab"
    name = "cab EOS2, EOS5, SQUIX 4, MACH 4S"
    capabilities = Capabilities(
        tape_widths_mm=(),
        dpi=dpi_for(DEFAULT_DOTS_PER_MM),
        modes=(PrintMode("standard", round(dpi_for(DEFAULT_DOTS_PER_MM))),),
        color_model="mono",
        cut_modes=(),
        continuous=False,
        label_sizes=LABEL_SIZES,
        max_width_mm=MAX_WIDTH_MM,
        data_formats=("png",),
        settings=(
            Choice("method", METHODS),
            Toggle("upside_down"),
            Distance("offset_mm", -MAX_OFFSET_MM, MAX_OFFSET_MM),
            Count("labels_per_job", 1, MAX_LABELS_PER_JOB, LABELS_PER_JOB),
        ),
    )

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port
        # the resolutions the printers reported, by address: prepare()
        # renders for the job's printer (PrintOptions.host); one backend
        # serves every cab printer of a print server, so nothing of one
        # printer stands in for another (or for none)
        self.reported: HostMemory[float] = HostMemory()
        # what qv answered, by address: (when, the answer), for print_label()
        self._versions: HostMemory[tuple[float, Version]] = HostMemory()
        # when a job ended on its last label with the interpreter still
        # active, by address (_wait_done()), for the next label's _wait_idle()
        self._ended: HostMemory[float] = HostMemory()
        # the serial number the web interface told, by address (_identify())
        self._serials: HostMemory[str] = HostMemory()

    def _dots(self, host: str) -> float:
        """The resolution this printer reported, else the default (also
        without an address)."""
        return self.reported.get(host, DEFAULT_DOTS_PER_MM) if host else DEFAULT_DOTS_PER_MM

    def display_name(self, info: PrinterInfo | None) -> str:
        """ "cab EOS2/300": the printer names its model and resolution."""
        if info and info.model and info.model.casefold() != "cab":
            return _cab_name(info.model)
        return self.name

    def discover(self, timeout: float = 3.0) -> list[FoundPrinter]:
        """cab printers on USB, then on the network."""
        plugged = [
            FoundPrinter("%s (USB)" % _cab_name(p.model), p.address, str(p.device))
            for p in usb.printers()
            if p.manufacturer.casefold().startswith("cab")
        ]
        return plugged + mdns.browse("_pdl-datastream._tcp", _is_cab, timeout)

    def query(self, host: str) -> PrinterInfo:
        """The status, model and label distance; the print method set on the
        printer if its web interface tells it."""
        with session(host, self.port) as s:
            status = s.status()
            try:
                version = s.version()
                pitch = s.pitch()
            except PrinterTimeout:
                # a printer with a problem may not answer text commands:
                # the status is what matters
                version, pitch = Version(), None
        dots = self._dots(host)
        if version.model:
            dots = self.reported[host] = version.dots_per_mm
        error = status_error(status)
        return PrinterInfo(
            model=version.model or "cab",
            tape_mm=None,
            remaining_mm=None,
            total_mm=None,
            state=status.state,
            error=error.code if error else "",
            firmware=version.firmware,
            pitch_mm=pitch,
            dpi=dpi_for(dots),
            # asked for the printer's state: read now, kept for the print
            print_method=web_setup(host, fresh=True).method,
        )

    def raster(self, tape_mm: float, options: PrintOptions) -> Raster:
        info = options.info  # this job's answer, else what this printer reported
        return Raster(info.dpi if info and info.dpi else dpi_for(self._dots(options.host)))

    def prepare(
        self, label: Label, options: PrintOptions, safe_mm: float = DEFAULT_SAFE_MM
    ) -> Image.Image:
        """In printer orientation: x across the print head, y along the feed.
        The resolution it was made for travels with the image (info)."""
        # what it is rendered at (12 dots/mm, not 11.999… from 304.8 dpi)
        dots = round(self.raster(label.tape_mm, options).dpi / 25.4, 6)
        image = super().prepare(replace(label, auto_length=False), options, safe_mm)
        if label.orientation == "along":
            image = image.rotate(-90, expand=True)
        # where this printer puts the print along the feed, made up for:
        # the label prints where the preview shows it
        offset = self.setting(options, "offset_mm")  # a Distance: always a float
        image = shift_along(image, offset if isinstance(offset, float) else 0.0, dots)
        image.info["dots_per_mm"] = dots
        # in printer orientation too: x across the print head (build_job)
        image.info["size_mm"] = label.media_size()
        return image

    def print_label(
        self,
        image: Image.Image,
        host: str,
        options: PrintOptions,
        progress: ProgressCallback | None = None,
    ) -> PrintResult | None:
        return self._print([image], host, options, progress)

    def series_batch(self, options: PrintOptions) -> int:
        """A series as jobs of "labels_per_job" labels (1: label by label)."""
        labels = self.setting(options, "labels_per_job")  # a Count: always an int
        return labels if isinstance(labels, int) else LABELS_PER_JOB

    def print_batch(
        self,
        images: Sequence[PackedImage],
        host: str,
        options: PrintOptions,
        progress: ProgressCallback | None = None,
    ) -> PrintResult | None:
        return self._print(list(images), host, options, progress)

    def _print(
        self,
        images: list[Image.Image | PackedImage],
        host: str,
        options: PrintOptions,
        progress: ProgressCallback | None,
    ) -> PrintResult | None:
        """One job: a label (build_job()) or several (build_series_job()),
        options.copies of each. A problem once the job was sent carries
        the labels printed for certain (params "printed", of "copies")
        and, if more may have printed, "printed_max" (_Tally)."""
        first = images[0]
        rendered = float(first.info.get("dots_per_mm", self._dots(host)))
        check_width(first.width / rendered, self.capabilities)
        copies = max(1, options.copies)
        total = copies * len(images)
        method = str(self.setting(options, "method"))
        # H needs a speed: the printer's own, if its web interface tells it
        speed = (web_setup(host).speed if method in METHOD_CODES else None) or DEFAULT_SPEED
        with session(host, self.port, usb.PRINT_WAIT) as s:
            report_progress(progress, 1, total)
            # a printer with a problem may not answer text commands (see
            # query()): its status first, so that it tells the problem
            # (out of labels, head open) instead of timing out on qv
            before = _checked(s)
            # the web interface's status only with the printer idle: its
            # label counter is then the start of this job (a label of a job
            # before not counted yet would shift it), its serial number which
            # printer this is
            counters = web_counters(host) if before.idle else Counters()
            self._identify(host, counters)
            # the printer may not have been asked yet (switched on later);
            # asked once for the labels of a series
            version = self._version(s, host)
            dots = rendered  # a printer that does not tell: as rendered, nothing learned
            if version.model:
                dots = self.reported[host] = version.dots_per_mm
            size_mm = first.info.get("size_mm")
            # else rendered again (RenderAgain)
            images = [_for_resolution(image, rendered, dots) for image in images]
            upside_down = self.setting(options, "upside_down") is True
            if len(images) == 1:
                job = build_job(
                    images[0], dots, options.gap_mm, copies, method, speed, upside_down, size_mm
                )
            else:
                job = build_series_job(
                    images, dots, options.gap_mm, copies, method, speed, upside_down, size_mm
                )
            # the label before of a series ended a moment ago: its
            # interpreter's second of activity is no other job to wait for
            ended = self._ended.get(host)
            waited, idle = _wait_idle(
                s, ended is not None and 0 <= _clock() - ended < TAIL_SECONDS
            )
            self._ended.pop(host, None)
            if not idle.idle:  # our job's tail: its last label may not be counted yet
                counters = Counters()
            elif waited and counters.labels is not None:  # another job printed meanwhile
                counters = web_counters(host)
            counter = _LabelCounter(host, counters) if counters.labels is not None else None
            tally = _Tally(total, len(images) == 1, counter)
            downloads, labels = _split(job)
            try:
                # a job cut off while sending would leave a partial image
                # download in the printer, which spoils the next job
                s.send(downloads)
                tally.sent = True  # labels may print from here on
                s.send(labels)
                if _wait_done(s, progress, tally):
                    self._ended[host] = _clock()
            except (PrinterError, OSError) as e:
                s.cancel()
                error = tally.failed(e)
                if error is e:
                    raise
                raise error from e
        return None

    def _identify(self, host: str, counters: Counters) -> None:
        """Another printer at this address (the web interface tells its
        serial number): what was kept of the one before (its model and
        resolution, a job of its that just ended) does not hold."""
        if not counters.serial:
            return
        if self._serials.get(host) not in (None, counters.serial):
            self._versions.pop(host, None)
            self._ended.pop(host, None)
        self._serials[host] = counters.serial

    def _version(self, s: _Session, host: str) -> Version:
        """What qv answered in the last VERSION_SECONDS, else asked now."""
        now = _clock()
        cached = self._versions.get(host)
        if cached and 0 <= now - cached[0] < VERSION_SECONDS:
            return cached[1]
        version = s.version()
        self._versions[host] = (now, version)
        return version


def _for_resolution(
    image: Image.Image | PackedImage, rendered: float, dots: float
) -> Image.Image | PackedImage:
    """for_resolution(); a packed image unpacked for it only if it is to
    be scaled, and packed again."""
    if not isinstance(image, PackedImage):
        return for_resolution(image, rendered, dots)
    if math.isclose(rendered, dots):
        return image
    return PackedImage.of(for_resolution(image.open(), rendered, dots))


def _cab_name(model: str) -> str:
    """ "cab EOS2/300", also for a model that already names the maker."""
    return model if model.casefold().startswith("cab ") else "cab " + model


def _is_cab(name: str) -> bool:
    return name.lower().startswith("cab ")


def _checked(s: _Session) -> Status:
    status = s.status()
    error = status_error(status)
    if error:
        raise error
    return status


def _wait_idle(s: _Session, own_tail: bool = False) -> tuple[bool, Status]:
    """Another job may still be printing: wait for it. Whether it had to,
    and the last answer. own_tail: the interpreter active without labels
    pending is still our last job's (_wait_done() ended on its last label
    a moment ago): no wait for it."""
    deadline = _clock() + PRINT_TIMEOUT

    def done(status: Status) -> bool:
        return status.idle or (own_tail and not status.pending)

    waited = False
    status = _checked(s)
    while not done(status):
        if _clock() > deadline:
            raise PrinterBusy("printer still busy after %.0f s" % PRINT_TIMEOUT)
        waited = True
        _sleep(POLL_INTERVAL)
        status = _checked(s)
    return waited, status


def _split(job: bytes) -> tuple[bytes, bytes]:
    """The job's image downloads, and its labels (from "m m" on): a job cut
    off within the downloads prints nothing."""
    at = job.index(CR + b"m m" + CR) + len(CR)
    return job[:at], job[at:]


class _Tally:
    """What is known of the labels a job printed: `done` for certain;
    failed() tells the most that may have printed too.

    ESC s tells it: its labels to print are those of the whole job for a
    job of one label and its copies (one A, the manual); for a series' job
    (an A for each label) that they are those of the whole job too is not
    confirmed (docs/cab.md), so only what both readings agree on counts as
    printed: the labels it was seen to count down (fewer than printed, if
    the count starts again for each A). At most the job's labels minus
    those still to print. The web interface's label counter (`counter`)
    only checks that: when it counts a label (as printed, later, per job)
    has not been seen, so where it tells another number the outcome is
    the range of both, never a number the counter alone tells."""

    def __init__(self, total: int, whole_job: bool, counter: _LabelCounter | None):
        self.total = total
        self.whole_job = whole_job  # one A: ESC s counts the whole job's labels
        self.counter = counter
        self.sent = False  # the labels' part of the job is on its way
        self.done = 0
        self.stopped: Status | None = None  # the answer that told the problem
        self._last: int | None = None  # labels to print at the poll before

    def seen(self, pending: int) -> None:
        """An ESC s answer's labels to print."""
        if self.whole_job:
            if 0 < pending <= self.total:
                self.done = max(self.done, self.total - pending)
        elif self._last is not None and pending < self._last:
            self.done = min(self.total, self.done + self._last - pending)
        self._last = pending

    def allowance(self) -> float:
        """Seconds without a label seen printed before the job counts as
        stuck: PRINT_TIMEOUT, and for a series' job LABEL_SECONDS for each
        label still to print (if ESC s counts each A on its own, a label
        printed may not show)."""
        if self.whole_job:
            return PRINT_TIMEOUT
        return PRINT_TIMEOUT + LABEL_SECONDS * (self.total - self.done)

    def counted(self) -> int | None:
        """The labels the counter counted since the job's start; None if it
        does not tell (COUNTER_TRIES reads failed), was reset (fewer than at
        the start) or counted more than the job has (another program's)."""
        if self.counter is None:
            return None
        for attempt in range(COUNTER_TRIES):
            if attempt:
                _sleep(POLL_INTERVAL)
            printed = self.counter.printed()
            if printed is not None:
                return printed if 0 <= printed <= self.total else None
        return None

    def failed(self, error: Exception) -> PrinterError:
        """The error, as a PrinterError, with what printed: the printer
        reported a problem (ESC c sent: it prints no more), or the outcome
        is not known (the connection was lost, the job never finished: up
        to every label of the job may print). The label counter, if it
        tells another number, widens the range to it."""
        mapped = error if isinstance(error, PrinterError) else map_error(error)
        if not self.sent:  # cut off within the image downloads: nothing printed
            return mapped
        if self.stopped is not None:
            done, most = self._stopped(self.stopped.pending)
        else:
            done, most = self.done, self.total
        counted = self.counted()
        if counted is not None:
            done, most = min(done, counted), max(most, counted)
        mapped.params.update(printed=done, copies=self.total)
        if most > done:
            mapped.params["printed_max"] = most
        return mapped

    def _stopped(self, pending: int) -> tuple[int, int]:
        """What ESC s tells of a job the printer stopped with a problem."""
        self.seen(pending)
        if self.whole_job:  # as the manual has it: the labels before were printed
            return self.done, self.done
        return self.done, self.total - pending if 0 < pending <= self.total else self.total


def _wait_done(s: _Session, progress: ProgressCallback | None, tally: _Tally) -> bool:
    """Until the job has run; True if it ended on its last label with the
    interpreter still active (the job's second after it, which the next
    job of a series need not wait for).

    A job of one label (and its copies) has run once the printer counted
    its labels down to 0, or, the labels never seen pending (printed
    between two polls), once it stayed idle for RUN_GRACE after its
    interpreter was seen active, for START_GRACE if it was not seen at
    all. While the image is read the interpreter is active without labels
    pending, which is not yet the job. A series' job (an A for each label)
    has run once the printer is idle at two polls in a row (ESC s may count
    each A on its own). The label counter decides nothing here. The
    timeout restarts with every printed label. `tally` keeps what printed,
    also when the printer reports a problem."""
    watch = _Watch(tally, progress, _clock() + tally.allowance())
    while True:
        status = s.status()
        ended = watch.poll(status)
        if ended is not None:
            return ended
        if _clock() > watch.deadline:
            raise PrinterTimeout("printer stayed busy: %s" % status.raw)
        _sleep(POLL_INTERVAL)


@dataclass
class _Watch:
    """What _wait_done() saw of a job so far, one ESC s answer after the
    other (poll())."""

    tally: _Tally
    progress: ProgressCallback | None
    deadline: float  # stuck after this without a label seen printed
    started: bool = False  # labels of the job seen pending
    active: bool = False  # the interpreter seen active since the job was sent
    last: int | None = None  # the labels printed at the last progress told
    idle_since: float | None = None

    def poll(self, status: Status) -> bool | None:
        """The job's end after this answer: True if it ended with the
        interpreter still active, False if idle; None while it runs. The
        problem the printer reports is raised."""
        tally = self.tally
        error = status_error(status)
        if error:
            tally.stopped = status
            raise error
        tally.seen(status.pending)
        self._progressed()
        if status.pending:
            self.started, self.idle_since = True, None
            return None
        if status.running:
            return self._running()
        return self._idle()

    def _finished(self, running: bool) -> bool:
        tally = self.tally
        tally.done = tally.total
        report_progress(self.progress, tally.total, tally.total)
        return running

    def _progressed(self) -> None:
        """A label more printed: told, and the timeout starts again."""
        tally = self.tally
        if tally.done != self.last:
            self.last = tally.done
            self.deadline = _clock() + tally.allowance()
            report_progress(self.progress, min(tally.total, tally.done + 1), tally.total)

    def _running(self) -> bool | None:
        """The interpreter active, no labels pending."""
        if self.started and self.tally.whole_job:
            # counted down to 0: printed; the rest is the interpreter's
            # second of activity after a job (and a tear-off feed)
            return self._finished(True)
        # the job's own: _wait_idle() saw the interpreter idle before it
        # was sent. A job that printed between two polls shows only this
        # (and the second of activity after it): done once the printer
        # stayed idle for RUN_GRACE, not START_GRACE
        self.active, self.idle_since = True, None
        return None

    def _idle(self) -> bool | None:
        """The printer idle: the job's end once it stayed so long enough;
        a series' job at two polls in a row (not a moment between two of
        its A)."""
        if self.idle_since is None:
            self.idle_since = _clock()
        idle = _clock() - self.idle_since
        grace = RUN_GRACE if self.active else START_GRACE
        if (self.started and (self.tally.whole_job or idle >= RUN_GRACE)) or idle >= grace:
            return self._finished(False)
        return None
