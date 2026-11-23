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
    assert typeset.resolves_to("Source Code Pro Bold", "Source Code Pro")
    assert typeset.resolves_to("Source Code Pro Black", "Source Code Pro", 900)
    # not a stand-in: another font of other widths, or none at all
    assert not typeset.resolves_to("Arial", "DejaVu Sans")
    assert not typeset.resolves_to("Calibri", "Liberation Sans")
    assert not typeset.resolves_to("Futura", "Liberation Sans")
    assert not typeset.resolves_to("Arial", "")


@pytest.mark.parametrize(
    ("asked", "loaded", "stands_for"),
    [
        ("Arial Narrow", "Liberation Sans Narrow", True),
        ("Arial Narrow", "Liberation Sans", False),  # other widths
        ("Georgia", "Gelasio", True),
        ("Helvetica", "TeX Gyre Heros", True),
        ("Helvetica", "Liberation Sans", True),
        ("Times", "TeX Gyre Termes", True),
        ("Times New Roman", "TeX Gyre Termes", True),
        ("Times New Roman", "Liberation Serif", True),
        ("Courier", "TeX Gyre Cursor", True),
        ("Courier New", "TeX Gyre Cursor", True),
        ("Courier New", "Liberation Mono", True),
        # a style named as a family: the alias of the longest family in it
        ("Arial Bold", "Liberation Sans", True),
        ("Calibri Light", "Carlito", True),
        ("Arial Narrow Bold", "Liberation Sans Narrow", True),
        ("Arial Narrow Bold", "Liberation Sans", False),
        ("Times New Roman Italic", "Liberation Serif", True),
        ("Arialic", "Liberation Sans", False),  # a word of its own, not Arial
        ("Times New Roman Bold Italic", "Liberation Serif", True),
        ("Calibri Semi-Bold", "Carlito", True),
        # families of their own, of other widths: not the stand-in's
        ("Arial Black", "Liberation Sans", False),
        ("Arial Rounded MT Bold", "Liberation Sans", False),
        ("Arial Unicode MS", "Liberation Sans", False),
        ("Helvetica Neue", "Liberation Sans", False),
        ("Helvetica Neue", "TeX Gyre Heros", False),
        ("Helvetica Narrow", "Liberation Sans", False),
        ("Arial Condensed", "Liberation Sans", False),
        ("Georgia Pro", "Liberation Serif", False),
    ],
)
def test_more_metric_aliases_and_styles(asked, loaded, stands_for):
    assert typeset.resolves_to(asked, loaded) is stands_for


@pytest.mark.parametrize(
    ("asked", "loaded", "weight", "stands_for"),
    [
        # the family itself, followed by style words only
        ("Arial Black", "Arial", 400, False),  # Arial loaded: not Arial Black
        ("Arial Black", "Arial", 900, True),  # the font loaded is that heavy
        ("Source Code Pro Black", "Source Code Pro", 900, True),
        ("Source Code Pro Black", "Source Code Pro", 400, False),
        ("Source Code Pro Heavy", "Source Code Pro", None, False),  # weight unknown
        ("Helvetica Neue", "Helvetica", 400, False),
        ("Arial Rounded MT Bold", "Arial", 700, False),
        ("Arial -", "Arial", 400, False),  # a separator, no style after it
        ("Arial-", "Liberation Sans", 400, False),
        # two words of one weight, a hyphen after the family
        ("Calibri Semi Bold", "Carlito", 600, True),
        ("Calibri Extra Light", "Carlito", 200, True),
        ("Arial-Bold", "Liberation Sans", 700, True),
        ("Arial-BoldItalic", "Liberation Sans", 700, False),  # one word: not known
        ("Arial-Bold-Italic", "Liberation Sans", 700, True),
        ("Source Code Pro ExtraBold", "Source Code Pro", 800, True),
        ("Source Code Pro Ultra Bold", "Source Code Pro", 800, True),
        ("Arial ExtraBold", "Liberation Sans", 700, False),
        ("Arialbold", "Arial", 700, False),  # not after a space or hyphen
    ],
)
def test_style_words_after_the_family(asked, loaded, weight, stands_for):
    assert typeset.resolves_to(asked, loaded, weight) is stands_for


@pytest.mark.parametrize(
    ("asked", "loaded", "stretch", "stands_for"),
    [
        # a width face of the family itself: the font loaded must be that wide
        ("DejaVu Sans Condensed", "DejaVu Sans", 3, True),  # semi-condensed
        ("DejaVu Sans Condensed", "DejaVu Sans", 4, False),  # the normal face
        ("Noto Sans Condensed", "Noto Sans", 2, True),
        ("Noto Sans SemiCondensed", "Noto Sans", 3, True),
        ("Noto Sans Semi Condensed Bold", "Noto Sans", 3, True),
        ("Noto Sans ExtraCondensed", "Noto Sans", 1, True),
        ("Noto Sans Condensed", "Noto Sans", 6, False),  # wider, not narrower
        ("Noto Sans Expanded", "Noto Sans", 6, True),
        ("Noto Sans SemiExpanded", "Noto Sans", 3, False),
        ("Source Sans 3 Narrow", "Source Sans 3", 2, True),
        ("DejaVu Sans Condensed", "DejaVu Sans", None, False),  # stretch unknown
        # a stand-in has the widths of the regular face: never for widths
        ("Arial Condensed", "Liberation Sans", 2, False),
        ("Calibri Condensed", "Carlito", 2, False),
    ],
)
def test_width_faces_count_when_the_font_loaded_is_that_wide(asked, loaded, stretch, stands_for):
    assert typeset.resolves_to(asked, loaded, 400, stretch) is stands_for


def test_installed_width_faces_on_this_system():
    """With the real fonts, where the test system has them."""
    from gi.repository import PangoCairo

    families = {f.get_name() for f in PangoCairo.FontMap.get_default().list_families()}
    checked = 0
    for family, face in (
        ("DejaVu Sans", "DejaVu Sans Condensed"),
        ("Noto Sans", "Noto Sans Condensed"),
        ("Noto Sans", "Noto Sans SemiCondensed"),
    ):
        if family not in families:
            continue
        loaded = typeset._look_up(face)
        # only where fontconfig gives the narrow face for the name
        desc = typeset.Pango.FontDescription()
        desc.set_family(face)
        fontmap_ = PangoCairo.FontMap.get_default()
        found = fontmap_.load_font(fontmap_.create_context(), desc).describe()
        if int(found.get_stretch()) < int(typeset.Pango.Stretch.NORMAL):
            assert loaded, face
            checked += 1
    if not checked:
        pytest.skip("no condensed face of DejaVu Sans or Noto Sans installed")


def test_font_installed_passes_the_weight_loaded(monkeypatch):
    """Arial Black where fontconfig loads regular Arial: missing."""
    for weight, installed in ((400, False), (900, True)):
        cairo_ns = SimpleNamespace(
            FontMap=SimpleNamespace(get_default=lambda w=weight: fontmap("Arial", w))
        )
        monkeypatch.setattr(typeset, "PangoCairo", cairo_ns)
        monkeypatch.setattr(typeset, "_fonts", {})
        assert typeset.font_installed("Arial Black") is installed


def fontmap(loads: str, weight: int = 400, stretch: int = 4):
    """A Pango font map without the asked family that loads `loads` for it
    (stretch 4: Pango.Stretch.NORMAL)."""
    described = SimpleNamespace(
        get_family=lambda: loads, get_weight=lambda: weight, get_stretch=lambda: stretch
    )
    font = SimpleNamespace(describe=lambda: described)
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
