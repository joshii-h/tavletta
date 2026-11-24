"""The OpenAPI 3.1 description of the REST API, written by hand.

Served at /api/v1/openapi.json (to every valid token, like the list of
labels). Keep it in step with http.py (ROUTES), requests.py (the fields)
and service.py (the answers); tests check that the routes match exactly
and that docs/openapi.yaml and site/docs/api.md, which tools/openapi_docs.py
writes from it, are up to date.

Every operation names the role it needs (x-tavletta-role) and every error
answer the codes it can carry (x-tavletta-codes): the API reference of the
site is made from these.
"""

from __future__ import annotations

from typing import Any

from tavletta import __version__
from tavletta.merge import limits
from tavletta.model import MAX_COUNTER
from tavletta.server.requests import (
    MAX_ASSIGNMENTS,
    MAX_COPIES,
    MAX_NAME,
    MAX_VALUE,
    MAX_WAIT,
)

ROLE = "x-tavletta-role"
CODES = "x-tavletta-codes"

# the codes of printer problems (backends/base.py: PrinterError.code and its
# subclasses), as "printer_<code>"; plugins may add their own
PRINTER_CODES = (
    "generic",
    "not_found",
    "timeout",
    "stalled",
    "port_closed",
    "busy",
    "paused",
    "out_of_memory",
    "cover_open",
    "head_open",
    "jam",
    "media_jam",
    "out_of_labels",
    "out_of_paper",
    "label_not_found",
    "wrong_media",
    "paper_size",
    "page_too_large",
    "too_long",
    "too_wide",
    "code_too_large",
    "ribbon",
    "ribbon_out",
    "ribbon_direct",
    "no_cassette",
    "bad_cassette",
    "supplies",
    "format",
    "resolution",
    "queue_paused",
    "job_held",
    "job_canceled",
    "usb_not_found",
    "usb_permission",
    "serial_permission",
    "battery",
    "overheat",
    "unsupported_model",
    "unknown_model",
    "editor_lite",
)


def _ref(name: str) -> dict:
    return {"$ref": "#/components/schemas/" + name}


def _json(schema: dict, description: str, example: Any = None) -> dict:
    content: dict[str, Any] = {"schema": schema}
    if example is not None:
        content["example"] = example
    return {"description": description, "content": {"application/json": content}}


def _error(description: str, *codes: str) -> dict:
    answer: dict[str, Any] = {
        "description": description,
        "content": {"application/json": {"schema": _ref("Error")}},
    }
    if codes:
        answer[CODES] = list(codes)
    return answer


_TEXTS = {
    "type": "object",
    "maxProperties": MAX_ASSIGNMENTS,
    "additionalProperties": {"type": ["string", "number", "null"]},
}

LABEL_FIELDS: dict[str, Any] = {
    "label": {
        "type": "string",
        "maxLength": 1024,
        "description": "Name of a label file in the labels folder (see GET /labels), "
        "subfolders with '/'; '.tavletta' may be left out. Either label or label_content.",
        "examples": ["asset-tag"],
    },
    "label_content": {
        "type": "object",
        "description": "Or the label itself, as the JSON of a .tavletta file, checked like "
        "a file that is opened. Its data file is ignored (send data_rows instead), its "
        "counter never saved.",
    },
    "printer": {
        "type": "string",
        "description": "With printers.toml on the server: the printer's name (default: "
        "the default printer the token may use, or the only one). Without it: a printer "
        "model id, if not the label's own (the label is fitted as in the app).",
        "examples": ["it-cab"],
    },
    "set": dict(
        _TEXTS,
        description="Element name or variable name -> value, as --set on the command "
        "line; variables in a value are filled in; 'date' sets the moment {=date} and "
        "{=time} start from (ISO 8601). At most %d entries." % MAX_ASSIGNMENTS,
        examples=[{"title": "Flour", "lot": "A-17"}],
    ),
    "set_literal": dict(
        _TEXTS, description="Like set, but braces in a value are text, not variables."
    ),
    "counter_start": {
        "type": "integer",
        "minimum": 0,
        "maximum": MAX_COUNTER,
        "description": "The {=counter} of the first label; it goes up by the label's "
        "step with every label, copy and row.",
    },
    "data_rows": {
        "type": "array",
        "minItems": 1,
        "maxItems": limits.MAX_ROWS,
        "items": {
            "type": "object",
            "maxProperties": limits.MAX_COLUMNS,
            "additionalProperties": {"type": ["string", "number", "null"]},
        },
        "description": "Serial labels: one label per row, column name -> value; the "
        "label's {Column} placeholders take the values. Numbers are taken as text, null "
        "as an empty cell. At most %d rows, %d columns, %d cells, %d characters a value."
        % (limits.MAX_ROWS, limits.MAX_COLUMNS, limits.MAX_CELLS, MAX_VALUE),
        "examples": [[{"AssetNumber": "100001"}]],
    },
    "data_file": {
        "type": "string",
        "description": "Or a CSV, TSV, ODS or XLSX file in the labels folder. Without "
        "data_rows and data_file, the label's own data file is used if it lies beside "
        "the label in the labels folder.",
    },
    "sheet": {
        "type": "string",
        "description": "Sheet of the data file, the first by default.",
    },
}

