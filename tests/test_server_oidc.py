"""Signing in to the web page with OpenID Connect (--oidc), against a fake
provider on 127.0.0.1 (tests/oidc_provider.py): the code flow with PKCE,
state, nonce and the browser binding, the ID token's checks, key rotation,
roles and printers from claims, the session cookie and its checks against
other web pages, logout, expiry, proxies, and the token API unchanged."""

from __future__ import annotations

import ipaddress
import time
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urlsplit

import pytest

jwt = pytest.importorskip("jwt")
pytest.importorskip("cryptography")

import oidc_provider  # noqa: E402
from oidc_provider import CLIENT_ID, CLIENT_SECRET, Key  # noqa: E402
from test_server import TOKEN, call  # noqa: E402

from tavletta.server.config import NamedPrinter  # noqa: E402
from tavletta.server.oidc import OidcSettings, Provider  # noqa: E402
from tavletta.server.weblogin import WebLogin  # noqa: E402

ROLES = {
    "preview": frozenset({"tavletta-preview"}),
    "print": frozenset({"tavletta-print"}),
    "admin": frozenset({"tavletta-admin"}),
}
PAGE = {"X-Tavletta-Request": "1"}
JSON = {"Content-Type": "application/json"}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def fake():
    provider, server = oidc_provider.start()
    yield provider
    server.shutdown()
    server.server_close()


def settings(fake, **changes) -> OidcSettings:
    base = OidcSettings(
        issuer=fake.issuer,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        roles_claim=("realm_access", "roles"),
        roles=ROLES,
        provider_name="Test ID",
    )
    return replace(base, **changes)


@pytest.fixture
def make_site(make_server, fake):
    def factory(clock=None, printers=None, token=None, **changes):
        login = WebLogin(
            settings(fake, **changes),
            printers=printers,
            **({"clock": clock} if clock else {}),
        )
        server, base = make_server(token=token, login=login, printers=printers, mcp=True)
        host = urlsplit(base).netloc
        return SimpleNamespace(
            base=base, server=server, login=login, fake=fake, origin="http://" + host
        )

    return factory


@pytest.fixture
def site(make_site):
    return make_site()


def set_cookies(response) -> dict[str, str]:
    return {line.split("=")[0]: line for line in response.headers.get_all("Set-Cookie") or []}


def value(line: str) -> str:
    return line.split(";")[0].split("=", 1)[1]


def start(site, after="#/print/a", headers=None):
    status, response, _ = call(
        site.base, "GET", "/oidc/login?return=" + quote(after, safe=""), headers=headers
    )
    assert status == 303, response.getheader("Location")
    return response.getheader("Location"), set_cookies(response)


def sign_in(site, claims=None, after="#/print/a", tamper=None, headers=None, prefix=""):
    """The whole round trip; (status, Location, Set-Cookie lines)."""
    location, cookies = start(site, after, headers)
    code = site.fake.authorize(location, claims)
    state = parse_qs(urlsplit(location).query)["state"][0]
    query = {"code": code, "state": state}
    name = prefix + "tavletta-login"
    cookie = name + "=" + value(cookies[name])
    if tamper:
        query, cookie = tamper(query, cookie)
    path = "/oidc/callback?" + "&".join("%s=%s" % (k, quote(v, safe="")) for k, v in query.items())
    extra = dict(headers or {}, **({"Cookie": cookie} if cookie else {}))
    status, response, _ = call(site.base, "GET", path, headers=extra)
    return status, response.getheader("Location"), set_cookies(response)


def session_of(cookies) -> str:
    return "tavletta-session=" + value(cookies["tavletta-session"])


def me(site, cookie, **headers):
    return call(site.base, "GET", "/api/v1/me", headers={"Cookie": cookie, **PAGE, **headers})


# --- the round trip -------------------------------------------------------------------


