"""Serial labels: filling a label from a row, stepping through the rows and
printing one label per row with a fake printer."""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import datetime

import pytest
from fake_backends import FakeMono

from tavletta.backends.base import PaperJam, PrintOptions, PrintResult, Progress
from tavletta.model import CodeElement, ImageElement, Label, TextElement
from tavletta.serial import (
    Cursor,
    Series,
    SeriesError,
    SeriesProgress,
    SeriesResult,
    UnprintableRows,
    filled,
    insertable,
    placeholder,
    print_series,
    row_span,
    unknown_columns,
    unprintable_rows,
    used_columns,
)
from tavletta.variables import Values

ROWS = [
    {"Name": "Screws", "Code": "B-1", "Shelf": "B"},
    {"Name": "Washers", "Code": "B-2", "Shelf": "B"},
    {"Name": "Fuses", "Code": "C-1", "Shelf": "C"},
]


def shelf_label() -> Label:
    return Label(
        tape_mm=12,
        printer="fake-mono",
        elements=[
            CodeElement(data="https://example.org/{Code}"),
            TextElement(text="{Name}\nShelf {Shelf} {{literal}} {Missing}"),
            ImageElement(name="{Name}.png"),
        ],
    )


# --- filling --------------------------------------------------------------------------


def test_filled_replaces_placeholders_in_texts_and_codes():
    label = shelf_label()
    result = filled(label, ROWS[0])
    code, text, image = result.elements
    assert isinstance(code, CodeElement) and isinstance(text, TextElement)
    assert code.data == "https://example.org/B-1"
    assert text.text == "Screws\nShelf B {literal} {Missing}"
    assert image == label.elements[2]  # an image has nothing to fill
    # the label keeps its placeholders
    assert label.elements[1].text == "{Name}\nShelf {Shelf} {{literal}} {Missing}"
    assert label.elements[0].data == "https://example.org/{Code}"


def test_filled_keeps_everything_else():
    label = shelf_label()
    label.elements[1].bold = False
    result = filled(label, ROWS[1])
    assert result.elements[1].bold is False
    assert (result.tape_mm, result.printer) == (label.tape_mm, label.printer)


def test_used_and_unknown_columns():
    label = shelf_label()
    assert used_columns(label) == ["Code", "Name", "Shelf", "Missing"]
    assert unknown_columns(label, list(ROWS[0])) == ["Missing"]
    assert used_columns(Label(tape_mm=25)) == []
    label.elements[1].text = "{=date} {Name}"  # a variable: no column, never unknown
    assert used_columns(label) == ["Code", "Name"]
    assert unknown_columns(label, list(ROWS[0])) == []


def test_insertable_columns_and_placeholders():
    assert insertable(["Name", "a{b", "c}", "=date", "Ort"]) == ["Name", "Ort"]
    assert placeholder("Name") == "{Name}"


# --- stepping through the rows -------------------------------------------------------------


def test_cursor_steps_within_the_rows():
    cursor = Cursor(3)
    assert (cursor.index, cursor.number, cursor.at_start, cursor.at_end) == (0, 1, True, False)
    cursor.previous()
    assert cursor.index == 0
    cursor.next()
    assert (cursor.number, cursor.at_start, cursor.at_end) == (2, False, False)
    cursor.last()
    assert (cursor.number, cursor.at_end) == (3, True)
    cursor.next()
    assert cursor.number == 3
    cursor.first()
    assert cursor.number == 1


def test_cursor_keeps_its_row_when_the_rows_change():
    assert Cursor(10, 4).index == 4
    assert Cursor(3, 7).index == 2  # fewer rows now: the last one
    assert Cursor(3, -2).index == 0


def test_cursor_without_rows():
    cursor = Cursor()
    assert (cursor.index, cursor.count, cursor.at_start, cursor.at_end) == (0, 0, True, True)
    cursor.next()
    cursor.last()
    assert cursor.index == 0


@pytest.mark.parametrize(
    "first, last, count, expected",
    [
        (1, 3, 3, [0, 1, 2]),
        (2, 2, 3, [1]),
        (3, 10, 5, [2, 3, 4]),  # rows 3 to 10 of 5: 3 to 5
        (0, 2, 5, [0, 1]),
        (4, 2, 5, []),
        (1, 5, 0, []),
    ],
)
def test_row_span(first, last, count, expected):
    assert list(row_span(first, last, count)) == expected


# --- printing a series ----------------------------------------------------------------