SCHEMAS: dict[str, Any] = {
    "Error": {
        "type": "object",
        "required": ["error"],
        "properties": {
            "error": {
                "type": "object",
                "required": ["code", "message"],
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Stable, for programs: the same in every language.",
                    },
                    "message": {
                        "type": "string",
                        "description": "For people, in the language of Accept-Language.",
                    },
                },
            }
        },
        "example": {"error": {"code": "label_not_found", "message": "No such label: x"}},
    },
    "Warning": {
        "type": "object",
        "required": ["code", "message"],
        "properties": {
            "code": {
                "type": "string",
                "examples": ["barcode_narrow", "font_missing", "other_labels_loaded"],
            },
            "message": {"type": "string"},
        },
    },
    "Health": {
        "type": "object",
        "required": ["status", "version"],
        "properties": {"status": {"const": "ok"}, "version": {"type": "string"}},
        "example": {"status": "ok", "version": __version__},
    },
    "NamedPrinter": {
        "type": "object",
        "description": "A printer of printers.toml.",
        "required": ["name", "printer", "host"],
        "properties": {
            "name": {"type": "string", "description": "What requests name in 'printer'."},
            "printer": {"type": "string", "description": "Printer model id."},
            "model": {"type": "string", "description": "Printer model, for people."},
            "host": {"type": "string"},
            "description": {"type": ["string", "null"]},
            "location": {"type": ["string", "null"]},
            "media": {"type": ["string", "null"]},
            "default": {"type": "boolean"},
            "max_copies": {"type": "integer"},
            "status": {
                "description": "The state (as PrinterStatus), or state UNREACHABLE with "
                "an error; null with status=false.",
                "type": ["object", "null"],
            },
        },
    },
    "Printer": {
        "type": "object",
        "description": "A printer model (without printers.toml).",
        "required": ["id", "name", "kind", "found"],
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "kind": {"enum": ["tape", "labels", "pages"]},
            "dpi": {"type": "number"},
            "color_model": {"enum": ["color", "mono", "two-color"]},
            "tape_widths_mm": {"type": "array", "items": {"type": "number"}},
            "label_sizes_mm": {
                "type": "array",
                "items": {"type": "array", "items": {"type": "number"}},
            },
            "configured_host": {"type": ["string", "null"]},
            "found": {
                "type": "array",
                "description": "Printers found on the network (--discover).",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "host": {"type": "string"},
                        "address": {"type": "string"},
                    },
                },
            },
        },
    },
    "PrinterStatus": {
        "type": "object",
        "required": ["printer", "host", "state"],
        "properties": {
            "printer": {"type": "string"},
            "host": {"type": "string"},
            "name": {"type": "string", "description": "Its name in printers.toml."},
            "model": {"type": "string"},
            "state": {"type": "string", "examples": ["IDLE", "PRINTING", "ERROR"]},
            "error": {"oneOf": [{"type": "null"}, _ref("Warning")]},
            "dpi": {"type": "number"},
            "firmware": {"type": ["string", "null"]},
            "modes": {"type": "array", "items": {"type": "string"}},
            "media": {
                "type": "object",
                "description": "What is loaded and how the printer is set up, as far "
                "as it tells (null: not told): tape_mm, remaining_mm, total_mm, pitch_mm "
                "(die-cut labels: the label distance measured), width_mm (the print "
                "width set on a ZPL printer: a setting, not the loaded labels), "
                "print_method (direct or transfer, as set on the printer), media_type "
                "(gap, mark or continuous: how the printer is set to find the labels), "
                "cassette. Not told by the printer but from the server's settings for "
                "it: job_print_method and job_media_type, what this server's jobs do.",
                "properties": {
                    "tape_mm": {"type": ["number", "null"]},
                    "remaining_mm": {"type": ["integer", "null"]},
                    "total_mm": {"type": ["integer", "null"]},
                    "pitch_mm": {
                        "type": ["number", "null"],
                        "description": "Die-cut labels: the distance from one label to "
                        "the next, as the printer measured it.",
                    },
                    "label_mm": {
                        "type": ["array", "null"],
                        "items": {"type": "number"},
                        "description": "Die-cut labels: the size of the labels loaded "
                        "(width, height), if the printer tells it.",
                    },
                    "width_mm": {
                        "type": ["number", "null"],
                        "description": "The print width set on the printer (ZPL): a "
                        "setting, which each Tavletta job sets too, not the width of the "
                        "loaded labels.",
                    },
                    "print_method": {
                        "type": ["string", "null"],
                        "enum": ["direct", "transfer", None],
                        "description": "Thermal printers: the print method set on the "
                        "printer, direct (thermal direct) or transfer (with a ribbon). A "
                        "job may print with another one: see job_print_method.",
                    },
                    "job_print_method": {
                        "type": ["string", "null"],
                        "enum": ["direct", "transfer", None],
                        "description": "Thermal printers: the print method this server's "
                        "jobs print with. The printer's settings (method in printers.toml, "
                        "else the app's) may set it for each job (cab), else it is the "
                        "one set on the printer (print_method; ZPL always). null: unknown.",
                    },
                    "media_type": {
                        "type": ["string", "null"],
                        "enum": ["gap", "mark", "continuous", None],
                        "description": "Die-cut printers: how the printer is set to find "
                        "the labels (ZPL), gap between labels, black mark or continuous "
                        "media. A job may find them otherwise: see job_media_type.",
                    },
                    "job_media_type": {
                        "type": ["string", "null"],
                        "enum": ["gap", "mark", "continuous", "transparent", None],
                        "description": "Die-cut printers: how this server's jobs find the "
                        "labels. The printer's settings (media in printers.toml, else the "
                        "app's) may set it for each job (ZPL ^MN, TSPL, EPL2, NIIMBOT), "
                        "else it is the one set on the printer (media_type). When it is "
                        "not media_type, what the printer reports of the loaded labels is "
                        "not for these jobs: no other_labels_loaded. null: unknown.",
                    },
                    "cassette": {"type": ["object", "null"]},
                },
            },
        },
    },
    "LabelFile": {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string", "examples": ["asset-tag.tavletta"]},
            "bytes": {"type": "integer"},
            "modified": {"type": "string", "format": "date-time"},
        },
    },
    "Label": {
        "type": "object",
        "required": ["name", "printer", "elements"],
        "properties": {
            "name": {"type": "string"},
            "printer": {"type": "object", "description": "Model id and name."},
            "size": {"type": "object", "description": "Tape, length, orientation, mm."},
            "elements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "kind": {"type": "string", "examples": ["text", "code", "image"]},
                        "name": {
                            "type": ["string", "null"],
                            "description": "What 'set' fills in.",
                        },
                        "text": {"type": "string"},
                        "data": {"type": "string"},
                        "symbology": {"type": "string"},
                        "file": {"type": ["string", "null"]},
                    },
                },
            },
            "variables": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Variables of your own, {=lot}: filled in with 'set'.",
            },
            "builtin_variables": {"type": "array", "items": {"type": "string"}},
            "columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Placeholders {Column}: the keys of data_rows.",
            },
            "data_file": {"type": ["string", "null"]},
            "data_file_unusable": {"type": "boolean"},
            "data_file_missing": {
                "type": "boolean",
                "description": "data_file is not there (any more): send the rows in data_rows.",
            },
            "data_sheet": {"type": ["string", "null"]},
            "counter": {
                "type": "object",
                "description": "used (the label has {=counter}), next, step.",
            },
            "features": {"type": "array", "items": {"type": "string"}},
            "on_sheet": {
                "type": "boolean",
                "description": "Printed on label sheets: at most 999 labels in one job.",
            },
        },
    },
    "PreviewRequest": {
        "type": "object",
        "additionalProperties": False,
        "properties": dict(
            LABEL_FIELDS,
            row={
                "type": "integer",
                "minimum": 1,
                "maximum": limits.MAX_ROWS,
                "default": 1,
                "description": "Data row to show, from 1.",
            },
            format={
                "enum": ["png", "json"],
                "default": "png",
                "description": "'json': width, height, warnings and the PNG in base64.",
            },
            view={
                "enum": ["preview", "printer"],
                "default": "preview",
                "description": "'printer': the image as the printer gets it (turned, in "
                "its colours).",
            },
        ),
    },
    "Preview": {
        "type": "object",
        "required": ["width", "height", "warnings", "png"],
        "properties": {
            "width": {"type": "integer"},
            "height": {"type": "integer"},
            "warnings": {
                "type": "array",
                "items": _ref("Warning"),
                "description": "What would print wrongly. other_labels_loaded: the printer "
                "of printers.toml reports other labels loaded than the label's (their size, "
                "or the label distance it measured). Its latest answer counts if it is of "
                "the last minute, an error as nothing known (asked anew in the "
                "background); a printer not known yet is waited for up to a second after "
                "the label is rendered.",
            },
            "png": {"type": "string", "contentEncoding": "base64"},
            "geometry": {
                "oneOf": [{"type": "null"}, _ref("Geometry")],
                "description": "Where the elements are in the image; null for "
                "view=printer and a label turned onto its sheet.",
            },
        },
    },
    "Geometry": {
        "type": "object",
        "description": "The elements' boxes in the preview image, in mm from its top left "
        "corner (tavletta/editing.py, docs/editing.md); a pixel of the image is "
        "mm * dpi / 25.4.",
        "required": ["width_mm", "height_mm", "layout", "elements", "dpi"],
        "properties": {
            "width_mm": {"type": "number", "description": "Along the tape: the length."},
            "height_mm": {"type": "number", "description": "Across: tape or label height."},
            "layout": {"enum": ["flow", "free"]},
            "dpi": {"type": "number", "description": "The image's resolution."},
            "elements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {
                            "type": "integer",
                            "description": "From 0, in the order of the label's elements "
                            "(GET /labels/{name} counts them from 1).",
                        },
                        "kind": {"type": "string"},
                        "box": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 4,
                            "maxItems": 4,
                            "description": "left, top, right, bottom in mm: its ink.",
                        },
                        "rotation": {"type": "integer"},
                    },
                },
            },
        },
        "example": {
            "width_mm": 40.0,
            "height_mm": 18.6,
            "layout": "free",
            "dpi": 300.0,
            "elements": [
                {"index": 0, "kind": "text", "box": [2.0, 1.5, 21.83, 6.2], "rotation": 0}
            ],
        },
    },
    "Me": {
        "type": "object",
        "required": ["name", "role", "token_required", "limits"],
        "properties": {
            "name": {
                "type": "string",
                "description": "The token's name in tokens.toml; 'token' for the one token of "
                "--token-file, 'local' without a token; 'oidc:<user>' for someone signed in "
                "to the web page with OpenID Connect.",
            },
            "display_name": {
                "type": "string",
                "description": "A person's name from the provider; else the same as name.",
            },
            "session": {
                "type": "boolean",
                "description": "true: signed in to the web page with OpenID Connect (its "
                "session cookie), not with a token.",
            },
            "role": {"enum": ["preview", "print", "admin"]},
            "printers": {
                "type": ["array", "null"],
                "items": {"type": "string"},
                "description": "The printers the token may use; null: all.",
            },
            "token_required": {
                "type": "boolean",
                "description": "false: the server has no token and no --oidc (loopback only).",
            },
            "limits": {
                "type": "object",
                "description": "What one request may ask for at most: labels in one job "
                "(--max-labels; on label sheets at most 999), copies, data rows, columns "
                "and cells, and the body in bytes (--max-body; also the largest label "
                "file to upload).",
                "properties": {
                    "max_labels": {"type": "integer"},
                    "max_sheet_labels": {"type": "integer"},
                    "max_copies": {"type": "integer"},
                    "max_rows": {"type": "integer"},
                    "max_columns": {"type": "integer"},
                    "max_cells": {"type": "integer"},
                    "max_body": {"type": "integer"},
                },
            },
        },
        "example": {
            "name": "shipping-terminal",
            "role": "print",
            "printers": ["it-cab"],
            "token_required": True,
            "limits": {
                "max_labels": 10000,
                "max_sheet_labels": 999,
                "max_copies": MAX_COPIES,
                "max_rows": limits.MAX_ROWS,
                "max_columns": limits.MAX_COLUMNS,
                "max_cells": limits.MAX_CELLS,
                "max_body": 4194304,
            },
        },
    },
    "DataRequest": {
        "type": "object",
        "additionalProperties": False,
        "required": ["content"],
        "properties": {
            "content": {
                "type": "string",
                "contentEncoding": "base64",
                "description": "The file: CSV (any delimiter, UTF-8, UTF-16 or Windows-1252), "
                "ODS or XLSX, told apart by content. Pasted text is a CSV file.",
            },
            "name": {
                "type": "string",
                "maxLength": MAX_NAME,
                "description": "The file's name, for messages.",
            },
            "sheet": {
                "type": "string",
                "description": "ODS, XLSX: the sheet (default the first).",
            },
            "header": {
                "type": "boolean",
                "default": True,
                "description": "false: the first row is data too; the columns are named A, B, C …",
            },
        },
    },
    "Data": {
        "type": "object",
        "required": ["kind", "columns", "rows"],
        "properties": {
            "kind": {"enum": ["csv", "ods", "xlsx"]},
            "delimiter": {
                "type": ["string", "null"],
                "description": "CSV: the delimiter found; null for one column and spreadsheets.",
            },
            "sheets": {"type": "array", "items": {"type": "string"}},
            "sheet": {"type": ["string", "null"], "description": "The sheet read."},
            "columns": {"type": "array", "items": {"type": "string"}},
            "rows": {
                "type": "array",
                "items": {"type": "object", "additionalProperties": {"type": "string"}},
                "description": "Every cell as the text it shows: leading zeros and number "
                "formats kept (00300 stays 00300). Rows without a filled cell are left out.",
            },
        },
        "example": {
            "kind": "csv",
            "delimiter": ";",
            "sheets": [],
            "sheet": None,
            "columns": ["AssetNumber", "Room"],
            "rows": [{"AssetNumber": "00300", "Room": "B 2.14"}],
        },
    },
    "PrintRequest": {
        "type": "object",
        "additionalProperties": False,
        "properties": dict(
            LABEL_FIELDS,
            host={
                "type": "string",
                "maxLength": 512,
                "description": "Printer address, only without printers.toml (default: the "
                "one set in the app, else with --discover the only printer found). "
                "Refused (host_given) with printers.toml.",
            },
            copies={
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_COPIES,
                "default": 1,
                "description": "Copies of each label (each row); at most the printer's "
                "max_copies. Copies times rows: at most --max-labels (10000 by default), "
                "on label sheets at most 999.",
            },
            rows={
                "type": "string",
                "maxLength": 50,
                "description": "The data rows to print, from 1: '3-10', '5', '7-' (to the "
                "last) or '-4'. To print again from where a job stopped.",
                "examples": ["14-20"],
            },
            no_save_counter={
                "type": "boolean",
                "default": False,
                "description": "Do not save the next {=counter} value into the label file.",
            },
            wait={
                "type": "number",
                "minimum": 0,
                "maximum": MAX_WAIT,
                "default": 0,
                "description": "Seconds to wait for the job to finish before answering: "
                "200 if it did, else 202.",
            },
        ),
    },
    "Job": {
        "type": "object",
        "required": ["id", "state", "printed", "total"],
        "properties": {
            "id": {"type": "string", "description": "Random, 16 characters."},
            "state": {
                "enum": ["queued", "waiting", "printing", "done", "failed", "canceled"],
                "description": "waiting: its turn came, another job holds the label file "
                "(its counter). done, failed and canceled are final.",
            },
            "printer": {"type": "string", "description": "Printer model id."},
            "printer_name": {
                "type": ["string", "null"],
                "description": "Its name in printers.toml.",
            },
            "host": {"type": "string", "description": "The printer's address."},
            "owner": {"type": ["string", "null"], "description": "Name of the token."},
            "label": {
                "type": ["string", "null"],
                "description": "Name in the labels folder; null for label_content.",
            },
            "printed": {
                "type": "integer",
                "description": "Labels printed so far, for certain.",
            },
            "printed_max": {
                "type": ["integer", "null"],
                "description": "After a failure whose outcome the printer could not tell "
                "(the connection was lost while it printed): up to this many labels may "
                "have printed. The counter goes on after them; rows_printed counts the "
                "rows printed for certain, so printing again from the next one may print "
                "labels twice. null otherwise.",
            },
            "total": {"type": "integer", "description": "Labels to print: copies x rows."},
            "rows_printed": {
                "type": "integer",
                "description": "Data rows printed in full: after a failure, print again "
                "with rows set to the next one (rows_printed + first row).",
            },
            "cancel_requested": {"type": "boolean", "description": "DELETE was sent."},
            "error": {
                "oneOf": [{"type": "null"}, _ref("Warning")],
                "description": "Why it failed: printer_<code>, counter_not_saved, "
                "label_locked or prepare_failed.",
            },
            "remark": {
                "type": ["string", "null"],
                "description": "A note of the printer, for people.",
            },
            "warnings": {
                "type": "array",
                "items": _ref("Warning"),
                "description": "Found when it was checked; it printed anyway. "
                "other_labels_loaded: the printer reports other labels loaded than the "
                "label's (their size, or the label distance it measured); for a printer "
                "of printers.toml as in a preview when the job is queued, and replaced by "
                "what the printer reports when the job prints, if it then tells of the "
                "loaded labels.",
            },
            "created": {"type": "string", "format": "date-time", "description": "UTC."},
            "started": {"type": ["string", "null"], "format": "date-time"},
            "finished": {"type": ["string", "null"], "format": "date-time"},
        },
        "example": {
            "id": "HxIwYqXuV4qYRBM7",
            "state": "done",
            "printer": "cab",
            "printer_name": "it-cab",
            "host": "cab-it.example.com",
            "owner": "inventory-tool",
            "label": "asset-tag.tavletta",
            "printed": 20,
            "printed_max": None,
            "total": 20,
            "rows_printed": 20,
            "cancel_requested": False,
            "error": None,
            "remark": None,
            "warnings": [],
            "created": "2026-11-12T15:34:45+00:00",
            "started": "2026-11-12T15:34:45+00:00",
            "finished": "2026-11-12T15:35:04+00:00",
        },
    },
}

