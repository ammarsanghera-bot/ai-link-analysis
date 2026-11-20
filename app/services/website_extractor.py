"""Secure website fetching and content extraction.

Responsibilities:

* fetch the homepage plus a few high-value pages (about, services, ...);
* enforce SSRF protection on every request and redirect hop;
* honour ``robots.txt`` and never try to bypass access restrictions;
* reduce the HTML to clean, de-duplicated text within a fixed content budget.
"""

import asyncio
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.config import Settings
from app.exceptions import (
    AppError,
    EmptyContentError,
    ForbiddenDestinationError,
    InvalidURLError,
    RobotsDisallowedError,
    WebsiteFetchError,
    WebsiteTimeoutError,
)
from app.services.html_parser import Link, ParsedPage, parse_html, site_key, unique
from app.utils.http_guard import SSRFGuardTransport
from app.utils.validators import Resolver, ValidatedURL, system_resolver, validate_public_url

logger = logging.getLogger(__name__)

TransportFactory = Callable[[], httpx.AsyncBaseTransport]

REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
HTML_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})
ROBOTS_MAX_BYTES = 512_000
MIN_CONTENT_SIGNAL_CHARS = 80
THIN_CONTENT_CHARS = 600
SUBPAGE_CONCURRENCY = 2
MIN_TRUNCATED_LINE_CHARS = 200

# Keyword -> weight used to rank internal links worth analysing.
PRIORITY_KEYWORDS = {
    "service": 10, "about": 10, "product": 9, "solution": 9, "what-we-do": 9,
    "feature": 8, "pricing": 7, "industr": 6, "company": 6, "who-we-are": 6,
    "our-story": 5, "case-stud": 4, "portfolio": 4, "customers": 4, "contact": 4,
    "faq": 3,
}  # fmt: skip
_EXCLUDED_SEGMENT = re.compile(
    r"(^|/)(login|log-in|signin|sign-in|signup|sign-up|register|cart|basket|checkout|"
    r"account|my-account|privacy[^/]*|terms[^/]*|legal|cookie[^/]*|careers?|jobs?|search|"
    r"wp-admin|wp-login\.php|feed|tag|category|author|unsubscribe)(/|$)",
    re.I,
)
_NON_HTML_EXTENSIONS = (
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".zip", ".gz",
    ".mp3", ".mp4", ".mov", ".avi", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".css", ".js", ".json", ".xml", ".rss", ".txt", ".csv", ".exe", ".dmg",
)  # fmt: skip


@dataclass(frozen=True)
class FetchResult:
    """Outcome of one HTTP exchange (after redirects have been followed)."""

    url: str
    status_code: int
    content_type: str = ""
    charset: str | None = None
    body: bytes = b""
    truncated: bool = False
    location: str | None = None

    @property
    def is_html(self) -> bool:
        return not self.content_type or self.content_type in HTML_CONTENT_TYPES


@dataclass(frozen=True)
class PageContent:
    """Cleaned body text of one analysed page."""

    url: str
    text: str


@dataclass
class ExtractedWebsite:
    """Everything extracted from a website, ready to be sent to the LLM."""

    requested_url: str
    final_url: str
    title: str | None = None
    meta_description: str | None = None
    meta_keywords: list[str] = field(default_factory=list)
    site_name: str | None = None
    language: str | None = None
    headings: dict[str, list[str]] = field(default_factory=dict)
    navigation_links: list[Link] = field(default_factory=list)
    internal_urls: list[str] = field(default_factory=list)
    social_links: list[Link] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    addresses: list[str] = field(default_factory=list)
    image_alts: list[str] = field(default_factory=list)
    product_service_mentions: list[str] = field(default_factory=list)
    structured_data: list[dict[str, str]] = field(default_factory=list)
    pages: list[PageContent] = field(default_factory=list)
    content_chars: int = 0
    content_truncated: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def corpus(self) -> str:
        """Lower-cased text of everything extracted, used for evidence checks."""
        parts: list[str] = [self.title or "", self.meta_description or ""]
        parts.extend(self.meta_keywords)
        parts.extend(text for texts in self.headings.values() for text in texts)
        parts.extend(link.text for link in self.navigation_links)
        parts.extend(self.product_service_mentions)
        parts.extend(self.image_alts)
        parts.extend(" ".join(item.values()) for item in self.structured_data)
        parts.extend(page.text for page in self.pages)
        return "\n".join(parts).lower()


