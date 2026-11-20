"""HTTPX transport that enforces SSRF protection on every outgoing request."""

from collections.abc import Iterable

import httpx

from app.exceptions import ForbiddenDestinationError
from app.utils.validators import (
    DEFAULT_ALLOWED_PORTS,
    DEFAULT_PORTS,
    Resolver,
    parse_ip_literal,
    resolve_public_addresses,
    system_resolver,
)


class SSRFGuardTransport(httpx.AsyncBaseTransport):
    """Resolve, validate and pin the destination address of each request.

    For every request (including each redirect hop) the transport:

    1. resolves the hostname and rejects it unless all addresses are public;
    2. rewrites the request to connect to one validated address, while keeping
       the original ``Host`` header and TLS SNI/certificate hostname.

    Because the connection goes to the address that was just validated, a
    second DNS lookup returning a different (internal) address - the classic
    DNS-rebinding attack - cannot redirect the request.
    """

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        *,
        resolver: Resolver = system_resolver,
        allowed_ports: Iterable[int] = DEFAULT_ALLOWED_PORTS,
    ) -> None:
        self._inner = inner
        self._resolver = resolver
        self._allowed_ports = frozenset(allowed_ports)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.scheme not in DEFAULT_PORTS:
            raise ForbiddenDestinationError("Only http:// and https:// URLs are supported.")
        port = url.port or DEFAULT_PORTS[url.scheme]
        if port not in self._allowed_ports:
            raise ForbiddenDestinationError(f"Port {port} is not allowed.")

        host = url.raw_host.decode("ascii")
        addresses = await resolve_public_addresses(host, port, self._resolver)

        extensions = dict(request.extensions)
        if url.scheme == "https" and parse_ip_literal(host) is None:
            # Keep SNI and certificate verification bound to the real hostname.
            extensions["sni_hostname"] = host

        # Passing ``stream`` (not ``content``) preserves the original headers,
        # including ``Host``, exactly as httpx built them.
        pinned_request = httpx.Request(
            method=request.method,
            url=url.copy_with(host=addresses[0]),
            headers=request.headers,
            stream=request.stream,
            extensions=extensions,
        )
        return await self._inner.handle_async_request(pinned_request)

    async def aclose(self) -> None:
        await self._inner.aclose()