_LANGUAGE = {
    "name": "Accept-Language",
    "in": "header",
    "required": False,
    "schema": {"type": "string"},
    "description": "Language of the messages, e.g. de-CH; English by default.",
}
_ASK = {
    "name": "X-Tavletta-Request",
    "in": "header",
    "required": False,
    "schema": {"type": "string"},
    "description": "Needed (any value) when the server has no token: a web page cannot "
    "send it, so it cannot make the server contact printers.",
}
_JOB_ID = {
    "name": "id",
    "in": "path",
    "required": True,
    "schema": {"type": "string"},
    "description": "The id of POST /print.",
}
_LOCATION = {
    "Location": {
        "description": "/api/v1/jobs/{id} of the job.",
        "schema": {"type": "string"},
    }
}

_BAD_REQUEST = (
    "bad_request",
    "invalid_field",
    "unknown_field",
    "no_label",
    "data_conflict",
    "data_column",
    "not_json",
    "no_rows",
    "no_data",
)
_LABEL_PROBLEMS = (
    "file_problem",
    "label_not_label",
    "label_newer",
    "label_damaged",
    "label_unprintable",
    "data_outside",
    "data_problem",
)


def _operation(summary: str, role: str | None, responses: dict, **extra) -> dict:
    """An operation with the answers every request can get."""
    if role is not None:
        responses.setdefault(
            "401", _error("Access token missing, wrong, expired or revoked", "unauthorized")
        )
        responses.setdefault(
            "429", _error("Too many wrong tokens from this address", "too_many_requests")
        )
    responses.setdefault(
        "403",
        _error(
            "Not allowed for this token's role, a web page (Origin) not allowed, or "
            "X-Tavletta-Request missing (no token, a request that contacts printers)",
            "forbidden",
            "origin_not_allowed",
            "header_required",
        ),
    )
    responses.setdefault("405", _error("Not a method of this path", "method_not_allowed"))
    responses.setdefault("408", _error("The request took longer than 30 s to send", "timeout"))
    responses.setdefault("500", _error("An error of the server (logged)", "internal"))
    responses.setdefault(
        "503", _error("Busy: no request slot, render slot or thread free", "busy")
    )
    responses.setdefault("501", {"description": "A method HTTP knows but the server does not"})
    responses.setdefault("421", _error("Not a loopback Host on a loopback server", "wrong_host"))
    if "requestBody" in extra:
        responses.setdefault("411", _error("No Content-Length", "length_required"))
        responses.setdefault("413", _error("Too large (--max-body, 4 MB by default)", "too_large"))
        responses.setdefault("415", _error("Not application/json", "not_json"))
    operation: dict[str, Any] = {"summary": summary}
    if role is None:
        operation["security"] = []
    operation[ROLE] = role or "none"
    operation["parameters"] = [_LANGUAGE]
    operation["responses"] = dict(sorted(responses.items()))
    operation.update(extra)
    return operation


