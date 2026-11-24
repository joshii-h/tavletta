"""Signing in to the web page with OpenID Connect (--oidc oidc.toml).

Any provider that follows OpenID Connect Core and Discovery: the
authorization code flow with PKCE (S256), state and nonce, as a
confidential client (client_secret_basic or client_secret_post). The
provider's settings come from <issuer>/.well-known/openid-configuration,
its keys from its jwks_uri (kept for an hour, fetched again for a key id
not seen before: keys rotate). The ID token is checked with PyJWT: the
signature (RSA, RSA-PSS, ECDSA or EdDSA, never "none" or a shared
secret), iss, aud, azp, exp, iat and nbf with a minute of leeway, and the
nonce.

Claims become a Caller: a claim (a path such as realm_access.roles or
groups) holds the provider's role or group names; [oidc.roles] says which
of them is which role of Tavletta. Someone without one of them gets no
access. Printers can be limited by a claim holding printer names or by
a list per role or group name.

oidc.toml:

    [oidc]
    issuer = "https://id.example.com/realms/example"
    client_id = "tavletta"
    client_secret_file = "/etc/tavletta/oidc-secret"
    public_url = "https://labels.example.com"  # the page's address
    provider_name = "Example ID"                  # on the sign in button
    roles_claim = "realm_access.roles"

    [oidc.roles]
    preview = ["tavletta-preview"]
    print = ["tavletta-print"]
    admin = ["tavletta-admin"]

PyJWT (with cryptography) is needed only for this: imported when the file
is read, so a server without --oidc runs without it.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import logging
import secrets
import ssl
import stat
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from tavletta.server.config import (
    ROLES,
    Caller,
    ConfigError,
    NamedPrinter,
    _problem,
    _Table,
    read_toml,
)
from tavletta.ui.i18n import _

log = logging.getLogger(__name__)

# signatures with a public key only: never "none", never HS* (the client
# secret as a key would let anyone who knows it make tokens)
ALGORITHMS = (
    "RS256",
    "RS384",
    "RS512",
    "PS256",
    "PS384",
    "PS512",
    "ES256",
    "ES384",
    "ES512",
    "EdDSA",
)
TOKEN_AUTH = ("client_secret_basic", "client_secret_post")
LEEWAY = 60  # seconds of clock difference between the provider and this server
TIMEOUT = 10.0  # seconds for each request to the provider
MAX_ANSWER = 1024 * 1024  # bytes of an answer of the provider
MAX_ID_TOKEN = 32 * 1024
METADATA_SECONDS = 3600.0  # discovery and keys kept this long
RETRY_SECONDS = 10.0  # after a failed fetch, no new one before this
KEY_REFRESH_SECONDS = 30.0  # an unknown key id fetches the keys at most this often
MAX_NAME = 128  # characters of a user name or display name


class OidcError(Exception):
    """A sign in that failed; `code` goes to the page (never details: the
    log has them)."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


# --- the settings ----------------------------------------------------------------


@dataclass(frozen=True)
class OidcSettings:
    issuer: str
    client_id: str
    client_secret: str = field(repr=False)
    roles_claim: tuple[str, ...]
    roles: Mapping[str, frozenset[str]]  # Tavletta role -> provider names
    public_url: str | None = None  # scheme://host[:port], no path
    provider_name: str = "OpenID Connect"
    scopes: tuple[str, ...] = ("openid", "profile", "email")
    token_auth: str = "client_secret_basic"
    username_claim: tuple[str, ...] = ("preferred_username",)
    name_claim: tuple[str, ...] = ("name",)
    printers_claim: tuple[str, ...] | None = None
    # provider name -> printers (None: all); empty: printers not limited this way
    role_printers: Mapping[str, frozenset[str] | None] = field(default_factory=dict)
    userinfo: bool = False
    token_login: bool = True  # the page also offers signing in with a token
    session_seconds: float = 8 * 3600.0
    idle_seconds: float = 3600.0
    ca_file: Path | None = None