class Recorder(FakeMono):
    """FakeMono that records the filled labels it was asked to prepare and
    can fail at a chosen print."""

    def __init__(self):
        super().__init__()
        self.prepared: list[Label] = []
        self.fail_at: int | None = None  # 1-based print_label call
        self.prepare_error: Exception | None = None
        self.result: PrintResult | None = None

    def prepare(self, label, options, safe_mm=1.5):
        if self.prepare_error:
            raise self.prepare_error
        self.prepared.append(label)
        return super().prepare(label, options, safe_mm)

    def print_label(self, image, host, options, progress=None):
        if self.fail_at is not None and len(self.jobs) // options.copies + 1 == self.fail_at:
            raise PaperJam("JAM")
        super().print_label(image, host, options, progress)
        return self.result


def series(indexes=range(3), copies=1) -> Series:
    return Series(shelf_label(), ROWS, indexes, PrintOptions(copies=copies), safe_mm=1.0)


def texts(backend: Recorder) -> list[str]:
    return [label.elements[1].text.split("\n")[0] for label in backend.prepared]


def test_one_label_per_row_with_copies():
    backend = Recorder()
    events: list[SeriesProgress] = []
    outcome = print_series(backend, "mono.local", series(copies=2), events.append)
    assert outcome.printed == [1, 2, 3] and not outcome.stopped and outcome.result is None
    assert texts(backend) == ["Screws", "Washers", "Fuses"]
    assert len(backend.jobs) == 6  # two copies of each
    assert {job[1] for job in backend.jobs} == {"mono.local"}
    assert [(e.label, e.labels, e.row, e.event.copy) for e in events] == [
        (1, 3, 1, 1),
        (1, 3, 1, 2),
        (2, 3, 2, 1),
        (2, 3, 2, 2),
        (3, 3, 3, 1),
        (3, 3, 3, 2),
    ]
    assert events[0].event == Progress(1, 2)


def test_only_the_chosen_rows():
    backend = Recorder()
    outcome = print_series(backend, "h", series(row_span(2, 3, 3)))
    assert outcome.printed == [2, 3]
    assert texts(backend) == ["Washers", "Fuses"]


def test_printing_without_a_progress_callback():
    backend = Recorder()
    assert print_series(backend, "h", series()).printed == [1, 2, 3]


def test_stop_ends_the_series_after_the_current_label():
    backend = Recorder()
    stop = threading.Event()

    def progress(event: SeriesProgress) -> None:
        if event.label == 2:  # the user presses Stop while row 2 prints
            stop.set()

    outcome = print_series(backend, "h", series(), progress, stop)
    assert outcome.printed == [1, 2] and outcome.stopped
    assert texts(backend) == ["Screws", "Washers"]


def test_a_stop_before_the_start_still_prints_one_label():
    """The event is looked at between labels: one is always printed, so
    "stopped after rows …" never names an empty range."""
    stop = threading.Event()
    stop.set()
    outcome = print_series(Recorder(), "h", series(), None, stop)
    assert outcome.printed == [1] and outcome.stopped


def test_printer_problem_names_the_row():
    backend = Recorder()
    backend.fail_at = 2
    with pytest.raises(SeriesError) as info:
        print_series(backend, "h", series(row_span(2, 3, 3)))
    error = info.value
    assert (error.row, error.printed, error.preparing) == (3, [2], False)
    assert isinstance(error.error, PaperJam)
    assert str(error) == "JAM"


def test_a_label_that_cannot_be_rendered_names_the_row():
    backend = Recorder()
    backend.prepare_error = ValueError("broken font")
    with pytest.raises(SeriesError) as info:
        print_series(backend, "h", series())
    assert (info.value.row, info.value.printed, info.value.preparing) == (1, [], True)


def test_result_of_the_printer_is_passed_on():
    backend = Recorder()
    backend.result = PrintResult(mode="standard")
    assert print_series(backend, "h", series()).result == PrintResult(mode="standard")


