"""What the print server's web page needs of the API, for any client:
who the token is (/me), data files read (/data), labels uploaded and
deleted (admin only), and where the elements are in a preview."""

from __future__ import annotations

import base64
import io
import json
import os
import threading

import pytest
from PIL import Image
from test_server import TOKEN, call, label_file, wait_for

from tavletta.model import Label, PageElement, TextElement
from tavletta.server import config as cfg
from tavletta.server import service as service_module

ADMIN, PRINTER, VIEWER = "admin-token-" * 2, "print-token-" * 2, "preview-token-" * 2
JSON = {"Content-Type": "application/json"}


@pytest.fixture
def roles(tmp_path):
    """tokens.toml with one token of each role."""
    path = tmp_path / "tokens.toml"
    entries = [("boss", "admin", ADMIN), ("desk", "print", PRINTER), ("wall", "preview", VIEWER)]
    path.write_text(
        "".join(
            '[tokens.%s]\nrole = "%s"\nhash = "%s"\n' % (name, role, cfg.hash_token(token))
            for name, role, token in entries
        )
    )
    path.chmod(0o600)
    return cfg.TokenStore(path)


@pytest.fixture
def base(make_server, roles):
    _server, url = make_server(tokens=roles, mcp=False)
    return url


def label_json(**fields) -> dict:
    fields.setdefault("printer", "fake-mono")
    fields.setdefault("tape_mm", 12)
    fields.setdefault("elements", [TextElement(text="Asset {Number}", name="title")])
    return Label(**fields).to_dict()


def put(base, name, content, token=ADMIN, query=""):
    raw = content if isinstance(content, bytes) else json.dumps(content).encode()
    path = "/api/v1/labels/%s%s" % (name, query)
    return call(base, "PUT", path, headers=JSON, token=token, raw=raw)


# --- /me -----------------------------------------------------------------------------


def test_me_tells_the_role_and_the_limits(base):
    for token, name, role in ((ADMIN, "boss", "admin"), (VIEWER, "wall", "preview")):
        status, _response, body = call(base, "GET", "/api/v1/me", token=token)
        assert status == 200
        assert (body["name"], body["role"], body["printers"]) == (name, role, None)
        assert body["token_required"] is True
        limits = body["limits"]
        assert limits["max_labels"] == 10_000 and limits["max_sheet_labels"] == 999
        assert limits["max_body"] == 4 * 1024 * 1024 and limits["max_copies"] == 9999
    assert call(base, "GET", "/api/v1/me")[0] == 401
    assert call(base, "GET", "/api/v1/me", token="wrong-token-123456")[0] == 401


def test_me_without_a_token_is_the_local_admin(make_server):
    _server, url = make_server()
    status, _response, body = call(url, "GET", "/api/v1/me")
    assert status == 200 and body["role"] == "admin" and body["token_required"] is False


def test_an_expired_token_is_refused_by_me(make_server, tmp_path):
    path = tmp_path / "old.toml"
    path.write_text(
        '[tokens.old]\nrole = "admin"\nhash = "%s"\nexpires = 2020-01-01\n' % cfg.hash_token(ADMIN)
    )
    path.chmod(0o600)
    _server, url = make_server(tokens=cfg.TokenStore(path), mcp=False)
    status, _response, body = call(url, "GET", "/api/v1/me", token=ADMIN)
    assert status == 401 and body["error"]["code"] == "unauthorized"


# --- /data ---------------------------------------------------------------------------


def data(base, content: bytes, token=VIEWER, **fields):
    body = dict(fields, content=base64.b64encode(content).decode())
    return call(base, "POST", "/api/v1/data", body=body, token=token)


@pytest.mark.parametrize(
    ("text", "delimiter"),
    [
        ("Number;Room\n00300;B 2.14\n00301;C 1\n", ";"),
        ("Number,Room\n00300,B 2.14\n00301,C 1\n", ","),
        ("Number\tRoom\n00300\tB 2.14\n00301\tC 1\n", "\t"),
    ],
)
def test_a_csv_is_read_with_its_leading_zeros(base, text, delimiter):
    status, _response, body = data(base, text.encode(), name="assets.csv")
    assert status == 200
    assert body["kind"] == "csv" and body["delimiter"] == delimiter
    assert body["columns"] == ["Number", "Room"]
    assert body["rows"] == [
        {"Number": "00300", "Room": "B 2.14"},
        {"Number": "00301", "Room": "C 1"},
    ]