# --- Link selection and aggregation (pure functions) --------------------------


def _dedupe_key(url: str) -> str:
    """Key that treats http/https, ``www.`` and trailing-slash variants as equal."""
    parts = urlsplit(url)
    return f"{site_key(parts.hostname)}{parts.path.rstrip('/')}?{parts.query}"


def select_priority_links(home: ParsedPage, limit: int) -> list[str]:
    """Pick up to ``limit`` internal URLs most likely to describe the business.

    Links are scored by keywords in their path and anchor text (about,
    services, products, pricing, ...). Shallow and navigation links rank
    higher, and at most two pages per top-level section are selected.
    """
    if limit <= 0:
        return []
    home_key = _dedupe_key(home.url)
    navigation_urls = {link.url for link in home.navigation_links}
    scored: dict[str, tuple[int, str]] = {}

    for link in home.internal_links:
        parts = urlsplit(link.url)
        path = parts.path.lower()
        key = _dedupe_key(link.url)
        if key == home_key or path.endswith(_NON_HTML_EXTENSIONS):
            continue
        if _EXCLUDED_SEGMENT.search(path):
            continue
        haystack = f"{path} {link.text.lower()}"
        score = max((w for keyword, w in PRIORITY_KEYWORDS.items() if keyword in haystack), default=0)
        if not score:
            continue
        depth = len([segment for segment in path.split("/") if segment])
        score += 2 if depth <= 1 else 1 if depth == 2 else 0
        score += 2 if link.url in navigation_urls else 0
        score -= 2 if parts.query else 0
        if key not in scored or score > scored[key][0]:
            scored[key] = (score, link.url)

    selected: list[str] = []
    per_section: dict[str, int] = {}
    for _, url in sorted(scored.values(), key=lambda item: -item[0]):
        section = urlsplit(url).path.strip("/").split("/", 1)[0].lower()
        if per_section.get(section, 0) >= 2:
            continue
        per_section[section] = per_section.get(section, 0) + 1
        selected.append(url)
        if len(selected) >= limit:
            break
    return selected


def _allocate_content(pages: list[ParsedPage], budget: int) -> tuple[list[PageContent], bool]:
    """Fit page text into ``budget`` characters, removing lines repeated across pages.

    The homepage gets twice the share of any other page; budget a page does
    not use rolls over to the pages after it.
    """
    weights = [2] + [1] * (len(pages) - 1)
    seen: set[str] = set()
    remaining = budget
    contents: list[PageContent] = []
    truncated = False

    for index, page in enumerate(pages):
        share = int(remaining * weights[index] / sum(weights[index:]))
        lines: list[str] = []
        used = 0
        for line in page.text_lines:
            if line.lower() in seen:
                continue
            available = share - used - 1
            if len(line) > available:
                truncated = True
                if available >= MIN_TRUNCATED_LINE_CHARS:
                    lines.append(line[:available])
                    used += available + 1
                break
            seen.add(line.lower())
            lines.append(line)
            used += len(line) + 1
        remaining -= used
        if lines:
            contents.append(PageContent(url=page.url, text="\n".join(lines)))
    return contents, truncated