_ONE_ASSET = {
    "label": "asset-tag",
    "printer": "it-cab",
    "data_rows": [{"AssetNumber": "100001"}],
    "wait": 30,
}
_SERIES = {
    "label": "asset-tag",
    "printer": "it-cab",
    "data_rows": [{"AssetNumber": str(n)} for n in range(100001, 100004)],
}
_COUNTER = {
    "label": "asset-tag-counter",
    "printer": "it-cab",
    "counter_start": 100001,
    "copies": 20,
    "no_save_counter": True,
}


def _status_paths() -> dict:
    return {
        "/printers": {
            "get": _operation(
                "The printers of printers.toml the token may use, with their state; without "
                "it the printer models, the address set for each and the printers found "
                "(--discover)",
                "preview",
                {
                    "200": _json(
                        {
                            "type": "object",
                            "properties": {
                                "printers": {
                                    "type": "array",
                                    "items": {"oneOf": [_ref("NamedPrinter"), _ref("Printer")]},
                                }
                            },
                        },
                        "Printers",
                    )
                },
                parameters=[
                    _LANGUAGE,
                    _ASK,
                    {
                        "name": "discover",
                        "in": "query",
                        "schema": {"type": "boolean", "default": True},
                        "description": "Without printers.toml and with --discover: search.",
                    },
                    {
                        "name": "status",
                        "in": "query",
                        "schema": {"type": "boolean", "default": True},
                        "description": "Ask each printer of printers.toml for its state "
                        "(remembered for 5 s).",
                    },
                ],
            )
        },
        "/printers/{id}/status": {
            "get": _operation(
                "Ask a printer for its state: media, problems, resolution",
                "preview",
                {
                    "200": _json(_ref("PrinterStatus"), "State"),
                    "400": _error(
                        "No address, or host given with printers.toml",
                        "no_host",
                        "host_given",
                        "invalid_field",
                    ),
                    "403": _error(
                        "Address not allowed (--allow-printer), or host with a preview token",
                        "host_not_allowed",
                        "forbidden",
                        "origin_not_allowed",
                        "header_required",
                    ),
                    "404": _error(
                        "Unknown printer, or one the token may not use", "printer_not_found"
                    ),
                    "502": _error(
                        "The printer cannot be reached or reports a problem",
                        *("printer_" + code for code in PRINTER_CODES),
                    ),
                },
                parameters=[
                    _LANGUAGE,
                    _ASK,
                    {
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                        "description": "Printer name (printers.toml) or printer model id.",
                    },
                    {
                        "name": "host",
                        "in": "query",
                        "schema": {"type": "string"},
                        "description": "Only without printers.toml; needs the role print.",
                    },
                ],
            )
        },
    }