def local_url(url: str) -> bool:
    """http:// is allowed only for an address of this computer (testing)."""
    host = urlsplit(url).hostname or ""
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _url(table: _Table, key: str, required: bool) -> str:
    value = table.text(key, required=required)
    if not value:
        return ""
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018 - raises for a port that is none
    except ValueError:
        raise table.fail(
            key, _("expected an https:// address (http:// only on this computer)")
        ) from None
    secure = parts.scheme == "https" or (parts.scheme == "http" and local_url(value))
    if not secure or not parts.hostname or parts.query or parts.fragment or "@" in parts.netloc:
        raise table.fail(key, _("expected an https:// address (http:// only on this computer)"))
    return value


def origin_of(url: str) -> str:
    """scheme://host[:port] as a browser sends it in Origin: lower case,
    without the scheme's own port."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    host = "[%s]" % host if ":" in host else host
    port = parts.port
    default = {"http": 80, "https": 443}.get(parts.scheme)
    return "%s://%s%s" % (parts.scheme, host, ":%d" % port if port and port != default else "")


def _texts(table: _Table, key: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    """A text or a list of texts."""
    value = table.data.pop(key, None)
    if value is None:
        return default
    items = [value] if isinstance(value, str) else value
    if (
        not isinstance(items, list)
        or not all(isinstance(v, str) and 0 < len(v) <= 200 for v in items)
        or any(ord(c) < 32 or ord(c) == 127 for v in items for c in v)
    ):
        raise table.fail(key, _("expected a list of texts"))
    return tuple(items)


def _claim_path(table: _Table, key: str, default: str | None) -> tuple[str, ...] | None:
    """ "realm_access.roles", or a list of names for names with dots in them
    (["resource_access", "my.client", "roles"])."""
    value = table.data.pop(key, default)
    if value is None:
        return None
    parts = tuple(value.split(".")) if isinstance(value, str) else value
    if (
        not isinstance(parts, (tuple, list))
        or not parts
        or not all(isinstance(p, str) and 0 < len(p) <= 200 for p in parts)
    ):
        raise table.fail(key, _("expected a list of texts"))
    return tuple(parts)


def _flag(table: _Table, key: str, default: bool) -> bool:
    if key not in table.data:
        return default
    return table.flag(key)


def _secret(table: _Table) -> str:
    path = Path(table.text("client_secret_file", required=True))
    try:
        secret = path.read_text(encoding="utf-8").strip()
        mode = path.stat().st_mode
    except (OSError, ValueError):
        raise ConfigError(_("Cannot read the client secret file %s") % path) from None
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        log.warning("the client secret file %s can be read by others: chmod 600 it", path)
    if not secret or not all(32 <= ord(c) < 127 for c in secret):
        raise ConfigError(_("Cannot read the client secret file %s") % path)
    return secret


def _role_names(table: _Table) -> dict[str, frozenset[str]]:
    data = table.data.pop("roles", None)
    if data is None:
        raise table.fail("roles", _("missing"))
    roles = _Table(table.path, table.where + ".roles", data)
    mapped = {role: frozenset(_texts(roles, role)) for role in ROLES}
    roles.done()
    if not any(mapped.values()):
        raise table.fail("roles", _("map at least one role"))
    return mapped


def _role_printers(
    table: _Table, printers: Mapping[str, NamedPrinter] | None
) -> dict[str, frozenset[str] | None]:
    data = table.data.pop("printers", None)
    if data is None:
        return {}
    if printers is None:
        raise table.fail("printers", _("printers can only be limited with --printers"))
    entries = _Table(table.path, table.where + ".printers", data)
    found: dict[str, frozenset[str] | None] = {}
    for name in list(entries.data):
        names = _texts(entries, name)
        if "*" in names:
            found[name] = None
            continue
        if unknown := [p for p in names if p not in printers]:
            raise entries.fail(name, _("Unknown printer: %s") % unknown[0])
        found[name] = frozenset(names)
    return found


def _scopes(table: _Table) -> tuple[str, ...]:
    scopes = _texts(table, "scopes", OidcSettings.scopes)
    if "openid" not in scopes or any(" " in s for s in scopes):
        raise table.fail("scopes", _("expected a list of texts"))
    return scopes


def _public_url(table: _Table) -> str | None:
    public = _url(table, "public_url", required=False)
    if public and urlsplit(public).path not in ("", "/"):
        raise table.fail(
            "public_url", _("expected an https:// address (http:// only on this computer)")
        )
    return origin_of(public) if public else None


def _printer_limits(table: _Table, printers: Mapping[str, NamedPrinter] | None):
    """printers_claim or [oidc.printers], not both; either needs --printers."""
    claimed = _claim_path(table, "printers_claim", None)
    listed = _role_printers(table, printers)
    if claimed and listed:
        raise table.fail("printers", _("use either printers_claim or [oidc.printers]"))
    if claimed and printers is None:
        raise table.fail("printers_claim", _("printers can only be limited with --printers"))
    return claimed, listed


def load_oidc(path: Path, printers: Mapping[str, NamedPrinter] | None = None) -> OidcSettings:
    """oidc.toml, checked as strictly as the other files (ConfigError)."""
    data = read_toml(path)
    if extra := sorted(set(data) - {"oidc"}):
        raise _problem(path, extra[0], _("unknown key"))
    table = _Table(path, "oidc", data.get("oidc"))
    scopes = _scopes(table)
    public = _public_url(table)
    printers_claim, role_printers = _printer_limits(table, printers)
    ca = table.text("ca_file")
    settings = OidcSettings(
        issuer=_url(table, "issuer", required=True),
        client_id=table.text("client_id", required=True),
        client_secret=_secret(table),
        roles_claim=_claim_path(table, "roles_claim", None) or _missing(table, "roles_claim"),
        roles=_role_names(table),
        public_url=public,
        provider_name=table.text("provider_name", default=OidcSettings.provider_name),
        scopes=scopes,
        token_auth=table.choice("token_auth", TOKEN_AUTH) or OidcSettings.token_auth,
        username_claim=_claim_path(table, "username_claim", "preferred_username") or (),
        name_claim=_claim_path(table, "name_claim", "name") or (),
        printers_claim=printers_claim,
        role_printers=role_printers,
        userinfo=table.flag("userinfo"),
        token_login=_flag(table, "token_login", True),
        session_seconds=3600.0 * (table.number("session_hours", 0.1, 168) or 8),
        idle_seconds=60.0 * (table.number("idle_minutes", 1, 10080) or 60),
        ca_file=Path(ca) if ca else None,
    )
    table.done()
    return settings


def _missing(table: _Table, key: str) -> tuple[str, ...]:
    raise table.fail(key, _("missing"))


# --- talking to the provider --------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """The provider's endpoints answer themselves: a redirect could lead
    from https to http, or a POST with the client secret elsewhere."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class Metadata:
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    userinfo_endpoint: str | None = None
    end_session_endpoint: str | None = None