def test_a_pasted_list_without_header(base):
    status, _response, body = data(base, b"00300\n00301\n", header=False)
    assert status == 200
    assert body["columns"] == ["A"] and body["rows"] == [{"A": "00300"}, {"A": "00301"}]
    assert body["delimiter"] is None


def test_xlsx_and_ods_are_read_as_shown(base, tmp_path):
    from test_merge import (
        date_styles,
        inline,
        make_ods,
        make_xlsx,
        num,
        ods_table,
        row,
        text,
        xrow,
    )

    xlsx = make_xlsx(
        tmp_path,
        xrow(inline("A1", "Other")),
        xrow(inline("A1", "Number")) + xrow(num("A2", "300", 2)) + xrow(num("A3", "301", 2)),
        names=("First", "Assets"),
        styles=date_styles((164, "00000")),
    )
    status, _response, body = data(base, xlsx.read_bytes(), sheet="Assets")
    assert status == 200 and body["kind"] == "xlsx"
    assert body["sheets"] == ["First", "Assets"] and body["sheet"] == "Assets"
    assert body["rows"] == [{"Number": "00300"}, {"Number": "00301"}]
    ods = make_ods(tmp_path, ods_table("T", row(text("Number")), row(text("00300"))))
    status, _response, body = data(base, ods.read_bytes())
    assert status == 200 and body["kind"] == "ods" and body["rows"] == [{"Number": "00300"}]


def test_data_that_is_no_table_is_refused(base):
    status, _response, body = data(base, b"%PDF-1.7 ...", name="scan.pdf")
    assert status == 422 and body["error"]["code"] == "data_problem"
    assert "scan.pdf" in body["error"]["message"]
    status, _response, body = call(
        base, "POST", "/api/v1/data", body={"content": "not base64!"}, token=VIEWER
    )
    assert status == 400 and body["error"]["code"] == "invalid_field"
    for wrong in ({}, {"content": 5}, {"content": "", "header": "no"}, {"content": "", "x": 1}):
        assert call(base, "POST", "/api/v1/data", body=wrong, token=VIEWER)[0] == 400
    status, _response, body = data(base, b"", name="n" * 300)
    assert status == 400
    status, _response, body = data(base, b"")
    assert status == 422  # empty


def test_data_needs_a_token_and_json(base):
    assert data(base, b"A\n1\n", token=None)[0] == 401
    raw = json.dumps({"content": ""}).encode()
    status = call(
        base,
        "POST",
        "/api/v1/data",
        headers={"Content-Type": "text/plain"},
        token=VIEWER,
        raw=raw,
    )[0]
    assert status == 415  # a form of another site cannot send it


def test_data_is_held_to_the_body_limit(make_server, roles):
    _server, url = make_server(tokens=roles, mcp=False, max_body=1024)
    status, _response, body = data(url, b"A\n" + b"1\n" * 1000)
    assert status == 413 and body["error"]["code"] == "too_large"


def test_data_is_held_to_the_row_limit(base, monkeypatch):
    from tavletta.merge import limits

    monkeypatch.setattr(limits, "MAX_ROWS", 3)
    status, _response, body = data(base, b"A\n1\n2\n3\n4\n")
    assert status == 422 and body["error"]["code"] == "data_problem"


# --- uploading and deleting labels -----------------------------------------------------


def test_admin_uploads_a_label(base, server_env):
    status, response, body = put(base, "Inventar%20(v2)", label_json())
    assert status == 201
    assert body["name"] == "Inventar (v2).tavletta" and body["bytes"] > 0
    assert response.getheader("Location") == "/api/v1/labels/Inventar%20%28v2%29.tavletta"
    saved = server_env.labels / "Inventar (v2).tavletta"
    assert Label.load(saved).elements[0].text == "Asset {Number}"
    names = [
        entry["name"] for entry in call(base, "GET", "/api/v1/labels", token=VIEWER)[2]["labels"]
    ]
    assert "Inventar (v2).tavletta" in names
    # no hidden upload left behind
    assert not [p for p in os.listdir(server_env.labels) if p.startswith(".")]


