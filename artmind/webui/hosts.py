"""Host-header allow-list for the chat-ui and admin-ui servers.

The UIs have no authentication. Bound to loopback they are still reachable by
a web page the user opens (DNS rebinding: the page's own hostname re-resolves
to 127.0.0.1, so the browser treats the UI as same-origin and the page can read
its answers, including stored conversation history). A rebound request keeps
the attacker's hostname in `Host`, so refusing every `Host` that is not a
loopback name closes that route. Bound to a non-loopback address the operator
chose to expose the port; we do not guess which names reach it, so every Host
is allowed and `exposure_warning` says what that means.
"""

import ipaddress

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]"})


def is_loopback_bind(host: str) -> bool:
    """True when binding `host` listens on loopback only."""
    name = host.strip().strip("[]").lower()
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def exposure_warning(host: str, port: int) -> str | None:
    """The startup warning for a non-loopback bind, or None when loopback."""
    if is_loopback_bind(host):
        return None
    return (
        f"WARNING: bound to {host}:{port}. This UI has no authentication: anyone who can "
        "reach the port can use it and read stored conversation history."
    )


def hostname_of(header: str) -> str:
    """The hostname in a `Host` header value, port and trailing dot dropped.
    An IPv6 literal keeps its brackets (`[::1]:8378` -> `[::1]`)."""
    value = header.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        return value[: end + 1] if end != -1 else value
    return value.split(":", 1)[0].rstrip(".")


class LoopbackHostMiddleware:
    """Answers 400 to any request whose Host is not a loopback name."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            raw = dict(scope["headers"]).get(b"host", b"").decode("latin-1")
            if hostname_of(raw) not in LOOPBACK_HOSTS:
                await PlainTextResponse("Invalid host header", status_code=400)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def guard_app(app, host: str, port: int, echo=None) -> None:
    """Apply the allow-list to `app` for a server about to bind `host`:
    loopback binds get the Host check; others get the exposure warning."""
    if is_loopback_bind(host):
        app.add_middleware(LoopbackHostMiddleware)
        return
    warning = exposure_warning(host, port)
    if echo is None:
        import click

        def echo(message: str) -> None:
            click.echo(message, err=True)

    echo(warning)