def test_signing_in_makes_a_session_for_the_page(site):
    location, cookies = start(site)
    query = parse_qs(urlsplit(location).query)
    assert location.startswith(site.fake.issuer + "/auth?")
    assert query["redirect_uri"] == [site.origin + "/oidc/callback"]
    assert query["code_challenge_method"] == ["S256"] and len(query["state"][0]) >= 43
    login = cookies["tavletta-login"]
    assert "HttpOnly" in login and "SameSite=Lax" in login and "Path=/" in login
    assert "Secure" not in login  # http://127.0.0.1: testing on this computer

    status, location, cookies = sign_in(site)
    assert status == 303 and location == "/#/print/a"
    line = cookies["tavletta-session"]
    assert "HttpOnly" in line and "SameSite=Lax" in line and "Max-Age=28800" in line
    assert value(cookies["tavletta-login"]) == ""  # the login cookie is gone
    status, _response, body = me(site, session_of(cookies))
    assert status == 200
    assert body["name"] == "oidc:alice" and body["display_name"] == "Alice Example"
    assert body["role"] == "print" and body["session"] is True and body["printers"] is None


def test_the_token_endpoint_gets_the_secret_and_the_verifier(site):
    sign_in(site)
    request = site.fake.token_requests[-1]
    assert request["authorization"].startswith("Basic ") and "client_secret" not in request
    assert len(request["code_verifier"]) >= 43


def test_client_secret_post(make_site):
    site = make_site(token_auth="client_secret_post")
    assert sign_in(site)[0] == 303
    request = site.fake.token_requests[-1]
    assert request["client_secret"] == CLIENT_SECRET and request["authorization"] is None


def test_the_info_names_the_provider(site):
    status, _response, body = call(site.base, "GET", "/oidc/info")
    assert status == 200 and body == {"provider": "Test ID", "token_login": True}


def test_without_oidc_nothing_changes(make_server):
    _server, base = make_server(token=TOKEN)
    assert call(base, "GET", "/oidc/info")[0] == 401  # as any unknown path with a token
    _server, base = make_server()
    assert call(base, "GET", "/oidc/login")[0] == 404
    status, _response, body = call(base, "GET", "/api/v1/me")
    assert status == 200 and body["session"] is False and body["token_required"] is False


# --- what a sign in must not accept ---------------------------------------------------


def failure(site, **kwargs) -> str:
    status, location, cookies = sign_in(site, **kwargs)
    assert status == 303 and "tavletta-session" not in cookies
    assert location.startswith("/#/signin-error/")
    return location.rsplit("/", 1)[1]


def test_a_wrong_state_fails(site):
    def tamper(query, cookie):
        return dict(query, state="x" * 43), cookie

    assert failure(site, tamper=tamper) == "expired"


def test_another_browser_cannot_finish_a_sign_in(site):
    assert failure(site, tamper=lambda q, c: (q, None)) == "expired"
    assert failure(site, tamper=lambda q, c: (q, "tavletta-login=" + "y" * 43)) == "expired"


def test_a_state_is_used_once(site):
    location, cookies = start(site)
    code = site.fake.authorize(location)
    state = parse_qs(urlsplit(location).query)["state"][0]
    cookie = {"Cookie": "tavletta-login=" + value(cookies["tavletta-login"])}
    path = "/oidc/callback?code=%s&state=%s" % (code, state)
    assert call(site.base, "GET", path, headers=cookie)[1].getheader("Location") == "/#/print/a"
    again = call(site.base, "GET", path, headers=cookie)[1].getheader("Location")
    assert again == "/#/signin-error/expired"


def test_a_state_given_twice_is_refused(site):
    # two state parameters: which one counts? neither
    location, cookies = start(site)
    state = parse_qs(urlsplit(location).query)["state"][0]
    path = "/oidc/callback?code=x&state=%s&state=%s" % (state, state)
    headers = {"Cookie": "tavletta-login=" + value(cookies["tavletta-login"])}
    answer = call(site.base, "GET", path, headers=headers)[1].getheader("Location")
    assert answer == "/#/signin-error/expired"


@pytest.mark.parametrize(
    ("claims", "why"),
    [
        ({"nonce": "other"}, "nonce"),
        ({"nonce": None}, "nonce missing"),
        ({"aud": "someone-else"}, "audience"),
        ({"aud": [CLIENT_ID, "other"]}, "several audiences without azp"),
        ({"aud": [CLIENT_ID, "other"], "azp": "other"}, "azp of another client"),
        ({"azp": "other"}, "azp of another client"),
        ({"iss": "https://evil.example/realms/test"}, "issuer"),
        ({"exp": int(time.time()) - 120}, "expired"),
        ({"iat": None}, "no iat"),
        ({"exp": None}, "no exp"),
        ({"nbf": int(time.time()) + 600}, "not yet valid"),
        ({"iat": int(time.time()) + 600}, "issued in the future"),
        ({"sub": None}, "no subject"),
    ],
)
def test_an_id_token_with_wrong_claims_fails(site, claims, why):
    assert failure(site, claims=claims) == "failed", why