class Provider:
    """The provider of one issuer: its settings and keys, fetched when
    first needed and kept for an hour."""

    def __init__(self, settings: OidcSettings, clock=time.monotonic):
        self.settings = settings
        self._clock = clock
        self._lock = threading.Lock()
        self._metadata: Metadata | None = None
        self._metadata_at = 0.0
        self._failed_at: float | None = None
        self._keys: dict[str, _Key] = {}  # by kid ("" for none)
        self._keys_at: float | None = None
        context = ssl.create_default_context(
            cafile=str(settings.ca_file) if settings.ca_file else None
        )
        self._opener = urllib.request.build_opener(
            _NoRedirect, urllib.request.HTTPSHandler(context=context)
        )

    # --- HTTP

    def _request(self, url: str, data: dict[str, str] | None = None, headers=None) -> dict:
        """A JSON object from the provider; OidcError for anything else."""
        if not (url.startswith("https://") or (url.startswith("http://") and local_url(url))):
            raise OidcError("failed", "not an https address: %s" % url)
        body = urlencode(data).encode() if data is not None else None
        request = urllib.request.Request(
            url, data=body, headers={"Accept": "application/json", **(headers or {})}
        )
        try:
            with self._opener.open(request, timeout=TIMEOUT) as answer:  # nosec B310
                raw = answer.read(MAX_ANSWER + 1)
        except urllib.error.HTTPError as e:
            raise OidcError("failed", "%s answered %d" % (_where(url), e.code)) from None
        except (OSError, ValueError) as e:
            raise OidcError("failed", "%s: %s" % (_where(url), e)) from None
        if len(raw) > MAX_ANSWER:
            raise OidcError("failed", "%s: answer too large" % _where(url))
        try:
            found = json.loads(raw)
        except (ValueError, RecursionError):
            raise OidcError("failed", "%s: not JSON" % _where(url)) from None
        if not isinstance(found, dict):
            raise OidcError("failed", "%s: not a JSON object" % _where(url))
        return found

    # --- discovery

    def metadata(self) -> Metadata:
        now = self._clock()
        with self._lock:
            if self._metadata and now - self._metadata_at < METADATA_SECONDS:
                return self._metadata
            if self._failed_at is not None and now - self._failed_at < RETRY_SECONDS:
                if self._metadata:
                    return self._metadata  # the one before, while the provider is away
                raise OidcError("failed", "the provider could not be reached a moment ago")
        try:
            found = self._discover()
        except OidcError:
            with self._lock:
                self._failed_at = self._clock()
                if self._metadata:
                    log.warning("OIDC discovery failed, keeping the settings fetched before")
                    return self._metadata
            raise
        with self._lock:
            self._metadata, self._metadata_at, self._failed_at = found, self._clock(), None
        return found

    def _discover(self) -> Metadata:
        issuer = self.settings.issuer
        doc = self._request(issuer.rstrip("/") + "/.well-known/openid-configuration")
        if doc.get("issuer") != issuer:  # OpenID Connect Discovery 4.3: exactly
            raise OidcError("failed", "the discovery document names another issuer")
        methods = doc.get("code_challenge_methods_supported")
        if isinstance(methods, list) and "S256" not in methods:
            raise OidcError("failed", "the provider does not offer PKCE with S256")
        required = {
            key: self._endpoint(doc, key) or ""
            for key in ("authorization_endpoint", "token_endpoint", "jwks_uri")
        }
        return Metadata(
            **required,
            userinfo_endpoint=self._endpoint(doc, "userinfo_endpoint", required=False),
            end_session_endpoint=self._endpoint(doc, "end_session_endpoint", required=False),
        )

    def _endpoint(self, doc: dict, key: str, required: bool = True) -> str | None:
        value = doc.get(key)
        if value is None and not required:
            return None
        secure = isinstance(value, str) and (
            value.startswith("https://") or (value.startswith("http://") and local_url(value))
        )
        if not secure:
            raise OidcError("failed", "the discovery document has no usable %s" % key)
        return value

    # --- the steps of a sign in

    def authorization_url(self, redirect_uri: str, state: str, nonce: str, verifier: str) -> str:
        endpoint = self.metadata().authorization_endpoint
        query = {
            "response_type": "code",
            "client_id": self.settings.client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(self.settings.scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge(verifier),
            "code_challenge_method": "S256",
        }
        return endpoint + ("&" if "?" in endpoint else "?") + urlencode(query)

    def exchange(self, code: str, redirect_uri: str, verifier: str) -> dict:
        """The token endpoint's answer for an authorization code."""
        settings = self.settings
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        }
        headers = {}
        if settings.token_auth == "client_secret_post":
            data |= {"client_id": settings.client_id, "client_secret": settings.client_secret}
        else:  # RFC 6749 2.3.1: both form-encoded first
            pair = "%s:%s" % (_form(settings.client_id), _form(settings.client_secret))
            headers["Authorization"] = "Basic " + base64.b64encode(pair.encode()).decode()
        answer = self._request(self.metadata().token_endpoint, data, headers)
        if not isinstance(answer.get("id_token"), str):
            raise OidcError("failed", "the token endpoint sent no ID token")
        return answer

    def userinfo(self, access_token: str, subject: str) -> dict:
        endpoint = self.metadata().userinfo_endpoint
        if not endpoint:
            raise OidcError("failed", "the provider has no userinfo endpoint")
        claims = self._request(endpoint, headers={"Authorization": "Bearer " + access_token})
        if claims.get("sub") != subject:  # Core 5.3.2: must be the same user
            raise OidcError("failed", "userinfo names another subject")
        return claims

    def end_session_url(self, id_token: str, after: str) -> str | None:
        """RP-initiated logout, if the provider offers it."""
        try:
            endpoint = self.metadata().end_session_endpoint
        except OidcError:
            return None
        if not endpoint:
            return None
        query = {
            "client_id": self.settings.client_id,
            "post_logout_redirect_uri": after,
        }
        if id_token:
            query["id_token_hint"] = id_token
        return endpoint + ("&" if "?" in endpoint else "?") + urlencode(query)

    # --- the ID token

    def validate(self, id_token: str, nonce: str) -> dict:
        """The claims of an ID token from the token endpoint, checked."""
        import jwt

        if len(id_token) > MAX_ID_TOKEN:
            raise OidcError("failed", "the ID token is too large")
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as e:
            raise OidcError("failed", "the ID token cannot be read: %s" % e) from None
        algorithm = header.get("alg")
        if algorithm not in ALGORITHMS:
            raise OidcError("failed", "the ID token is signed with %r" % algorithm)
        key = self._key(header.get("kid"), algorithm).jwk
        settings = self.settings
        try:
            claims = jwt.decode(
                id_token,
                key.key,
                algorithms=[algorithm],
                audience=settings.client_id,
                issuer=settings.issuer,
                leeway=LEEWAY,
                options={"require": ["iss", "sub", "aud", "exp", "iat"]},
            )
        except jwt.PyJWTError as e:
            raise OidcError("failed", "the ID token is not valid: %s" % e) from None
        _check_claims(claims, settings, nonce)
        return claims

    def _key(self, kid: object, algorithm: str) -> _Key:
        if kid is not None and not isinstance(kid, str):
            raise OidcError("failed", "the ID token's key id is not text")
        key = self._find(kid, algorithm)
        if key is None and self._may_refresh():  # a new key: rotated
            self._load_keys()
            key = self._find(kid, algorithm)
        if key is None:
            raise OidcError("failed", "no key of the provider matches the ID token")
        return key

    def _may_refresh(self) -> bool:
        with self._lock:
            return self._keys_at is None or self._clock() - self._keys_at >= KEY_REFRESH_SECONDS

    def _find(self, kid: str | None, algorithm: str) -> _Key | None:
        with self._lock:
            stale = self._keys_at is None or self._clock() - self._keys_at >= METADATA_SECONDS
            keys = dict(self._keys)
        if stale:
            keys = self._load_keys()
        if kid is not None:
            key = keys.get(kid)
            return key if key is not None and key.fits(algorithm) else None
        fitting = [k for k in keys.values() if k.fits(algorithm)]
        return fitting[0] if len(fitting) == 1 else None  # no kid: only if one key can be meant

    def _load_keys(self) -> dict[str, _Key]:
        from jwt import PyJWK
        from jwt.exceptions import PyJWKError

        doc = self._request(self.metadata().jwks_uri)
        entries = doc.get("keys")
        if not isinstance(entries, list):
            raise OidcError("failed", "the provider's key set has no keys")
        keys = {}
        for entry in entries[:100]:
            if not isinstance(entry, dict) or entry.get("use", "sig") != "sig":
                continue
            alg = entry.get("alg")
            try:
                key = _Key(PyJWK(entry), entry["kty"], alg if isinstance(alg, str) else None)
            except (PyJWKError, ValueError, TypeError, KeyError):  # a kind PyJWT cannot use
                continue
            kid = entry.get("kid")
            keys[kid if isinstance(kid, str) else ""] = key
        with self._lock:
            self._keys, self._keys_at = keys, self._clock()
        return keys


