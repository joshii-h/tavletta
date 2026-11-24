"""Serial labels: one label per row of a data file.

The label keeps its {column} placeholders; each row fills them, together
with the variables, in one pass (variables.expand_label with the row as
`columns`), for the preview and for printing. Printing a series is a print
job (job.py) per row: no backend can take several different labels in one
job, and a loop works for every printer, can be stopped between two rows
and tells exactly which row a problem stopped at. The counter goes on from
row to row as from copy to copy.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime

from tavletta import codes
from tavletta.backends.base import (
    Backend,
    PrinterError,
    PrintOptions,
    PrintResult,
    Progress,
    RenderAgain,
)
from tavletta.job import Job, PrepareFailed, merge_results
from tavletta.merge.fill import VARIABLE
from tavletta.model import CodeElement, Label, TextElement
from tavletta.render import DEFAULT_SAFE_MM
from tavletta.sheets.layout import SheetJob
from tavletta.variables import (
    Values,
    column_names,
    expand,
    expand_label,
    uses_counter,
    varies_per_copy,
)


def filled(label: Label, row: Mapping[str, str], values: Values | None = None) -> Label:
    """A copy of the label with the row's values (and, with `values`, the
    variables) filled in; the label itself is not changed."""
    return expand_label(label, values, row)[0]


def _fillable(label: Label) -> list[str]:
    return [
        element.text if isinstance(element, TextElement) else element.data
        for element in label.elements
        if isinstance(element, (TextElement, CodeElement))
    ]


def used_columns(label: Label) -> list[str]:
    """The column names the label's texts and codes refer to, each once."""
    names: dict[str, None] = {}
    for text in _fillable(label):
        names.update(dict.fromkeys(column_names(text)))
    return list(names)


def unknown_columns(label: Label, columns: Sequence[str]) -> list[str]:
    """Placeholders without a column of that name: printed as they are."""
    return [name for name in used_columns(label) if name not in columns]


def insertable(columns: Sequence[str]) -> list[str]:
    """Columns a placeholder can name: braces cannot be part of one, and a
    name starting with "=" would be a variable ("{=date}")."""
    return [
        name
        for name in columns
        if "{" not in name and "}" not in name and not name.strip().startswith(VARIABLE)
    ]


def placeholder(column: str) -> str:
    return "{%s}" % column


class Cursor:
    """The row shown in the preview: index from 0, count rows."""

    def __init__(self, count: int = 0, index: int = 0):
        self.count = count
        self.index = 0
        self.go(index)

    def go(self, index: int) -> None:
        self.index = max(0, min(index, self.count - 1))

    def first(self) -> None:
        self.go(0)

    def previous(self) -> None:
        self.go(self.index - 1)

    def next(self) -> None:
        self.go(self.index + 1)

    def last(self) -> None:
        self.go(self.count - 1)

    @property
    def number(self) -> int:
        """The row as the interface counts: from 1."""
        return self.index + 1

    @property
    def at_start(self) -> bool:
        return self.index <= 0

    @property
    def at_end(self) -> bool:
        return self.index >= self.count - 1


def row_span(first: int, last: int, count: int) -> range:
    """Rows `first` to `last` (counted from 1, both included) as indexes,
    limited to the rows there are; empty if first is after last."""
    start = max(1, first)
    end = min(last, count)
    return range(start - 1, max(start - 1, end))


# --- printing a series --------------------------------------------------------------


@dataclass(frozen=True)
class SeriesProgress:
    """Which label of the series is printing, and the backend's progress."""

    label: int  # 1-based position in the series
    labels: int  # labels (rows) in the series
    row: int  # the row being printed, counted from 1
    event: Progress


@dataclass
class SeriesResult:
    """What a series printed so far: kept up to date while it prints, so a
    caller still has it after an error or an interruption."""

    printed: list[int] = field(default_factory=list)  # the rows printed, counted from 1
    stopped: bool = False  # stopped by the user before the last row
    result: PrintResult | None = None  # of the last label, if anything differed
    labels: int = 0  # labels printed, also those of a row that failed
    counter: int | None = None  # {=counter} of the next label; None without one


class SeriesError(Exception):
    """Printing stopped at `row` (counted from 1) because of `error`; the
    rows in `printed` were printed before."""

    def __init__(
        self,
        row: int,
        printed: list[int],
        error: Exception,
        preparing: bool,
        outcome: SeriesResult | None = None,
        copies_printed: int = 0,
        position: tuple[int, int] | None = None,
    ):
        super().__init__(str(error))
        self.row = row
        # on label sheets: where that row's label goes (position, sheet; from 1)
        self.position = position
        self.copies_printed = copies_printed  # of the failed row, before the problem
        self.printed = printed
        self.error = error
        self.preparing = preparing  # the label could not be rendered
        self.outcome = outcome or SeriesResult(list(printed))