def test_an_existing_label_is_replaced_only_when_asked(base, server_env):
    status, _response, body = put(base, "box.tavletta", label_json())
    assert status == 409 and body["error"]["code"] == "label_exists"
    assert "box.tavletta" in body["error"]["message"]
    assert Label.load(server_env.labels / "box.tavletta").elements[0].text == "Box {=counter}"
    status, _response, body = put(base, "box", label_json(), query="?replace=true")
    assert status == 200 and body["name"] == "box.tavletta"
    assert Label.load(server_env.labels / "box.tavletta").elements[0].text == "Asset {Number}"


def test_a_folder_or_link_of_the_name_is_not_replaced(base, server_env, tmp_path):
    (server_env.labels / "shelf.tavletta").mkdir()
    status, _response, body = put(base, "shelf", label_json(), query="?replace=true")
    assert status == 409
    outside = tmp_path / "outside.tavletta"
    outside.write_text("{}")
    (server_env.labels / "link.tavletta").symlink_to(outside)
    status, _response, body = put(base, "link", label_json(), query="?replace=true")
    assert status == 409 and outside.read_text() == "{}"


@pytest.mark.parametrize(
    "name",
    [
        "..",
        ".hidden",
        "sub%2Fbox",
        "sub/box",
        "a%5Cb",
        "%2E%2E%2Fescape",
        "%00x",
        "trailing.",
        "%20space",
        "x" * 101,
        "semi;colon",
        "quote%22",
        "lt%3Cscript%3E",
    ],
)
def test_upload_names_stay_in_the_folder(base, server_env, name):
    status, _response, body = put(base, name, label_json())
    assert status in (400, 404, 405), name
    assert sorted(os.listdir(server_env.labels)) == ["box.tavletta"]


def test_an_upload_is_checked_like_a_file_that_is_opened(base, server_env):
    cases = [
        (b"not json at all", "label_not_label"),
        (json.dumps([1, 2]).encode(), "label_not_label"),
        (json.dumps({"version": 999, "printer": "fake-mono"}).encode(), "label_newer"),
        (json.dumps({"printer": "fake-mono", "tape_mm": "wide"}).encode(), "label_damaged"),
        (json.dumps({"printer": "no-such-printer"}).encode(), "label_newer"),
    ]
    for raw, code in cases:
        status, _response, body = put(base, "bad", raw)
        assert (status, body["error"]["code"]) == (422, code), raw
    assert sorted(os.listdir(server_env.labels)) == ["box.tavletta"]


def test_an_upload_keeps_the_limits_of_label_content(base, server_env):
    many = label_json(elements=[TextElement(text="x") for _ in range(201)])
    status, _response, body = put(base, "many", many)
    assert status == 413 and body["error"]["code"] == "label_too_large"
    page = label_json(elements=[PageElement(data="JVBERi0=")])
    status, _response, body = put(base, "page", page)
    assert status == 422 and body["error"]["code"] == "pages_not_allowed"
    assert sorted(os.listdir(server_env.labels)) == ["box.tavletta"]


def test_an_upload_is_held_to_the_body_limit(make_server, roles):
    _server, url = make_server(tokens=roles, mcp=False, max_body=2048)
    big = label_json(elements=[TextElement(text="x" * 4000)])
    status, _response, body = put(url, "big", big)
    assert status == 413 and body["error"]["code"] == "too_large"


def test_only_admin_uploads_and_deletes(base, server_env):
    for token in (PRINTER, VIEWER):
        status, _response, body = put(base, "new", label_json(), token=token)
        assert status == 403 and body["error"]["code"] == "forbidden"
        status, _response, body = call(base, "DELETE", "/api/v1/labels/box", token=token)
        assert status == 403 and body["error"]["code"] == "forbidden"
    assert put(base, "new", label_json(), token=None)[0] == 401
    assert call(base, "DELETE", "/api/v1/labels/box")[0] == 401
    assert sorted(os.listdir(server_env.labels)) == ["box.tavletta"]


def test_the_one_token_is_admin(make_server, server_env):
    _server, url = make_server(token=TOKEN, mcp=False)
    assert put(url, "new", label_json(), token=TOKEN)[0] == 201
    assert call(url, "DELETE", "/api/v1/labels/new", token=TOKEN)[0] == 204


def test_an_upload_needs_json_as_its_type(base):
    raw = json.dumps(label_json()).encode()
    for kind in (
        "text/plain",
        "multipart/form-data; boundary=x",
        "application/x-www-form-urlencoded",
    ):
        status = call(
            base, "PUT", "/api/v1/labels/new", headers={"Content-Type": kind}, token=ADMIN, raw=raw
        )[0]
        assert status == 415, kind


