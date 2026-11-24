"""Sign ins under way and the sessions of the web page (oidc.py), in
memory: bounded, with expiry, gone when the server restarts.

A session is found by the SHA-256 of its cookie's value: the store holds
no value a cookie could be made from. A new session gets a new id after
every sign in (no session fixation).
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from tavletta.server.config import Caller

LOGIN_SECONDS = 600.0  # from the sign in button to the provider's answer
# sign ins taken (their state), remembered for LOGIN_SECONDS so a callback
# is taken once; when more come, the oldest are forgotten: a callback of
# one of those sent again still fails at the provider, which takes a code once
MAX_TAKEN = 10_000
MAX_SESSIONS = 10_000
SESSIONS_PER_USER = 20  # a user's own oldest makes room


def _key(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def new_secret() -> str:
    """A state, nonce, PKCE verifier, browser binding or session id: 256
    random bits."""
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class Login:
    """A sign in under way: what the callback must find again."""

    state: str
    nonce: str
    verifier: str = field(repr=False)  # PKCE
    after: str  # the page's address (#/...) to go back to
    started: float


class Logins:
    """Sign ins under way, kept by nobody but the browser that started
    one: sealed (encrypted and signed, Fernet, with a key made when the
    server starts) into its login cookie. No store on the server, so none
    to fill: no limit of sign ins under way, and none of another browser
    is dropped for a new one. A sign in is good for LOGIN_SECONDS and taken
    once (MAX_TAKEN)."""

    def __init__(self, clock=time.monotonic):
        from cryptography.fernet import Fernet  # with PyJWT for --oidc

        self._fernet = Fernet(Fernet.generate_key())
        self._clock = clock
        self._lock = threading.Lock()
        self._taken: OrderedDict[str, float] = OrderedDict()  # state -> when started

    def start(self, after: str) -> tuple[Login, str]:
        """A new sign in, and the value of the browser's login cookie that
        holds it."""
        login = Login(new_secret(), new_secret(), new_secret(), after, self._clock())
        sealed = json.dumps([login.state, login.nonce, login.verifier, after, login.started])
        return login, self._fernet.encrypt(sealed.encode()).decode()

    def take(self, state: str, browser: str | None) -> Login | None:
        """The sign in of this state, if this browser's cookie holds it and
        it is not too old."""
        from cryptography.fernet import InvalidToken

        if not browser:
            return None
        try:
            data = json.loads(self._fernet.decrypt(browser.encode()))
        except (InvalidToken, ValueError):
            return None
        if not isinstance(data, list) or len(data) != 5:
            return None
        login = Login(*data)
        if not secrets.compare_digest(login.state.encode(), state.encode()):
            return None
        now = self._clock()
        if not 0 <= now - login.started < LOGIN_SECONDS:
            return None
        with self._lock:
            while self._taken and (
                len(self._taken) >= MAX_TAKEN
                or now - next(iter(self._taken.values())) >= LOGIN_SECONDS
            ):
                self._taken.popitem(last=False)
            if login.state in self._taken:
                return None
            self._taken[login.state] = login.started
        return login


@dataclass
class Session:
    caller: Caller
    subject: str  # the provider's sub: whose sessions these are
    id_token: str = field(repr=False)  # for the provider's logout
    created: float = 0.0
    seen: float = 0.0


class Sessions:
    """Sessions by the SHA-256 of their id: at most `lifetime` seconds
    long, ended after `idle` seconds without a request."""

    def __init__(self, lifetime: float, idle: float, clock=time.monotonic):
        self.lifetime, self.idle = lifetime, idle
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: OrderedDict[bytes, Session] = OrderedDict()  # least recently used first

    def create(self, caller: Caller, subject: str, id_token: str) -> str:
        """A new session; its id (the cookie's value)."""
        value = new_secret()
        now = self._clock()
        with self._lock:
            mine = [k for k, s in self._sessions.items() if s.subject == subject]
            if len(mine) >= SESSIONS_PER_USER:
                del self._sessions[mine[0]]
            if len(self._sessions) >= MAX_SESSIONS:
                self._drop_expired(now)
            if len(self._sessions) >= MAX_SESSIONS:
                self._sessions.popitem(last=False)
            self._sessions[_key(value)] = Session(caller, subject, id_token, now, now)
        return value

    def find(self, value: str) -> Session | None:
        """The session of a cookie's value, if it is still valid; its idle
        time starts again."""
        key = _key(value)
        now = self._clock()
        with self._lock:
            session = self._sessions.get(key)
            if session is None:
                return None
            if not self._valid(session, now):
                del self._sessions[key]
                return None
            session.seen = now
            self._sessions.move_to_end(key)
            return session

    def end(self, value: str) -> Session | None:
        with self._lock:
            return self._sessions.pop(_key(value), None)

    def _valid(self, session: Session, now: float) -> bool:
        return now - session.created < self.lifetime and now - session.seen < self.idle

    def _drop_expired(self, now: float) -> None:
        for key in [k for k, s in self._sessions.items() if not self._valid(s, now)]:
            del self._sessions[key]

    def __len__(self) -> int:
        return len(self._sessions)