class UnprintableRows(Exception):
    """Rows whose codes cannot be drawn: an EAN-13 with letters, an empty
    cell where a code needs content. Found before the first label is
    printed; `rows` counted from 1."""

    def __init__(self, rows: list[int]):
        super().__init__("codes that cannot be drawn in rows %s" % rows[:20])
        self.rows = rows


def _code_fails(element: CodeElement) -> bool:
    """Whether the code cannot be made: the renderer would draw a
    crossed-out box instead (any error, as it does)."""
    try:
        codes.verify(element)
    except Exception:  # noqa: BLE001 - CodeError, or an encoder's own error
        return True
    return False


def unprintable_rows(
    label: Label,
    rows: Sequence[Mapping[str, str]],
    indexes: Sequence[int],
    values: Values | None = None,
    stop: threading.Event | None = None,
) -> list[int]:
    """The rows (counted from 1) whose filled codes cannot be drawn: the
    renderer would print a crossed-out box instead. Only codes with column
    placeholders are checked; the others are the same for every row and
    the preview already shows their problem. Variables are filled in with
    `values` (those of the first label), the counter as it will be on each
    copy of each row: a counter can overflow a code's digits on a later
    copy (an EAN-13 at 99999 + 1). Without variables that change per copy
    one check per row does. `stop` set: no row after the first is
    checked (those found so far are returned), as a series prints its
    first row in any case."""
    varying = [e for e in label.elements if isinstance(e, CodeElement) and column_names(e.data)]
    if not varying:
        return []
    first = values or Values(datetime.now())
    copies = range(1, first.copies + 1) if varies_per_copy(label) else range(1, 2)
    bad = []
    for position, index in enumerate(indexes):
        if position and stop is not None and stop.is_set():
            break
        row_values = replace(first, counter=first.counter + first.step * first.copies * position)
        if any(
            _code_fails(
                replace(code, data=expand(code.data, row_values.for_copy(copy), rows[index])[0])
            )
            for copy in copies
            for code in varying
        ):
            bad.append(index + 1)
    return bad


@dataclass(frozen=True)
class Series:
    """What to print: the label, the rows and how. `values` are those of the
    first label (the counter to start from, the time, --set values);
    without them, now and the label's counter."""

    label: Label
    rows: Sequence[Mapping[str, str]]
    indexes: Sequence[int]  # which rows, see row_span()
    options: PrintOptions
    safe_mm: float = DEFAULT_SAFE_MM
    values: Values | None = None
    # page printers: on label sheets, each position one label (its count is
    # set from the rows and copies), see _print_on_sheets()
    sheet: SheetJob | None = None

    def first_values(self) -> Values:
        return self.values or Values.for_label(self.label, datetime.now(), self.options.copies)


def print_series(
    backend: Backend,
    host: str,
    series: Series,
    progress: Callable[[SeriesProgress], None] | None = None,
    stop: threading.Event | None = None,
    outcome: SeriesResult | None = None,
    check: bool = True,
) -> SeriesResult:
    """Print one label per row, `options.copies` of each, as a print job per
    row. `stop` is looked at between two rows: the row being printed is
    always finished, the first one too. Raises UnprintableRows before the
    first label if any row's codes cannot be drawn, SeriesError when a
    label fails. `outcome` (returned) is kept up to date while printing.
    `check` False: the caller has run unprintable_rows() already."""
    values = series.first_values()
    outcome = outcome if outcome is not None else SeriesResult()
    if check:
        # stopped while the rows are checked: the check ends after the
        # first row, which is printed, then the series stops (below); a
        # job of sheets cannot stop, all its rows are checked
        checking_stop = stop if series.sheet is None else None
        bad = unprintable_rows(series.label, series.rows, series.indexes, values, checking_stop)
        if bad:
            raise UnprintableRows(bad)
    counting = uses_counter(series.label)
    outcome.counter = values.counter if counting else None
    if series.sheet is not None:
        return _print_on_sheets(backend, host, series, values, progress, outcome)
    batch = backend.series_batch(series.options)
    probe = Job(backend, series.label, series.options, series.safe_mm, values)
    if batch > 1 and len(series.indexes) > 1 and not probe.per_copy:
        return _print_in_batches(backend, host, series, values, batch, progress, stop, outcome)
    total = len(series.indexes)
    for position, index in enumerate(series.indexes, start=1):
        if outcome.printed and stop is not None and stop.is_set():
            outcome.stopped = True
            return outcome
        row = index + 1

        def report(event: Progress, position=position, row=row) -> None:
            if progress:
                progress(SeriesProgress(position, total, row, event))

        # the counter goes on from the labels before; UUID and ID are new
        start = replace(values, counter=values.counter + values.step * outcome.labels)
        job = Job(backend, series.label, series.options, series.safe_mm, start, series.rows[index])
        _print_row(job, host, report, row, outcome, values if counting else None)
        outcome.printed.append(row)
    return outcome


