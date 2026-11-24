"""The print server's web page: plain HTML, CSS and JavaScript from
tavletta/server/web/, served at / and /ui/ (`tavletta serve --no-web-ui`
turns it off).

The page and its files need no token (they hold nothing but the program
itself); everything it shows comes from the REST API, with the token the
user gives, which the page keeps in sessionStorage only. Every file is sent
with a strict Content-Security-Policy: scripts and styles only from these
files, nothing inline, no eval, no frames, no requests elsewhere.

Its texts are translated from the app's gettext catalogs:
/ui/messages.json holds the texts the scripts ask for (t("…") in the
.js files, found when the server starts) in the language of ?lang= or
Accept-Language.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from tavletta.ui.i18n import LOCALE_DIR, _, accepted_languages, language

WEB_DIR = Path(__file__).parent / "web"
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}
# the page may load its own files and talk to this server, nothing else; no
# inline script or style, no eval, never in a frame, no <base>, no form sent
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)
HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Frame-Options": "DENY",  # frame-ancestors for browsers without CSP 2
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    # asked again each time, answered 304 while unchanged
    "Cache-Control": "no-cache",
}
# t("…") in the scripts: a plain string, no expression (tests check that
# every t( is one of these, xgettext --keyword=t finds the same)
_TEXT = re.compile(r'\bt\(\s*"((?:[^"\\\n]|\\.)*)"')
_LANG = re.compile(r"[a-z]{2,3}(?:[_-][A-Za-z]{2})?\Z")


@dataclass(frozen=True)
class Asset:
    data: bytes
    kind: str
    etag: str


def _etag(data: bytes) -> str:
    return '"%s"' % hashlib.sha256(data).hexdigest()[:32]


def asset(name: str) -> Asset | None:
    """A file of the page by its name in web/ ("app.js"); None for anything
    else. Only the files there, looked up by name in a table made once:
    nothing a request names can lead elsewhere, or grow a cache."""
    return _assets().get(name)


@cache
def _assets() -> dict[str, Asset]:
    found = {}
    for path in sorted(WEB_DIR.iterdir()):
        kind = TYPES.get(path.suffix)
        if kind and path.is_file() and not path.name.startswith("."):
            data = path.read_bytes()
            found[path.name] = Asset(data, kind, _etag(data))
    return found


@cache
def texts() -> tuple[str, ...]:
    """The texts the scripts translate, in the order they appear."""
    found: dict[str, None] = {}
    for script in sorted(WEB_DIR.glob("*.js")):
        for literal in _TEXT.findall(script.read_text(encoding="utf-8")):
            found[json.loads('"%s"' % literal)] = None
    return tuple(found)


@cache
def languages() -> tuple[str, ...]:
    """The languages there is a catalog for, English (the texts themselves) first."""
    found = sorted(p.parent.parent.name for p in LOCALE_DIR.glob("*/LC_MESSAGES/tavletta.mo"))
    return ("en", *found)


def chosen_language(asked: str | None, header: str | None) -> str:
    """The language of the page: ?lang= if there is a catalog for it, else
    the first of Accept-Language there is one for, else English."""
    available = languages()
    wanted = [asked.replace("-", "_")] if asked and _LANG.match(asked) else []
    if not wanted and header:
        wanted = accepted_languages(header) or ["en"]
    for code in wanted:
        for candidate in (code, code.split("_")[0]):
            if candidate in available:
                return candidate
    return "en"


def messages(asked: str | None, header: str | None) -> Asset:
    """/ui/messages.json: the texts in the chosen language."""
    code = chosen_language(asked, header)
    return _messages(code)


@cache
def _messages(code: str) -> Asset:
    with language(code.replace("_", "-")):
        table = {text: _(text) for text in texts()}
    body = {
        "language": code,
        "languages": list(languages()),
        "messages": {k: v for k, v in table.items() if v != k},
    }
    data = json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
    return Asset(data, "application/json; charset=utf-8", _etag(data))