def test_admin_deletes_a_label(base, server_env):
    label_file(server_env.labels, "shelf/old.tavletta", TextElement(text="x"))
    status, _response, body = call(base, "DELETE", "/api/v1/labels/shelf/old", token=ADMIN)
    assert status == 204 and body == b""
    assert not (server_env.labels / "shelf" / "old.tavletta").exists()
    status, _response, body = call(base, "DELETE", "/api/v1/labels/shelf/old", token=ADMIN)
    assert status == 404 and body["error"]["code"] == "label_not_found"
    for name in ("..%2Fx", "%2Fetc%2Fpasswd", ".hidden"):
        assert call(base, "DELETE", "/api/v1/labels/" + name, token=ADMIN)[0] in (400, 404)


def test_a_link_out_of_the_folder_is_not_deleted(base, server_env, tmp_path):
    outside = tmp_path / "keep.tavletta"
    Label(printer="fake-mono", tape_mm=12).save(outside)
    (server_env.labels / "link.tavletta").symlink_to(outside)
    status, _response, body = call(base, "DELETE", "/api/v1/labels/link", token=ADMIN)
    assert status == 404
    assert outside.exists()


def test_a_label_being_printed_is_not_replaced_or_deleted(base, server_env, monkeypatch):
    monkeypatch.setattr(service_module, "LABEL_FREE_SECONDS", 0.2)
    server_env.printer.gate = threading.Event()
    status, _response, job = call(
        base, "POST", "/api/v1/print", body={"label": "box"}, token=ADMIN
    )
    assert status == 202
    try:
        assert server_env.printer.started.wait(10)
        status, _response, body = put(base, "box", label_json(), query="?replace=true")
        assert status == 409 and body["error"]["code"] == "label_locked"
        status, _response, body = call(base, "DELETE", "/api/v1/labels/box", token=ADMIN)
        assert status == 409 and body["error"]["code"] == "label_locked"
    finally:
        server_env.printer.gate.set()

    wait_for(
        lambda: call(base, "GET", "/api/v1/jobs/" + job["id"], token=ADMIN)[2]["state"] == "done"
    )
    assert call(base, "DELETE", "/api/v1/labels/box", token=ADMIN)[0] == 204


def test_a_full_disk_is_told(base, monkeypatch):
    import errno

    def full(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(service_module.os, "fsync", full)
    status, _response, body = put(base, "new", label_json())
    assert status == 507 and body["error"]["code"] == "not_saved"


def test_a_file_system_without_hard_links(base, server_env, monkeypatch):
    import errno

    def refuse(*_args, **_kwargs):
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(service_module.os, "link", refuse)
    assert put(base, "new", label_json())[0] == 201
    assert Label.load(server_env.labels / "new.tavletta").elements[0].text == "Asset {Number}"
    assert put(base, "new", label_json())[0] == 409


# --- the preview's geometry ------------------------------------------------------------


def test_the_preview_tells_where_the_elements_are(base, server_env):
    label_file(
        server_env.labels,
        "two.tavletta",
        TextElement(text="Asset {Number}", name="title"),
        TextElement(text="Room"),
    )
    body = {"label": "two", "data_rows": [{"Number": "00300"}], "format": "json"}
    status, _response, preview = call(base, "POST", "/api/v1/preview", body=body, token=VIEWER)
    assert status == 200
    geometry = preview["geometry"]
    assert [e["index"] for e in geometry["elements"]] == [0, 1]
    assert geometry["layout"] == "flow" and geometry["dpi"] > 0
    image = Image.open(io.BytesIO(base64.b64decode(preview["png"])))
    scale = geometry["dpi"] / 25.4
    for element in geometry["elements"]:
        left, top, right, bottom = element["box"]
        assert 0 <= left < right <= image.width / scale + 0.5
        assert 0 <= top < bottom <= image.height / scale + 0.5
    # the printer's view is turned: no boxes
    body["view"] = "printer"
    status, _response, preview = call(base, "POST", "/api/v1/preview", body=body, token=VIEWER)
    assert status == 200 and preview["geometry"] is None


def test_geometry_is_none_when_the_render_has_no_boxes():
    class Rendered:
        boxes = ()

    label = Label(printer="fake-mono", tape_mm=12, elements=[TextElement(text="x")])
    assert service_module._geometry(label, Rendered(), 300, None) is None