def _print_in_batches(
    backend: Backend,
    host: str,
    series: Series,
    values: Values,
    batch: int,
    progress: Callable[[SeriesProgress], None] | None,
    stop: threading.Event | None,
    outcome: SeriesResult,
) -> SeriesResult:
    """The rows as jobs of `batch` labels each (Backend.print_batch()): the
    printer runs a job's labels through without stopping between them.
    `stop` is looked at between two jobs. A failure as SeriesError at the
    first row not printed in full, the rows before it printed."""
    options = series.options if series.options.host else replace(series.options, host=host)
    copies = max(1, options.copies)
    counting = uses_counter(series.label)
    indexes = list(series.indexes)
    total = len(indexes)
    for start in range(0, total, batch):
        if outcome.printed and stop is not None and stop.is_set():
            outcome.stopped = True
            return outcome
        chunk = indexes[start : start + batch]
        jobs = []
        for n, index in enumerate(chunk):
            # the counter goes on from the labels before; UUID and ID are new
            first = replace(
                values, counter=values.counter + values.step * (outcome.labels + n * copies)
            )
            jobs.append(
                Job(backend, series.label, options, series.safe_mm, first, series.rows[index])
            )

        def report(event: Progress, start=start, chunk=chunk) -> None:
            if progress:
                n = min(len(chunk), (event.copy - 1) // copies + 1)
                progress(SeriesProgress(start + n, total, chunk[n - 1] + 1, event))

        images, unprepared = _images(jobs)
        sent = chunk[: len(images)]
        try:
            if images:
                outcome.result = merge_results(
                    [outcome.result, _send_batch(backend, host, jobs, images, options, report)]
                )
        except Exception as e:
            done = e.params.get("printed", 0) if isinstance(e, PrinterError) else 0
            done = done if isinstance(done, int) and not isinstance(done, bool) else 0
            rows, partial = divmod(max(0, min(done, copies * len(sent))), copies)
            _done(outcome, chunk[:rows], done, values if counting else None)
            raise SeriesError(
                chunk[min(rows, len(sent) - 1)] + 1,
                list(outcome.printed),
                e,
                False,
                outcome,
                partial,
            ) from e
        except BaseException:  # Ctrl+C: the labels sent may be out, their numbers used
            outcome.labels += copies * len(sent)
            if counting:
                outcome.counter = values.counter + values.step * outcome.labels
            raise
        _done(outcome, sent, copies * len(sent), values if counting else None)
        if unprepared is not None:  # the rows before it are printed: on from that row
            cause = (
                unprepared.__cause__ if isinstance(unprepared.__cause__, Exception) else unprepared
            )
            raise SeriesError(
                chunk[len(images)] + 1, list(outcome.printed), cause, True, outcome
            ) from unprepared
    return outcome


def _images(jobs: list[Job]) -> tuple[list, PrepareFailed | None]:
    """The images of the jobs, up to the first that cannot be rendered
    (and its error)."""
    images = []
    for job in jobs:
        try:
            images.append(job.image(job.expanded(1)[0]))
        except PrepareFailed as e:
            return images, e
    return images, None


def _send_batch(backend: Backend, host: str, jobs: list[Job], images, options, report):
    """One job of `images` (of the first of `jobs`); rendered once more
    for the resolution the printer reported if it is another
    (RenderAgain)."""
    try:
        return backend.print_batch(images, host, options, report)
    except RenderAgain:
        again = [job.image(job.expanded(1)[0], reported=True) for job in jobs[: len(images)]]
        return backend.print_batch(again, host, options, report)


def _done(outcome: SeriesResult, indexes, labels: int, values: Values | None) -> None:
    """Rows (indexes from 0) printed in full and the labels that came out;
    the counter on by them (`values`, None for a label without one)."""
    outcome.printed += [index + 1 for index in indexes]
    outcome.labels += labels
    if values is not None:
        outcome.counter = values.counter + values.step * outcome.labels


def _print_row(
    job: Job, host: str, report, row: int, outcome: SeriesResult, values: Values | None
) -> None:
    """One row's job; a failure as SeriesError. The counter (`values`, None
    for a label without one) goes on by the labels printed, also after a
    failure or Ctrl+C: the labels sent used their numbers."""
    try:
        # queued, not confirmed or another mode on any row: the series says so
        outcome.result = merge_results([outcome.result, job.run(host, report)])
    except PrepareFailed as e:
        cause = e.__cause__ if isinstance(e.__cause__, Exception) else e
        raise SeriesError(row, list(outcome.printed), cause, True, outcome, job.printed) from e
    except Exception as e:  # noqa: BLE001 - the interface maps PrinterError codes
        raise SeriesError(row, list(outcome.printed), e, False, outcome, job.printed) from e
    finally:
        outcome.labels += job.printed
        if values is not None:
            outcome.counter = values.counter + values.step * outcome.labels


# --- serial labels on label sheets ---------------------------------------------------


def _print_on_sheets(
    backend: Backend,
    host: str,
    series: Series,
    values: Values,
    progress: Callable[[SeriesProgress], None] | None,
    outcome: SeriesResult,
) -> SeriesResult:
    """One job of label sheets (job.Job with a label for each position):
    the rows in order, each `copies` times on consecutive positions, from
    the chosen positions of the first sheet on, each position with its own
    counter, copy number, UUID and ID. It cannot be stopped between rows.
    More labels than one job of sheets takes (sheets.layout.MAX_LABELS)
    are refused before the first (TooManyLabels). A failure names the row
    to go on from, the copies of it printed and where on the sheets it
    goes; the labels that came out count (also on Ctrl+C, see Job.run)."""
    assert series.sheet is not None
    copies = max(1, series.options.copies)
    total = len(series.indexes) * copies
    sheet = replace(series.sheet, count=total)
    slots = sheet.slots()
    starts = [sum(len(page) for page in slots[:i]) for i in range(len(slots))]
    rows = [index + 1 for index in series.indexes]

    def position(n: int) -> tuple[Values, Mapping[str, str] | None]:
        row, copy = divmod(n, copies)
        start = replace(values, counter=values.counter + values.step * copies * row)
        return start.for_copy(copy + 1), series.rows[series.indexes[row]]

    def report(event: Progress) -> None:
        if progress:
            page = min(max(0, event.copy - 1), len(slots) - 1)
            row = rows[min(starts[page] // copies, len(rows) - 1)]
            progress(SeriesProgress(event.copy, event.copies, row, event))

    def count(done: int) -> None:
        outcome.labels += done
        outcome.printed = rows[: done // copies]
        if outcome.counter is not None:
            outcome.counter = values.counter + values.step * outcome.labels

    def stopped_at(done: int, error: Exception, preparing: bool) -> SeriesError:
        """The SeriesError after `done` labels came out (whole sheets). A
        backend that fails after every sheet came out stops at the last
        row with all its copies printed, as a row's own job that fails
        after its last copy (_print_row); there is no position left."""
        count(done)
        at = min(done // copies, len(rows) - 1)
        where = None
        if done < total:
            page = next(i for i, first in enumerate(starts) if first + len(slots[i]) > done)
            where = (slots[page][done - starts[page]] + 1, page + 1)
        else:
            outcome.printed = rows[:at]
        printed = list(outcome.printed)
        return SeriesError(rows[at], printed, error, preparing, outcome, done - at * copies, where)

    options = replace(series.options, copies=total)
    job = Job(backend, series.label, options, series.safe_mm, values, None, sheet, position)
    try:
        outcome.result = job.run(host, report)
    except PrepareFailed as e:
        cause = e.__cause__ if isinstance(e.__cause__, Exception) else e
        # a sheet is sent whole: a label that cannot be drawn stops its sheet
        before = max((first for first in starts if first <= (job.position or 0)), default=0)
        raise stopped_at(before, cause, True) from e
    except Exception as e:  # noqa: BLE001 - the interface maps PrinterError codes
        raise stopped_at(min(job.printed, total), e, False) from e
    except BaseException:  # Ctrl+C: the labels that came out used their numbers
        count(job.printed)
        raise
    count(job.printed)
    return outcome