def _label_paths() -> dict:
    return {
        "/labels": {
            "get": _operation(
                "The label files in the labels folder and its subfolders",
                "preview",
                {
                    "200": _json(
                        {
                            "type": "object",
                            "properties": {
                                "labels": {"type": "array", "items": _ref("LabelFile")}
                            },
                        },
                        "Labels",
                    )
                },
            )
        },
        "/labels/{name}": {
            "get": _operation(
                "What a label can be filled in with: elements, variables, data columns, "
                "size, counter",
                "preview",
                {
                    "200": _json(_ref("Label"), "Label"),
                    "400": _error("Not a name inside the labels folder", "invalid_field"),
                    "404": _error("No such label", "label_not_found"),
                    "422": _error(
                        "The file is no label or damaged",
                        "file_problem",
                        "label_not_label",
                        "label_newer",
                        "label_damaged",
                    ),
                },
                parameters=[_LANGUAGE, _LABEL_NAME],
            ),
            **_label_admin_paths(),
        },
    }


_LABEL_NAME = {
    "name": "name",
    "in": "path",
    "required": True,
    "schema": {"type": "string"},
    "description": "May contain slashes for subfolders; '.tavletta' may be left out.",
}


def _label_admin_paths() -> dict:
    return {
        "put": _operation(
            "Upload a label file (admin): checked as the app opens a file, then put into "
            "the labels folder in one step",
            "admin",
            {
                "200": _json(_ref("LabelFile"), "Replaced (replace=true)"),
                "201": dict(
                    _json(_ref("LabelFile"), "Added"),
                    headers={
                        "Location": {
                            "description": "/api/v1/labels/{name} of the label.",
                            "schema": {"type": "string"},
                        }
                    },
                ),
                "400": _error(
                    "Not a name for a new label: letters, digits, spaces and . _ - ( ) + , "
                    "(up to 100), in the folder itself",
                    "invalid_field",
                    "not_json",
                ),
                "409": _error(
                    "A label of that name exists (send replace=true), or a print of it "
                    "holds the file",
                    "label_exists",
                    "label_locked",
                ),
                "413": _error(
                    "Over --max-body, or a label too large to render",
                    "too_large",
                    "label_too_large",
                ),
                "422": _error(
                    "Not a label Tavletta can open",
                    "label_not_label",
                    "label_newer",
                    "label_damaged",
                    "pages_not_allowed",
                ),
                "500": _error("Not saved (logged)", "not_saved", "internal"),
                "507": _error("No space left in the labels folder", "not_saved"),
            },
            parameters=[
                _LANGUAGE,
                dict(
                    _LABEL_NAME,
                    description="The file's name, without folder; '.tavletta' may be left out.",
                ),
                {
                    "name": "replace",
                    "in": "query",
                    "schema": {"type": "boolean", "default": False},
                    "description": "Replace a label of that name.",
                },
            ],
            requestBody={
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "description": "The content of a .tavletta file.",
                        }
                    }
                },
            },
        ),
        "delete": _operation(
            "Delete a label file (admin); its data file stays",
            "admin",
            {
                "204": {"description": "Deleted"},
                "400": _error("Not a name inside the labels folder", "invalid_field"),
                "404": _error("No such label", "label_not_found"),
                "409": _error("A print of it holds the file", "label_locked"),
                "500": _error("Not deleted (logged)", "not_saved", "internal"),
            },
            parameters=[_LANGUAGE, _LABEL_NAME],
        ),
    }


