"""URL validation and SSRF protection helpers.

Two layers of defence live here:

1. ``validate_public_url`` - purely syntactic checks performed before any
   network activity (scheme, credentials, port, hostname shape, IP literals).
2. ``resolve_public_addresses`` - DNS resolution followed by validation of
   *every* resolved address, so that a public-looking hostname pointing at an
   internal address is rejected.

The guarded HTTP transport (``app.utils.http_guard``) then connects to the
exact address validated here, which closes the DNS-rebinding window between
"check" and "use".
"""

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from app.exceptions import (
    ForbiddenDestinationError,
    InvalidURLError,
    WebsiteFetchError,
    WebsiteTimeoutError,
)

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Awaitable[list[str]]]

MAX_URL_LENGTH = 2048
DNS_TIMEOUT_SECONDS = 5.0
DEFAULT_PORTS = {"http": 80, "https": 443}
DEFAULT_ALLOWED_PORTS = frozenset({80, 443})

# Hostnames that always refer to the local machine or to internal services.
BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "broadcasthost",
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
        "kubernetes.default.svc",
    }
)
# Suffixes reserved for local or internal name resolution.
BLOCKED_SUFFIXES = (
    ".localhost",
    ".local",
    ".localdomain",
    ".internal",
    ".intranet",
    ".lan",
    ".home",
    ".corp",
    ".home.arpa",
    ".svc",
    ".cluster.local",
    ".onion",
)
# Well-known cloud metadata service addresses (AWS, GCP, Azure, ECS, Alibaba, Oracle).
METADATA_ADDRESSES = frozenset(
    ipaddress.ip_address(address)
    for address in (
        "169.254.169.254",
        "169.254.170.2",
        "100.100.100.200",
        "192.0.0.192",
        "fd00:ec2::254",
    )
)

_NAT64_NETWORK = ipaddress.ip_network("64:ff9b::/96")
_UNSAFE_URL_CHARS = re.compile(r"[\x00-\x20\x7f\\]")
_HOST_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_HEX_LABEL = re.compile(r"^0x[0-9a-f]*$")


@dataclass(frozen=True)
class ValidatedURL:
    """A URL that passed syntactic validation, in normalised form."""

    url: str
    scheme: str
    host: str
    port: int

    @property
    def origin(self) -> str:
        """Return ``scheme://host[:port]`` for this URL."""
        return origin_of(self.scheme, self.host, self.port)


def origin_of(scheme: str, host: str, port: int) -> str:
    """Build a normalised origin string, omitting default ports."""
    netloc = f"[{host}]" if ":" in host else host
    if port != DEFAULT_PORTS.get(scheme):
        netloc = f"{netloc}:{port}"
    return f"{scheme}://{netloc}"


