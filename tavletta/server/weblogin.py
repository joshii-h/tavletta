"""Signing in to the web page with OpenID Connect: the routes under /oidc/
and the session cookie, apart from HTTP (http.py sends the answers).

    GET  /oidc/info      the provider's name for the button (public)
    GET  /oidc/login     to the provider (?return=#/... : where to go back)
    GET  /oidc/callback  the provider's answer: a session, back to the page
    POST /oidc/logout    ends the session; the provider's logout address

Cookies are HttpOnly, SameSite=Lax (the provider's redirect back is a
navigation from another site, which Lax lets through, a request of a page
of another site not) and, over HTTPS, Secure with the __Host- prefix (only
this host, path /). Over plain HTTP only on this computer, for testing.
The browser that started a sign in must finish it: a second cookie holds
the sign in itself, sealed (sessions.Logins), so a callback sent to
someone else (signing them in as another user) fails, and the server
keeps nothing until the session.

Requests to the API with the session cookie are checked in http.py: the
header X-Tavletta-Request, and the page's own origin (Origin, required
for anything but GET; Sec-Fetch-Site). MCP takes no cookie.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from urllib.parse import urlencode, urlsplit

from tavletta.server.config import Caller, NamedPrinter
from tavletta.server.oidc import (
    OidcError,
    OidcSettings,
    Provider,
    caller_for,
    local_url,
    user_name,
)
from tavletta.server.sessions import Logins, Session, Sessions

log = logging.getLogger(__name__)

PREFIX = "/oidc/"
CALLBACK = "/oidc/callback"
# where the page goes back to: an address of the page itself, nothing else
_AFTER = re.compile(r"#/[A-Za-z0-9._~!$&'()*+,;=:@%/-]{0,500}\Z")
DEFAULT_AFTER = "#/templates"
# what the page is told when a sign in fails (it says what they mean)
ERRORS = ("failed", "expired", "denied", "no_role")
LOGIN_COOKIE_SECONDS = 600
# token exchanges with the provider at once (each a request with the
# client's secret, up to 10 s); a callback waits this long for one
EXCHANGES = 4
EXCHANGE_WAIT = 10.0


@dataclass
class Answer:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    cookies: list[str] = field(default_factory=list)
    body: object | None = None
    # a code the token endpoint did not take (or an ID token that did not
    # check out): counted for the address (http.py), as each costs a
    # request to the provider with the client's secret
    exchange_failed: bool = False


def cookies(lines: Iterable[str]) -> dict[str, str | None]:
    """The cookies of Cookie headers; a name sent twice is None (which
    one would be ours?)."""
    found: dict[str, str | None] = {}
    for line in lines:
        for part in line.split(";"):
            name, sep, value = part.strip().partition("=")
            if not sep or not name:
                continue
            found[name] = None if name in found else value.strip()
    return found


def after(value: str | None) -> str:
    return value if value and _AFTER.match(value) else DEFAULT_AFTER


class WebLogin:
    """The sign ins and sessions of one provider."""

    def __init__(
        self,
        settings: OidcSettings,
        provider: Provider | None = None,
        printers: Mapping[str, NamedPrinter] | None = None,
        clock=time.monotonic,
    ):
        self.settings = settings
        self.provider = provider or Provider(settings, clock)
        self.printers = printers
        self.logins = Logins(clock)
        self._exchanges = threading.BoundedSemaphore(EXCHANGES)
        self.sessions = Sessions(settings.session_seconds, settings.idle_seconds, clock)

    # --- where the page is

    def origin(self, site: str) -> str:
        """The page's origin as the browser sees it: public_url, else what
        the request says (Host, or a trusted proxy's X-Forwarded-*)."""
        return self.settings.public_url or site

    def usable(self, origin: str) -> bool:
        """Cookies need HTTPS, or this computer (testing)."""
        parts = urlsplit(origin)
        if not parts.hostname:
            return False
        return parts.scheme == "https" or (parts.scheme == "http" and local_url(origin))

    def names(self, origin: str) -> tuple[str, str]:
        """The session cookie's and the login cookie's names."""
        if origin.startswith("https://"):
            return "__Host-tavletta-session", "__Host-tavletta-login"
        return "tavletta-session", "tavletta-login"

    def _cookie(self, origin: str, name: str, value: str, seconds: float) -> str:
        secure = "; Secure" if origin.startswith("https://") else ""
        return "%s=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax%s" % (
            name,
            value,
            max(0, int(seconds)),
            secure,
        )

    # --- the routes

    def info(self) -> dict:
        return {
            "provider": self.settings.provider_name,
            "token_login": self.settings.token_login,
        }

    def start(self, site: str, asked: str | None, moved: bool = False) -> Answer:
        """GET /oidc/login: to the provider. Started at another address
        than public_url: there first, where the callback comes to (once:
        a proxy that hides the address the browser sees must not make it
        go round in circles). The sign in goes into the browser's login
        cookie (sessions.Logins), replacing one it started before."""
        back = after(asked)
        public = self.settings.public_url
        if public and not moved and not _same_host(site, public):
            query = urlencode({"return": back, "moved": "1"})
            return _redirect(public + "/oidc/login?" + query)
        origin = self.origin(site)
        if not self.usable(origin):
            log.error(
                "OIDC sign in refused at %s: cookies need HTTPS; set public_url in the "
                "OIDC settings, or --trusted-proxy for a proxy that sends X-Forwarded-Proto",
                origin,
            )
            return _failed("failed")
        login_cookie = self.names(origin)[1]
        login, browser = self.logins.start(back)
        try:
            url = self.provider.authorization_url(
                origin + CALLBACK, login.state, login.nonce, login.verifier
            )
        except OidcError as e:
            log.warning("OIDC sign in cannot start: %s", e)
            return _failed(e.code)
        answer = _redirect(url)
        answer.cookies.append(self._cookie(origin, login_cookie, browser, LOGIN_COOKIE_SECONDS))
        return answer

    def finish(self, site: str, query: Mapping[str, list[str]], jar: Mapping) -> Answer:
        """GET /oidc/callback: the provider's answer."""
        origin = self.origin(site)
        session_cookie, login_cookie = self.names(origin)
        state = _one(query, "state")
        login = self.logins.take(state, jar.get(login_cookie)) if state else None
        try:
            answer = self._finish(origin, query, login, jar.get(session_cookie))
        except _ExchangeFailed as e:
            log.warning("OIDC sign in failed: %s", e.error)
            answer = _failed(e.error.code)
            answer.exchange_failed = True
        except OidcError as e:
            log.warning("OIDC sign in failed: %s", e)
            answer = _failed(e.code)
        if login is not None:  # its sign in is over; another's (a forged callback) is not
            answer.cookies.append(self._cookie(origin, login_cookie, "", 0))
        return answer

    def _finish(self, origin: str, query, login, old: str | None) -> Answer:
        error = _one(query, "error")
        if error is not None:  # canceled at the provider, or refused there
            if login is None:
                raise OidcError("expired", "an error answer without a sign in under way")
            # its text goes to the log as a quoted value (no line breaks)
            raise OidcError("denied" if error == "access_denied" else "failed", "error %r" % error)
        if login is None:
            raise OidcError("expired", "no sign in under way for this state and browser")
        issuer = _one(query, "iss")
        if issuer is not None and issuer != self.settings.issuer:  # RFC 9207
            raise OidcError("failed", "the answer names another issuer")
        code = _one(query, "code")
        if not code:
            raise OidcError("failed", "the answer has no code")
        if not self._exchanges.acquire(timeout=EXCHANGE_WAIT):
            raise OidcError("failed", "%d sign ins at the provider at once" % EXCHANGES)
        try:
            caller, subject, id_token = self._caller(origin, code, login)
        except OidcError as e:
            if e.code == "no_role":  # signed in at the provider, without a role here
                raise
            raise _ExchangeFailed(e) from e
        finally:
            self._exchanges.release()
        if old:
            self.sessions.end(old)  # a new id for every sign in
        value = self.sessions.create(caller, subject, id_token)
        log.info("signed in: %s, role %s", caller.name, caller.role)
        answer = _redirect("/" + login.after)
        answer.cookies.append(
            self._cookie(origin, self.names(origin)[0], value, self.settings.session_seconds)
        )
        return answer

    def _caller(self, origin: str, code: str, login) -> tuple[Caller, str, str]:
        provider = self.provider
        tokens = provider.exchange(code, origin + CALLBACK, login.verifier)
        id_token = tokens["id_token"]
        claims = provider.validate(id_token, login.nonce)
        if self.settings.userinfo:
            access = tokens.get("access_token")
            if not isinstance(access, str):
                raise OidcError("failed", "the token endpoint sent no access token")
            claims = provider.userinfo(access, claims["sub"]) | claims
        caller = caller_for(self.settings, claims, self.printers)
        if caller is None:
            raise OidcError("no_role", "%s has no role here" % user_name(self.settings, claims))
        return caller, claims["sub"], id_token

    def session(self, site: str, jar: Mapping) -> Session | None:
        value = jar.get(self.names(self.origin(site))[0])
        return self.sessions.find(value) if value else None

    def logout(self, site: str, jar: Mapping) -> Answer:
        """POST /oidc/logout: the session ended; where the page goes next
        (the provider's logout, which comes back to the page, or the page)."""
        origin = self.origin(site)
        name = self.names(origin)[0]
        value = jar.get(name)
        session = self.sessions.end(value) if value else None
        target = "/"
        if session is not None:
            log.info("signed out: %s", session.caller.name)
            target = self.provider.end_session_url(session.id_token, origin + "/") or "/"
        answer = Answer(200, body={"redirect": target})
        answer.cookies.append(self._cookie(origin, name, "", 0))
        return answer


def _same_host(site: str, public: str) -> bool:
    """Whether the request came to public_url's host and port (a port not
    given: public_url's scheme's own, so "labels.example.com:443" through a
    proxy that sends no scheme is public_url's https host)."""
    here, there = urlsplit(site), urlsplit(public)
    default = {"http": 80, "https": 443}.get(there.scheme)
    try:
        port = here.port
    except ValueError:  # not a port at all
        return False
    return here.hostname == there.hostname and (port or default) == (there.port or default)


class _ExchangeFailed(Exception):
    """The provider did not sign in a callback's code (OidcError)."""

    def __init__(self, error: OidcError):
        super().__init__(str(error))
        self.error = error


def _one(query: Mapping[str, list[str]], key: str) -> str | None:
    """A parameter given once (twice: none, it could be a trick)."""
    values = query.get(key) or []
    return values[0] if len(values) == 1 else None


def _redirect(location: str) -> Answer:
    return Answer(303, {"Location": location})


def _failed(code: str) -> Answer:
    return _redirect("/#/signin-error/" + (code if code in ERRORS else "failed"))
