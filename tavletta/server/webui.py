"""The print server's web page: plain HTML, CSS and JavaScript from
tavletta/server/web/, served at / and /ui/ (`tavletta serve --no-web-ui`
turns it off).

The page and its files need no token (they hold nothing but the program
itself); everything it shows comes from the REST API, with the token the
user gives, which the page keeps in sessionStorage only, or with the
session cookie of signing in with OpenID Connect (weblogin.py). Every file is sent
with a strict Content-Security-Policy: scripts and styles only from these
files, nothing inline, no eval, no frames, no requests elsewhere.

Its texts are translated from the app's gettext catalogs:
/ui/messages.json holds the texts the scripts ask for (t("…") in the
.js files, found when the server starts) in the language of ?lang= or
Accept-Language, and the direction of its script. The page itself (/)
comes in that language too: its lang and dir, and the note shown without
JavaScript. Every catalog of the app is offered: the compiled ones of
the installation, and in a checkout (an editable install has none
compiled) po/<lang>.po read directly.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import cache
from html import escape, unescape
from pathlib import Path

from tavletta.ui.i18n import LOCALE_DIR, N_, RTL_LANGUAGES, _, accepted_languages, language

WEB_DIR = Path(__file__).parent / "web"
# a checkout's catalogs (not in an installed package: there they are compiled)
PO_DIR = Path(__file__).resolve().parents[2] / "po"
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
# Chinese named by its script (zh-Hant, zh-Hans, without a region): the
# region of the catalog in that script, before Accept-Language (or the
# check of ?lang=) loses it
_SCRIPT = re.compile(r"\bzh[-_](hant|hans)(?=\s*(?:[;,]|\Z))", re.IGNORECASE)
_SCRIPT_REGION = {"hant": "zh-TW", "hans": "zh-CN"}
# a language whose catalogs all have a region: the one for a region
# without its own catalog, or none named
_NEAREST = {"zh_MO": "zh_HK", "zh": "zh_CN"}
# the one text of index.html itself, for browsers without JavaScript: the
# content of its <p class="noscript">, in any HTML spelling (&#39; or ')
NOSCRIPT = N_("Tavletta's web page needs JavaScript. The REST API is at /api/v1.")
_NOTE = re.compile(r'<p class="noscript">(.*?)</p>', re.DOTALL)
_OPENING = '<html lang="en">'


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


def _compiled() -> set[str]:
    return {p.parent.parent.name for p in LOCALE_DIR.glob("*/LC_MESSAGES/tavletta.mo")}


@cache
def languages() -> tuple[str, ...]:
    """The languages there is a catalog for, English (the texts themselves)
    first: compiled, or as po/<lang>.po of a checkout."""
    found = _compiled() | {p.stem for p in PO_DIR.glob("*.po")}
    return ("en", *sorted(found))


def direction(code: str) -> str:
    """The direction of the language's script: "rtl" or "ltr"."""
    return "rtl" if code.split("_")[0] in RTL_LANGUAGES else "ltr"


def chosen_language(asked: str | None, header: str | None) -> str:
    """The language of the page: ?lang= if there is a catalog for it (or
    for its language or nearest region), else the first of Accept-Language
    there is one for, else English. A ?lang= without a catalog, or not a
    language name, counts as none; English in Accept-Language ends the
    search there ("en, de": English)."""
    available = languages()
    asked = _by_script(asked) if asked else asked
    wanted = [asked.replace("-", "_")] if asked and _LANG.match(asked) else []
    if header:
        wanted += accepted_languages(_by_script(header)) or ["en"]
    for code in wanted:
        if found := _catalog_for(code, available):
            return found
    return "en"


def _by_script(languages: str) -> str:
    """zh-Hant and zh-Hans (without a region) as zh-TW and zh-CN."""
    return _SCRIPT.sub(lambda m: _SCRIPT_REGION[m.group(1).lower()], languages)