def test_unprintable_rows_are_found_before_the_first_label():
    label = Label(
        tape_mm=12,
        printer="fake-mono",
        elements=[
            CodeElement(data="{EAN}", symbology="ean13"),
            CodeElement(data="{Text}", symbology="qr"),
            CodeElement(data="ABC", symbology="code128"),  # no placeholder: not checked
        ],
    )
    rows = [
        {"EAN": "4006381333931", "Text": "short"},
        {"EAN": "4006381333932", "Text": "x"},  # wrong check digit
        {"EAN": "400638133393", "Text": ""},  # an empty QR code
        {"EAN": "400638133393", "Text": "x" * 3000},  # more than a QR code holds
        {"EAN": "400638133393", "Text": "1" * 2400},  # digits: fit, denser than bytes
    ]
    assert unprintable_rows(label, rows, range(5)) == [2, 3, 4]
    assert unprintable_rows(label, rows, [0, 4]) == []
    assert unprintable_rows(shelf_label(), ROWS, range(3)) == []  # QR from text: fine
    backend = Recorder()
    with pytest.raises(UnprintableRows) as info:
        print_series(backend, "host", Series(label, rows, range(5), PrintOptions()))
    assert info.value.rows == [2, 3, 4] and backend.prepared == [] and backend.jobs == []


def test_rows_whose_codes_the_encoder_refuses_are_unprintable():
    """Content the quick check passes but no code holds: GS1 data of the
    wrong length, more than a DataMatrix or PDF417 code holds, a QR code
    over the room of its error correction level. Printed, each would be a
    crossed-out box."""
    for symbology, good, bad, level in (
        ("gs1_128", "(01)09501101530003", "(01)123", ""),
        ("datamatrix", "ok", "x" * 5000, ""),
        ("pdf417", "ok", "x" * 5000, ""),
        ("qr", "x" * 1271, "x" * 1500, "H"),  # fits at M, not at H
    ):
        label = Label(
            tape_mm=12,
            printer="fake-mono",
            elements=[CodeElement(data="{C}", symbology=symbology, error_correction=level)],
        )
        rows = [{"C": good}, {"C": bad}]
        assert unprintable_rows(label, rows, range(2)) == [2], symbology


def test_stopped_while_the_rows_are_checked(monkeypatch):
    """The check of many rows takes a while: Stop ends it after the first
    row, which is printed as always; the rows not checked are not."""
    from tavletta import serial as serial_module

    label = Label(
        tape_mm=12, printer="fake-mono", elements=[CodeElement(data="{C}", symbology="qr")]
    )
    rows = [{"C": "ok"}] * 10 + [{"C": "x" * 3000}] + [{"C": "ok"}] * 40
    stop = threading.Event()
    checked = []
    real = serial_module._code_fails

    def code_fails(element):
        checked.append(element.data)
        if len(checked) == 3:
            stop.set()  # the user stops on the third row
        return real(element)

    monkeypatch.setattr(serial_module, "_code_fails", code_fails)
    backend = Recorder()
    series = Series(label, rows, range(51), PrintOptions())
    outcome = print_series(backend, "host", series, stop=stop)
    assert outcome.stopped and outcome.printed == [1] and len(backend.jobs) == 1
    assert len(checked) == 3  # no row checked after the stop
    # set before: the first row only (row 11 is not looked at)
    assert unprintable_rows(label, rows, range(51), stop=stop) == []
    assert unprintable_rows(label, rows, range(10, 51), stop=stop) == [11]


def test_a_counter_that_overflows_a_code_on_a_later_copy():
    """The second copy's counter makes the EAN-13 13 digits with a wrong
    check digit: found before printing, not halfway through the row."""
    label = Label(
        tape_mm=12,
        printer="fake-mono",
        elements=[CodeElement(data="{P}{=counter}", symbology="ean13")],
    )
    rows = [{"P": "40063813339"}]
    one = Values(datetime(2026, 1, 1), counter=9, copies=1)
    two = Values(datetime(2026, 1, 1), counter=9, copies=2)
    assert unprintable_rows(label, rows, [0], one) == []
    assert unprintable_rows(label, rows, [0], two) == [1]


def test_the_counter_goes_on_from_row_to_row():
    label = Label(
        tape_mm=12, printer="fake-mono", elements=[TextElement(text="{=counter} {Name}")]
    )
    backend = Recorder()
    values = Values(datetime(2026, 1, 1), counter=5, step=2, copies=2)
    series = Series(label, ROWS, range(3), PrintOptions(copies=2), 1.0, values)
    outcome = print_series(backend, "host", series)
    texts = [label.elements[0].text for label in backend.prepared]
    assert texts == [
        "5 Screws",
        "7 Screws",
        "9 Washers",
        "11 Washers",
        "13 Fuses",
        "15 Fuses",
    ]
    assert (outcome.labels, outcome.counter) == (6, 17)


