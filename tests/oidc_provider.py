"""A fake OpenID Connect provider on 127.0.0.1 for the tests: discovery,
keys, an authorization step without a browser, the token endpoint (client
secret, PKCE, redirect_uri checked as a provider does), userinfo and the
logout endpoint. Signs ID tokens with keys made for the test run."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

CLIENT_ID = "tavletta"
CLIENT_SECRET = "s3cret value/+="


@dataclass
class Key:
    kid: str
    alg: str
    private: object

    @classmethod
    def rsa(cls, kid: str, alg: str = "RS256") -> Key:
        return cls(kid, alg, rsa.generate_private_key(public_exponent=65537, key_size=2048))

    @classmethod
    def ec(cls, kid: str) -> Key:
        return cls(kid, "ES256", ec.generate_private_key(ec.SECP256R1()))

    def jwk(self) -> dict:
        public = self.private.public_key()  # type: ignore[attr-defined]
        kind = RSAAlgorithm if self.alg[:2] in ("RS", "PS") else ECAlgorithm
        data = json.loads(kind.to_jwk(public))
        return data | {"kid": self.kid, "use": "sig", "alg": self.alg}


@dataclass
class Grant:
    """What /authorize was asked: the token endpoint checks it."""

    nonce: str
    challenge: str
    redirect_uri: str
    claims: dict


@dataclass
class Fake:
    issuer: str = ""
    keys: list[Key] = field(default_factory=list)  # published
    signer: Key | None = None  # signs ID tokens
    grants: dict[str, Grant] = field(default_factory=dict)
    jwks_fetches: int = 0
    discovery: dict = field(default_factory=dict)  # overrides of the document
    userinfo: dict = field(default_factory=dict)
    token_requests: list[dict] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def claims(self, grant: Grant) -> dict:
        now = int(time.time())
        base = {
            "iss": self.issuer,
            "aud": CLIENT_ID,
            "sub": "f2b1-alice",
            "exp": now + 300,
            "iat": now,
            "nonce": grant.nonce,
            "preferred_username": "alice",
            "name": "Alice Example",
            "realm_access": {"roles": ["tavletta-print", "offline_access"]},
        }
        merged = base | grant.claims
        return {k: v for k, v in merged.items() if v is not None}  # None: left out

    def sign(self, claims: dict, key: Key | None = None, headers: dict | None = None) -> str:
        key = key or self.signer
        assert key is not None
        return jwt.encode(
            claims, key.private, algorithm=key.alg, headers={"kid": key.kid, **(headers or {})}
        )

    def authorize(self, location: str, claims: dict | None = None) -> str:
        """What a browser does at the provider: a code for this request."""
        query = {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}
        assert query["response_type"] == "code" and query["client_id"] == CLIENT_ID
        assert query["code_challenge_method"] == "S256"
        assert "openid" in query["scope"].split()
        code = secrets.token_urlsafe(16)
        with self.lock:
            self.grants[code] = Grant(
                query["nonce"], query["code_challenge"], query["redirect_uri"], claims or {}
            )
        return code


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, format, *args):  # noqa: A002
        pass

    def _json(self, status: int, body: object) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        fake = self.server.fake
        path = urlsplit(self.path).path
        if path == "/realms/test/.well-known/openid-configuration":
            base = fake.issuer
            doc = {
                "issuer": base,
                "authorization_endpoint": base + "/auth",
                "token_endpoint": base + "/token",
                "jwks_uri": base + "/certs",
                "userinfo_endpoint": base + "/userinfo",
                "end_session_endpoint": base + "/logout",
                "code_challenge_methods_supported": ["plain", "S256"],
            }
            self._json(200, {k: v for k, v in (doc | fake.discovery).items() if v is not None})
        elif path == "/realms/test/certs":
            with fake.lock:
                fake.jwks_fetches += 1
            self._json(200, {"keys": [k.jwk() for k in fake.keys]})
        elif path == "/realms/test/userinfo":
            auth = self.headers.get("Authorization", "")
            if auth != "Bearer access-1":
                self._json(401, {"error": "invalid_token"})
                return
            self._json(200, {"sub": "f2b1-alice"} | fake.userinfo)
        else:
            self._json(404, {"error": "not_found"})

    def do_POST(self):  # noqa: N802
        fake = self.server.fake
        if urlsplit(self.path).path != "/realms/test/token":
            self._json(404, {"error": "not_found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
        fake.token_requests.append(form | {"authorization": self.headers.get("Authorization")})
        if not self._client_ok(form):
            self._json(401, {"error": "invalid_client"})
            return
        with fake.lock:
            grant = fake.grants.pop(form.get("code", ""), None)
        if (
            grant is None
            or form.get("grant_type") != "authorization_code"
            or form.get("redirect_uri") != grant.redirect_uri
            or _challenge(form.get("code_verifier", "")) != grant.challenge
        ):
            self._json(400, {"error": "invalid_grant"})
            return
        token = grant.claims.pop("__token__", None) or fake.sign(fake.claims(grant))
        self._json(200, {"id_token": token, "access_token": "access-1", "token_type": "Bearer"})

    def _client_ok(self, form: dict) -> bool:
        basic = self.headers.get("Authorization", "")
        if basic.startswith("Basic "):
            pair = base64.b64decode(basic[6:]).decode()
            from urllib.parse import unquote_plus

            user, _sep, password = pair.partition(":")
            return unquote_plus(user) == CLIENT_ID and unquote_plus(password) == CLIENT_SECRET
        return form.get("client_id") == CLIENT_ID and form.get("client_secret") == CLIENT_SECRET


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    fake: Fake


def start() -> tuple[Fake, _Server]:
    server = _Server(("127.0.0.1", 0), _Handler)
    fake = Fake(issuer="http://127.0.0.1:%d/realms/test" % server.server_address[1])
    key = Key.rsa("key-1")
    fake.keys, fake.signer = [key], key
    server.fake = fake
    threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    ).start()
    return fake, server
