"""The print server's web page at / and /ui/: served without a token,
with its security headers, only its own files, its texts in the language
asked for, and not at all with --no-web-ui."""

from __future__ import annotations

import pytest
from test_server import TOKEN, call

from tavletta.server import webui

JSON = {"Content-Type": "application/json"}


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
    assert webui.chosen_language(None, None) == "en"