def _data_paths() -> dict:
    return {
        "/data": {
            "post": _operation(
                "Read a data file sent along (CSV, ODS, XLSX) as the app reads one: its "
                "rows for data_rows; nothing is kept",
                "preview",
                {
                    "200": _json(_ref("Data"), "The rows"),
                    "400": _error("Invalid request", "invalid_field", "unknown_field", "not_json"),
                    "413": _error(
                        "Over --max-body (the file in base64), over the limits of data files, "
                        "or more than 500000 cells (rows times columns)",
                        "too_large",
                    ),
                    "422": _error("Not a table, damaged or empty", "data_problem"),
                },
                requestBody={
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": _ref("DataRequest"),
                            "example": {
                                "name": "assets.csv",
                                "content": "QXNzZXROdW1iZXIKMDAzMDAKMDAzMDEK",
                            },
                        }
                    },
                },
            )
        },
    }


def _print_paths() -> dict:
    return {
        "/preview": {
            "post": _operation(
                "The label filled in, as an image; nothing is printed",
                "preview",
                {
                    "200": {
                        "description": "PNG, or JSON with format=json. Warning codes also "
                        "in the header X-Tavletta-Warnings (comma-separated).",
                        "headers": {
                            "X-Tavletta-Warnings": {
                                "description": "Codes of the warnings, if any.",
                                "schema": {"type": "string"},
                            }
                        },
                        "content": {
                            "image/png": {"schema": {"type": "string", "format": "binary"}},
                            "application/json": {"schema": _ref("Preview")},
                        },
                    },
                    "400": _error(
                        "Invalid request", *_BAD_REQUEST, "no_printer", "too_many_copies"
                    ),
                    "404": _error(
                        "No such label, printer or data file",
                        "label_not_found",
                        "printer_not_found",
                        "data_not_found",
                    ),
                    "413": _error(
                        "Too large: body, data rows or the label to render",
                        "too_large",
                        "label_too_large",
                    ),
                    "422": _error(
                        "The label or its data cannot be used",
                        *_LABEL_PROBLEMS,
                        "prepare_failed",
                    ),
                },
                requestBody={
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": _ref("PreviewRequest"),
                            "example": {
                                "label": "asset-tag",
                                "printer": "it-cab",
                                "data_rows": [{"AssetNumber": "100001"}],
                                "format": "png",
                            },
                        }
                    },
                },
            )
        },
        "/print": {
            "post": _operation(
                "Print: the request is checked, then queued; each printer prints one job "
                "at a time",
                "print",
                {
                    "200": _json(_ref("Job"), "The job finished within 'wait'"),
                    "202": dict(
                        _json(_ref("Job"), "Queued or printing; follow it at Location"),
                        headers=_LOCATION,
                    ),
                    "400": _error(
                        "Invalid request",
                        *_BAD_REQUEST,
                        "no_printer",
                        "no_host",
                        "host_given",
                        "too_many_copies",
                    ),
                    "403": _error(
                        "Not allowed: a preview token, an address not allowed, a web page or "
                        "X-Tavletta-Request missing",
                        "forbidden",
                        "host_not_allowed",
                        "origin_not_allowed",
                        "header_required",
                    ),
                    "404": _error(
                        "No such label, printer or data file, or a printer the token may not use",
                        "label_not_found",
                        "printer_not_found",
                        "data_not_found",
                    ),
                    "409": _error(
                        "The label file is not writable: the next counter value could not "
                        "be saved, nothing was printed",
                        "counter_not_saved",
                    ),
                    "413": _error(
                        "Too large: body, data rows, labels in one job or the label to render",
                        "too_large",
                        "too_many_labels",
                        "label_too_large",
                    ),
                    "422": _error("The label would print wrongly", *_LABEL_PROBLEMS),
                    "429": _error(
                        "Too many wrong tokens, or too many jobs waiting (100 in all, 20 per "
                        "named token)",
                        "too_many_requests",
                        "queue_full",
                    ),
                },
                requestBody={
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": _ref("PrintRequest"),
                            "examples": {
                                "one": {"summary": "One asset label", "value": _ONE_ASSET},
                                "series": {
                                    "summary": "A series, one label per row",
                                    "value": _SERIES,
                                },
                                "counter": {
                                    "summary": "20 numbers from the counter",
                                    "value": _COUNTER,
                                },
                            },
                        }
                    },
                },
            )
        },
        "/jobs/{id}": {
            "get": _operation(
                "State of a print job (a print token: its own jobs; admin: all). Kept 24 "
                "hours after it finished",
                "print",
                {"200": _json(_ref("Job"), "Job"), "404": _error("No such job", "job_not_found")},
                parameters=[_LANGUAGE, _JOB_ID],
            ),
            "delete": _operation(
                "Cancel a job: at once while it waits, after the labels already sent to the printer "
                "while it prints (cab: the job of up to labels_per_job labels)",
                "print",
                {
                    "202": _json(
                        _ref("Job"), "Canceled, or stopping after the labels already sent"
                    ),
                    "404": _error("No such job", "job_not_found"),
                    "409": _error("Already finished", "job_finished"),
                },
                parameters=[_LANGUAGE, _JOB_ID],
            ),
        },
    }


