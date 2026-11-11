"""The demo login: one shared HTTP Basic credential in front of
everything except the deployment probes.

A pure ASGI middleware, deliberately: it runs before routing, so the
static mount, every router and even unknown paths answer 401 to an
unauthenticated caller (a crawler learns nothing about the URL space),
and it types cleanly with no wrapper overhead. It activates only when
`GLASSHOUSE_DEMO_PASSWORD` is configured - local dev and every pure
test run open, exactly as before.

Identity stays L0-honest: the login maps to one actor string
(`DEMO_USERNAME`) and nothing more; the ledger's gateway attestation
records who asserted it on every write. Cross-site protection for
Basic's ambient credentials is the `Sec-Fetch-Site` check on unsafe
methods: `cross-site` AND `same-site` are refused (the demo lives on a
subdomain, so a compromised sibling on the parent domain is same-site,
not same-origin); `same-origin`, `none` and an absent header (curl,
non-browser clients) pass.

`/static` is deliberately NOT exempt: browsers cache Basic credentials
per (origin, realm), so after the first page's challenge every
subresource request is answered from the credential cache with no
second prompt - which is why `REALM` must never change.

The agent's door (`/mcp`, `glasshouse.agents`) has its own credential and
its own gate, `AgentDoorMiddleware`: a bearer token, because an MCP
client is a program that sends a header, not a browser that answers a
Basic challenge. The demo login steps aside for that one prefix, and only
when the door is mounted; with no token configured `/mcp` is an unknown
path behind the login like any other. Each token maps to one actor string
and nothing more, the same grade of identity as the login: it does not
prove who holds it, or that two tokens are held by two people. It is not
OAuth: there is no authorisation server here to send a client to, and
the gate does not pretend there is.
"""

from __future__ import annotations

import base64
import binascii
import secrets
from contextvars import ContextVar

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

DEMO_USERNAME = "demo"
REALM = "glasshouse demo"  # constant by contract: the /static credential-cache story
EXEMPT_PATHS = frozenset({"/healthz", "/readyz"})  # the deployment probes, nothing else
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_CHALLENGE = JSONResponse(
    {"detail": "authentication required"},
    status_code=401,
    headers={"WWW-Authenticate": f'Basic realm="{REALM}", charset="UTF-8"'},
)
_CROSS_SITE = JSONResponse({"detail": "cross-site request refused"}, status_code=403)
# Both hostile Fetch Metadata classifications for a subdomain-hosted
# service: a sibling *.a115.co.uk origin is same-site, not same-origin.
_REFUSED_FETCH_SITES = (b"cross-site", b"same-site")


# Which actor the request being served was authenticated as, by its
# bearer token. Set by the door below for the duration of one request
# and read by the MCP tools (`api/app.py`): the caller's identity comes
# from the token it holds, never from anything it sends.
agent_caller: ContextVar[str | None] = ContextVar("agent_caller", default=None)

AGENT_PATH = "/mcp"
_AGENT_CHALLENGE = JSONResponse(
    {"detail": "a bearer token is required"},
    status_code=401,
    headers={"WWW-Authenticate": 'Bearer realm="glasshouse agents"'},
)


def _is_agent_path(path: str) -> bool:
    return path in (AGENT_PATH, AGENT_PATH + "/")


class AgentDoorMiddleware:
    """The agent's door and its gate in one place: a request for `/mcp`
    is answered by the MCP app, and only if it carries one of the
    configured bearer tokens; every other request passes through
    untouched. Each token belongs to one actor, and the request is served
    as that actor (`agent_caller`). A
    middleware, not a mount: a mount answers `/mcp` with a redirect to
    `/mcp/`, which a client behind a TLS proxy may follow to the wrong
    scheme, and the path an operator pastes into a client should simply
    work."""

    def __init__(self, app: ASGIApp, *, door: ASGIApp, tokens: dict[str, str]) -> None:
        self.app = app
        self._door = door
        self._accepted = [
            (b"bearer " + token.encode("utf-8"), actor) for token, actor in tokens.items()
        ]

    def _actor_for(self, presented: bytes) -> str | None:
        # Every token is compared, in constant time each, whichever
        # matches: how long the answer takes says nothing about which
        # tokens exist.
        found: str | None = None
        for expected, actor in self._accepted:
            if secrets.compare_digest(presented, expected):
                found = actor
        return found

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _is_agent_path(scope["path"]):
            await self.app(scope, receive, send)
            return
        presented = b""
        for name, value in scope["headers"]:
            if name == b"authorization":
                scheme, _, credential = value.partition(b" ")
                presented = scheme.lower() + b" " + credential.strip()
                break
        actor = self._actor_for(presented)
        if actor is None:
            await _AGENT_CHALLENGE(scope, receive, send)
            return
        inner: Scope = {**scope, "path": AGENT_PATH, "raw_path": AGENT_PATH.encode()}
        served_as = agent_caller.set(actor)
        try:
            await self._door(inner, receive, send)
        finally:
            agent_caller.reset(served_as)


class DemoAuthMiddleware:
    def __init__(
        self, app: ASGIApp, *, username: str, password: str, agent_door: bool = False
    ) -> None:
        self.app = app
        self._username = username.encode("utf-8")
        self._password = password.encode("utf-8")
        # True only when /mcp is mounted behind its own token gate.
        self._agent_door = agent_door

    def _authorised(self, scope: Scope) -> bool:
        header: bytes | None = None
        for name, value in scope["headers"]:  # ASGI header names are lowercased bytes
            if name == b"authorization":
                header = value
                break
        if header is None:
            return False
        scheme, _, payload = header.partition(b" ")
        if scheme.lower() != b"basic":
            return False
        try:
            decoded = base64.b64decode(payload.strip(), validate=True)
        except (binascii.Error, ValueError):
            return False
        username, separator, password = decoded.partition(b":")
        if not separator:  # a colon-less payload is malformed, never a 500
            return False
        # Non-short-circuiting constant-time comparison on both halves.
        return bool(
            secrets.compare_digest(username, self._username)
            & secrets.compare_digest(password, self._password)
        )

    @staticmethod
    def _cross_site(scope: Scope) -> bool:
        if scope["method"] not in _UNSAFE_METHODS:
            return False
        return any(
            name == b"sec-fetch-site" and value in _REFUSED_FETCH_SITES
            for name, value in scope["headers"]
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return
        if self._agent_door and _is_agent_path(scope["path"]):
            await self.app(scope, receive, send)
            return
        if not self._authorised(scope):
            await _CHALLENGE(scope, receive, send)
            return
        if self._cross_site(scope):
            await _CROSS_SITE(scope, receive, send)
            return
        scope.setdefault("state", {})["demo_actor"] = DEMO_USERNAME
        await self.app(scope, receive, send)


def authenticated_actor(request: Request) -> str | None:
    """The identity the gate established, or None when the gate is off
    (dev). Routes read identity through this and never re-parse the
    header: the middleware is the single authority."""
    return getattr(request.state, "demo_actor", None)