def test_a_token_expired_within_the_leeway_passes(site):
    assert sign_in(site, claims={"exp": int(time.time()) - 30})[0] == 303
    assert sign_in(site, claims={"exp": int(time.time()) - 30})[1] == "/#/print/a"


def test_several_audiences_with_our_azp_pass(site):
    claims = {"aud": [CLIENT_ID, "other"], "azp": CLIENT_ID}
    assert sign_in(site, claims=claims)[1] == "/#/print/a"


def test_a_bad_signature_fails(site):
    impostor = Key.rsa("key-1")  # the provider's key id, another key
    location, cookies = start(site)
    code = site.fake.authorize(location)
    grant = site.fake.grants[code]
    grant.claims["__token__"] = site.fake.sign(site.fake.claims(grant), key=impostor)
    state = parse_qs(urlsplit(location).query)["state"][0]
    headers = {"Cookie": "tavletta-login=" + value(cookies["tavletta-login"])}
    path = "/oidc/callback?code=%s&state=%s" % (code, state)
    assert call(site.base, "GET", path, headers=headers)[1].getheader("Location") == (
        "/#/signin-error/failed"
    )


def token_with(site, make):
    """Sign in with an ID token made by make(claims)."""
    location, cookies = start(site)
    code = site.fake.authorize(location)
    grant = site.fake.grants[code]
    grant.claims["__token__"] = make(site.fake.claims(grant))
    state = parse_qs(urlsplit(location).query)["state"][0]
    headers = {"Cookie": "tavletta-login=" + value(cookies["tavletta-login"])}
    path = "/oidc/callback?code=%s&state=%s" % (code, state)
    return call(site.base, "GET", path, headers=headers)[1].getheader("Location")


def test_unsigned_and_shared_secret_tokens_fail(site):
    unsigned = lambda claims: jwt.encode(claims, None, algorithm="none")  # noqa: E731
    hmac_secret = lambda claims: jwt.encode(  # noqa: E731
        claims, CLIENT_SECRET.encode() * 3, algorithm="HS256", headers={"kid": "key-1"}
    )
    assert token_with(site, unsigned) == "/#/signin-error/failed"
    assert token_with(site, hmac_secret) == "/#/signin-error/failed"
    assert token_with(site, lambda claims: "not.a.token") == "/#/signin-error/failed"


def test_an_ec_key_and_a_token_without_kid(site):
    key = Key.ec("ec-1")
    site.fake.keys, site.fake.signer = [key], key
    assert sign_in(site)[1] == "/#/print/a"
    # without kid: the only key of its kind is meant
    no_kid = lambda claims: jwt.encode(claims, key.private, algorithm="ES256")  # noqa: E731
    assert token_with(site, no_kid) == "/#/print/a"


def test_a_key_of_another_algorithm_is_not_used(site):
    rs512 = Key.rsa("key-1", "RS512")  # the key says RS512, the token RS256
    site.fake.keys, site.fake.signer = [rs512], rs512
    forged = lambda claims: jwt.encode(  # noqa: E731
        claims, rs512.private, algorithm="RS256", headers={"kid": "key-1"}
    )
    assert token_with(site, forged) == "/#/signin-error/failed"
    assert sign_in(site)[1] == "/#/print/a"


def test_keys_rotate(make_site):
    clock = Clock()
    site = make_site(clock=clock)
    assert sign_in(site)[1] == "/#/print/a"
    assert site.fake.jwks_fetches == 1
    assert sign_in(site)[1] == "/#/print/a"
    assert site.fake.jwks_fetches == 1  # kept
    new = Key.rsa("key-2")
    site.fake.keys, site.fake.signer = [site.fake.keys[0], new], new
    # a key id not seen: fetched again, but not more often than every 30 s
    assert sign_in(site)[1] == "/#/signin-error/failed"
    assert site.fake.jwks_fetches == 1
    clock.now += 31
    assert sign_in(site)[1] == "/#/print/a"
    assert site.fake.jwks_fetches == 2
    # the old key gone from the set: tokens signed with it fail
    old = site.fake.keys[0]
    site.fake.keys = [new]
    clock.now += 3601
    signed_old = lambda claims: site.fake.sign(claims, key=old)  # noqa: E731
    assert token_with(site, signed_old) == "/#/signin-error/failed"