def test_a_failed_row_counts_the_labels_the_printer_reports():
    label = Label(tape_mm=12, printer="fake-mono", elements=[TextElement(text="{Name}")])
    label.elements.append(TextElement(text="{=counter}"))
    backend = Recorder()
    values = Values(datetime(2026, 1, 1), counter=1, copies=1)

    def jam_on_second(image, host, options, progress=None):
        if backend.prepared[-1].elements[0].text == "Washers":
            raise PaperJam("JAM", printed=0)
        return

    backend.print_label = jam_on_second
    with pytest.raises(SeriesError) as info:
        print_series(backend, "host", Series(label, ROWS, range(3), PrintOptions(), 1.0, values))
    assert info.value.row == 2 and info.value.outcome.printed == [1]
    assert (info.value.outcome.labels, info.value.outcome.counter) == (1, 2)


def test_without_a_counter_there_is_none_to_save():
    outcome = print_series(Recorder(), "host", series())
    assert outcome.counter is None and outcome.labels == 3


def test_codes_are_checked_with_the_counter_of_each_row():
    """The counter grows from row to row: a code it makes too long is found."""
    label = Label(
        tape_mm=12,
        printer="fake-mono",
        elements=[CodeElement(data="{Prefix}{=counter:00}", symbology="ean13")],
    )
    rows = [{"Prefix": "4006381333"}] * 3
    values = Values(datetime(2026, 1, 1), counter=98, copies=1)
    assert unprintable_rows(label, rows, range(3), values) == [3]  # 100: 13 digits
    # each row starts after the copies of the rows before: 97 98 | 99 100 | 101 102
    values = Values(datetime(2026, 1, 1), counter=97, copies=2)
    assert unprintable_rows(label, rows, range(3), values) == [2, 3]


# --- serial labels on label sheets -----------------------------------------------------


class SheetPrinter(Recorder):
    """A page printer: keeps the pages; fails on a chosen sheet (1-based)
    with the sheets before counted printed, as the system printer does."""

    def __init__(self):
        super().__init__()
        self.pages: list = []
        self.fail_sheet = 0
        self.progress: list = []

    def print_pages(self, pages, host, options, progress=None):
        self.options = options
        for n, page in enumerate(pages, 1):
            if n == self.fail_sheet:
                raise PaperJam("jam", printed=n - 1, copies=len(pages), pages=True)
            self.pages.append(page)
            if progress:
                progress(Progress(n, len(pages)))
        return


def sheet_series(positions=(), copies=1, indexes=range(3), label=None):
    from tavletta.sheets import find
    from tavletta.sheets.layout import SheetJob

    sheet = SheetJob(find("Avery L7160"), 0, tuple(positions))  # 21 per sheet
    values = Values(datetime(2026, 1, 1), counter=1)
    return Series(
        label or shelf_label(), ROWS, indexes, PrintOptions(copies=copies), 1.0, values, sheet
    )


def test_serial_labels_fill_the_positions_of_label_sheets():
    """Each position one label: the rows in order, the copies of a row on
    consecutive positions, from the chosen positions of the first sheet."""
    backend = SheetPrinter()
    events = []
    outcome = print_series(backend, "office", sheet_series(copies=2), events.append)
    assert texts(backend) == ["Screws", "Screws", "Washers", "Washers", "Fuses", "Fuses"]
    assert len(backend.pages) == 1 and backend.options.copies == 6
    assert outcome.printed == [1, 2, 3] and outcome.labels == 6
    assert [(e.label, e.labels, e.row) for e in events] == [(1, 1, 1)]


def test_serial_labels_on_sheets_over_the_job_limit_print_nothing():
    """Rows times copies over what one job of sheets takes: refused before
    the first sheet, the counter where it was (no rows cut off silently
    and counted as printed)."""
    from tavletta.sheets.layout import MAX_LABELS, TooManyLabels

    backend = SheetPrinter()
    rows = [{"Name": "R%d" % n, "Code": "C", "Shelf": "S"} for n in range(MAX_LABELS + 1)]
    label = Label(tape_mm=12, printer="fake-mono", elements=[TextElement(text="{=counter}")])
    series = replace(sheet_series(label=label), rows=rows, indexes=range(len(rows)))
    outcome = SeriesResult()
    with pytest.raises(TooManyLabels):
        print_series(backend, "office", series, outcome=outcome)
    assert backend.pages == [] and outcome.labels == 0 and outcome.printed == []
    assert outcome.counter == 1

    class Counting(SheetPrinter):  # the sheets counted, not composed
        def print_pages(self, pages, host, options, progress=None):
            self.pages = [None] * len(pages)

    # at the limit all of them, and the counter goes on by each label printed
    backend = Counting()
    outcome = print_series(backend, "office", replace(series, indexes=range(MAX_LABELS)))
    assert outcome.labels == MAX_LABELS and len(outcome.printed) == MAX_LABELS
    assert outcome.counter == MAX_LABELS + 1 and len(backend.pages) == 48  # 21 a sheet