def build_extracted_website(
    requested_url: str, pages: list[ParsedPage], warnings: list[str], content_budget: int
) -> ExtractedWebsite:
    """Merge parsed pages (homepage first) into one ``ExtractedWebsite``.

    Raises:
        EmptyContentError: the pages contain no meaningful text to analyse.
    """
    home = pages[0]

    def first(attribute: str) -> str | None:
        return next((value for page in pages if (value := getattr(page, attribute))), None)

    def merged(attribute: str, limit: int) -> list:
        return unique([item for page in pages for item in getattr(page, attribute)], limit)

    contents, truncated = _allocate_content(pages, content_budget)
    site = ExtractedWebsite(
        requested_url=requested_url,
        final_url=home.url,
        title=first("title"),
        meta_description=first("meta_description"),
        meta_keywords=merged("meta_keywords", 20),
        site_name=first("site_name"),
        language=first("language"),
        headings={
            level: unique([text for page in pages for text in page.headings.get(level, [])], 25)
            for level in ("h1", "h2", "h3")
        },
        navigation_links=home.navigation_links[:30],
        internal_urls=unique([link.url for page in pages for link in page.internal_links], 40),
        social_links=merged("social_links", 12),
        emails=merged("emails", 5),
        phones=merged("phones", 5),
        addresses=merged("addresses", 3),
        image_alts=merged("image_alts", 25),
        product_service_mentions=merged("product_service_mentions", 40),
        structured_data=[item for page in pages for item in page.structured_data][:12],
        pages=contents,
        content_chars=sum(len(content.text) for content in contents),
        content_truncated=truncated,
        warnings=list(warnings),
    )

    signal = (
        site.content_chars
        + len(site.meta_description or "")
        + sum(len(text) for texts in site.headings.values() for text in texts)
    )
    if signal < MIN_CONTENT_SIGNAL_CHARS:
        raise EmptyContentError()
    if site.content_chars < THIN_CONTENT_CHARS:
        site.warnings.append(
            "Very little text content was found on the website, so the analysis has low "
            "confidence. The site may rely on JavaScript rendering, which is not executed."
        )
    if truncated:
        site.warnings.append("Website content was truncated to fit the configured content budget.")
    return site


# --- Fetching -----------------------------------------------------------------