def _embedded_ipv4(address: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """Return the IPv4 address tunnelled inside an IPv6 address, if any."""
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address.sixtofour is not None:
        return address.sixtofour
    if address.teredo is not None:
        return address.teredo[1]
    if address in _NAT64_NETWORK:
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    # Deprecated IPv4-compatible form ``::a.b.c.d`` (excluding ``::`` and ``::1``).
    if int(address) >> 32 == 0 and int(address) > 1:
        return ipaddress.IPv4Address(int(address))
    return None


def ip_block_reason(address: IPAddress) -> str | None:
    """Explain why ``address`` must not be contacted, or return ``None`` if it is public."""
    if isinstance(address, ipaddress.IPv6Address):
        embedded = _embedded_ipv4(address)
        if embedded is not None:
            return ip_block_reason(embedded)
        if address.is_site_local:
            return "a site-local address"

    if address in METADATA_ADDRESSES:
        return "a cloud metadata endpoint"
    if address.is_loopback:
        return "a loopback address"
    if address.is_link_local:
        return "a link-local address"
    if address.is_unspecified:
        return "an unspecified address"
    if address.is_multicast:
        return "a multicast address"
    if address.is_private:
        return "a private network address"
    if address.is_reserved:
        return "a reserved address"
    if not address.is_global:
        return "a non-public address"
    return None


def parse_ip_literal(host: str) -> IPAddress | None:
    """Parse ``host`` as an IP literal, returning ``None`` for ordinary hostnames."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _looks_numeric(host: str) -> bool:
    """Detect hosts that URL parsers may interpret as IPv4 (``2130706433``, ``0x7f.1``)."""
    last_label = host.rsplit(".", 1)[-1]
    return last_label.isdigit() or bool(_HEX_LABEL.match(last_label))


def _normalise_hostname(raw_host: str) -> str:
    """Lower-case, IDNA-encode and syntax-check a DNS hostname."""
    host = raw_host.rstrip(".").lower()
    try:
        # IDNA applies NFKC, so full-width or circled look-alikes are folded
        # to ASCII *before* the blocklist and IP checks run.
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise InvalidURLError("The URL hostname is not valid.") from exc
    if not host or len(host) > 253:
        raise InvalidURLError("The URL hostname is not valid.")
    return host


def validate_public_url(
    raw_url: str, allowed_ports: Iterable[int] = DEFAULT_ALLOWED_PORTS
) -> ValidatedURL:
    """Validate that ``raw_url`` is a well-formed, public-looking HTTP(S) URL.

    Raises:
        InvalidURLError: the value is not a usable HTTP(S) URL (HTTP 400).
        ForbiddenDestinationError: the URL targets localhost, a private or
            internal address, or a disallowed port (HTTP 403).
    """
    if not isinstance(raw_url, str) or not raw_url.strip():
        raise InvalidURLError("A URL is required.")
    url = raw_url.strip()
    if len(url) > MAX_URL_LENGTH:
        raise InvalidURLError(f"The URL is longer than {MAX_URL_LENGTH} characters.")
    if _UNSAFE_URL_CHARS.search(url):
        raise InvalidURLError("The URL contains whitespace, control characters or backslashes.")

    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise InvalidURLError("The URL could not be parsed.") from exc

    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS:
        raise InvalidURLError("Only http:// and https:// URLs are supported.")
    if parts.username is not None or parts.password is not None:
        raise InvalidURLError("URLs containing credentials are not supported.")
    if not parts.hostname:
        raise InvalidURLError("The URL does not contain a hostname.")

    effective_port = port or DEFAULT_PORTS[scheme]
    if effective_port not in set(allowed_ports):
        raise ForbiddenDestinationError(f"Port {effective_port} is not allowed.")

    raw_host = parts.hostname
    if ":" in raw_host:
        # IPv6 literal. Zone identifiers (``%eth0``) only make sense locally.
        address = parse_ip_literal(raw_host)
        if address is None:
            raise InvalidURLError("The URL contains an invalid IPv6 address.")
        host = str(address)
    else:
        host = _normalise_hostname(raw_host)
        address = parse_ip_literal(host)
        if address is None and _looks_numeric(host):
            raise InvalidURLError("IP addresses must use standard dotted-decimal notation.")

    if address is not None:
        reason = ip_block_reason(address)
        if reason:
            raise ForbiddenDestinationError(f"The URL points to {reason}, which is not allowed.")
    else:
        if host in BLOCKED_HOSTNAMES or host.endswith(BLOCKED_SUFFIXES):
            raise ForbiddenDestinationError(
                "The URL points to a local or internal hostname, which is not allowed."
            )
        labels = host.split(".")
        if len(labels) < 2 or not all(_HOST_LABEL.match(label) for label in labels):
            raise InvalidURLError("The URL must contain a valid public domain name.")

    netloc = origin_of(scheme, host, effective_port).split("://", 1)[1]
    normalised = urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))
    return ValidatedURL(url=normalised, scheme=scheme, host=host, port=effective_port)


async def system_resolver(host: str, port: int) -> list[str]:
    """Resolve ``host`` with the operating system resolver (A and AAAA records)."""
    loop = asyncio.get_running_loop()
    try:
        async with asyncio.timeout(DNS_TIMEOUT_SECONDS):
            infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except TimeoutError as exc:
        raise WebsiteTimeoutError("DNS resolution for the website timed out.") from exc
    except (socket.gaierror, UnicodeError) as exc:
        raise WebsiteFetchError("The website's hostname could not be resolved.") from exc
    return list(dict.fromkeys(info[4][0] for info in infos))


async def resolve_public_addresses(
    host: str, port: int, resolver: Resolver = system_resolver
) -> list[str]:
    """Resolve ``host`` and require every resulting address to be public.

    Rejecting the request when *any* record is non-public prevents attackers
    from mixing public and internal records and hoping the internal one is
    picked. IPv4 addresses are returned first because IPv6 routes are often
    unavailable inside containers.

    Raises:
        ForbiddenDestinationError: at least one address is not public.
        WebsiteFetchError: the hostname does not resolve.
    """
    literal = parse_ip_literal(host)
    candidates = [str(literal)] if literal is not None else await resolver(host, port)
    if not candidates:
        raise WebsiteFetchError("The website's hostname could not be resolved.")

    addresses: list[IPAddress] = []
    for candidate in candidates:
        address = parse_ip_literal(candidate.split("%", 1)[0])
        if address is None:
            raise WebsiteFetchError("The website's hostname resolved to an invalid address.")
        reason = ip_block_reason(address)
        if reason:
            raise ForbiddenDestinationError(
                f"The website's hostname resolves to {reason}, which is not allowed."
            )
        addresses.append(address)

    addresses.sort(key=lambda item: item.version)
    return [str(address) for address in addresses]
