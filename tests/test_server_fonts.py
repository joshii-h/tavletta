"""A label asking for a font the server does not have: a warning, not a
silent fallback (a container without the fonts of the designer's computer)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tavletta import typeset
from tests.test_server import call

MISSING = "Tavletta Font That Is Not There"


def content(font: str) -> dict:
    text = {"kind": "text", "text": "IT-004711", "font": font}
    return {"printer": "fake-mono", "tape_mm": 12, "elements": [text]}


@pytest.fixture
def api(make_server):
    server, base = make_server()
    return SimpleNamespace(server=server, base=base)


def test_font_installed():
    assert typeset.font_installed("Sans")
    assert typeset.font_installed("monospace")
    assert typeset.font_installed("")  # the default, Sans
    assert typeset.font_installed("%s, Sans" % MISSING)  # a list with a fallback
    assert not typeset.font_installed(MISSING)
    assert not typeset.font_installed(MISSING.lower())


def test_preview_warns_about_a_missing_font(api):
    body = {"label_content": content(MISSING), "format": "json"}
    status, _response, answer = call(api.base, "POST", "/api/v1/preview", body=body)
    assert status == 200
    codes = [w["code"] for w in answer["warnings"]]
    assert codes == ["font_missing"]
    assert MISSING in answer["warnings"][0]["message"]
    body = {"label_content": content(MISSING)}
    status, response, _png = call(api.base, "POST", "/api/v1/preview", body=body)
    assert status == 200 and response.getheader("X-Tavletta-Warnings") == "font_missing"


def test_preview_with_an_installed_font_has_no_warning(api):
    body = {"label_content": content("Sans"), "format": "json"}
    status, _response, answer = call(api.base, "POST", "/api/v1/preview", body=body)
    assert status == 200 and answer["warnings"] == []


def test_print_job_carries_the_warning(api):
    body = {"label_content": content(MISSING), "wait": 10}
    status, _response, job = call(api.base, "POST", "/api/v1/print", body=body)
    assert status == 200 and job["state"] == "done"
    assert [w["code"] for w in job["warnings"]] == ["font_missing"]


def test_a_metric_alias_counts_as_installed():
    """Arial drawn in Liberation Sans, Calibri in Carlito: the same widths,
    so no font_missing (the Docker image ships these stand-ins)."""
    assert typeset.resolves_to("Arial", "Liberation Sans")
    assert typeset.resolves_to("Times New Roman", "Liberation Serif")
    assert typeset.resolves_to("Courier New", "Liberation Mono")
    assert typeset.resolves_to("Calibri", "Carlito")
    assert typeset.resolves_to("Cambria", "Caladea")
    assert typeset.resolves_to("Source Code Pro Black", "Source Code Pro")
    # not a stand-in: another font of other widths, or none at all
    assert not typeset.resolves_to("Arial", "DejaVu Sans")
    assert not typeset.resolves_to("Calibri", "Liberation Sans")
    assert not typeset.resolves_to("Futura", "Liberation Sans")
    assert not typeset.resolves_to("Arial", "")


def fontmap(loads: str):
    """A Pango font map without the asked family that loads `loads` for it."""
    font = SimpleNamespace(describe=lambda: SimpleNamespace(get_family=lambda: loads))
    return SimpleNamespace(
        list_families=lambda: [SimpleNamespace(get_name=lambda: "DejaVu Sans")],
        create_context=lambda: None,
        load_font=lambda _context, _desc: font,
    )


@pytest.mark.parametrize(
    ("family", "loads", "installed"),
    [
        ("Arial", "Liberation Sans", True),
        ("Calibri", "Carlito", True),
        ("Cambria", "Caladea", True),
        ("Arial", "DejaVu Sans", False),  # no Liberation: still a warning
        ("Calibri", "DejaVu Sans", False),
        (MISSING, "DejaVu Sans", False),
    ],
)
def test_font_installed_through_fontconfig(monkeypatch, family, loads, installed):
    cairo_ns = SimpleNamespace(FontMap=SimpleNamespace(get_default=lambda: fontmap(loads)))
    monkeypatch.setattr(typeset, "PangoCairo", cairo_ns)
    monkeypatch.setattr(typeset, "_fonts", {})
    assert typeset.font_installed(family) is installed


def test_arial_with_liberation_installed_has_no_warning(api):
    from gi.repository import PangoCairo

    families = {f.get_name() for f in PangoCairo.FontMap.get_default().list_families()}
    if "Arial" in families or "Liberation Sans" not in families:
        pytest.skip("needs Liberation Sans and no real Arial")
    body = {"label_content": content("Arial"), "format": "json"}
    status, _response, answer = call(api.base, "POST", "/api/v1/preview", body=body)
    assert status == 200 and answer["warnings"] == []
