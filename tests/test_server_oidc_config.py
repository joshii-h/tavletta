"""oidc.toml read strictly, `tavletta serve --oidc`, the claims of a user
mapped to a role, and the stores of sign ins and sessions (bounded, with
expiry)."""

from __future__ import annotations

import pytest

pytest.importorskip("jwt")

from tavletta import cli  # noqa: E402
from tavletta.server import main as server_main  # noqa: E402
from tavletta.server.config import (  # noqa: E402
    Caller,  # noqa: E402
    ConfigError,
    NamedPrinter,
)
from tavletta.server.oidc import OidcSettings, caller_for, challenge, load_oidc, origin_of  # noqa: E402
from tavletta.server.sessions import (  # noqa: E402
    LOGINS_PER_CLIENT,
    MAX_LOGINS,
    SESSIONS_PER_USER,
    Logins,
    Sessions,
)
from tavletta.server.weblogin import after, cookies  # noqa: E402

PRINTERS = {name: NamedPrinter(name, "fake-mono", name + ".local") for name in ("lager", "office")}


@pytest.fixture
def secret(tmp_path):
    path = tmp_path / "secret"
    path.write_text("s3cret\n")
    path.chmod(0o600)
    return path


def oidc_file(tmp_path, secret, extra="", roles='admin = ["tavletta-admin"]'):
    path = tmp_path / "oidc.toml"
    path.write_text(
        "[oidc]\n"
        'issuer = "https://id.example.com/realms/example"\n'
        'client_id = "tavletta"\n'
        'client_secret_file = "%s"\n'
        'roles_claim = "realm_access.roles"\n'
        "%s\n"
        "[oidc.roles]\n%s\n" % (secret, extra, roles)
    )
    return path


def test_a_small_file(tmp_path, secret):
    settings = load_oidc(oidc_file(tmp_path, secret))
    assert settings.issuer == "https://id.example.com/realms/example"
    assert settings.client_secret == "s3cret" and "s3cret" not in repr(settings)
    assert settings.roles_claim == ("realm_access", "roles")
    assert settings.roles["admin"] == {"tavletta-admin"} and not settings.roles["print"]
    assert settings.scopes == ("openid", "profile", "email") and settings.token_login
    assert settings.session_seconds == 8 * 3600 and settings.idle_seconds == 3600
    assert settings.public_url is None and settings.provider_name == "OpenID Connect"


def test_every_key(tmp_path, secret):
    extra = (
        'public_url = "https://labels.example.com/"\n'
        'provider_name = "Example ID"\n'
        'scopes = ["openid", "groups"]\n'
        'token_auth = "client_secret_post"\n'
        'username_claim = "email"\n'
        'name_claim = ["profile", "display.name"]\n'
        "userinfo = true\n"
        "token_login = false\n"
        "session_hours = 1.5\n"
        "idle_minutes = 15\n"
        'ca_file = "/etc/ssl/certs/ca.pem"\n'
    )
    path = oidc_file(tmp_path, secret, extra, 'print = "p"\nadmin = ["a", "b"]')
    with path.open("a") as file:
        file.write('[oidc.printers]\np = ["lager"]\nb = ["*"]\n')
    settings = load_oidc(path, PRINTERS)
    assert settings.public_url == "https://labels.example.com"
    assert origin_of("HTTPS://Labels.Example.COM:443/") == "https://labels.example.com"
    assert origin_of("http://[::1]:8790") == "http://[::1]:8790"
    assert settings.scopes == ("openid", "groups") and settings.token_auth == "client_secret_post"
    assert settings.name_claim == ("profile", "display.name")
    assert settings.userinfo and not settings.token_login
    assert settings.session_seconds == 5400 and settings.idle_seconds == 900
    assert settings.role_printers == {"p": frozenset({"lager"}), "b": None}
    assert settings.roles["admin"] == {"a", "b"} and str(settings.ca_file).endswith("ca.pem")


@pytest.mark.parametrize(
    ("extra", "roles", "message"),
    [
        ("", "", "map at least one role"),
        ("", 'owner = ["x"]', "unknown key"),
        ("", "admin = 5", "expected a list of texts"),
        ('public_url = "http://labels.example.com"', None, "https://"),
        ('public_url = "https://labels.example.com/tavletta"', None, "https://"),
        ('public_url = "https://user@labels.example.com"', None, "https://"),
        ('public_url = "https://labels.example.com:99999"', None, "https://"),
        ('scopes = ["profile"]', None, "expected a list of texts"),
        ('scopes = ["openid profile"]', None, "expected a list of texts"),
        ('token_auth = "private_key_jwt"', None, "expected one of"),
        ('printers_claim = "printers"', None, "--printers"),
        ('username_claim = ""', None, "expected a list of texts"),
        ("session_hours = 0", None, "expected a number"),
        ('token_login = "no"', None, "expected true or false"),
        ("colour = 1", None, "unknown key"),
    ],
)
def test_mistakes_are_named(tmp_path, secret, extra, roles, message):
    path = oidc_file(tmp_path, secret, extra, 'admin = ["a"]' if roles is None else roles)
    with pytest.raises(ConfigError) as error:
        load_oidc(path)
    assert message in str(error.value)