@dataclass(frozen=True)
class _Key:
    """A key of the provider's key set, with what its JWK says of it."""

    jwk: Any  # PyJWK
    kty: str
    alg: str | None

    def fits(self, algorithm: str) -> bool:
        """For this algorithm: of its kind, and of this algorithm if the
        key names one."""
        kind = {"RS": "RSA", "PS": "RSA", "ES": "EC", "Ed": "OKP"}[algorithm[:2]]
        return self.kty == kind and self.alg in (None, algorithm)


def _check_claims(claims: dict, settings: OidcSettings, nonce: str) -> None:
    """What PyJWT does not check: azp and nonce (Core 3.1.3.7)."""
    audience = claims.get("aud")
    several = isinstance(audience, list) and len(audience) > 1
    azp = claims.get("azp")
    if (several or azp is not None) and azp != settings.client_id:
        raise OidcError("failed", "the ID token was issued to another client (azp)")
    given = claims.get("nonce")
    if not isinstance(given, str) or not secrets.compare_digest(given, nonce):
        raise OidcError("failed", "the ID token's nonce does not match")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise OidcError("failed", "the ID token has no subject")


def _where(url: str) -> str:
    """An endpoint for the log: without its query."""
    parts = urlsplit(url)
    return "%s://%s%s" % (parts.scheme, parts.netloc, parts.path)


