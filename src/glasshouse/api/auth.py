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
methods: only the literal `cross-site` is refused, so curl and older
clients (which send no such header) keep working.

`/static` is deliberately NOT exempt: browsers cache Basic credentials
per (origin, realm), so after the first page's challenge every
subresource request is answered from the credential cache with no
second prompt - which is why `REALM` must never change.
"""

from __future__ import annotations

import base64
import binascii
import secrets

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


class DemoAuthMiddleware:
    def __init__(self, app: ASGIApp, *, username: str, password: str) -> None:
        self.app = app
        self._username = username.encode("utf-8")
        self._password = password.encode("utf-8")

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
            name == b"sec-fetch-site" and value == b"cross-site" for name, value in scope["headers"]
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in EXEMPT_PATHS:
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