def _paths() -> dict:
    return {
        "/health": {
            "get": _operation(
                "Is the server running; no token needed (for health checks)",
                None,
                {"200": _json(_ref("Health"), "Running")},
            )
        },
        "/openapi.json": {
            "get": _operation(
                "This description (OpenAPI 3.1); a token of any role",
                "preview",
                {"200": _json({"type": "object"}, "OpenAPI")},
            )
        },
        "/me": {
            "get": _operation(
                "The token's name, role and printers, and the limits of a request (for the "
                "web page)",
                "preview",
                {"200": _json(_ref("Me"), "Who")},
            )
        },
        **_status_paths(),
        **_label_paths(),
        **_data_paths(),
        **_print_paths(),
    }


# every error code with its status and what to do, for programs (and the
# table of the API reference); printer_<code> stands for PRINTER_CODES
ERROR_CODES: tuple[tuple[int, str, str], ...] = (
    (400, "bad_request", "The request cannot be read (e.g. a body that ends early)."),
    (400, "not_json", "The body is no valid JSON (415 with another Content-Type)."),
    (400, "invalid_field", "A field has a wrong type or value; the message names it."),
    (400, "unknown_field", "A field the request does not have (a typo): nothing is done."),
    (400, "no_label", "Give either label or label_content."),
    (400, "data_conflict", "Give data_rows or data_file, not both."),
    (400, "data_column", "set names a {placeholder}: a data column, send it in data_rows."),
    (400, "no_rows", "rows selects no data row."),
    (400, "no_data", "sheet without a data file."),
    (400, "no_printer", "Several printers and no default: name one in printer."),
    (400, "no_host", "No printer address (without printers.toml)."),
    (400, "host_given", "host with printers.toml: choose the printer by name."),
    (400, "too_many_copies", "More copies than the printer's max_copies."),
    (401, "unauthorized", "Token missing, wrong, expired or revoked."),
    (403, "forbidden", "The token's role is too low (e.g. preview printing)."),
    (403, "host_not_allowed", "The printer address is not allowed (--allow-printer)."),
    (403, "origin_not_allowed", "A web page of an origin not allowed (--allow-origin)."),
    (403, "header_required", "No token on the server: send X-Tavletta-Request."),
    (404, "not_found", "No such path."),
    (404, "label_not_found", "No such label file in the labels folder."),
    (404, "data_not_found", "No such data file in the labels folder."),
    (404, "printer_not_found", "No such printer, or one the token may not use."),
    (404, "job_not_found", "No such job (another token's, or older than 24 hours)."),
    (405, "method_not_allowed", "Not a method of this path."),
    (408, "timeout", "The request took longer than 30 s to send."),
    (409, "job_finished", "The job has finished: nothing to cancel."),
    (409, "counter_not_saved", "The label file is not writable: nothing was printed."),
    (409, "label_exists", "A label of that name exists: upload with replace=true."),
    (409, "label_locked", "A print of the label holds the file: retry when it has ended."),
    (411, "length_required", "No Content-Length (chunked bodies are not accepted)."),
    (413, "too_large", "Body over --max-body, or too many data rows, columns or cells."),
    (
        413,
        "too_many_labels",
        "Copies times rows over --max-labels (10000 by default); on label sheets over 999.",
    ),
    (413, "label_too_large", "The label is too large to render (elements, text, pixels)."),
    (415, "not_json", "Send Content-Type: application/json."),
    (421, "wrong_host", "A loopback server asked for another host name."),
    (422, "file_problem", "The label file cannot be opened."),
    (422, "label_not_label", "The file is no Tavletta label."),
    (422, "label_newer", "The label needs a newer Tavletta: update the server."),
    (422, "label_damaged", "The label file is damaged."),
    (422, "label_unprintable", "It would print wrongly: a missing column, a bad code, …"),
    (
        422,
        "pages_not_allowed",
        "Document pages in label_content need the server switch --allow-pdf-pages.",
    ),
    (422, "data_outside", "The label's data file lies outside the labels folder."),
    (422, "data_problem", "The data file cannot be read, or has no such sheet."),
    (422, "prepare_failed", "The label cannot be prepared for the printer."),
    (429, "too_many_requests", "Ten wrong tokens from this address: wait a minute."),
    (429, "queue_full", "Too many jobs waiting: retry later."),
    (500, "internal", "An error of the server; it is logged."),
    (500, "not_saved", "The labels folder could not be written (507: it is full)."),
    (502, "printer_<code>", "The printer is unreachable or reports a problem."),
    (503, "busy", "All request or render slots taken: retry after a moment."),
)
# why a job failed: its "error" (the job answer, not an HTTP status)
JOB_ERROR_CODES: tuple[tuple[str, str], ...] = (
    ("printer_<code>", "The printer is unreachable or reports a problem (jam, out of labels…)."),
    ("counter_not_saved", "Printed, but the next counter value could not be saved."),
    ("label_locked", "Another print held the label file too long."),
    ("prepare_failed", "The label could not be prepared for the printer."),
)


