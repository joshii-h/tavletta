"""The print server's web page at / and /ui/: served without a token,
with its security headers, only its own files, its texts in the language
asked for, and not at all with --no-web-ui."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_server import TOKEN, call

from tavletta.server import webui

JSON = {"Content-Type": "application/json"}
# the po files of the tree the tests are in: webui.PO_DIR is next to the
# module, which in a package's tests (autopkgtest) is the installed one
PO_DIR = Path(__file__).resolve().parent.parent / "po"


@pytest.fixture
def base(make_server):
    """A server with a token: the page needs none."""
    _server, url = make_server(token=TOKEN, mcp=False)
    return url


# --- the page --------------------------------------------------------------------------


def test_the_page_is_served_without_a_token(base):
    status, response, body = call(base, "GET", "/")
    assert status == 200
    assert response.getheader("Content-Type") == "text/html; charset=utf-8"
    assert b'<script type="module" src="/ui/app.js"></script>' in body
    csp = response.getheader("Content-Security-Policy")
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "unsafe" not in csp and "default-src 'none'" in csp
    assert response.getheader("X-Frame-Options") == "DENY"
    assert response.getheader("X-Content-Type-Options") == "nosniff"
    assert response.getheader("Referrer-Policy") == "no-referrer"
    assert response.getheader("Cache-Control") == "no-cache"
    for name, kind in (("app.js", "text/javascript"), ("style.css", "text/css")):
        status, response, _body = call(base, "GET", "/ui/" + name)
        assert status == 200 and response.getheader("Content-Type").startswith(kind)
        assert response.getheader("Content-Security-Policy") == webui.CSP


def test_the_page_comes_in_the_language_asked_for(base, checkout):
    """/ names its language and direction, and the note for browsers
    without JavaScript is translated."""
    status, response, body = call(base, "GET", "/", headers={"Accept-Language": "de-CH"})
    assert status == 200 and response.getheader("Vary") == "Accept-Language"
    page = body.decode("utf-8")
    assert '<html lang="de" dir="ltr">' in page
    assert "Die Webseite von Tavletta braucht JavaScript." in page
    assert "needs JavaScript" not in page
    page = call(base, "GET", "/?lang=ar")[2].decode("utf-8")
    assert '<html lang="ar" dir="rtl">' in page and "JavaScript" in page
    page = call(base, "GET", "/ui/index.html?lang=xx")[2].decode("utf-8")
    assert '<html lang="en" dir="ltr">' in page
    assert "Tavletta&#x27;s web page needs JavaScript." in page


def test_the_page_vary_keeps_the_origin(make_server):
    """For an allowed origin the answer depends on Origin and on
    Accept-Language: Vary names both."""
    _server, url = make_server(token=TOKEN, mcp=False, origins=("https://app.example",))
    headers = {"Origin": "https://app.example", "Accept-Language": "de"}
    for path in ("/", "/ui/messages.json"):
        status, response, _body = call(url, "GET", path, headers=headers)
        assert status == 200
        assert response.getheader("Access-Control-Allow-Origin") == "https://app.example"
        assert response.getheader("Vary") == "Origin, Accept-Language"
    response = call(url, "GET", "/ui/app.js", headers=headers)[1]
    assert response.getheader("Vary") == "Origin"


@pytest.fixture
def index_html(monkeypatch):
    """Serve another index.html: webui.asset with these bytes for it."""
    real = webui.asset

    def use(data: bytes) -> None:
        page = webui.Asset(data, "text/html", '"x"')
        monkeypatch.setattr(
            webui, "asset", lambda name: page if name == "index.html" else real(name)
        )

    webui._page.cache_clear()
    yield use
    webui._page.cache_clear()


@pytest.mark.parametrize(
    "page",
    [
        b"<html><body></body></html>",
        b'<html lang="en"><body><p class="noscript">JavaScript!</p></body></html>',
        b'<html lang="en"><body><p class="noscript">%s</p><p class="noscript">%s</p></body></html>'
        % ((webui.NOSCRIPT.encode(),) * 2),
    ],
    ids=["no markers", "another note", "the note twice"],
)
def test_the_page_needs_its_markers(index_html, make_server, page):
    """A changed index.html without the <html lang="en"> or the note is
    noticed when the server starts, instead of served untranslated."""
    index_html(page)
    with pytest.raises(RuntimeError):
        webui.check()
    with pytest.raises(RuntimeError):
        make_server(token=TOKEN, mcp=False)
    make_server(token=TOKEN, mcp=False, web_ui=False)  # without the page: no matter


@pytest.mark.parametrize("apostrophe", [b"'", b"&#39;", b"&#x27;", b"&apos;"])
def test_the_note_in_any_html_spelling(index_html, checkout, apostrophe):
    note = webui.NOSCRIPT.encode().replace(b"'", apostrophe)
    index_html(b'<html lang="en"><body><p class="noscript">%s</p></body></html>' % note)
    webui.check()
    page = webui.page("de", None).data.decode("utf-8")
    assert '<html lang="de" dir="ltr">' in page
    assert '<p class="noscript">Die Webseite von Tavletta braucht JavaScript.' in page


def test_the_note_is_translated_everywhere():
    """Every catalog has the note of the page (a text outside the scripts)."""
    for po in sorted(PO_DIR.glob("*.po")):
        assert webui.NOSCRIPT in webui.read_po(po), po.name


def test_the_page_answers_304_while_unchanged(base):
    _status, response, _body = call(base, "GET", "/ui/app.js")
    tag = response.getheader("ETag")
    status, response, body = call(base, "GET", "/ui/app.js", headers={"If-None-Match": tag})
    assert status == 304 and body == b""
    status = call(base, "GET", "/ui/app.js", headers={"If-None-Match": '"other"'})[0]
    assert status == 200


@pytest.mark.parametrize(
    "path",
    [
        "/ui/",
        "/ui/missing.js",
        "/ui/../http.py",
        "/ui/%2E%2E/http.py",
        "/ui/..%2Fhttp.py",
        "/ui/web/app.js",
        "/ui/.hidden.js",
        "/ui/webui.py",
        "/index.html",
        "/ui/app.js/x",
    ],
)
def test_nothing_else_is_served(base, path):
    status, _response, body = call(base, "GET", path, token=TOKEN)
    assert status == 404 and body["error"]["code"] == "not_found"


def test_the_page_is_only_read(base):
    for method in ("POST", "PUT", "DELETE"):
        status = call(base, method, "/", headers=JSON, raw=b"{}")[0]
        assert status == 405


def test_the_page_keeps_the_host_check(base):
    status = call(base, "GET", "/", headers={"Host": "evil.example"})[0]
    assert status == 421


def test_no_web_ui(make_server):
    _server, url = make_server(web_ui=False)
    for path in ("/", "/ui/app.js", "/ui/messages.json"):
        status, _response, body = call(url, "GET", path)
        assert status == 404 and body["error"]["code"] == "not_found"
    assert call(url, "GET", "/api/v1/health")[0] == 200


def test_no_web_ui_flag(monkeypatch, tmp_path):
    from tavletta import cli

    args = cli.parser().parse_args(["serve", "--no-web-ui", "--labels-dir", str(tmp_path)])
    assert args.no_web_ui is True
    assert cli.parser().parse_args(["serve"]).no_web_ui is False


# --- the page's texts --------------------------------------------------------------------


def test_every_text_of_the_scripts_is_a_plain_string():
    """webui.texts() and xgettext --keyword=t find t("…") only: a t( with
    anything else (a variable, a template) would never be translated."""
    import re

    for script in webui.WEB_DIR.glob("*.js"):
        source = script.read_text(encoding="utf-8")
        calls = re.findall(r"(?<!function )\bt\(", source)
        plain = re.findall(r'\bt\(\s*"(?:[^"\\\n]|\\.)*"', source)
        assert len(calls) == len(plain), script.name
    assert "Sign in" in webui.texts()


def test_the_texts_come_in_the_language_asked_for(base, monkeypatch):
    webui._messages.cache_clear()
    monkeypatch.setattr(webui, "languages", lambda: ("en", "de"))
    status, response, body = call(base, "GET", "/ui/messages.json?lang=en")
    assert status == 200 and body["language"] == "en" and body["messages"] == {}
    assert response.getheader("Vary") == "Accept-Language"
    status, _response, body = call(
        base, "GET", "/ui/messages.json", headers={"Accept-Language": "de-CH, en;q=0.5"}
    )
    assert body["language"] == "de" and body["languages"] == ["en", "de"]
    status, _response, body = call(base, "GET", "/ui/messages.json?lang=xx")
    assert body["language"] == "en"
    # no catalog for ?lang=: Accept-Language decides
    headers = {"Accept-Language": "de"}
    status, _response, body = call(base, "GET", "/ui/messages.json?lang=xx", headers=headers)
    assert body["language"] == "de"
    status, _response, body = call(base, "GET", "/ui/messages.json?lang=../../etc")
    assert body["language"] == "en"


def test_chosen_language(monkeypatch):
    monkeypatch.setattr(webui, "languages", lambda: ("en", "de", "pt_BR", "zh_TW"))
    assert webui.chosen_language("de", None) == "de"
    assert webui.chosen_language("de-CH", None) == "de"
    assert webui.chosen_language("pt-BR", None) == "pt_BR"
    assert webui.chosen_language(None, "zh-TW,de") == "zh_TW"
    assert webui.chosen_language(None, "fr, de;q=0.5") == "de"
    assert webui.chosen_language(None, "en, de") == "en"
    assert webui.chosen_language("<script>", "de") == "de"
    assert webui.chosen_language("fr", "de") == "de"
    assert webui.chosen_language("fr", "en, de") == "en"
    assert webui.chosen_language(None, None) == "en"


def test_the_texts_are_translated(monkeypatch, tmp_path):
    """With a catalog: the msgstr of each text the scripts use."""
    import subprocess
    from shutil import which

    if which("msgfmt") is None:
        pytest.skip("no msgfmt")
    from pathlib import Path

    root = Path(webui.__file__).parents[2]
    out = tmp_path / "de" / "LC_MESSAGES"
    out.mkdir(parents=True)
    subprocess.run(
        ["msgfmt", "-o", str(out / "tavletta.mo"), str(root / "po" / "de.po")], check=True
    )
    from tavletta.ui import i18n

    monkeypatch.setattr(webui, "LOCALE_DIR", tmp_path)
    monkeypatch.setattr(i18n, "LOCALE_DIR", tmp_path)
    i18n.translation_for.cache_clear()
    webui.languages.cache_clear()
    webui._messages.cache_clear()
    try:
        asset = webui.messages("de", None)
        body = json.loads(asset.data)
        assert body["messages"]["Sign in"] == "Anmelden"
        assert set(body["messages"]) <= set(webui.texts())
    finally:
        i18n.translation_for.cache_clear()
        webui.languages.cache_clear()
        webui._messages.cache_clear()


def test_the_icon_is_the_apps():
    """web/icon.svg is the app's own 32 px drawing (a copy: the package
    data cannot reach data/)."""
    from pathlib import Path

    root = Path(webui.__file__).parents[2]
    drawing = root / "data" / "icons" / "tavletta-32.svg"
    assert (webui.WEB_DIR / "icon.svg").read_bytes() == drawing.read_bytes()


# --- every language of the app ------------------------------------------------------------


@pytest.fixture
def checkout(monkeypatch, tmp_path):
    """No compiled catalog (an editable install): the po files of po/."""
    monkeypatch.setattr(webui, "LOCALE_DIR", tmp_path)
    monkeypatch.setattr(webui, "PO_DIR", PO_DIR)
    caches = (webui.languages, webui._messages, webui._page)
    for cached in caches:
        cached.cache_clear()
    yield
    for cached in caches:
        cached.cache_clear()


def test_every_catalog_of_the_app_is_offered(checkout):
    catalogs = sorted(p.stem for p in PO_DIR.glob("*.po"))
    assert len(catalogs) >= 26 and {"ar", "zh_HK", "zh_TW", "zh_CN", "pt", "pt_BR"} <= set(
        catalogs
    )
    assert webui.languages() == ("en", *catalogs)
    body = json.loads(webui.messages("de", None).data)
    assert body["languages"] == ["en", *catalogs]


def test_a_checkout_translates_from_its_po_files(checkout):
    for code in webui.languages()[1:]:
        body = json.loads(webui.messages(code, None).data)
        assert body["language"] == code
        assert body["messages"]["Sign in"] != "Sign in", code
    assert json.loads(webui.messages("de", None).data)["messages"]["Sign in"] == "Anmelden"


def test_arabic_is_written_right_to_left(checkout):
    assert json.loads(webui.messages("ar", None).data)["dir"] == "rtl"
    assert json.loads(webui.messages("de", None).data)["dir"] == "ltr"
    assert json.loads(webui.messages("en", None).data)["dir"] == "ltr"


def test_the_language_of_a_region_or_script(checkout):
    choose = webui.chosen_language
    assert choose(None, "zh-HK") == "zh_HK"
    assert choose(None, "zh-TW, en;q=0.5") == "zh_TW"
    assert choose(None, "zh-Hant") == "zh_TW"
    assert choose(None, "zh-Hant-HK") == "zh_HK"
    assert choose(None, "zh-Hans, zh;q=0.8") == "zh_CN"
    assert choose(None, "zh-MO") == "zh_HK"
    assert choose(None, "zh-SG") == "zh_CN"
    assert choose(None, "zh") == "zh_CN"
    assert choose(None, "pt-BR") == "pt_BR"
    assert choose(None, "pt-PT") == "pt"
    assert choose(None, "ar-EG") == "ar"
    assert choose(None, "de-AT") == "de"
    assert choose("zh-HK", "de") == "zh_HK"
    assert choose(None, "en-US, de-CH;q=0.8") == "en"


def test_the_po_files_read_as_msgfmt_compiles_them(tmp_path):
    """Without compiled catalogs the server reads po/<lang>.po itself: the
    same translations as msgfmt makes of it (fuzzy, empty, context and
    plural entries left out, escapes undone)."""
    import gettext
    import subprocess
    from shutil import which

    if which("msgfmt") is None:
        pytest.skip("no msgfmt")
    catalogs = sorted(PO_DIR.glob("*.po"))
    assert catalogs
    for po in catalogs:
        mo = tmp_path / po.stem / "LC_MESSAGES" / "tavletta.mo"
        mo.parent.mkdir(parents=True)
        subprocess.run(["msgfmt", "-o", str(mo), str(po)], check=True)
        compiled = gettext.translation("tavletta", tmp_path, languages=[po.stem])
        expected = {
            k: v
            for k, v in compiled._catalog.items()  # type: ignore[attr-defined]
            if isinstance(k, str) and k and "\x04" not in k
        }
        assert webui.read_po(po) == expected, po.name


def test_read_po_leaves_out_what_msgfmt_leaves_out(tmp_path):
    po = tmp_path / "xx.po"
    po.write_text(
        'msgid ""\nmsgstr "Language: xx\\n"\n\n'
        '#, fuzzy\nmsgid "Fuzzy"\nmsgstr "F"\n\n'
        'msgid "Empty"\nmsgstr ""\n\n'
        'msgctxt "month name"\nmsgid "May"\nmsgstr "Mai"\n\n'
        'msgid "One"\nmsgid_plural "Many"\nmsgstr[0] "Eins"\n\n'
        '#~ msgid "Old"\n#~ msgstr "Alt"\n\n'
        'msgid ""\n"Two \\"lines\\"\\n"\n"and a \\\\ tab\\t"\nmsgstr "Zwei\\nZeilen"\n',
        encoding="utf-8",
    )
    assert webui.read_po(po) == {'Two "lines"\nand a \\ tab\t': "Zwei\nZeilen"}
    assert webui.read_po(tmp_path / "missing.po") == {}
