# Print server: REST API, MCP and web page

`tavletta serve` prints labels without a window, for other programs and
for AI clients. It renders with Pango and Cairo like the command line and
needs no display and no GTK. The code is in `tavletta/server/`; the user
guide is `site/docs/server.md`.

The server is built for network printers first: a printer list
(`printers.toml`) names the printers clients may use, by name; named
access tokens with roles (`tokens.toml`) say who may do what. Finding
printers on the network (mDNS) and USB are optional.

## Quick start

`tavletta serve` prints for other programs, without a window or display:
a REST API under `/api/v1` (JSON; the OpenAPI description at
`/api/v1/openapi.json`) to list printers and labels, ask a printer for its
state, render a preview, and print with values, data rows and copies,
with each named printer's settings (print method, print upside down,
quality …) from `printers.toml`. Jobs
are queued, one per printer at a time, and can be canceled between two
labels; counters are saved as on the command line. A web page at `/`
does the same for people in a browser ([Web page](#web-page)). `--mcp`
serves the same as MCP tools at `/mcp`; `tavletta mcp` speaks MCP on
standard input and output for Claude Desktop and Claude Code:

```sh
tavletta serve --labels-dir ~/Labels --token-file ~/.config/tavletta/token --mcp
claude mcp add tavletta -- tavletta mcp --labels-dir ~/Labels
```

Only label and data files inside the labels folder are used. Network
printers come first: `--printers printers.toml` names the printers clients
may use, by name (searching the network only with `--discover`), and
`--tokens tokens.toml` gives each device or person its own access token
with a role (preview, print, admin), made with `tavletta token new`; the
file keeps only hashes, and deleting an entry revokes it. The server
listens on 127.0.0.1 and refuses other addresses without an access token
(`Authorization: Bearer`). A Dockerfile, Compose files (network printers;
an override for the host's network, mDNS and USB) and a systemd user unit
are in `packaging/`. Details, API reference and security notes: the sections below.

The server is published as a container image for amd64 and arm64, with
build provenance and a signature
([how to check them](#container)):

```sh
docker pull ghcr.io/joshii-h/tavletta:1.1.2
```

## Options

```sh
tavletta serve [--host 127.0.0.1] [--port 8790] [--labels-dir DIR] [--mcp]
               [--printers printers.toml | --allow-printer ADDR] [--discover]
               [--tokens tokens.toml | --token-file FILE] [--allow-origin URL]
               [--max-body MB] [--max-labels N] [--trusted-proxy CIDR]
               [--no-web-ui] [--oidc oidc.toml]
tavletta mcp [--labels-dir DIR] [--printers printers.toml | --allow-printer ADDR]
             [--discover] [--max-labels N]
tavletta token new NAME [--role preview|print|admin] [--tokens FILE]
                        [--printer NAME ... --printers FILE] [--expires DATE]
                        [--description TEXT] [--scrypt | --from-stdin]
```

| Option | Meaning |
|---|---|
| `--host` | address to listen on; `127.0.0.1` by default. Any other address needs a token. A name is resolved once and only its first address is used (`localhost`: usually 127.0.0.1, not ::1 as well) |
| `--port` | `8790` by default |
| `--printers` | `printers.toml`: the printers clients may use, by name; nothing else can be printed on. Not with `--allow-printer` |
| `--discover` | search the network for printers (mDNS); off by default |
| `--tokens` | `tokens.toml`: named access tokens with roles. Not with `--token-file` |
| `--token-file` | one access token for everything (role admin); else the variable `TAVLETTA_TOKEN`; at least 16 characters |
| `--labels-dir` | the only folder label and data files are read from; `~/.local/share/tavletta/labels` by default (made if missing) |
| `--mcp` | MCP (Streamable HTTP) at `/mcp` as well |
| `--oidc` | `oidc.toml`: people sign in to the web page with an OpenID Connect provider ([below](#signing-in-with-openid-connect-oidctoml)); tokens keep working for programs. Not with `--no-web-ui`; needs PyJWT with cryptography (not in the Flatpak); with `--trusted-proxy` it needs `public_url` |
| `--no-web-ui` | no web page at `/` (and `/ui/`): those paths answer 404, as before there was one; for servers that only serve programs |
| `--allow-origin` | web origin whose pages may call the server (`https://app.example`): CORS preflights are answered and the answers carry `Access-Control-Allow-Origin`; repeatable |
| `--allow-printer` | without a printer list: only these printers, compared with the host of the address (never the whole address). A pattern of only digits, dots and `*` is an address pattern: four IPv4 parts, `*` standing for whole parts (`192.168.1.*`, `10.*`, `*.5`, `*.*`); a network as Python's ipaddress reads it (`192.168.1.0/24`, `2001:db8::/32`) is one too. Address patterns match IP addresses only, a host name if every address it resolves to matches one of them (so `192.168.1.evil.example` is not in `192.168.1.*`, and `*.*` matches `printer.local` only by the addresses it resolves to, not by its name). Any other pattern is a name pattern (`*.local`, `cups.example.com`) and matches host names. The server then prints on the first address the resolver gives (only that one; there is no trying the others), not on the name (a name looked up again could point elsewhere; an `ipps` printer must then accept its IP address). `*` alone allows everything without a lookup. A pattern that can never match is refused at startup: a network that is none (`10.0.0.0/33`), an address pattern with a part over 255 or a leading zero (`010.1.1.1`), a `*` within a part (`10*`), more than four parts, or fewer than four without a `*` (`1.2.3`, `10`); repeatable |
| `--max-body` | largest request body in MB, 4 by default (parsed JSON takes many times its size); also the largest label file to upload and data file to read (base64: three quarters of it) |
| `--max-labels` | most labels in one print job, copies times rows; 10 000 by default, on label sheets at most 999 (413 `too_many_labels`) |
| `--trusted-proxy` | a reverse proxy, best its own address (`172.30.0.10/32`), whose `X-Forwarded-For` names the client: the rate limit, the 8 requests per client and the log use that address (all header lines together, ports taken off; anything that is no address stops the search at the proxy), the proxy itself is not held to 8 connections. `0.0.0.0/0` or `::/0` (everyone) is warned about. With a proxy, publish the server's port nowhere else; repeatable |

Without `printers.toml`, printer settings (address, safety margin, cut,
mode, colour) come from the app's `settings.json`, as for `tavletta print`,
and a request may name any address (limited by `--allow-printer`).

## Printers (printers.toml)

```toml
[printers.shipping]
backend = "cab"                      # printer model: vc500w, cab, system, plugins
host = "cab-shipping.example.com"    # address or printer URI
description = "Shipping labels"
location = "Warehouse, gate 2"
media = "100 x 50 mm"                # what is loaded, for people
default = true                       # when a request names no printer
max_copies = 200                     # copies of each label, at most
safe_mm = 1.5                        # settings as in the app, for this printer

[printers.kitchen]
backend = "vc500w"
host = "192.0.2.50"
cut = "full"                         # as the model offers: full, half, none
mode = "vivid"                       # vivid, color
data_format = "jpeg"
color = { brightness = 0, red = 0, green = 0, blue = 0 }

[printers.inventory]
backend = "cab"
host = "cab-it.example.com"
media = "57 x 32 mm, ribbon"

[printers.inventory.settings]         # the printer settings of the app
method = "transfer"                  # print method: printer, transfer, direct
upside_down = true                   # print turned by 180 degrees (cab: O R)
offset_mm = 0.5                      # the print moved along the feed (cab)
labels_per_job = 250                 # labels of a series sent as one job (cab)
```

- Names: letters, digits, `.`, `_`, `-`, up to 64 characters. `backend` and
  `host` are required; `cut`, `mode` and `data_format` must be ones the
  model has, `safe_mm` 0–10, colour steps −3 to 3. At most one default.
- Read strictly at startup: an unknown key, a wrong type, an unknown model
  or address, `cut` on a model that does not cut, `color` on a model
  without colour settings, control characters in a text stop the server
  with the file, the key and the problem, for example `printers.toml:
  printers.kitchen.cut: expected one of: full, half, none`. Changes need
  a restart.
- `[printers.<name>.settings]`: the printer settings the app keeps for the
  model, with the same keys and values as in `settings.json`, and used the
  same way (`PrinterSettings`): the model's own settings (cab: `method`,
  `upside_down`, `offset_mm` from −5 to 5, `labels_per_job` from 1 to 1000; ZPL and TSPL: `dpi`, `media`, `finish`, `darkness`,
  `speed`, `upside_down`; EPL2: `dpi`, `media`, `darkness`, `upside_down`; ESC/POS: `dpi`, `upside_down`; DYMO: `darkness`,
  `upside_down`; NIIMBOT: `media`, `density`), `quality` (Brother QL:
  `standard`, `high`), the extras (P-touch: `chain`, `high_res`,
  `feed_mm`) and `outlines` on a label sheet (system printer). Only what
  the model has: anything else stops the server like an unknown key. A
  setting left out is the model's default (for `method`: as set on the
  printer). Every print to that printer uses them, through the REST API
  and MCP alike. The printer's status tells both: `media.print_method` as
  set on the printer, `media.job_print_method` as its jobs print (a cab
  set to thermal direct, with `method = "transfer"`: `direct` and
  `transfer`); the same for `media.media_type` and `media.job_media_type`
  (a ZPL printer set to gap labels, with `media = "mark"`: `gap` and
  `mark`).
- With the list, `printer` in a request is a name from it; the label is
  moved to that printer's model as in the app. Without `printer` the
  default printer is used, or the only one; else printing is refused
  (`no_printer`), a preview uses the label's own model. `host` in a
  request is refused (`host_given`): printers are chosen by name only.
  More copies than `max_copies` are refused (`too_many_copies`).
- `GET /printers` lists the printers of the list a token may use, each
  asked for its state at once (`status`; `UNREACHABLE` with the error if it
  does not answer; `?status=false` skips asking). `GET
  /printers/{name}/status` asks one. What a printer answered (or its
  error) is remembered for 5 s for both, one question at a time per
  printer; messages are made for each request in its language. Previews
  and print requests ask a printer that tells of its labels too, for the
  warning `other_labels_loaded` (below).
- `packaging/docker/printers.example.toml` is a starting point.

## Access tokens and roles (tokens.toml)

```toml
[tokens.shipping-pc]
hash = "sha256:6b86b2…"              # never the token itself
role = "print"                       # preview, print or admin; required
id = "3f9c0a1b2c4d"                  # written by token new: finds the entry at once
printers = ["shipping"]              # only these (needs --printers); all by default
expires = 2027-12-31                 # valid through that day; or a date and time
description = "PC at gate 2"
```

| Role | May |
|---|---|
| `preview` | list printers and labels, describe labels, previews, printer state |
| `print` | as `preview`, and print; see and cancel its own jobs |
| `admin` | as `print`, and see and cancel every job |

- `tavletta token new shipping-pc --role print --tokens tokens.toml`
  makes a random token, `tav_<id>_<secret>` (12 hex digits, 43 random
  characters), adds its SHA-256 hash and the id to the file and prints
  the token once. The file is made with mode 600 if new and locked while
  the entry is added (two at once lose none); the result is checked as
  the server reads it before it is written; names are quoted, so a dot in
  one stays part of the name. `--printer` (repeatable, with `--printers`
  to check the names), `--expires 2027-12-31`, `--description`.
  `--from-stdin` takes a token you chose from standard input instead
  (asked for without echo on a terminal; printable ASCII, at least 16
  characters; not printed again, a line says it was added; refused if it
  is in the file already under another name, or if it looks like a
  made one, `tav_` with an id: it would be looked for by that id, which
  no entry has) and keeps it as an scrypt
  hash, which slows down guessing a chosen token;
  `--scrypt` does that for a made one too (random tokens need no slow
  hash). The default file is `~/.config/tavletta/tokens.toml`.
- The file is read strictly: the role is required, the same hash (also an
  scrypt hash copied with its salt) or id under two names is refused (the
  same chosen token hashed twice cannot be seen in the file; `token new`
  refuses it), scrypt parameters must be digits
  within the memory scrypt may use (salt at least 8, hash 16 to 64
  bytes). A file every user may change is refused, one others may read
  is warned about.
- A token with an id is checked against the entry with that id only;
  entries with an id are never tried for other tokens. A token with an id
  no entry has is compared with a dummy entry too, so the answer takes as
  long as for a known id. A token without an id is compared with the
  entries without an id: SHA-256 first, then scrypt one after the other,
  at most two scrypt checks at once. An id that is not 12 hexadecimal
  digits is warned about and ignored (the token still works, found the
  slow way). A known token whose entry changed is looked up again.
  `token new` checks the file's mode as the server does, also of an empty
  file.
- Revoking: remove the entry. The server reads the file again when it
  changes (inode, time or size; checked with every request) and after
  `SIGHUP` (the handler only sets a flag, installed before the server
  starts; the next request reads). A file
  that became unusable lets no token in until it is fixed, so a revoked
  token never stays valid because of a typo elsewhere; the error is
  logged. Tokens known before stay known when their entry is unchanged.
- An expired token is refused like a wrong one (401). A job of another
  token is "not found" for a `print` token (404); a `preview` token gets
  403 (`forbidden`) for print and job requests and for a `host` of its
  own; a printer a token may not use is "not found" (404), like one that
  does not exist, so the names of the others stay unknown. Without
  `printer` in a request, the default among the printers the token may
  use is chosen, or the only one.
- `--tokens` without `--printers` (or `--allow-printer`) is warned about at
  startup: `print` tokens can then print on any address.
- Each job keeps the name of its token (`owner`); the log names the token
  with every request and job, never the token itself.
- `--token-file` (or `TAVLETTA_TOKEN`) stays for simple setups: one token,
  role admin, named `token`. Without any token (loopback only) requests
  count as `local`, role admin.

## Signing in with OpenID Connect (oidc.toml)

`--oidc oidc.toml` lets people sign in to the web page with the
organisation's identity provider (Keycloak, Authentik, Microsoft Entra
ID, Okta, Zitadel, Google …): any provider that follows OpenID Connect
Core and Discovery. Their roles at the provider become Tavletta's roles.
Access tokens (`tokens.toml`, `--token-file`) stay as they are, for
programs and MCP clients; the page offers both, or only the provider.

```toml
[oidc]
issuer = "https://id.example.com/realms/example"   # as the provider names itself
client_id = "tavletta"
client_secret_file = "/etc/tavletta/oidc-secret"   # mode 600, the secret alone
public_url = "https://labels.example.com"          # the page's address in the browser
provider_name = "Example ID"                       # "Sign in with Example ID"
roles_claim = "realm_access.roles"                 # where the role names are

[oidc.roles]                                       # names at the provider
preview = ["tavletta-preview"]
print = ["tavletta-print"]
admin = ["tavletta-admin"]
```

| Key | Meaning |
|---|---|
| `issuer` | the provider's issuer, exactly as its `/.well-known/openid-configuration` names it; `https://` (`http://` only for an address of this computer, for testing) |
| `client_id`, `client_secret_file` | the client registered at the provider (confidential); the file holds the secret only (read at startup, a file others can read is warned about) |
| `token_auth` | how the secret is sent to the token endpoint: `client_secret_basic` (default) or `client_secret_post`. `private_key_jwt` is not supported |
| `public_url` | the page's origin as the browser sees it (`https://host[:port]`, no path). Required with `--trusted-proxy` (the server does not start without it), recommended behind every reverse proxy; without it the origin comes from `Host`, or from the last value of `X-Forwarded-Proto` and `X-Forwarded-Host` of a `--trusted-proxy` (the one it appended), lower case and without the scheme's own port (`:443`) |
| `provider_name` | the button's text: "Sign in with …" (default `OpenID Connect`) |
| `scopes` | `["openid", "profile", "email"]` by default; must contain `openid` |
| `roles_claim` | required: the claim with the role or group names, a path through nested objects: `realm_access.roles`, `resource_access.tavletta.roles`, `groups`, `roles`. For a name with a dot: a list (`["resource_access", "my.client", "roles"]`). The claim is a text, a list of texts, or an object whose keys are the names |
| `[oidc.roles]` | for `preview`, `print`, `admin`: the names (a text or a list) at the provider. The highest role someone has counts; at least one role must be mapped |
| `username_claim` | names the person in the log and as the owner of jobs, as `oidc:<value>` (no token name can have a colon): `preferred_username` by default, else `sub`. Must be unique and stable at the provider |
| `name_claim` | shown on the page: `name` by default |
| `printers_claim` | a claim with the printer names someone may use (needs `--printers`; names not in `printers.toml` are ignored; no claim: no printer) |
| `[oidc.printers]` | instead: printers per role or group name, `"*"` for all: `tavletta-lager = ["lager-cab"]`. Someone gets the printers of all their names in the table, none if none is in it (needs `--printers`) |
| `userinfo` | `true`: the claims of the userinfo endpoint too (for providers that put groups only there); the ID token's claims win |
| `token_login` | `false`: the page offers only the provider (tokens still work for the API) |
| `session_hours`, `idle_minutes` | a session ends after 8 hours, or after 60 minutes without a request (0.1 to 168 hours, 1 minute to a week). Where admins sign in, shorter is better (e.g. 2 hours, 15 minutes): signing out at the provider, a disabled account or a removed role count only once the Tavletta session has ended (there is no back-channel logout) |
| `ca_file` | certificates to trust for the provider (an internal CA) besides the system's |

Everything is read strictly, as `tokens.toml`: an unknown key, a wrong
type, a printer that is not in `printers.toml` stops the server with the
file and key. Changes need a restart (sessions are in memory and end
with it). PyJWT with cryptography must be installed (`pip install
tavletta[oidc]`; Debian and Fedora: `python3-jwt`, `python3-cryptography`;
the container image and the server packages have them). The Flatpak has
not, and nothing can be installed into it: for `--oidc` run the server
from the deb or rpm package, the container image or pip; `tavletta serve
--oidc` in the Flatpak says so. A server with `--oidc` may listen beyond
loopback without a token: every request then needs a session or a
token. Behind a reverse proxy give it with `--trusted-proxy` (and
`public_url`): without, every sign in comes from the proxy's address and
shares its limits, which the server warns about at startup when it
listens beyond loopback.

**How it works.** The page shows "Sign in with <provider_name>"; that is
`GET /oidc/login?return=#/…`, which redirects to the provider with the
authorization code flow: PKCE with S256, a random `state` and `nonce`
(256 bits each), the scopes, and `redirect_uri = <public_url>/oidc/callback`.
A sign in started at another address than `public_url` is sent there
first (once). The provider redirects back to `/oidc/callback`, which

- takes the sign in of that `state` (once; at most 10 minutes old) and
  checks that the same browser started it: a second cookie,
  `__Host-tavletta-login`, holds a secret whose SHA-256 the sign in
  keeps, so a callback address sent to someone else does not sign them
  in as another person;
- refuses an `iss` parameter of another issuer (RFC 9207) and an error
  answer (`access_denied`: "Signing in was canceled");
- exchanges the code at the token endpoint with the secret and the PKCE
  verifier (no redirects followed; 10 s; at most 1 MB);
- checks the ID token with PyJWT: signed with RS256/384/512,
  PS256/384/512, ES256/384/512 or EdDSA by a key of the provider's
  `jwks_uri` (`none` and HS* are refused; a key with an `alg` is used for
  that algorithm only), `iss` and `aud` exactly, `azp` when present or
  when there are several audiences, `exp`, `iat` and `nbf` with 60 s of
  leeway, `sub`, and the `nonce`;
- maps the claims: without a mapped role there is no session, the page
  says "you are signed in at <provider>, but have no access" and the log
  names the user;
- starts a new session (a new id on every sign in; a session cookie sent
  along is ended) and redirects to the page the user was on (only
  addresses of the page, `#/…`: no open redirect).

The provider's settings (discovery) and keys are fetched when first
needed (and at startup, in the background: a provider that is away does
not stop the server) and kept for an hour; a key id not seen before
fetches the keys again, at most every 30 s (keys rotate). An address
may start 30 sign ins a minute (`/oidc/login`; a callback for no sign in
under way counts as one), else 429 for a minute; this is counted apart
from wrong tokens, and a sign in the provider fails (away, an answer that
does not check out) is not counted at all, so people behind one address
do not lock each other out while it is away. At most 100 sign ins of one
address are under way at once (10 minutes each); more are refused
(429), none of another browser is dropped for them, and a browser that
starts again replaces its own sign in under way. With `--oidc`, a
request with neither a token nor a session (the page loading while
signed out) is 401 but not counted, so people behind one address do not
block each other.

**The session.** The cookie `__Host-tavletta-session` is `Secure`,
`HttpOnly`, `SameSite=Lax`, `Path=/`, no `Domain` (the `__Host-` prefix
makes browsers insist on that), and holds 256 random bits; the server
keeps the session in memory under the SHA-256 of that value (at most
10 000 sessions, 20 per person, the least used gives way). It counts for
the REST API only, never for `/mcp` (MCP clients use tokens), and only
from the page itself: with `X-Tavletta-Request`, an `Origin` (required
for anything but GET) that is the page's own (`public_url`), and a
`Sec-Fetch-Site` of `same-origin` if the browser sends one; a request
with `Authorization` uses its token, not the cookie. Roles and printers
are taken at sign in and kept for the session: a change at the provider
(a role removed, the account disabled, a sign out there) counts from the
next sign in; Tavletta does not take part in back-channel or front-channel
logout, so keep `session_hours` and `idle_minutes` short where that
matters. A restart of the server ends every session. Over plain HTTP (only `http://localhost`
or `127.0.0.1`, for testing) the cookies are `tavletta-session` and
`tavletta-login` without `Secure`; any other plain HTTP origin is refused
("Signing in did not work"; the log says why).

**Signing out** (`POST /oidc/logout`, from the page) ends the session and
sends the browser to the provider's `end_session_endpoint` with
`id_token_hint`, `client_id` and `post_logout_redirect_uri =
<public_url>/`, if the provider offers one, which ends the session there
too and comes back to the page.

**At the provider** (Keycloak as the example; other providers have the
same settings under other names):

- a client `tavletta`, OpenID Connect, *Client authentication* on
  (confidential), *Standard flow* only (no direct access grants, no
  implicit flow), *PKCE method* S256;
- *Valid redirect URIs* `https://labels.example.com/oidc/callback`,
  *Valid post logout redirect URIs* `https://labels.example.com/`, *Web
  origins* `+`;
- the secret from *Credentials* into `client_secret_file`;
- the roles, either as client roles of `tavletta` (`tavletta-preview`,
  `tavletta-print`, `tavletta-admin`) or as realm roles, given to users or
  groups. Keycloak puts both only into the access token by default; Tavletta
  reads the ID token (and, with `userinfo = true`, the userinfo), so the
  client needs a mapper (*Client scopes* → `tavletta-dedicated` → *Add
  mapper* → *By configuration*): *User Client Role*
  (`oidc-usermodel-client-role-mapper`), client `tavletta`, claim name
  `resource_access.${client_id}.roles`, multivalued, *Add to ID token* on,
  and `roles_claim = "resource_access.tavletta.roles"`; or *User Realm
  Role* (`oidc-usermodel-realm-role-mapper`), claim name
  `realm_access.roles`, *Add to ID token* on, and `roles_claim =
  "realm_access.roles"`. Without the mapper everyone gets "no access".
  The issuer is `https://<keycloak>/realms/<realm>`.

Other providers: Authentik sends the user's groups as `groups`
(`roles_claim = "groups"`, the groups named as in `[oidc.roles]`), its
issuer is `https://<authentik>/application/o/<application slug>/`;
Microsoft Entra ID sends app roles as `roles`, Zitadel project roles as
`urn:zitadel:iam:org:project:roles` (an object: its keys are the names).

Tested against Keycloak 26 (sign in, roles, no role, signing out at the
provider too).

## REST API

JSON in and out under `/api/v1`. The full description is served at
`/api/v1/openapi.json` (OpenAPI 3.1, `tavletta/server/openapi.py`), to a
token of any role, like the list of labels (it shows nothing a `preview`
token cannot see otherwise, but the version). `docs/openapi.yaml` and the
reference page `site/docs/api.md` are written from it by
`tools/openapi_docs.py`; a test fails when they are out of date.
`HEAD` of a `GET` request (and of the web page and its files) answers
with the same status and headers, without the body; `/mcp` and `/oidc/`
take no `HEAD` (405).

| Request | Answer |
|---|---|
| `GET /health` | `{"status": "ok", "version": …}`; the only request without a token (besides the files of the web page) |
| `GET /openapi.json` | the OpenAPI description |
| `GET /me` | the token's `name`, `role` and `printers`, whether the server needs a token, and the `limits` of a request (labels in one job, on sheets, copies, data rows, columns, cells, body bytes); for someone signed in with OpenID Connect also `display_name` and `session: true` |
| `GET /printers?status=false` | with `printers.toml`: its printers the token may use, with `name`, `printer` (model), `host`, `description`, `location`, `media`, `default`, `max_copies` and `status` |
| `GET /printers?discover=false` | without it: printer models: id, name, kind (`tape`, `labels`, `pages`), resolution, tape widths or label sizes, the address set in the app (`configured_host`) and, with `--discover`, the printers found on the network (`found`; remembered for 30 s) |
| `GET /printers/{name}/status` | the printer's state: `state`, `error` (code and message), `dpi`, `modes`, `media` (tape, remaining, label pitch, the size of the labels loaded (`label_mm`, if the printer tells it), print width, print method and media type set on the printer, cassette; from the server's settings for the printer: `job_print_method` and `job_media_type`, what the server's jobs print with and how they find the labels, which the printer's `method` and `media` settings may choose for each job, else as set on the printer). Without a printer list `{id}` is a model and `?host=` the address (default: set in the app). 502 if the printer cannot be reached |
| `GET /labels` | label files in the labels folder and below: `name`, `bytes`, `modified` |
| `GET /labels/{name}` | elements with their names (what `set` fills in), `variables`, `builtin_variables`, `columns` of the placeholders, `data_file` (if usable), size, counter, features, `on_sheet` (printed on label sheets: at most 999 labels in one job) |
| `PUT /labels/{name}` | admin: upload a label file (the body is the `.tavletta` file, `Content-Type: application/json`); 201, or 200 with `?replace=true` for an existing one; 409 `label_exists`. See [Uploading labels](#uploading-labels) |
| `DELETE /labels/{name}` | admin: delete a label file (its data file stays); 204 |
| `POST /data` | read a data file sent along (`{"content": base64, "name", "sheet", "header"}`): `kind` (`csv`, `ods`, `xlsx`), `delimiter`, `sheets`, `sheet`, `columns`, `rows`, every cell as the text it shows (leading zeros and number formats kept). Nothing is kept on the server; the web page sends the rows it chose as `data_rows` |
| `POST /preview` | PNG of the label filled in; `"format": "json"` gives width, height, warnings, the PNG in base64 and `geometry`: where the elements are (boxes in mm, `tavletta/editing.py`) and the image's resolution (`null` for `"view": "printer"`); `"view": "printer"` the image as the printer gets it. Warning codes also in `X-Tavletta-Warnings` |
| `POST /print` | 202 and the job (`Location: /api/v1/jobs/{id}`); 200 if it finished within `wait` seconds |
| `GET /jobs/{id}` | `state` (`queued`, `waiting` for the label file, `printing`, `done`, `failed`, `canceled`), `printed` of `total` labels, `rows_printed`, `printer_name`, `owner`, `error` (code, message), `remark`, `warnings`, times |
| `DELETE /jobs/{id}` | cancel: a waiting job at once, a printing one after the label being printed (cab: after the job of up to `labels_per_job` labels being printed). 409 if it has finished |

Fields of `POST /preview` and `POST /print` (unknown fields are refused):

| Field | |
|---|---|
| `label` | name in the labels folder, `.tavletta` may be left out; subfolders with `/` |
| `label_content` | or the label itself: the JSON of an `.tavletta` file, checked like a file that is opened. Its data file is ignored, its counter never saved |
| `printer` | with `printers.toml` the printer's name; else a printer model id, if not the label's own (the label is fitted as in the app) |
| `set`, `set_literal` | `{"name": "value"}` as `--set` and `--set-literal`: element names, variables, `date` |
| `counter_start` | counter of the first label |
| `data_rows` | serial labels: `[{"Column": "value"}, …]`; numbers and `null` are taken as text and an empty cell |
| `data_file`, `sheet` | or a CSV, ODS or XLSX file in the labels folder. Without both, the label's own data file is used if it lies beside the label in the labels folder |
| `row` | preview only: the data row shown, from 1 |
| `host`, `copies`, `rows`, `no_save_counter`, `wait` | print only: address, only without `printers.toml` (default: set in the app, else with `--discover` the only printer found), copies of each label (1–9999, or the printer's `max_copies`), data rows (`3-10`, `5`, `7-`, `-4`), do not save the counter, seconds to wait for the job (0–300) |

Errors are `{"error": {"code": "…", "message": "…"}}`. The code is stable
(`label_not_found`, `invalid_field`, `label_unprintable`, `printer_jam`, …);
the message is translated into the language of `Accept-Language` (the
first one there is a catalog for; English ends the list). A failed job
keeps its cause, so `GET /jobs/{id}` translates it for each request.

Checks are those of `tavletta print`: an empty label, unknown or unclosed
variables, dates out of range, elements or images that cannot be drawn,
placeholders without a column and codes a row cannot make are refused
(422) before anything is queued; other warnings are returned with the job.

Like the window and `tavletta print`, the server warns when the printer
has other die-cut labels loaded than the label's: labels of another size,
or another label distance measured (warning `other_labels_loaded`; a ZPL
printer's hint suggests calibrating it, its distance may be from an
earlier roll). The label prints anyway. Only printers that tell of their
labels are asked for it: cab, ZPL and Brother QL with die-cut labels. A
preview and a print request for a printer of `printers.toml` take the
printer's latest answer if it is of the last minute, without waiting; if
the latest was an error, nothing is known (the roll may have changed).
The printer is asked anew in the background once that is older than 5 s
(not while a job of the server prints on it). Only a printer the server
knows nothing of yet, or whose answer is older than a minute, is waited
for, after the label is rendered, up to a second; a printer that does not
answer by then gives no warning, and one whose latest question ended in
an error is not waited for again. When the job prints, the warning is
replaced by what the printer reports then, if it tells of the loaded
labels (a busy printer, or one that has not measured the labels, leaves
it as it was); without `printers.toml` only the job warns. Not for a
job that finds the labels otherwise than the printer reports
(`job_media_type` is not `media_type`: a continuous job on a printer set
to gap labels).

Example, with the example labels of `docs/examples` as the labels folder
(`serial.tavletta` has the placeholders `{Code}`, `{Name}`, `{Shelf}` and
`{Box}`):

```sh
TOKEN=$(cat ~/.config/tavletta/token)
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8790/api/v1/labels/serial
curl -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"label": "serial", "data_rows": [{"Code": "B-07-1", "Name": "Tea", "Shelf": "B-07", "Box": "1"}]}' \
     -o preview.png http://127.0.0.1:8790/api/v1/preview
curl -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"label": "serial", "wait": 60, "data_rows": [
          {"Code": "B-07-1", "Name": "Tea", "Shelf": "B-07", "Box": "1"},
          {"Code": "B-07-2", "Name": "Rice", "Shelf": "B-07", "Box": "2"}]}' \
     http://127.0.0.1:8790/api/v1/print
```

`set` fills in elements by their name (an element named in the app) and
variables of your own (`{=lot}`); `GET /labels/{name}` lists both.

An inventory tool printing one asset label: the label as a file in the
labels folder (`"label": "asset-tag"`) or sent along (`label_content`, the
JSON of a label file), the asset's fields as one data row, the printer by
its name in `printers.toml`, and the number for `{=counter}` if the label
has one:

```sh
curl -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"label": "asset-tag", "printer": "it-cab", "copies": 1,
          "data_rows": [{"AssetNumber": "100001", "Location": "Store 2"}],
          "wait": 30}' \
     http://127.0.0.1:8790/api/v1/print
```

The answer is the job (`state` `done` once printed within `wait`, else
look it up with `GET /jobs/{id}`). `{AssetNumber}` in the label takes the
row's value, in either layout. The walkthrough for such a tool, with
series, retries and code in Python and C#: `site/docs/api-guide.md`; every
request and error code: `site/docs/api.md` and `docs/openapi.yaml`.

`{=counter}`: the label file keeps the number of the next label and the
server saves it after printing (not for `label_content`, nor with
`no_save_counter`). For a tool that keeps its own numbers, put the number
into the data row (`{AssetNumber}` in the label) or send `counter_start`
(with `no_save_counter`): one source of truth, and two servers or a
restored backup cannot hand out a number twice.

## Print jobs

- A job is checked when it is asked for and queued for its printer
  (backend and address). Each printer prints one job at a time; jobs of
  different printers run side by side. At most 100 jobs wait or print at
  once (429 above that); finished jobs are kept for 24 hours (at most
  1000).
- Printing goes through `job.py` and `serial.py`, as in the window and on
  the command line: per-copy variables render each copy, serial labels are
  a job per row.
- The counter: the label file is held (a lock per file in the server and
  the file lock of `model.locked()`, which the command line uses as well)
  from reading the counter when the job starts until the next value is
  saved, also after a printer error. The date of `{=date}` is the moment
  the job starts, unless `date` is set. A file whose counter could not be
  saved is refused before printing (409), as on the command line.
- Cancel is looked at between two labels: between copies rendered one by
  one and between data rows. Copies the printer makes of one image cannot
  be stopped. A cab printer gets a series as jobs of up to
  `labels_per_job` labels (250 by default, see [cab.md](cab.md)): cancel
  is looked at between those jobs, a job that has started is printed in
  full.
- `SIGTERM` (systemd, `docker stop`): waiting jobs are canceled, a printing
  one stops after its label and saves its counter.

## Web page

`tavletta serve` also serves a web page at `/` for people who print from a
browser: no app to install, the same server, tokens and printers. It is
plain HTML, CSS and JavaScript (`tavletta/server/web/`, about 110 KB, no
framework, no build step) and is part of the Python package, so every
installation has it (pip, the container image, the Debian and Fedora
packages, Flatpak). `--no-web-ui` turns it off.

- **Signing in**: with an access token. The page keeps it in the tab's
  `sessionStorage` only (gone with the tab; never in `localStorage`, a
  cookie or the address) and sends it as `Authorization: Bearer` with
  every request. "Sign out" forgets it and everything shown or entered.
  A 401 (token expired or revoked) asks for a token again and keeps what
  was entered. Without a token on the server (loopback only) the page
  needs no sign in. With `--oidc` the page offers "Sign in with
  <provider>" too, or only that ([Signing in with OpenID
  Connect](#signing-in-with-openid-connect-oidctoml)); the header shows the
  person's name and role, and "Sign out" also signs out at the provider.
- **Roles**: the page asks `GET /me` and leaves out what the role may not
  do: a `preview` token sees templates, printers and previews, the print
  buttons say why they cannot be used; `print` prints; `admin` also
  uploads and deletes templates. The server checks every request anyway.
- **Printers**: their state (text and symbol, never colour alone), the
  labels or tape loaded, problems; a printer that cannot be reached
  cannot be chosen for printing.
- **Templates**: the labels folder with a preview, size and fields of
  each. A template with data fields is previewed with the first row of
  its own data file, else with sample values, never the placeholders
  (`{Inventory number}` is longer than most values and runs off the
  label): digits for fields named like a number (number, Nr., ID,
  counter, code) and as many as a code of only digits takes (EAN, UPC,
  ITF-14), else the field's name, shortened while the boxes of the
  preview's `geometry` run off the label or into another text or code.
  A data file that is missing (`data_file_missing` of
  `GET /labels/{name}`) or unreadable is a note under the preview. Print one: the fields filled in (data columns, variables, named
  elements), a preview that follows the input; the elements a field
  fills are outlined in the preview, and pointing at an element names
  it (the boxes come with the preview, `geometry`).
- **Series**: values from a CSV, XLSX or ODS file (`POST /data` reads it
  as the app does: cells as shown, `00300` stays `00300`) or a pasted
  list, their columns assigned to the template's fields beside a preview
  of the first label (the field in use is outlined in it, each field says
  its value on the first label, each column's choice its first values;
  columns named as a field, case and spaces aside, are assigned
  automatically and the page says so), and all rows,
  some rows (`1-20, 25, 30-`, or checkboxes) or the rows whose values lie
  in ranges of a field. Without data, ranges make the values: an
  inventory number from 3000 to 5685 is 2686 labels; leading zeros keep
  the width (`00300`–`00450`), text around the number stays
  (`INV-0001`–`INV-0250`), a step is optional, and `3000-5685` typed
  into "From" is split. Several fields are combined row by row, never
  every combination. The count updates as one types; the first and last
  label are shown before printing, and the limits (`--max-labels`, the
  printer's `max_copies`, 999 on label sheets, `--max-body` for the rows
  sent) before the request is sent. From 100 labels on, a dialog with
  the number, template and printer asks first. The job shows its
  progress and can be canceled (labels printed stay printed); after a
  cancel or a failure it continues with the first row not printed in
  full. The result says when not all numbers from the first to the last
  label were printed.
- **Languages**: every language of the app (`po/*.po`); the texts come
  from its catalogs (`/ui/messages.json?lang=de`, by default in the
  language of the browser; a region without a catalog takes the
  language's, `zh-Hant` and `zh-MO` traditional Chinese), from the
  compiled ones of the installation or, in a checkout, from `po/`
  directly. Arabic is laid out right to left (`dir` of messages.json).
  Numbers follow the page's language in the region the browser names
  for it, German always Swiss as `2'686`. Times are on a 24-hour clock
  in every language (`15:49`): browsers tell neither the system's clock
  setting nor its region, and English browsers say `en-US` wherever they
  are. The scripts' texts are
  `t("…")` calls, which `xgettext --language=JavaScript --keyword=t`
  extracts (see CONTRIBUTING.md).
- **Accessibility**: one `<h1>` per view, focused after each change of
  view, landmarks, a skip link, visible labels and hints, errors under
  the field and in a summary at the top that links to them, live regions
  for counts and progress (not for every label), `<dialog>` for
  confirmations, everything by keyboard, focus visible, light and dark
  from the system, forced colours, reduced motion, from 320 px wide.

### Uploading labels

`PUT /api/v1/labels/{name}` (role `admin`) takes a `.tavletta` file as it
is, `Content-Type: application/json`. It is checked as the app opens a
file (`Label.load()` of exactly these bytes, written to a hidden file in
the labels folder that is never listed), held to the limits of a label
sent with a request (200 elements, 50 images and codes; document pages
only with `--allow-pdf-pages`) and of `--max-body`, then put in place in
one step (a hard link, so a name taken meanwhile is not overwritten; a
file system without hard links: the name is taken first). The name is
one file in the folder itself: letters of any script, digits, spaces and
`. _ - ( ) + ,`, up to 100 characters and 255 bytes, not starting with
a dot or a space, taken as given (not normalised), no name Windows
keeps for devices (`con`, `nul`, `com1` …); anything else (`..`, `/`,
`\`, NUL) is 400. An existing label is
replaced only with `?replace=true`, and only if it is a file (not a
folder or link). `DELETE /api/v1/labels/{name}` removes a label file of
the folder (links leading out of it are not found; a file that cannot be
removed, a read-only folder, is 500 `not_deleted`). Neither happens
while a job of this server waits or prints for that label (409
`label_in_use` at once: it would print a label deleted, or print the
layout replaced and then save its counter into the new file), nor while
the window or the command line prints it (the file lock; 409
`label_locked` after 5 s). A print request checked just before its label
is deleted is not queued (404 `label_not_found`); one checked while the
label is replaced prints the layout it was checked with. Both are logged
with the token's name. Uploads a server left behind when it was killed
while checking one (`.upload-…` in the folder) are removed at the next
start once they are an hour old.

The upload and `/data` are not MCP tools: an AI client fills in and
prints the labels it is given; changing the labels everyone prints with
is for an administrator, by the web page or the REST API with an admin
token, not for a model that can be talked into it by the text it reads.
A client of MCP sends rows as `data_rows` itself.

### Security of the page

- The page and its files (`/`, `/ui/…`) are answered without a token:
  they are the program, the same for everyone. Only these files, by
  name; nothing else of the package or the disk.
- `Content-Security-Policy: default-src 'none'; script-src 'self';
  style-src 'self'; img-src 'self' data: blob:; connect-src 'self';
  frame-ancestors 'none'; base-uri 'none'; form-action 'none'`: no inline
  script or style, no `eval`, no frames, requests to this server only.
  Also `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  `Cross-Origin-Opener-Policy` and `-Resource-Policy: same-origin`,
  `X-Content-Type-Options: nosniff`.
- What the page shows from files and labels (CSV cells, names, messages)
  is set as text (`textContent`), never as markup.
- The page sends `X-Tavletta-Request` and JSON with every request: the
  checks of the API against other web pages ([Security](#security))
  apply to it unchanged. Signed in with OpenID Connect, its session
  cookie (HttpOnly, the page never sees it) counts only with that header
  and from the page's own origin, never for MCP.
- `/oidc/info`, `/oidc/login` and `/oidc/callback` are answered without a
  token (only with `--oidc`; 404 otherwise); their redirects carry
  `Referrer-Policy: no-referrer`, and nothing of the callback's address
  (code, state) is logged.
- `/data` answers at most 500 000 cells and 16 MB of names and values
  (413): a small file with a long cell or column name in every row would
  otherwise make a huge answer. A file is read in a slot of its own, not
  a render slot (previews and prints do not wait for a spreadsheet): one
  at a time, one per client address (503 `busy`, after up to 20 s for
  the slot), and within 10 s (413): a few MB of XLSX can unpack to
  millions of XML elements, which the app reads, but a server should not
  spend half a minute on for anyone with the role preview.
- The files are sent with an `ETag` and `Cache-Control: no-cache`: a
  browser asks again and gets 304 while they are unchanged, and the new
  files right after an update.

## MCP

The same abilities as tools for AI clients (Model Context Protocol):

| Tool | |
|---|---|
| `list_printers` | `GET /printers` |
| `printer_status` | `GET /printers/{id}/status` |
| `list_labels` | `GET /labels` |
| `describe_label` | `GET /labels/{name}` |
| `preview_label` | the image as `image/png` content, warnings as text and `structuredContent` |
| `print_label` | `POST /print`; waits 10 s for the job unless `wait` says otherwise |
| `job_status` | `GET /jobs/{id}`, optionally waiting (`wait`) |
| `cancel_job` | `DELETE /jobs/{id}` |

Arguments are the fields of the REST API. Results carry the JSON as text
and as `structuredContent`; API errors are tool results with `isError` and
the translated message (stdio: the system language). Over HTTP the tools
act with the request's token: `tools/list` leaves out `print_label`,
`job_status` and `cancel_job` for a `preview` token, and a session belongs
to the token that opened it (another token gets 404). stdio counts as
`local` (admin); `--printers` works there too.

Protocol (`tavletta/server/mcp.py`, no dependency):

- JSON-RPC 2.0, one message per request (no batches).
- Both eras of the protocol: `2026-07-28` (stateless: `_meta` carries
  `io.modelcontextprotocol/protocolVersion` and `clientCapabilities` on
  every request, `server/discover`, results with `resultType`) and the
  `initialize` handshake of `2025-11-25` and `2025-06-18` (the requested
  version if supported, else `2025-11-25`). `2025-03-26` is not offered:
  it needs JSON-RPC batches.
- `ping`, `tools/list`, `tools/call`; `notifications/initialized` is
  accepted, `notifications/cancelled` ends the wait of `print_label` or
  `job_status` (the job goes on; `cancel_job` stops it). No resources,
  prompts or sampling.
- **stdio** (`tavletta mcp`): one JSON message per line; logs on stderr,
  stdout carries nothing else. Tool calls run in up to four threads.
- **Streamable HTTP** (`tavletta serve --mcp`, `/mcp`): POST answers with
  `application/json` (no SSE stream), notifications with 202. GET is 405.
  Handshake clients get an `Mcp-Session-Id` (24 random bytes) with the
  initialize answer and must send it afterwards (400 without, 404 for an
  unknown or expired session, one hour unused); DELETE ends it. Modern
  requests need `MCP-Protocol-Version`, `Mcp-Method` and, for
  `tools/call`, `Mcp-Name` (Base64 form decoded) matching the body (400,
  `-32020` otherwise); an unsupported version is 400 with `-32022` and
  the supported versions, an unknown method 404.
- The same token, Origin and Host checks as the REST API.

Why not the official `mcp` package: it brings pydantic, starlette,
uvicorn, anyio, httpx and more into a program that needs eight tools and
two transports; the standard library is enough and keeps the server's
dependencies those of the app.

### Claude Code

```sh
claude mcp add tavletta -- tavletta mcp --labels-dir ~/Labels
# or over HTTP, to a running `tavletta serve --mcp`:
claude mcp add --transport http tavletta http://127.0.0.1:8790/mcp \
    --header "Authorization: Bearer $(cat ~/.config/tavletta/token)"
```

### Claude Desktop

`claude_desktop_config.json` (Linux: `~/.config/Claude/`):

```json
{
  "mcpServers": {
    "tavletta": {
      "command": "tavletta",
      "args": ["mcp", "--labels-dir", "/home/me/Labels"]
    }
  }
}
```

With the Flatpak: `"command": "flatpak"`, `"args": ["run",
"--command=tavletta", "xyz.joshuahirsig.Tavletta", "mcp", "--labels-dir",
"/home/me/Labels"]` (the folder must be visible in the sandbox).

## Security

- **Listening**: `127.0.0.1` by default. A host name is resolved once
  and the server binds that address (no second lookup that could change).
  Another address than loopback is refused without a token; behind a
  reverse proxy with TLS for access from other computers (the server
  speaks plain HTTP).
- **Token**: `Authorization: Bearer`, compared with
  `hmac.compare_digest` (named tokens: their SHA-256 or scrypt hash; a
  token found once is remembered by its SHA-256 until the file changes).
  Tokens must be printable ASCII. Ten wrong tokens from one address (an
  IPv6 address by its /64 network, a link-local one by itself) within a minute make further wrong
  ones 429 until a minute after the tenth; the 429s are not counted. A
  valid token always works: a blocked address still has its SHA-256 and
  known tokens checked, only the slow scrypt check of tokens not seen
  before is skipped. Behind a reverse proxy all clients share the
  proxy's address unless it is given with `--trusted-proxy`. A token file
  readable by others is warned about.
  Without a token (loopback only) every local program can print.
- **Web pages**: on a loopback address, requests whose `Host` is not
  exactly a loopback name with an optional port (`localhost`,
  `127.0.0.1`, `[::1]`; no user name, nothing after it) are refused
  (421), against DNS rebinding. A request with an `Origin` is refused
  (403) unless it is the server's own or allowed with `--allow-origin`.
  POST (also to `/mcp`) needs `Content-Type: application/json` (415), so
  a page cannot send one without a CORS preflight, which is answered only
  for allowed origins. Without a token, a page could still make the
  browser load `GET /printers/{id}/status` as an image, so requests that
  contact printers need the header `X-Tavletta-Request` (any value; 403
  without), which a page cannot add.
- **Printers on the network (SSRF)**: the server connects to printer
  addresses, which is its job. Without `printers.toml`, a client with
  access can make it connect to any address it names (ports 9100, 631 …
  of other hosts in its network). Use `printers.toml` (or at least
  `--allow-printer`) for anything but a single-user setup. Addresses with
  a user name or password (`user@host`) are refused, and answers never
  show them (an address set in the app is shown without them).
- **Files**: names are relative to the labels folder; absolute paths,
  `..`, hidden files and folders, backslashes and NUL are refused, and the
  real path (links followed) must lie inside the folder, so a link to a
  file elsewhere is "not found". Label files end in `.tavletta`, data files
  are tables (`.csv`, `.tsv`, `.txt`, `.ods`, `.xlsx`). A label's own data
  file is used only if it lies beside the label inside the folder; a label
  sent as content never reads a file.
- **Input**: request bodies need a `Content-Length` (ASCII digits) of at
  most 4 MB (`--max-body`; no chunked bodies), bodies over 512 KB are
  parsed two at a time; labels are checked by the model (`Label.from_dict`,
  sizes clamped); data rows and data files keep the limits of
  `tavletta/merge/limits.py` (100 000 rows, 1000 columns, 2 000 000 cells);
  values up to 100 000 characters, 1000 `set` entries, 9999 copies.
- **Image files**: only PNG, JPEG, GIF, BMP, WebP and TIFF are read (EPS
  would run Ghostscript, ICO and ICNS are decoded before their size is
  known). A file whose header says it unpacks to more than 80 megapixels
  is refused when it is opened, before it is decoded; Pillow's own check
  is turned off (a warning filter is not safe in threads). The EXIF
  orientation is applied when the image is decoded, and the size is
  checked again afterwards.
- **Codes**: QR codes and every other symbology come from Zint
  (`zint-bindings`; QR from segno if Zint is missing) as a matrix of
  modules, which the renderer draws with every module the same number of
  whole printer dots, centred in its place; barcodes are therefore a
  little wider than the 1.0 renderer drew them. A linear barcode whose
  modules are thinner than 0.17 mm is drawn and warned about
  (`barcode_narrow`), one of a single printer dot is `code_thin`: it may
  not scan. A Code 128 holds at most 200 characters; a longer one is
  refused for printing in the window, on the command line and by the
  server.
- **Shipping-label PDF pages**: pages in `label_content` are refused
  (422 `pages_not_allowed`) unless the server runs with
  `--allow-pdf-pages`; then PDFs are drawn by Poppler in a separate,
  limited process (see "Document pages" below). The Docker image has
  Poppler; a native install needs `gir1.2-poppler-0.18` / `poppler-glib`.
- **Fonts**: a text whose font fontconfig does not have is drawn in another
  one and warned about (`font_missing`, in the preview and in the job);
  generic names (`Sans`, `Serif`, `Monospace`) always count, and so does
  an installed metric-compatible stand-in (Liberation Sans for Arial,
  Carlito for Calibri, Gelasio for Georgia: `typeset.METRIC_ALIASES`). See the
  installation guide (`site/docs/server-install.md`) for the fonts of the
  image and how to add more.
- **Rendering**: a label of a request has at most 200 elements, 5000
  characters and 100 lines in each text or code (filled in, and in the
  cells of the columns it uses), then at most 50 images and codes (each
  takes time to draw); more is 413 (`label_too_large`), the first of
  these limits that is broken is told. Within limits:
  - no image made while rendering may exceed 40 megapixels (a text
    block, a decoded image file, the label) and the label not the
    printer's longest length, checked before anything large is drawn;
  - the elements of a label together stay within these 40 megapixels as
    they are drawn;
  - the image files of a label (each counted once) may unpack to at most
    20 megapixels together, checked from their headers before any is
    decoded;
  - each image file is decoded once per render, nothing is kept between
    renders (the window keeps a few in caches); neither are codes,
    symbol paths or text sizes: no client's content stays in the
    process beyond its request.

  Elements are measured first and drawn at their final size (a text
  fitted to a page is not drawn large and scaled down). An element over
  the limits refuses the label (413), it is not drawn as a crossed-out
  box. Every render takes one of two render slots: previews, checks
  before printing and the renders of print jobs, which wait their turn.
  A REST preview or print request is parsed only once it has a slot, so
  waiting requests hold their body, not the parsed label; the printer of
  a print request is looked up after the slot is given back (a name
  lookup or a search can take seconds). An MCP request over HTTP with a
  body over 512 KB is parsed in a render slot too; a smaller one within
  the two-at-a-time parse limit, its tool takes a slot when it renders;
  `print_label` and `job_status` give the slot back while they wait for
  the job. stdio (`tavletta mcp`) has one client, the process's own: its
  lines are parsed as they come. With eight requests waiting, the next
  one gets 503 (`busy`) at once, as one that waits 20 s. The server draws
  a small text in its main thread before it serves: PyGObject sets up
  Pango on first use, which is not safe in several threads at once (the
  window renders in the main thread, where GTK has done it). Labels
  scaled down to a fixed length or page are drawn at their final size and
  look a little sharper than before; others render exactly as before.
- **Roles**: see [tokens.toml](#access-tokens-and-roles-tokenstoml). The token's name, never the token,
  is logged and kept with each job.
- **Printers**: with `printers.toml` clients choose by name and cannot
  make the server connect anywhere else; per token the printers can be
  limited. Without it an address is printable ASCII without spaces, at
  most 512 characters, and `--allow-printer` limits the addresses a client
  may make the server connect to (the backends talk to whatever address
  they are given, as in the app). The network is searched only with
  `--discover`.
- **Resources**: at most 32 connections and requests at once (503 `busy`
  beyond); when all are taken, four more connections are let in that may
  only ask for `/health` and have 1 s to send it, so a stuck server still
  answers its health check. Clients that open slow connections can still
  keep these four busy for a second each; a health check that fails may
  simply be tried again. At most 8 connections from one address (an
  IPv6 address by its /64 network); no thread to be had is a 503 too. 30 s
  to send the whole request: a watchdog stops reading at the deadline
  however the client trickles (inside a line, a header or the body), and
  what was read then is not taken as a request (408 or the connection
  closed); a body the client stops sending is 400, two `Content-Length`
  headers are 400; the answer then has 60 s of its own. Jobs: 100
  waiting or printing, 20 of them for one named token (of tokens.toml;
  one shared token or none is everyone, only the 100 apply), about 256
  MB of requests held by waiting jobs, 10 000 labels per job; a worker
  per printer ends after 60 s without jobs, at most 16 printers at once;
  a new printer takes the place of the one idle longest (429
  `queue_full` beyond any of these). A finished job keeps only its
  outcome, not the request. MCP sessions: 10 per named token (its own
  oldest makes room), 100 in all; only a token's own session ever makes
  room, a full table of others' sessions is 503. MCP bodies are parsed
  within the two-at-a-time limit too. A trusted proxy's requests without
  a usable X-Forwarded-For are held to the overall limits only, not to
  the 8 per client. The systemd unit and the Compose file limit
  memory (1 GB) and processes, and `MALLOC_ARENA_MAX=2` keeps threads
  from holding on to memory a large label used.
- **Logs** (stderr, the journal): client address, token name (`-` before
  a token was accepted; `oidc:<user>` for someone signed in to the page),
  method, path without the query (`-` for both if
  the request line could not be read), status; for each job its id, token name, label,
  printer and outcome; sign ins and outs, and why a sign in failed. Never
  headers, tokens, cookies, codes or label content.
- No shell is run and no path from a request is used outside the labels
  folder.

### Document pages (`--allow-pdf-pages`)

A label sent along (`label_content`) may not hold document pages
(`page` elements: a carrier's PDF or an image) unless the server runs
with `--allow-pdf-pages`; otherwise it is 422 (`pages_not_allowed`).
Labels of the labels folder may hold them either way. Pages count as
images towards the 50 images and codes; within the render limits:

- an image page counts towards the 20 megapixels of image files (from
  its header, also a TIFF frame before it is decoded) and is decoded once
  per render, never kept; only PNG, JPEG, GIF, BMP, WebP and TIFF are
  read, in the window too (never EPS, which would run Ghostscript);
- a PDF of at most 10 MB is read and drawn by Poppler in a child process
  (`tavletta/shipping/pdfchild.py`) limited to 10 s of processor time,
  20 s in all and an address space of 512 MB plus 24 bytes per pixel of
  the drawing, without core files and with 256 files open at most; one
  that breaks these limits refuses the label (413), also when Poppler or
  GLib abort because the memory limit stopped an allocation; a damaged
  one or another crash of the child is a warning (`page_unreadable`).
  The child gets only the environment that finds Python, its libraries
  and fonts, not the server's token (`TAVLETTA_TOKEN`) or anything else.
  Poppler never runs in the server's process; only the drawings the
  children send back are kept, for the preview or print job they belong
  to (see below);
- the PDF pages of one label take 30 s in all at most, counted while
  their children run (not while the render waits for its slot): a label
  whose pages need longer is refused (413) like a page that breaks its
  own limit, so 50 slow pages cannot hold a render slot for minutes. Each
  row of a series is a label of its own with its own 30 s, and what the
  children drew is kept until the preview or print job ends (at most 32
  drawings and 32 MB per job, 64 MB for all jobs together; beyond that
  nothing more is kept): the same page on every row is drawn once.

Each page drawn starts a child (about 0.1 s, a black-and-white printer
two), so the option is off by default.

## Server build

- `packaging/docker/Dockerfile`: Debian slim with its security updates,
  Python, PyGObject, Pango and Cairo (no GTK), Poppler for the PDF pages
  of shipping labels (about 35 MB with its libraries), the app in a venv,
  user `tavletta` (uid 10001), `MALLOC_ARENA_MAX=2`, a `HEALTHCHECK` on
  `/api/v1/health`, `LICENSE` and `THIRD_PARTY_NOTICES.md` in
  `/usr/share/doc/tavletta`. No volumes: mount the labels at `/labels` and `printers.toml`, `tokens.toml` (and, if wanted,
  `tavletta/settings.json`) at `/config` (`XDG_CONFIG_HOME`), readable by
  whatever user the container runs as. It listens on all interfaces of
  the container, so it does not start without a token.
- `packaging/docker/compose.yaml`: the published image
  `ghcr.io/joshii-h/tavletta:1.1` ([Container image](#container)), network
  printers, running as the owner of `config/` and `labels/` (`user:`, `TAVLETTA_UID` and
  `TAVLETTA_GID`, 1000 by default), 1 GB memory, 128 processes. `config/` with
  `printers.toml` and `tokens.toml` mounted read-only as a folder (a single
  mounted file would keep showing the old file after `token new` replaced
  it), port on `127.0.0.1` only, read-only file system, no capabilities,
  no discovery.
- `packaging/docker/compose.host.yaml`, an override
  (`-f compose.yaml -f compose.host.yaml`): the host's network
  (`network_mode: host`, listening on 127.0.0.1), `--discover` with the
  host's D-Bus socket for mDNS through Avahi, `/dev/usb` for backends that
  talk USB themselves, with the groups `lp` and `dialout`. CUPS queues of the host (USB printers set up there
  too) are at `ipp://localhost:631/printers/<queue>`; name them in
  `printers.toml`.
- `packaging/docker/compose.build.yaml`, an override
  (`-f compose.yaml -f compose.build.yaml up -d --build`): builds the image
  from the checkout as `tavletta-server` instead of pulling it.
- `packaging/docker/smoke-test.sh IMAGE`: starts the image as
  `compose.yaml` does (read-only, no capabilities, not root) with a
  throwaway token, asks for `/health` and a preview of
  `docs/examples/shelf.tavletta` and waits for the healthcheck.
- `packaging/systemd/tavletta.service`: a user service on `127.0.0.1:8790`
  with MCP and a token file; it makes the labels and config folders,
  then runs sandboxed: the system read-only (`ProtectSystem=strict`), the
  home folder read-only (`ProtectHome=read-only`) but for those two
  folders, `~/.ssh` and `~/.gnupg` not visible, no capabilities, only the
  system calls of ordinary services (`@system-service`), kernel
  settings, modules, logs, clock, host name and cgroups protected, other
  processes invisible, no namespaces, only Internet, Unix and netlink
  sockets, `MemoryMax=1G`, `TasksMax=128`, `MALLOC_ARENA_MAX=2`.
  `systemctl --user reload tavletta` reads tokens.toml again (without
  tokens.toml the reload does nothing, the server goes on). The unit is
  `Type=notify`: the server tells systemd it is ready once it listens, so
  a reload is not sent while it starts; `tavletta serve` also ignores
  SIGHUP before it imports anything (the first thing the `tavletta`
  launcher does) until its handler is installed. The unit and the
  package must be updated together: a unit of `Type=notify` with an
  older Tavletta that never says it is ready is stopped after the start
  timeout, and an older unit with a newer Tavletta loses the readiness
  (the notification is ignored, nothing breaks).

```sh
cd packaging/docker
mkdir -p config labels && cp printers.example.toml config/printers.toml
docker compose pull
docker run --rm --user "$(id -u)" -v ./config:/config --entrypoint tavletta \
    ghcr.io/joshii-h/tavletta:1.1 token new shipping-pc --role print \
    --tokens /config/tokens.toml --printers /config/printers.toml
docker compose up -d
```

For a quick try without files:

```sh
docker run -e TAVLETTA_TOKEN="$(openssl rand -hex 32)" \
    -v ~/Labels:/labels -p 127.0.0.1:8790:8790 ghcr.io/joshii-h/tavletta:1.1
```

## Container image { #container }

`.github/workflows/docker.yml` builds the image on GitHub (the push mirror)
for `linux/amd64` and `linux/arm64` and publishes it as
`ghcr.io/joshii-h/tavletta`:

| Tag | |
|---|---|
| `1.1.2` | a release, from its tag `v1.1.2` |
| `1.1` | the latest bug fix release of 1.1 (what `compose.yaml` uses) |
| `latest` | the latest release (not a pre-release) |
| `edge` | the state of `main`, not a release |

```sh
docker pull ghcr.io/joshii-h/tavletta:1.1.2
```

Before anything is pushed the amd64 image is started with
`packaging/docker/smoke-test.sh` and scanned with Trivy; a release with a
fixable critical or high vulnerability is not published. Pull requests and
manual runs build without pushing. Each pushed image has an SBOM and SLSA
provenance (BuildKit), a GitHub build provenance attestation and a keyless
cosign signature. To check that an image was built by this workflow from
this repository:

```sh
gh attestation verify oci://ghcr.io/joshii-h/tavletta:1.1.2 --repo joshii-h/tavletta
cosign verify ghcr.io/joshii-h/tavletta:1.1.2 \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com \
    --certificate-identity-regexp '^https://github\.com/joshii-h/tavletta/\.github/workflows/docker\.yml@refs/tags/v'
docker buildx imagetools inspect ghcr.io/joshii-h/tavletta:1.1.2 --format '{{ json .SBOM }}'
```

## Roadmap

Planned for the server, not there yet:

- **Single sign-on, further**: back-channel logout (a sign out or a
  disabled account at the provider ending the Tavletta session at once)
  and `private_key_jwt` for the client; signing in with OpenID Connect
  itself is there ([oidc.toml](#signing-in-with-openid-connect-oidctoml)).
- **Audit log**: who printed what, where and when, as JSON lines in a file
  of its own, kept apart from the operational log.
- **Metrics**: Prometheus at `/metrics` (jobs by state and printer, labels
  printed, queue length, printer state, request durations), behind its own
  token or role.