DESCRIPTION = (
    "Print labels made with Tavletta. JSON in and out. All requests but /health need "
    "'Authorization: Bearer <token>' when the server has a token. Named tokens "
    "(tokens.toml) have a role: preview (look only), print (also print and follow its own "
    "jobs) or admin (also all jobs); x-tavletta-role names the least role of each "
    'operation. Errors are {"error": {"code", "message"}}: the code is stable, the '
    "message in the language of Accept-Language; x-tavletta-codes lists the codes of each "
    "answer."
)


def spec(mcp: bool = False) -> dict:
    description = DESCRIPTION
    if mcp:
        description += " MCP (Streamable HTTP) is served at /mcp."
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Tavletta",
            "version": __version__,
            "description": description,
            "license": {"name": "AGPL-3.0-or-later", "identifier": "AGPL-3.0-or-later"},
        },
        "servers": [{"url": "/api/v1"}],
        "x-tavletta-error-codes": [
            {"status": status, "code": code, "meaning": meaning}
            for status, code, meaning in ERROR_CODES
        ],
        "x-tavletta-job-error-codes": [
            {"code": code, "meaning": meaning} for code, meaning in JOB_ERROR_CODES
        ],
        "x-tavletta-printer-codes": list(PRINTER_CODES),
        "security": [{"bearer": []}],
        "paths": _paths(),
        "components": {
            "securitySchemes": {
                "bearer": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "A token of tokens.toml (tavletta token new) or the one token of "
                    "--token-file. Ten wrong tokens "
                    "from one address within a minute: 429 for a minute.",
                }
            },
            "schemas": SCHEMAS,
        },
    }