def test_serial_labels_on_sheets_count_per_position():
    """Variables that change per label: each position its own counter."""
    label = Label(
        tape_mm=12, printer="fake-mono", elements=[TextElement(text="{=counter} {Name}")]
    )
    backend = SheetPrinter()
    outcome = print_series(backend, "office", sheet_series(copies=2, label=label))
    assert [lab.elements[0].text for lab in backend.prepared] == [
        "1 Screws",
        "2 Screws",
        "3 Washers",
        "4 Washers",
        "5 Fuses",
        "6 Fuses",
    ]
    assert outcome.counter == 7


def test_serial_labels_on_sheets_fail_with_where_to_go_on():
    """The second sheet jams: the rows of the first sheet are printed, the
    error names the next row, its copies already out and its position."""
    rows = [{"Name": "R%d" % n, "Code": "C", "Shelf": "S"} for n in range(1, 31)]
    backend = SheetPrinter()
    backend.fail_sheet = 2
    series = replace(sheet_series(positions=range(16, 21), copies=2), rows=rows, indexes=range(30))
    with pytest.raises(SeriesError) as info:
        print_series(backend, "office", series)
    error = info.value
    # 5 positions on the first sheet: rows 1, 2 and the first copy of row 3
    assert error.printed == [1, 2] and error.row == 3 and error.copies_printed == 1
    assert error.position == (1, 2)  # its second copy: the first position of sheet 2
    assert error.outcome.labels == 5


def test_serial_labels_on_sheets_fail_after_every_sheet_came_out():
    """A backend may fail after the last sheet is out (a job state asked
    afterwards): the last row stops with all its copies printed, no
    position left, every label counted; not an IndexError."""
    label = Label(tape_mm=12, printer="fake-mono", elements=[TextElement(text="{=counter}")])

    class FailsAtTheEnd(SheetPrinter):
        def print_pages(self, pages, host, options, progress=None):
            raise PaperJam("jam", printed=len(pages), copies=len(pages), pages=True)

    with pytest.raises(SeriesError) as info:
        print_series(FailsAtTheEnd(), "office", sheet_series(copies=2, label=label))
    error = info.value
    assert (error.row, error.copies_printed, error.position) == (3, 2, None)
    assert error.printed == [1, 2] and isinstance(error.error, PaperJam)
    assert error.outcome.labels == 6 and error.outcome.counter == 7


def test_serial_labels_on_sheets_that_cannot_be_drawn():
    backend = SheetPrinter()
    backend.prepare_error = RuntimeError("cairo")
    with pytest.raises(SeriesError) as info:
        print_series(backend, "office", sheet_series())
    assert info.value.preparing and info.value.row == 1 and info.value.position == (1, 1)
    assert backend.pages == []


def test_ctrl_c_on_sheets_counts_the_labels_that_may_have_come_out():
    """Interrupted while the sheets are sent: every label of the job used
    its number (job.Job counts them), so the counter goes on after them."""
    label = Label(
        tape_mm=12, printer="fake-mono", elements=[TextElement(text="{=counter} {Name}")]
    )
    backend = SheetPrinter()

    def interrupted(pages, host, options, progress=None):
        raise KeyboardInterrupt

    backend.print_pages = interrupted
    outcome = SeriesResult()
    with pytest.raises(KeyboardInterrupt):
        print_series(backend, "office", sheet_series(copies=2, label=label), outcome=outcome)
    assert outcome.labels == 6 and outcome.counter == 7


def test_a_row_the_printer_did_not_confirm_is_kept_for_the_series():
    """The result of every row counts: one row sent without confirmation
    and the series says so, also when the last row was confirmed."""
    backend = Recorder()
    results = iter([None, PrintResult(unconfirmed=True), None])
    real = backend.print_label

    def print_label(image, host, options, progress=None):
        real(image, host, options, progress)
        return next(results)

    backend.print_label = print_label
    outcome = print_series(backend, "h", series())
    assert outcome.result == PrintResult(unconfirmed=True)