class _SiteFetcher:
    """Fetches pages for one extraction run.

    Each redirect hop is re-validated and checked against the ``robots.txt``
    of the origin it targets. Parsed robots files are cached per origin.
    """

    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self._robots_agent = settings.user_agent.split("/", 1)[0].strip() or "*"
        self._robots: dict[str, RobotFileParser] = {}

    async def fetch(self, url: str, *, max_bytes: int, check_robots: bool = True) -> FetchResult:
        """GET ``url``, following redirects manually and safely."""
        current = url
        for hop in range(self._settings.max_redirects + 1):
            try:
                target = validate_public_url(current, self._settings.allowed_ports)
            except InvalidURLError as exc:
                if hop == 0:
                    raise
                raise WebsiteFetchError("The website redirected to an invalid URL.") from exc
            except ForbiddenDestinationError as exc:
                if hop == 0:
                    raise
                raise ForbiddenDestinationError(
                    "The website redirected to a private or internal destination, "
                    "which is not allowed."
                ) from exc

            if check_robots:
                await self._ensure_allowed(target)
            result = await self._request(target.url, max_bytes)
            if result.location is None:
                return result
            current = urljoin(target.url, result.location)
        raise WebsiteFetchError("The website redirected too many times.")

    async def _request(self, url: str, max_bytes: int) -> FetchResult:
        """Perform a single GET, reading at most ``max_bytes`` of the body."""
        try:
            async with self._client.stream("GET", url) as response:
                location = response.headers.get("location")
                if response.status_code in REDIRECT_STATUSES and location:
                    return FetchResult(url=url, status_code=response.status_code, location=location)

                chunks: list[bytes] = []
                size = 0
                truncated = False
                async for chunk in response.aiter_bytes():
                    if size + len(chunk) > max_bytes:
                        chunks.append(chunk[: max_bytes - size])
                        truncated = True
                        break
                    chunks.append(chunk)
                    size += len(chunk)

                content_type = response.headers.get("content-type", "")
                return FetchResult(
                    url=url,
                    status_code=response.status_code,
                    content_type=content_type.split(";", 1)[0].strip().lower(),
                    charset=response.charset_encoding,
                    body=b"".join(chunks),
                    truncated=truncated,
                )
        except httpx.TimeoutException as exc:
            raise WebsiteTimeoutError("The website did not respond in time.") from exc
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            logger.info(
                "website request failed",
                extra={"host": urlsplit(url).hostname, "error_type": type(exc).__name__},
            )
            raise WebsiteFetchError(
                "Could not connect to the website. It may be offline or refusing connections."
            ) from exc

    async def _ensure_allowed(self, target: ValidatedURL) -> None:
        """Raise ``RobotsDisallowedError`` unless robots.txt permits ``target``."""
        parser = self._robots.get(target.origin)
        if parser is None:
            parser = await self._load_robots(target.origin)
            self._robots[target.origin] = parser
        if not parser.can_fetch(self._robots_agent, target.url):
            if parser.disallow_all:
                raise RobotsDisallowedError(
                    "The website's robots.txt is unavailable or access-restricted, so automated "
                    "access is treated as disallowed."
                )
            raise RobotsDisallowedError()

    async def _load_robots(self, origin: str) -> RobotFileParser:
        """Fetch and parse ``robots.txt`` for ``origin``.

        Status handling follows common crawler practice: 401/403 and 5xx mean
        "do not crawl", other 4xx mean "no restrictions".
        """
        parser = RobotFileParser()
        result = await self.fetch(
            f"{origin}/robots.txt", max_bytes=ROBOTS_MAX_BYTES, check_robots=False
        )
        if result.status_code in (401, 403) or result.status_code >= 500:
            parser.disallow_all = True
        elif result.status_code >= 400:
            parser.allow_all = True
        else:
            parser.parse(result.body.decode("utf-8", errors="replace").splitlines())
        return parser


def _homepage_error(result: FetchResult) -> WebsiteFetchError | None:
    """Translate an unusable homepage response into a client-facing error."""
    status = result.status_code
    if status in (401, 403):
        return WebsiteFetchError(
            f"The website refused automated access (HTTP {status}). "
            "Access restrictions are respected and not bypassed."
        )
    if status == 429:
        return WebsiteFetchError("The website is rate-limiting requests (HTTP 429).")
    if status in (404, 410):
        return WebsiteFetchError(f"The page was not found on the website (HTTP {status}).")
    if status >= 500:
        return WebsiteFetchError(f"The website returned a server error (HTTP {status}).")
    if status >= 400 or status < 200:
        return WebsiteFetchError(f"The website returned an unexpected response (HTTP {status}).")
    if not result.is_html:
        return WebsiteFetchError(
            f"The URL did not return an HTML page (content type: {result.content_type})."
        )
    return None