def test_an_error_from_the_provider(site):
    def denied(query, cookie):
        return {"error": "access_denied", "state": query["state"]}, cookie

    assert failure(site, tamper=denied) == "denied"

    def other(query, cookie):
        return {"error": "server_error", "state": query["state"]}, cookie

    assert failure(site, tamper=other) == "failed"


def test_another_issuer_in_the_answer_fails(site):  # RFC 9207
    def mixed_up(query, cookie):
        return dict(query, iss="https://evil.example"), cookie

    assert failure(site, tamper=mixed_up) == "failed"

    def same(query, cookie):
        return dict(query, iss=site.fake.issuer), cookie

    assert sign_in(site, tamper=same)[1] == "/#/print/a"


def test_a_provider_that_names_another_issuer_is_not_used(site):
    site.fake.discovery = {"issuer": "https://evil.example"}
    status, response, _ = call(site.base, "GET", "/oidc/login")
    assert status == 303 and response.getheader("Location") == "/#/signin-error/failed"


def test_a_provider_without_s256_is_not_used(site):
    site.fake.discovery = {"code_challenge_methods_supported": ["plain"]}
    assert call(site.base, "GET", "/oidc/login")[1].getheader("Location") == (
        "/#/signin-error/failed"
    )


def test_a_provider_that_cannot_be_reached(make_site):
    site = make_site()
    site.login.provider.settings = replace(
        site.login.settings, issuer="http://127.0.0.1:9/realms/none"
    )
    status, response, _ = call(site.base, "GET", "/oidc/login")
    assert status == 303 and response.getheader("Location") == "/#/signin-error/failed"


def test_failed_sign_ins_are_rate_limited(site):
    for _ in range(10):
        failure(site, claims={"nonce": "other"})
    status, _response, body = call(site.base, "GET", "/oidc/login")
    assert status == 429 and body["error"]["code"] == "too_many_requests"


# --- roles and printers ------------------------------------------------------------------


def test_without_a_role_there_is_no_access(site, caplog):
    claims = {"realm_access": {"roles": ["offline_access"]}}
    with caplog.at_level("WARNING"):
        assert failure(site, claims=claims) == "no_role"
    assert "oidc:alice has no role" in caplog.text
    assert failure(site, claims={"realm_access": None}) == "no_role"
    assert failure(site, claims={"realm_access": {"roles": "tavletta-nothing"}}) == "no_role"


def test_the_highest_role_counts(site):
    claims = {"realm_access": {"roles": ["tavletta-preview", "tavletta-admin"]}}
    cookies = sign_in(site, claims=claims)[2]
    assert me(site, session_of(cookies))[2]["role"] == "admin"


def test_client_roles_and_a_name_claim(make_site):
    site = make_site(
        roles_claim=("resource_access", "my.client", "roles"),
        username_claim=("email",),
        name_claim=("given_name",),
    )
    claims = {
        "resource_access": {"my.client": {"roles": ["tavletta-preview"]}},
        "email": "alice@example.com",
        "given_name": "Alice\x07",
    }
    body = me(site, session_of(sign_in(site, claims=claims)[2]))[2]
    assert body["role"] == "preview" and body["name"] == "oidc:alice@example.com"
    assert body["display_name"] == "Alice"  # control characters taken out


PRINTERS = {
    name: NamedPrinter(name, "fake-mono", "%s.local" % name) for name in ("lager", "office")
}