def _form(text: str) -> str:
    from urllib.parse import quote_plus

    return quote_plus(text)


def challenge(verifier: str) -> str:
    """PKCE S256 (RFC 7636 4.2)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


# --- claims to a caller -----------------------------------------------------------


def claim(claims: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    found: Any = claims
    for part in path:
        if not isinstance(found, Mapping):
            return None
        found = found.get(part)
    return found


def _names(value: Any) -> set[str]:
    """A claim's names: a text, or the texts of a list."""
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return {v for v in value if isinstance(v, str)}
    return set()


def _plain(value: Any) -> str:
    """A name for logs and the page: printable, not too long."""
    if not isinstance(value, str):
        return ""
    return "".join(c for c in value if c.isprintable())[:MAX_NAME].strip()


def user_name(settings: OidcSettings, claims: Mapping[str, Any]) -> str:
    """How a person is named in logs and jobs: "oidc:" and the user name
    claim (else the subject); a token's name cannot have a colon."""
    return "oidc:" + (
        _plain(claim(claims, settings.username_claim)) or _plain(claims.get("sub")) or "-"
    )


def caller_for(
    settings: OidcSettings,
    claims: Mapping[str, Any],
    printers: Mapping[str, NamedPrinter] | None = None,
) -> Caller | None:
    """The Caller of a user signed in; None for one without a role here."""
    names = _names(claim(claims, settings.roles_claim))
    role = next((r for r in reversed(ROLES) if settings.roles[r] & names), None)
    if role is None:
        return None
    user = user_name(settings, claims)
    shown = _plain(claim(claims, settings.name_claim)) or user.removeprefix("oidc:")
    return Caller(
        user,
        role,
        _printers(settings, claims, names, printers),
        named=True,
        display=shown,
    )


def _printers(
    settings: OidcSettings,
    claims: Mapping[str, Any],
    names: set[str],
    printers: Mapping[str, NamedPrinter] | None,
) -> frozenset[str] | None:
    if settings.printers_claim:
        given = _names(claim(claims, settings.printers_claim))
        return frozenset(p for p in given if printers is None or p in printers)
    if not settings.role_printers:
        return None
    allowed: set[str] = set()
    for name in names & set(settings.role_printers):
        listed = settings.role_printers[name]
        if listed is None:
            return None
        allowed |= listed
    return frozenset(allowed)
