"""Sign ins under way and the sessions of the web page (oidc.py), in
memory: bounded, with expiry, gone when the server restarts.

A session is found by the SHA-256 of its cookie's value: the store holds
no value a cookie could be made from. A new session gets a new id after
every sign in (no session fixation).
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from tavletta.server.config import Caller

LOGIN_SECONDS = 600.0  # from the sign in button to the provider's answer
MAX_LOGINS = 1000  # sign ins under way, in all
LOGINS_PER_CLIENT = 10  # per client address: its own oldest makes room
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
    verifier: str  # PKCE
    binding: bytes = field(repr=False)  # SHA-256 of the browser's login cookie
    after: str  # the page's address (#/...) to go back to
    client: str
    started: float


class Logins:
    """Sign ins under way, by their state; each is taken once."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._logins: OrderedDict[str, Login] = OrderedDict()

    def start(self, client: str, after: str) -> tuple[Login, str]:
        """A new sign in, and the value of the cookie that binds it to the
        browser that started it."""
        browser = new_secret()
        login = Login(
            new_secret(), new_secret(), new_secret(), _key(browser), after, client, self._clock()
        )
        with self._lock:
            self._expire()
            mine = [s for s, entry in self._logins.items() if entry.client == client]
            if len(mine) >= LOGINS_PER_CLIENT:
                del self._logins[mine[0]]
            if len(self._logins) >= MAX_LOGINS:
                self._logins.popitem(last=False)
            self._logins[login.state] = login
        return login, browser

    def take(self, state: str, browser: str | None) -> Login | None:
        """The sign in of this state, if this browser started it and it is
        not too old; gone from the store either way (one try)."""
        with self._lock:
            self._expire()
            login = self._logins.pop(state, None)
        if login is None or browser is None:
            return None
        if not secrets.compare_digest(login.binding, _key(browser)):
            return None
        return login

    def _expire(self) -> None:
        limit = self._clock() - LOGIN_SECONDS
        while self._logins:
            state, login = next(iter(self._logins.items()))
            if login.started > limit:
                break
            del self._logins[state]

    def __len__(self) -> int:
        return len(self._logins)


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