def test_printers_per_role_name(make_site):
    site = make_site(
        printers=PRINTERS,
        role_printers={"tavletta-print": frozenset({"lager"}), "everything": None},
    )
    body = me(site, session_of(sign_in(site)[2]))[2]
    assert body["printers"] == ["lager"]
    status, _response, listed = call(
        site.base,
        "GET",
        "/api/v1/printers?discover=false&status=false",
        headers={"Cookie": session_of(sign_in(site)[2]), **PAGE},
    )
    assert status == 200 and [p["name"] for p in listed["printers"]] == ["lager"]
    claims = {"realm_access": {"roles": ["tavletta-print", "everything"]}}
    assert me(site, session_of(sign_in(site, claims=claims)[2]))[2]["printers"] is None
    claims = {"realm_access": {"roles": ["tavletta-admin"]}}  # a role without a list
    assert me(site, session_of(sign_in(site, claims=claims)[2]))[2]["printers"] == []


def test_printers_from_a_claim(make_site):
    site = make_site(printers=PRINTERS, printers_claim=("tavletta_printers",))
    claims = {"tavletta_printers": ["office", "unknown"]}
    assert me(site, session_of(sign_in(site, claims=claims)[2]))[2]["printers"] == ["office"]
    assert me(site, session_of(sign_in(site)[2]))[2]["printers"] == []  # none: none


def test_userinfo_claims(make_site):
    site = make_site(userinfo=True, roles_claim=("groups",))
    site.fake.userinfo = {"groups": ["tavletta-admin"]}
    assert me(site, session_of(sign_in(site)[2]))[2]["role"] == "admin"
    site.fake.userinfo = {"sub": "someone-else", "groups": ["tavletta-admin"]}
    assert failure(site) == "failed"


# --- the session cookie on the API ---------------------------------------------------------


def test_the_session_needs_the_page_header(site):
    cookie = session_of(sign_in(site)[2])
    status, _response, body = call(site.base, "GET", "/api/v1/me", headers={"Cookie": cookie})
    assert status == 403 and body["error"]["code"] == "header_required"


def test_the_session_is_refused_from_other_pages(site):
    cookie = session_of(sign_in(site)[2])
    for headers in (
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
    ):
        status, _response, body = me(site, cookie, **headers)
        assert status == 403, headers
    assert me(site, cookie, Origin=site.origin, **{"Sec-Fetch-Site": "same-origin"})[0] == 200


def test_a_change_with_the_session_needs_the_origin(site):
    claims = {"realm_access": {"roles": ["tavletta-admin"]}}
    cookie = session_of(sign_in(site, claims=claims)[2])
    headers = {"Cookie": cookie, **PAGE, **JSON}
    status, _response, body = call(
        site.base, "DELETE", "/api/v1/labels/x.tavletta", headers=headers
    )
    assert status == 403 and body["error"]["code"] == "origin_not_allowed"
    headers["Origin"] = site.origin
    status = call(site.base, "DELETE", "/api/v1/labels/x.tavletta", headers=headers)[0]
    assert status == 404  # through: there is no such label


def test_mcp_takes_no_session(site):
    cookie = session_of(sign_in(site)[2])
    headers = {"Cookie": cookie, **PAGE, **JSON, "Origin": site.origin}
    status = call(site.base, "POST", "/mcp", headers=headers, raw=b"{}")[0]
    assert status == 401


def test_tokens_work_as_before(make_site):
    site = make_site(token=TOKEN)
    status, _response, body = call(site.base, "GET", "/api/v1/me", token=TOKEN)
    assert status == 200 and body["name"] == "token" and body["session"] is False
    assert call(site.base, "GET", "/api/v1/me", token="wrong" * 4)[0] == 401
    assert call(site.base, "GET", "/api/v1/me")[0] == 401
    # a wrong token beside a valid session: the token counts
    cookie = session_of(sign_in(site)[2])
    assert me(site, cookie, Authorization="Bearer " + "w" * 20)[0] == 401
    assert me(site, cookie, Authorization="Bearer " + TOKEN)[2]["name"] == "token"


def test_without_a_token_only_sessions_get_in(site):
    assert call(site.base, "GET", "/api/v1/me")[0] == 401
    assert call(site.base, "GET", "/api/v1/me", token=TOKEN)[0] == 401
    assert call(site.base, "GET", "/api/v1/health")[0] == 200
    assert call(site.base, "GET", "/")[0] == 200


def test_a_cookie_sent_twice_is_refused(site):
    cookie = session_of(sign_in(site)[2])
    assert me(site, cookie + "; " + cookie)[0] == 401
    assert me(site, "tavletta-session=" + "z" * 43)[0] == 401