def test_the_issuer_needs_https_beyond_this_computer(tmp_path, secret):
    path = oidc_file(tmp_path, secret)
    path.write_text(path.read_text().replace("https://id.example.com", "http://id.example.com"))
    with pytest.raises(ConfigError, match="https://"):
        load_oidc(path)
    path.write_text(path.read_text().replace("http://id.example.com", "http://127.0.0.1:8080"))
    assert load_oidc(path).issuer.startswith("http://127.0.0.1")


def test_other_tables_and_missing_keys(tmp_path, secret):
    path = tmp_path / "oidc.toml"
    path.write_text("[server]\nx = 1\n")
    with pytest.raises(ConfigError, match="unknown key"):
        load_oidc(path)
    path.write_text("[oidc]\n")
    with pytest.raises(ConfigError, match="missing"):
        load_oidc(path)
    path = oidc_file(tmp_path, secret)
    path.write_text(path.read_text().replace('roles_claim = "realm_access.roles"\n', ""))
    with pytest.raises(ConfigError, match="roles_claim: missing"):
        load_oidc(path)
    path = oidc_file(tmp_path, secret)
    path.write_text(path.read_text().split("[oidc.roles]")[0])
    with pytest.raises(ConfigError, match="roles: missing"):
        load_oidc(path)


def test_printers_need_the_printer_list(tmp_path, secret):
    path = oidc_file(tmp_path, secret)
    with path.open("a") as file:
        file.write('[oidc.printers]\na = ["lager"]\n')
    with pytest.raises(ConfigError, match="--printers"):
        load_oidc(path)
    path.write_text(path.read_text().replace('["lager"]', '["nowhere"]'))
    with pytest.raises(ConfigError, match="Unknown printer: nowhere"):
        load_oidc(path, PRINTERS)
    path.write_text(path.read_text().replace("[oidc.roles]", 'printers_claim = "p"\n[oidc.roles]'))
    path.write_text(path.read_text().replace('["nowhere"]', '["lager"]'))
    with pytest.raises(ConfigError, match="either printers_claim"):
        load_oidc(path, PRINTERS)


def test_the_secret_file(tmp_path, secret, caplog):
    path = oidc_file(tmp_path, tmp_path / "none")
    with pytest.raises(ConfigError, match="client secret file"):
        load_oidc(path)
    secret.write_text("  \n")
    with pytest.raises(ConfigError, match="client secret file"):
        load_oidc(oidc_file(tmp_path, secret))
    secret.write_text("s3cret")
    secret.chmod(0o644)
    load_oidc(oidc_file(tmp_path, secret))
    assert "can be read by others" in caplog.text


# --- tavletta serve --oidc --------------------------------------------------------------


@pytest.fixture
def started(monkeypatch):
    found = {}

    def serve_forever(self):
        found["server"] = self
        raise KeyboardInterrupt

    from tavletta.server import http as server_http

    monkeypatch.setattr(server_http.Server, "serve_forever", serve_forever)
    monkeypatch.setattr(server_main.signal, "signal", lambda *args: None)
    monkeypatch.delenv("TAVLETTA_TOKEN", raising=False)
    return found


def test_serve_with_oidc(tmp_path, secret, started, server_env):
    path = oidc_file(tmp_path, secret)
    labels = tmp_path / "labels"
    labels.mkdir(exist_ok=True)
    code = cli.main(["serve", "--port", "0", "--labels-dir", str(labels), "--oidc", str(path)])
    assert code == cli.EXIT_OK
    config = started["server"].config
    assert config.login is not None and config.secured and config.token is None


def test_oidc_alone_may_listen_beyond_loopback(tmp_path, secret, started, server_env, monkeypatch):
    from tavletta.server import http as server_http

    monkeypatch.setattr(server_http, "binds_loopback", lambda host: False)
    path = oidc_file(tmp_path, secret)
    labels = tmp_path / "labels"
    labels.mkdir(exist_ok=True)
    base = ["serve", "--port", "0", "--labels-dir", str(labels)]
    assert cli.main([*base, "--oidc", str(path)]) == cli.EXIT_OK
    assert cli.main(base + ["--host", "192.0.2.1"]) == cli.EXIT_USAGE