class WebsiteExtractor:
    """Fetches a website safely and extracts analysable content from it."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport_factory: TransportFactory | None = None,
        resolver: Resolver = system_resolver,
    ) -> None:
        self._settings = settings
        self._transport_factory = transport_factory or self._default_transport
        self._resolver = resolver

    @staticmethod
    def _default_transport() -> httpx.AsyncBaseTransport:
        # Connections are pinned to validated IP addresses. Disabling keep-alive
        # ensures a TLS connection verified for one hostname is never reused for
        # another hostname that happens to share the same address.
        return httpx.AsyncHTTPTransport(limits=httpx.Limits(max_keepalive_connections=0))

    async def extract(self, target: ValidatedURL) -> ExtractedWebsite:
        """Fetch ``target`` plus a few priority pages and return the cleaned content.

        Raises:
            ForbiddenDestinationError, RobotsDisallowedError: access is not permitted.
            WebsiteFetchError, EmptyContentError: the site is unusable.
            WebsiteTimeoutError: the homepage did not arrive within the time budget.
        """
        settings = self._settings
        started = time.perf_counter()
        deadline = asyncio.get_running_loop().time() + settings.fetch_total_timeout_seconds
        transport = SSRFGuardTransport(
            self._transport_factory(),
            resolver=self._resolver,
            allowed_ports=settings.allowed_ports,
        )
        client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(
                settings.fetch_timeout_seconds,
                connect=min(5.0, settings.fetch_timeout_seconds),
            ),
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                "Accept-Language": "en,*;q=0.5",
            },
            follow_redirects=False,
            trust_env=False,
        )

        async with client:
            fetcher = _SiteFetcher(client, settings)
            try:
                async with asyncio.timeout_at(deadline):
                    result = await fetcher.fetch(target.url, max_bytes=settings.max_response_bytes)
                    error = _homepage_error(result)
                    if error:
                        raise error
                    home = await asyncio.to_thread(
                        parse_html, result.body, result.url, result.charset
                    )
            except TimeoutError as exc:
                raise WebsiteTimeoutError("The website took too long to respond.") from exc

            warnings: list[str] = []
            if result.truncated:
                warnings.append("The homepage HTML exceeded the size limit and was truncated.")
            links = select_priority_links(home, settings.max_pages - 1)
            subpages = await self._fetch_subpages(fetcher, links, home, deadline, warnings)

        site = build_extracted_website(
            target.url, [home, *subpages], warnings, settings.max_content_chars
        )
        logger.info(
            "website extracted",
            extra={
                "host": target.host,
                "pages": len(site.pages),
                "content_chars": site.content_chars,
                "truncated": site.content_truncated,
                "duration_ms": round((time.perf_counter() - started) * 1000),
            },
        )
        return site

    async def _fetch_subpages(
        self,
        fetcher: _SiteFetcher,
        urls: list[str],
        home: ParsedPage,
        deadline: float,
        warnings: list[str],
    ) -> list[ParsedPage]:
        """Fetch secondary pages concurrently; failures only produce warnings."""
        if not urls:
            return []
        semaphore = asyncio.Semaphore(SUBPAGE_CONCURRENCY)
        tasks = [asyncio.create_task(self._fetch_subpage(fetcher, url, semaphore)) for url in urls]
        timeout = max(0.0, deadline - asyncio.get_running_loop().time())
        _, pending = await asyncio.wait(tasks, timeout=timeout)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if pending:
            warnings.append(
                f"{len(pending)} additional page(s) were skipped because the time budget ran out."
            )

        pages: list[ParsedPage] = []
        seen = {_dedupe_key(home.url)}
        home_site = site_key(urlsplit(home.url).hostname)
        for task in tasks:
            if task in pending or task.exception() is not None:
                continue
            page, warning = task.result()
            if warning:
                warnings.append(warning)
            if page is None or site_key(urlsplit(page.url).hostname) != home_site:
                continue
            if _dedupe_key(page.url) not in seen:
                seen.add(_dedupe_key(page.url))
                pages.append(page)
        return pages

    async def _fetch_subpage(
        self, fetcher: _SiteFetcher, url: str, semaphore: asyncio.Semaphore
    ) -> tuple[ParsedPage | None, str | None]:
        """Fetch and parse one secondary page, returning ``(page, warning)``."""
        async with semaphore:
            try:
                result = await fetcher.fetch(url, max_bytes=self._settings.max_response_bytes)
            except RobotsDisallowedError:
                return None, f"Skipped {url}: disallowed by robots.txt."
            except AppError as exc:
                return None, f"Skipped {url}: {exc.message}"
        if result.status_code >= 400 or not result.is_html:
            return None, f"Skipped {url}: HTTP {result.status_code} or non-HTML content."
        page = await asyncio.to_thread(parse_html, result.body, result.url, result.charset)
        return page, None