def test_a_new_sign_in_gets_a_new_session(site):
    old = session_of(sign_in(site)[2])
    location, cookies = start(site)
    code = site.fake.authorize(location)
    state = parse_qs(urlsplit(location).query)["state"][0]
    headers = {"Cookie": "tavletta-login=%s; %s" % (value(cookies["tavletta-login"]), old)}
    _status, response, _ = call(
        site.base, "GET", "/oidc/callback?code=%s&state=%s" % (code, state), headers=headers
    )
    new = session_of(set_cookies(response))
    assert new != old
    assert me(site, old)[0] == 401 and me(site, new)[0] == 200


# --- signing out and expiry ----------------------------------------------------------------


def test_signing_out(site):
    cookie = session_of(sign_in(site)[2])
    headers = {"Cookie": cookie, **JSON}
    assert call(site.base, "POST", "/oidc/logout", headers=headers, raw=b"{}")[0] == 403
    headers |= PAGE
    assert call(site.base, "POST", "/oidc/logout", headers=headers, raw=b"{}")[0] == 403
    headers["Origin"] = site.origin
    status, response, body = call(site.base, "POST", "/oidc/logout", headers=headers, raw=b"{}")
    assert status == 200
    target = urlsplit(body["redirect"])
    assert body["redirect"].startswith(site.fake.issuer + "/logout?")
    query = parse_qs(target.query)
    assert query["post_logout_redirect_uri"] == [site.origin + "/"]
    assert query["client_id"] == [CLIENT_ID] and query["id_token_hint"][0].count(".") == 2
    assert value(set_cookies(response)["tavletta-session"]) == ""
    assert me(site, cookie)[0] == 401
    # again, or without a session: the page itself
    status, _response, body = call(site.base, "POST", "/oidc/logout", headers=headers, raw=b"{}")
    assert status == 200 and body == {"redirect": "/"}
    assert call(site.base, "GET", "/oidc/logout")[0] == 405


def test_a_provider_without_logout(site):
    site.fake.discovery = {"end_session_endpoint": None}
    cookie = session_of(sign_in(site)[2])
    headers = {"Cookie": cookie, **JSON, **PAGE, "Origin": site.origin}
    assert call(site.base, "POST", "/oidc/logout", headers=headers, raw=b"{}")[2] == {
        "redirect": "/"
    }


def test_sessions_expire(make_site):
    clock = Clock()
    site = make_site(clock=clock, session_seconds=3600.0, idle_seconds=600.0)
    cookie = session_of(sign_in(site)[2])
    clock.now += 599
    assert me(site, cookie)[0] == 200
    clock.now += 599  # in use: the idle time started again
    assert me(site, cookie)[0] == 200
    clock.now += 601
    assert me(site, cookie)[0] == 401
    cookie = session_of(sign_in(site)[2])
    for _ in range(7):
        clock.now += 500
        assert me(site, cookie)[0] == 200
    clock.now += 500  # over an hour in all
    assert me(site, cookie)[0] == 401


def test_a_sign_in_left_too_long_expires(make_site):
    clock = Clock()
    site = make_site(clock=clock)

    def late(query, cookie):
        clock.now += 601
        return query, cookie

    assert failure(site, tamper=late) == "expired"


# --- where the page is: proxies, HTTPS, public_url ----------------------------------------


def test_behind_a_trusted_proxy(make_site):
    site = make_site()
    site.server.config = replace(
        site.server.config, trusted_proxies=(ipaddress.ip_network("127.0.0.1/32"),)
    )
    proxy = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "labels.example.com"}
    location, cookies = start(site, headers=proxy)
    query = parse_qs(urlsplit(location).query)
    assert query["redirect_uri"] == ["https://labels.example.com/oidc/callback"]
    login = cookies["__Host-tavletta-login"]
    assert "Secure" in login and "Path=/" in login and "Domain" not in login
    code = site.fake.authorize(location)
    headers = proxy | {"Cookie": "__Host-tavletta-login=" + value(login)}
    path = "/oidc/callback?code=%s&state=%s" % (code, query["state"][0])
    _status, response, _ = call(site.base, "GET", path, headers=headers)
    session = set_cookies(response)["__Host-tavletta-session"]
    assert "Secure" in session and "HttpOnly" in session
    cookie = "__Host-tavletta-session=" + value(session)
    good = {"Cookie": cookie, **PAGE, **proxy, "Origin": "https://labels.example.com"}
    assert call(site.base, "GET", "/api/v1/me", headers=good)[0] == 200
    bad = dict(good, Origin=site.origin)
    assert call(site.base, "GET", "/api/v1/me", headers=bad)[0] == 403