def test_serve_refuses_oidc_without_the_page_or_a_good_file(tmp_path, secret, capsys):
    path = oidc_file(tmp_path, secret)
    base = ["serve", "--port", "0", "--labels-dir", str(tmp_path)]
    assert cli.main([*base, "--oidc", str(path), "--no-web-ui"]) == cli.EXIT_USAGE
    assert "--no-web-ui" in capsys.readouterr().err
    path.write_text("[oidc]\n")
    assert cli.main([*base, "--oidc", str(path)]) == cli.EXIT_FILE


def test_serve_says_when_pyjwt_is_missing(tmp_path, secret, capsys, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "jwt", None)
    path = oidc_file(tmp_path, secret)
    code = cli.main(["serve", "--labels-dir", str(tmp_path), "--oidc", str(path)])
    assert code == cli.EXIT_USAGE and "PyJWT" in capsys.readouterr().err


# --- claims to a caller ------------------------------------------------------------------


def mapping(**changes) -> OidcSettings:
    from dataclasses import replace

    base = OidcSettings(
        issuer="https://id.example.com",
        client_id="tavletta",
        client_secret="x",
        roles_claim=("groups",),
        roles={"preview": frozenset({"v"}), "print": frozenset({"p"}), "admin": frozenset()},
    )
    return replace(base, **changes)


def test_claims_to_a_caller():
    settings = mapping()
    assert caller_for(settings, {"sub": "1", "groups": ["v"]}).role == "preview"
    assert caller_for(settings, {"sub": "1", "groups": "p"}).role == "print"
    assert caller_for(settings, {"sub": "1", "groups": ["admin"]}) is None
    assert caller_for(settings, {"sub": "1", "groups": [1, None]}) is None
    assert caller_for(settings, {"sub": "1", "groups": {"p": {"org": "x"}}}).role == "print"
    caller = caller_for(settings, {"sub": "id-1", "groups": ["p"]})
    assert caller.name == "oidc:id-1" and caller.display == "id-1" and caller.named
    long = caller_for(settings, {"sub": "1", "preferred_username": "x" * 500, "groups": "p"})
    assert len(long.name) == len("oidc:") + 128


def test_pkce_challenge():  # RFC 7636 appendix B
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_return_addresses_and_cookies():
    assert after("#/print/a%20b") == "#/print/a%20b"
    assert after(None) == after("http://x") == after("/x") == "#/templates"
    assert cookies(["a=1; b=2", "c=3"]) == {"a": "1", "b": "2", "c": "3"}
    assert cookies(["a=1; a=2; junk; =x"]) == {"a": None}


# --- the stores ---------------------------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_sign_ins_are_bounded():
    clock = Clock()
    logins = Logins(clock)
    first, browser = logins.start("192.0.2.1", "#/")
    for _ in range(LOGINS_PER_CLIENT):
        logins.start("192.0.2.1", "#/")
    assert len(logins) == LOGINS_PER_CLIENT
    assert logins.take(first.state, browser) is None  # its own oldest made room
    for n in range(MAX_LOGINS + 5):
        logins.start("198.51.100.%d" % (n % 250) + str(n), "#/")
    assert len(logins) == MAX_LOGINS
    clock.now += 601
    logins.start("192.0.2.9", "#/")
    assert len(logins) == 1  # the others expired


def test_a_sign_in_is_taken_once_by_its_browser():
    logins = Logins()
    login, browser = logins.start("c", "#/x")
    assert logins.take(login.state, "other") is None
    login, browser = logins.start("c", "#/x")
    assert logins.take(login.state, browser) == login
    assert logins.take(login.state, browser) is None


def test_sessions_are_bounded_per_user():
    clock = Clock()
    sessions = Sessions(3600, 600, clock)
    caller = Caller("oidc:a", "print")
    first = sessions.create(caller, "a", "t")
    for _ in range(SESSIONS_PER_USER):
        sessions.create(caller, "a", "t")
    assert sessions.find(first) is None and len(sessions) == SESSIONS_PER_USER
    other = sessions.create(Caller("oidc:b"), "b", "t")
    assert sessions.find(other).caller.name == "oidc:b"
    assert sessions.end(other) is not None and sessions.find(other) is None


def test_a_full_session_store_drops_expired_then_the_least_used(monkeypatch):
    from tavletta.server import sessions as module

    monkeypatch.setattr(module, "MAX_SESSIONS", 3)
    clock = Clock()
    sessions = Sessions(3600, 600, clock)
    ids = [sessions.create(Caller("u%d" % n), "u%d" % n, "") for n in range(3)]
    sessions.find(ids[0])  # used: the second is now the least used
    sessions.create(Caller("u3"), "u3", "")
    assert sessions.find(ids[1]) is None and sessions.find(ids[0]) is not None
    clock.now += 601
    sessions.create(Caller("u4"), "u4", "")
    assert len(sessions) == 1
