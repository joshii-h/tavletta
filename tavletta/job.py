"""A print job: variables filled in per label, rendered and sent to a backend.

Used by the window and by the command line. Without variables that change
from label to label the label is rendered once and the printer makes the
copies; with them (counter, copy number, UUID, ID) every copy is rendered
and sent on its own, so five copies are five numbers. On a label sheet
(page printers) each position is one copy: the pages are composed from the
same image, or from one render per position.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import datetime

from PIL import Image

from tavletta.backends.base import (
    Backend,
    PrinterError,
    PrinterInfo,
    PrintOptions,
    PrintResult,
    Progress,
    ProgressCallback,
    RenderAgain,
    check_codes,
)
from tavletta.model import Label, measured_differs
from tavletta.render import DEFAULT_SAFE_MM, Rendered, RenderWarning, whole_tape
from tavletta.sheets import Sheet
from tavletta.sheets.layout import Copies, LabelSource, Pages, SheetJob, clip_preview
from tavletta.variables import Values, expand_label, uses_counter, varies_per_copy

# serial labels on sheets: position n (from 0) -> its values and data row
PositionValues = Callable[[int], tuple[Values, Mapping[str, str] | None]]

# the preview and the export at most this fine: an A4 page at 1200 dpi
# would be 140 megapixels, rendered on every keystroke
PREVIEW_MAX_DPI = 600.0
# and a page (system printer) at most this many pixels: an A4 page about
# 345 dpi, A3 about 290 dpi (a render of 16 megapixels takes about a
# second and 200 MB)
PREVIEW_MAX_PIXELS = 16_000_000


def preview_image(
    backend: Backend,
    label: Label,
    options: PrintOptions,
    safe_mm: float = DEFAULT_SAFE_MM,
    sheet: Sheet | None = None,
    rotated: bool = False,
) -> tuple[Rendered, Image.Image, float]:
    """The label as the window's preview shows it and its export (and the
    command line's --output) writes it, rendered once: at the resolution
    it is printed at, at most PREVIEW_MAX_DPI and a page at most
    PREVIEW_MAX_PIXELS (Backend.label_dpi()), in label space on the whole
    tape (render.whole_tape), a round label on a sheet in its shape.
    Returns the render (warnings, length), the image and its resolution."""
    dpi = backend.label_dpi(label, options, PREVIEW_MAX_DPI, PREVIEW_MAX_PIXELS)
    rendered = backend.render_label(label, options, safe_mm, bleed=False, dpi=dpi)
    tape = round(label.tape_mm / 25.4 * dpi) if backend.capabilities.continuous else 0
    image = backend.preview(whole_tape(rendered.image, tape), options.mode)
    if sheet is not None:
        image = clip_preview(image, sheet, rotated)
    return rendered, image, dpi


@dataclass(frozen=True)
class OtherLabels:
    """Die-cut labels loaded that are not the label's: their size, or the
    label distance the printer measured. The label's own size, gap and
    distance are kept for the message (texts.other_labels_text())."""

    size_mm: tuple[float, float]  # the label's (Label.media_size())
    gap_mm: float
    pitch_mm: float  # the label's distance from label to label
    # the size of the loaded labels if it is not the label's; None: the same or unknown
    loaded_mm: tuple[float, float] | None
    # the label distance measured if it is not the label's (and the size is
    # not known to differ); None: the same or unknown
    measured_mm: float | None
    # the printer may have measured it on an earlier roll: calibrate it
    # (Backend.pitch_may_be_old)
    calibrate: bool = False


def other_labels(
    backend: Backend, label: Label, info: PrinterInfo | None, chosen: Mapping[str, object]
) -> OtherLabels | None:
    """What the printer reports of the loaded die-cut labels (`info`), if
    it is not the label: their size, else their measured distance. The
    window, the command line and the server warn of it; printing is not
    refused. None for tape and page printers, a printer that reports
    neither, and a job that senses the labels otherwise than the printer
    reports them (`chosen`, PrintOptions.settings: Backend.senses_as_reported;
    a continuous job on a printer set to gap labels)."""
    caps = backend.capabilities
    if info is None or caps.continuous or caps.pages:
        return None
    if not info.label_mm and not info.pitch_mm:
        return None
    if not backend.senses_as_reported(chosen, info):
        return None
    size = label.media_size()
    loaded = info.label_mm
    if loaded and not any(measured_differs(a, b) for a, b in zip(loaded, size, strict=True)):
        loaded = None
    measured = info.pitch_mm
    if loaded or (measured and not measured_differs(measured, label.pitch_mm())):
        measured = None
    if not loaded and not measured:
        return None
    return OtherLabels(
        size, label.gap_mm, label.pitch_mm(), loaded, measured, backend.pitch_may_be_old
    )


def labels_reported(backend: Backend, info: PrinterInfo, chosen: Mapping[str, object]) -> bool:
    """Whether an answer of the printer tells of the labels a job prints
    on: their size or measured distance, or how it is set to find them,
    otherwise than the job does (nothing to compare: Backend.
    senses_as_reported). A busy or unsynchronised printer tells none of
    it: its answer without a media type is no sign of another sensing."""
    if info.label_mm or info.pitch_mm:
        return True
    return bool(info.media_type) and not backend.senses_as_reported(chosen, info)


class PrepareFailed(Exception):
    """The label could not be rendered for printing; the cause is chained."""


def merge_results(results: list[PrintResult | None]) -> PrintResult | None:
    """One result for the copies sent one by one."""
    queued = any(r.queued for r in results if r)
    unconfirmed = any(r.unconfirmed for r in results if r)
    mode, reason = next(((r.mode, r.reason) for r in results if r and r.mode), ("", ""))
    if not (mode or queued or unconfirmed):
        return None
    return PrintResult(mode=mode, queued=queued, unconfirmed=unconfirmed, reason=reason)


class Job:
    """Print `label` with `options` on `backend`; `printed` counts the labels
    sent so far, also when a later copy fails."""

    def __init__(
        self,
        backend: Backend,
        label: Label,
        options: PrintOptions,
        safe_mm: float = DEFAULT_SAFE_MM,
        values: Values | None = None,
        columns: Mapping[str, str] | None = None,
        sheet: SheetJob | None = None,
        positions: PositionValues | None = None,
    ):
        """sheet: print on label sheets; options.copies is then the number
        of labels (sheet.count). positions: what position n of the sheets
        is filled with (values and data row; serial labels on sheets),
        instead of copy n + 1 of this label."""
        self.backend = backend
        self.sheet = sheet
        self.label = label
        self.options = options
        self.safe_mm = safe_mm
        self.values = values or Values.for_label(label, datetime.now(), options.copies)
        self.columns = columns  # serial labels: the row the placeholders are filled from
        self.positions = positions
        self.position: int | None = None  # on sheets: the position being rendered
        self.printed = 0
        self.stopped = False  # run() ended early because `stop` was set
        # the first label is filled in once: its UUID is the same in the
        # check and the print; the others are made as they are needed, a
        # job of many copies does not keep them all
        self._first: tuple[Label, list[RenderWarning]] | None = None

    def label_at(self, n: int) -> Label:
        """The label for position n (from 0) of the sheets."""
        if self.positions is None:
            return self.expanded(n + 1)[0]
        values, columns = self.positions(n)
        return expand_label(self.label, values, columns)[0]

    def expanded(self, copy: int) -> tuple[Label, list[RenderWarning]]:
        """The label of the given copy with its variables filled in, and the
        variables that could not be."""
        if copy != 1:
            return expand_label(self.label, self.values.for_copy(copy), self.columns)
        if self._first is None:
            self._first = expand_label(self.label, self.values, self.columns)
        return self._first

    @property
    def per_copy(self) -> bool:
        return self.options.copies > 1 and varies_per_copy(self.label)

    @property
    def renders(self) -> int:
        """How many images the job needs: one, or one per copy."""
        return self.options.copies if self.per_copy else 1

    def labels(self) -> Iterator[tuple[int, Label]]:
        """(copy number, label with its values) for each render the job needs."""
        for copy in range(1, self.renders + 1):
            yield copy, self.expanded(copy)[0]

    def check(self) -> list[RenderWarning]:
        """The problems of the first label: variables that cannot be filled
        in and what the preview would point out."""
        label, warnings = self.expanded(1)
        # as the print renders it: the printer's resolution, tape, reduction
        rendered = self.backend.render_label(label, self.options, self.safe_mm)
        return warnings + rendered.warnings

    def images(self) -> Iterator[tuple[int, Image.Image]]:
        """(copy number, image as the printer gets it) for each render."""
        for copy, label in self.labels():
            yield copy, self.image(label)

    def image(self, label: Label, reported: bool = False) -> Image.Image:
        """The image the printer gets for one label of the job (labels()).
        `reported`: rendered for what the backend learned from the printer
        just now, not for the answer the job was given (options.info),
        which may be older (RenderAgain)."""
        one_each = self.per_copy or self.positions is not None
        options = replace(self.options, copies=1) if one_each else self.options
        if reported:
            options = replace(options, info=None)
        try:
            return self.backend.prepare(label, options, self.safe_mm)
        except Exception as e:  # noqa: BLE001 - whatever went wrong while drawing
            raise PrepareFailed(str(e)) from e

    def export(self, label: Label) -> tuple[Image.Image, float]:
        """One label as the window exports it (preview_image(), rendered
        once), and its resolution. A code that does not fit fails here
        (PrepareFailed, CodeTooLarge), as it would fail to print; what only
        the printer's image would show (image()) is not looked for: the dry
        run prepares its first label for that."""
        sheet = self.sheet
        try:
            rendered, image, dpi = preview_image(
                self.backend,
                label,
                self.options,
                self.safe_mm,
                sheet.sheet if sheet else None,
                sheet.rotated if sheet else False,
            )
            check_codes(rendered.warnings)
        except Exception as e:  # noqa: BLE001 - whatever went wrong while drawing
            raise PrepareFailed(str(e)) from e
        return image, dpi

    def run(
        self,
        host: str,
        progress: ProgressCallback | None = None,
        stop: threading.Event | None = None,
    ) -> PrintResult | None:
        """Print; raises PrepareFailed or the backend's PrinterError.

        `stop` is looked at between two copies sent one by one: the copy
        being printed is finished, the first one too; `stopped` tells.
        Copies the printer makes of one render cannot be stopped.

        `printed` counts the labels that came out: those sent before a
        failure, and those a backend reports in PrinterError.params
        ["printed"] for the failed send (copies of one job before the
        problem). The error then carries "printed" and "copies" of the
        whole job. Interrupted (Ctrl+C) during a send, the labels of that
        send count as printed: the printer may have them, and a number
        skipped is better than one printed twice."""
        if not self.options.host:  # prepare() renders for this printer
            self.options = replace(self.options, host=host)
        if self.sheet is not None:
            return self._run_sheets(self.sheet, host, progress)
        if not self.per_copy:
            label = self.expanded(1)[0]
            return self._send(self.image(label), label, host, self.options, progress)
        single = replace(self.options, copies=1)
        results = []
        for copy, label in self.labels():
            if self.printed and stop is not None and stop.is_set():
                self.stopped = True
                break
            progress_of_copy = self._progress(progress, copy)
            results.append(self._send(self.image(label), label, host, single, progress_of_copy))
        return merge_results(results)

    def _send(
        self, image, label: Label, host: str, options: PrintOptions, progress
    ) -> PrintResult | None:
        try:
            result = self._print_label(image, label, host, options, progress)
        except PrinterError as e:
            done = e.params.get("printed", 0)
            if isinstance(done, int) and not isinstance(done, bool):
                self.printed += max(0, min(done, options.copies))
            if self.printed:
                e.params.update(printed=self.printed, copies=self.options.copies)
            raise
        except Exception:
            raise
        except BaseException:  # KeyboardInterrupt, SystemExit
            self.printed += options.copies
            raise
        self.printed += options.copies
        return result

    def _print_label(self, image, label: Label, host: str, options, progress):
        """Print; once more, rendered anew for what the printer reported
        now, if that is another resolution than the image was made for
        (RenderAgain)."""
        try:
            return self.backend.print_label(image, host, options, progress)
        except RenderAgain:
            again = self.image(label, reported=True)
            return self.backend.print_label(again, host, options, progress)

    def _run_sheets(
        self, sheet: SheetJob, host: str, progress: ProgressCallback | None
    ) -> PrintResult | None:
        """The labels on sheets, each position one label: the same image, or
        one render per position (variables per copy, or `positions`: serial
        labels). A backend's error counts the sheets printed (params
        "printed", "pages"): the labels on them count as printed; on Ctrl+C
        every label of the job does (see run())."""
        each = self.per_copy or self.positions is not None
        first = None if each else self.image(self.expanded(1)[0])
        source: LabelSource = _EachCopy(self) if first is None else Copies(first)
        slots = sheet.slots()
        pages = Pages(sheet, source, self.backend.capabilities.dpi)
        try:
            result = self.backend.print_pages(pages, host, self.options, progress)
        except PrinterError as e:
            done = e.params.get("printed", 0)
            if isinstance(done, int) and not isinstance(done, bool):
                self.printed += sum(len(page) for page in slots[: max(0, done)])
            raise
        except Exception:
            raise
        except BaseException:  # KeyboardInterrupt, SystemExit: see run()
            self.printed += sum(len(page) for page in slots)
            raise
        self.printed += sum(len(page) for page in slots)
        return result

    def _progress(self, progress: ProgressCallback | None, copy: int):
        """The backend reports copy 1 of 1: tell the caller which one it is."""
        if progress is None:
            return None
        copies = self.options.copies
        return lambda event: progress(Progress(copy, copies, event.stage))

    def next_counter(self) -> int | None:
        """The counter for the next print, None if the label has none."""
        if not uses_counter(self.label):
            return None
        return self.values.counter + self.values.step * self.printed


class _EachCopy:
    """A label source for a sheet with variables that change per label:
    position n (counted over all sheets) is copy n + 1, rendered on its own;
    at_dpi(): the same, rendered at another resolution (render_at, see
    Copies)."""

    def __init__(self, job: Job, dpi: float | None = None):
        self.job, self.dpi = job, dpi

    def __call__(self, n: int) -> Image.Image:
        self.job.position = n
        image = self.job.image(self.job.label_at(n))
        render_at = image.info.get("render_at")
        if self.dpi is not None and callable(render_at):
            return render_at(self.dpi)
        return image

    def at_dpi(self, dpi: float) -> _EachCopy | None:
        if not callable(self(0).info.get("render_at")):
            return None
        return _EachCopy(self.job, dpi)