def test_proxy_headers_of_others_are_ignored(site):
    proxy = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "labels.example.com"}
    location, cookies = start(site, headers=proxy)
    assert parse_qs(urlsplit(location).query)["redirect_uri"] == [site.origin + "/oidc/callback"]
    assert "tavletta-login" in cookies


def test_plain_http_is_refused_beyond_this_computer(make_site):
    site = make_site()
    site.server.config = replace(
        site.server.config, trusted_proxies=(ipaddress.ip_network("127.0.0.1/32"),)
    )
    proxy = {"X-Forwarded-Proto": "http", "X-Forwarded-Host": "labels.example.com"}
    status, response, _ = call(site.base, "GET", "/oidc/login", headers=proxy)
    assert response.getheader("Location") == "/#/signin-error/failed"
    assert not response.headers.get_all("Set-Cookie")
    proxy = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "evil.example/x"}
    status, response, _ = call(site.base, "GET", "/oidc/login", headers=proxy)
    assert response.getheader("Location") == "/#/signin-error/failed"


def test_public_url(make_site):
    site = make_site(public_url="https://labels.example.com")
    status, response, _ = call(site.base, "GET", "/oidc/login?return=%23/printers")
    assert status == 303
    assert response.getheader("Location") == (
        "https://labels.example.com/oidc/login?return=%23%2Fprinters&moved=1"
    )
    # sent there once only, even if the address still looks another
    status, response, _ = call(site.base, "GET", "/oidc/login?moved=1")
    assert status == 303 and response.getheader("Location").startswith(site.fake.issuer)
    # through the proxy the request is at public_url: the page's own origin
    site.server.config = replace(
        site.server.config, trusted_proxies=(ipaddress.ip_network("127.0.0.1/32"),)
    )
    proxy = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "labels.example.com"}
    cookies = sign_in(site, headers=proxy, prefix="__Host-")[2]
    cookie = "__Host-tavletta-session=" + value(cookies["__Host-tavletta-session"])
    assert me(site, cookie, Origin="https://labels.example.com")[0] == 200
    assert me(site, cookie, Origin=site.origin)[0] == 403
    assert me(site, cookie)[0] == 200  # GET without Origin: the page header is enough


@pytest.mark.parametrize(
    "asked",
    [
        "https://evil.example/",
        "//evil.example",
        "/\\evil.example",
        "#/x\r\nSet-Cookie: a=b",
        "#/" + "a" * 600,
        "javascript:alert(1)",
        "",
    ],
)
def test_no_open_redirect(site, asked):
    location, cookies = start(site, after=asked)
    code = site.fake.authorize(location)
    state = parse_qs(urlsplit(location).query)["state"][0]
    headers = {"Cookie": "tavletta-login=" + value(cookies["tavletta-login"])}
    path = "/oidc/callback?code=%s&state=%s" % (code, state)
    assert call(site.base, "GET", path, headers=headers)[1].getheader("Location") == (
        "/#/templates"
    )


def test_the_answers_leak_nothing(site, caplog):
    with caplog.at_level("DEBUG"):
        _status, _location, cookies = sign_in(site, claims={"nonce": "other"})
        sign_in(site)
    text = caplog.text
    assert "code=" not in text and "state=" not in text and "eyJ" not in text
    assert CLIENT_SECRET not in text
    _status, response, _ = call(site.base, "GET", "/oidc/login")
    assert response.getheader("Cache-Control") == "no-store"
    assert response.getheader("Referrer-Policy") == "no-referrer"
    assert repr(site.login.settings).count(CLIENT_SECRET) == 0


def test_the_provider_class_alone(fake):
    provider = Provider(settings(fake))
    assert provider.end_session_url("", "http://127.0.0.1/") is not None
    provider = Provider(settings(fake, issuer="http://127.0.0.1:9/realms/none"))
    assert provider.end_session_url("t", "/") is None