def _catalog_for(code: str, available: tuple[str, ...]) -> str | None:
    """The catalog for a language: its own, else the language's (de_AT:
    de), else the nearest region's (zh_MO: zh_HK, zh_SG: zh_CN)."""
    base = code.split("_")[0]
    regional = sorted(c for c in available if c.startswith(base + "_"))
    for candidate in (code, base, _NEAREST.get(code), _NEAREST.get(base), *regional[:1]):
        if candidate in available:
            return candidate
    return None


def messages(asked: str | None, header: str | None) -> Asset:
    """/ui/messages.json: the texts in the chosen language."""
    code = chosen_language(asked, header)
    return _messages(code)


def _translated(code: str, wanted: tuple[str, ...]) -> dict[str, str]:
    """The texts in a language: from its compiled catalog, else (a checkout)
    from its po file."""
    if code == "en" or code in _compiled():
        with language(code.replace("_", "-")):
            return {text: _(text) for text in wanted}
    catalog = read_po(PO_DIR / f"{code}.po")
    return {text: catalog.get(text, text) for text in wanted}


def page(asked: str | None, header: str | None) -> Asset | None:
    """The page (/) in the chosen language: index.html with its lang, dir
    and the note for browsers without JavaScript; None without index.html."""
    return _page(chosen_language(asked, header))


def check() -> None:
    """Raise RuntimeError if index.html lacks what its translation needs:
    called when the server starts, so a changed file fails there and in
    the tests, not with an error on each request for /."""
    _page("en")


@cache
def _page(code: str) -> Asset | None:
    found = asset("index.html")
    if found is None:
        return None
    source = found.data.decode("utf-8")
    notes = [m for m in _NOTE.finditer(source) if unescape(m.group(1)) == NOSCRIPT]
    if source.count(_OPENING) != 1 or len(notes) != 1:
        raise RuntimeError(
            'index.html needs one <html lang="en"> and one <p class="noscript"> with NOSCRIPT'
        )
    start, end = notes[0].span(1)
    translated = escape(_translated(code, (NOSCRIPT,))[NOSCRIPT])
    lang = '<html lang="%s" dir="%s">' % (code.replace("_", "-"), direction(code))
    data = (source[:start] + translated + source[end:]).replace(_OPENING, lang).encode("utf-8")
    return Asset(data, found.kind, _etag(data))


@cache
def _messages(code: str) -> Asset:
    table = _translated(code, texts())
    body = {
        "language": code,
        "dir": direction(code),
        "languages": list(languages()),
        "messages": {k: v for k, v in table.items() if v != k},
    }
    data = json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
    return Asset(data, "application/json; charset=utf-8", _etag(data))


_PO_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


def _unquote(line: str) -> str:
    """The text of a quoted po line ("a\\nb" -> a, newline, b)."""
    body = line[line.index('"') + 1 : line.rindex('"')]
    return re.sub(r"\\(.)", lambda m: _PO_ESCAPES.get(m.group(1), m.group(1)), body)


def read_po(path: Path) -> dict[str, str]:
    """The translations of a po file as msgfmt compiles them: no fuzzy,
    empty or obsolete entries, no plurals; texts with a context (msgctxt)
    are not the page's and are left out. {} if there is no such file."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    found: dict[str, str] = {}
    entry: dict[str, str] = {}
    flags = ""
    key = ""

    def flush() -> None:
        msgid, msgstr = entry.get("msgid", ""), entry.get("msgstr", "")
        usable = msgid and msgstr and "fuzzy" not in flags
        if usable and "msgctxt" not in entry and "msgid_plural" not in entry:
            found[msgid] = msgstr

    for line in [*lines, ""]:
        line = line.strip()
        if not line:
            flush()
            entry, flags, key = {}, "", ""
        elif line.startswith("#,"):
            flags += line
        elif line.startswith("#"):
            continue  # comments, and obsolete entries (#~)
        elif line.startswith('"'):
            if key:
                entry[key] += _unquote(line)
        else:
            key = line.split(None, 1)[0]
            entry[key] = _unquote(line)
    return found
