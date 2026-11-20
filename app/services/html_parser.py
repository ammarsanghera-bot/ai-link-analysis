"""Pure HTML parsing: turn one HTML document into a structured ``ParsedPage``.

Nothing in this module performs network I/O, which keeps it easy to test.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Comment, Tag

MAX_ITEM_CHARS = 200
MAX_HEADINGS_PER_LEVEL = 25
MAX_LINKS = 60
MAX_IMAGE_ALTS = 30
MAX_SERVICE_MENTIONS = 40
MAX_STRUCTURED_ITEMS = 12

# Elements that never contain analysable body copy.
NOISE_TAGS = [
    "script", "style", "noscript", "template", "svg", "canvas", "iframe", "object",
    "embed", "nav", "footer", "form", "button", "select", "input", "textarea",
    "dialog", "video", "audio", "map",
]  # fmt: skip
BLOCK_TAGS = [
    "address", "article", "aside", "blockquote", "dd", "details", "div", "dl", "dt",
    "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li",
    "main", "ol", "p", "pre", "section", "summary", "table", "td", "th", "tr", "ul",
]  # fmt: skip
HEADING_LEVELS = ("h1", "h2", "h3")
_INVISIBLE_PARENTS = frozenset({"script", "style", "noscript", "template"})

SOCIAL_DOMAINS = {
    "facebook.com": "Facebook",
    "instagram.com": "Instagram",
    "linkedin.com": "LinkedIn",
    "twitter.com": "X (Twitter)",
    "x.com": "X (Twitter)",
    "youtube.com": "YouTube",
    "tiktok.com": "TikTok",
    "pinterest.com": "Pinterest",
    "github.com": "GitHub",
    "threads.net": "Threads",
    "wa.me": "WhatsApp",
    "t.me": "Telegram",
}
_SHARE_URL = re.compile(r"/(sharer|share|intent)\b|shareArticle|[?&](u|url|text)=", re.I)
_OVERLAY_HINT = re.compile(r"cookie|consent|gdpr|popup|newsletter|subscribe-", re.I)
_SERVICE_HINT = re.compile(
    r"service|product|solution|feature|offering|pricing|plan|capabilit|what-we-do", re.I
)
_SERVICE_PATH = re.compile(
    r"/(services?|products?|solutions?|features?|offerings?|what-we-do)/[^/?#]+", re.I
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_ASSET_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")
_INTL_PHONE = re.compile(r"(?<![\w+])\+\d[\d\s().-]{6,18}\d(?!\w)")
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([,.;:!?])")
_JSON_LD_SKIP_TYPES = frozenset(
    {"BreadcrumbList", "ListItem", "ImageObject", "SearchAction", "EntryPoint", "ReadAction"}
)
_JSON_LD_TEXT_KEYS = (
    "name", "legalName", "description", "slogan", "serviceType", "category",
    "areaServed", "priceRange", "foundingDate",
)  # fmt: skip
_JSON_LD_OFFER_TYPES = frozenset({"Product", "Service", "Offer", "SoftwareApplication"})


@dataclass(frozen=True)
class Link:
    """A hyperlink with its visible text."""

    text: str
    url: str


@dataclass
class ParsedPage:
    """Structured content extracted from a single HTML page."""

    url: str
    title: str | None = None
    meta_description: str | None = None
    meta_keywords: list[str] = field(default_factory=list)
    site_name: str | None = None
    language: str | None = None
    headings: dict[str, list[str]] = field(default_factory=dict)
    navigation_links: list[Link] = field(default_factory=list)
    internal_links: list[Link] = field(default_factory=list)
    social_links: list[Link] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    addresses: list[str] = field(default_factory=list)
    image_alts: list[str] = field(default_factory=list)
    product_service_mentions: list[str] = field(default_factory=list)
    structured_data: list[dict[str, str]] = field(default_factory=list)
    text_lines: list[str] = field(default_factory=list)


# --- Small helpers ------------------------------------------------------------


def clean_text(value: str | None, limit: int = MAX_ITEM_CHARS) -> str:
    """Collapse whitespace and truncate ``value`` to ``limit`` characters."""
    text = " ".join((value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def unique(items: list[Any], limit: int | None = None) -> list[Any]:
    """De-duplicate ``items`` case-insensitively while preserving order."""
    seen: set[Any] = set()
    result: list[Any] = []
    for item in items:
        key = item.lower() if isinstance(item, str) else item
        if item and key not in seen:
            seen.add(key)
            result.append(item)
            if limit is not None and len(result) >= limit:
                break
    return result


def site_key(host: str | None) -> str:
    """Normalise a hostname so that ``www.example.com`` equals ``example.com``."""
    return (host or "").lower().removeprefix("www.")


def strip_fragment(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))


def _attr(tag: Tag, name: str) -> str:
    """Return an attribute as a plain string (multi-valued attributes are joined)."""
    value = tag.get(name)
    if isinstance(value, list):
        return " ".join(value)
    return value or ""


def _meta_content(soup: BeautifulSoup, *names: str) -> str | None:
    """Return the first non-empty ``<meta>`` content matching a name or property."""
    for name in names:
        for attribute in ("name", "property"):
            tag = soup.find("meta", attrs={attribute: re.compile(f"^{re.escape(name)}$", re.I)})
            if isinstance(tag, Tag):
                content = clean_text(_attr(tag, "content"), 500)
                if content:
                    return content
    return None


# --- Structured data (JSON-LD) ------------------------------------------------


def _walk_json_ld(node: Any) -> list[dict[str, Any]]:
    """Flatten a JSON-LD document into the list of typed objects it contains."""
    found: list[dict[str, Any]] = []
    if isinstance(node, list):
        for item in node:
            found.extend(_walk_json_ld(item))
    elif isinstance(node, dict):
        if "@type" in node:
            found.append(node)
        for value in node.values():
            if isinstance(value, (dict, list)):
                found.extend(_walk_json_ld(value))
    return found


def _json_ld_text(value: Any) -> str:
    """Render a JSON-LD value (string, number, list or nested object) as text."""
    if isinstance(value, (str, int, float)):
        return clean_text(str(value), 300)
    if isinstance(value, list):
        return clean_text(", ".join(filter(None, (_json_ld_text(item) for item in value[:10]))), 300)
    if isinstance(value, dict):
        if "name" in value:
            return _json_ld_text(value["name"])
        parts = [value.get(key) for key in (
            "streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry"
        )]  # fmt: skip
        return clean_text(", ".join(_json_ld_text(part) for part in parts if part), 300)
    return ""


def _parse_json_ld(soup: BeautifulSoup, page: ParsedPage) -> None:
    """Collect business facts published as schema.org JSON-LD."""
    for script in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        try:
            document = json.loads(script.string or script.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _walk_json_ld(document):
            raw_type = node.get("@type")
            node_type = raw_type[0] if isinstance(raw_type, list) and raw_type else raw_type
            if not isinstance(node_type, str) or node_type in _JSON_LD_SKIP_TYPES:
                continue

            summary = {"type": node_type}
            for key in _JSON_LD_TEXT_KEYS:
                text = _json_ld_text(node.get(key))
                if text:
                    summary[key] = text
            if len(summary) > 1 and len(page.structured_data) < MAX_STRUCTURED_ITEMS:
                page.structured_data.append(summary)

            if node_type in _JSON_LD_OFFER_TYPES and summary.get("name"):
                page.product_service_mentions.append(summary["name"])
            if isinstance(node.get("telephone"), str):
                page.phones.append(clean_text(node["telephone"], 40))
            if isinstance(node.get("email"), str):
                page.emails.append(node["email"].removeprefix("mailto:").strip().lower())
            if isinstance(node.get("address"), (dict, str)):
                page.addresses.append(_json_ld_text(node["address"]))
            same_as = node.get("sameAs")
            for profile in [same_as] if isinstance(same_as, str) else same_as or []:
                if isinstance(profile, str):
                    _add_social_link(page, profile)


# --- Links, contacts and media ------------------------------------------------


def _add_social_link(page: ParsedPage, url: str) -> bool:
    """Record ``url`` as a social profile if it belongs to a known network."""
    parts = urlsplit(url)
    host = site_key(parts.hostname)
    for domain, platform in SOCIAL_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            if parts.path.strip("/") and not _SHARE_URL.search(url):
                page.social_links.append(Link(platform, strip_fragment(url)))
            return True
    return False


def _parse_links(soup: BeautifulSoup, page: ParsedPage, base_url: str) -> None:
    """Classify every anchor as navigation, internal, social or contact link."""
    base_tag = soup.find("base", href=True)
    if isinstance(base_tag, Tag):
        base_url = urljoin(base_url, _attr(base_tag, "href"))
    own_site = site_key(urlsplit(page.url).hostname)

    for anchor in soup.find_all("a", href=True):
        href = _attr(anchor, "href").strip()
        lowered = href.lower()
        if lowered.startswith("mailto:"):
            page.emails.append(href[7:].split("?", 1)[0].strip().lower())
            continue
        if lowered.startswith("tel:"):
            page.phones.append(clean_text(href[4:], 40))
            continue
        if not href or lowered.startswith(("#", "javascript:", "data:", "sms:")):
            continue

        url = urljoin(base_url, href)
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            continue
        if _add_social_link(page, url):
            continue
        if site_key(parts.hostname) != own_site:
            continue

        text = clean_text(anchor.get_text(" "), 100) or clean_text(_attr(anchor, "title"), 100)
        link = Link(text, strip_fragment(url))
        page.internal_links.append(link)
        if anchor.find_parent(["nav", "header"]) or anchor.find_parent(
            attrs={"role": "navigation"}
        ):
            page.navigation_links.append(link)
        if text and 2 < len(text) <= 80 and _SERVICE_PATH.search(parts.path):
            page.product_service_mentions.append(text)


def _parse_service_sections(soup: BeautifulSoup, page: ParsedPage) -> None:
    """Collect card titles from sections whose id/class hints at products or services."""
    for section in soup.find_all(["section", "div", "ul", "article"]):
        if not _SERVICE_HINT.search(f"{_attr(section, 'id')} {_attr(section, 'class')}"):
            continue
        for heading in section.find_all(["h3", "h4"], limit=12):
            text = clean_text(heading.get_text(" "), 100)
            if 2 < len(text) <= 100:
                page.product_service_mentions.append(text)


def _parse_contacts(soup: BeautifulSoup, page: ParsedPage) -> None:
    """Find publicly listed e-mail addresses, phone numbers and postal addresses."""
    visible_text = " ".join(
        string
        for string in soup.find_all(string=True)
        if not isinstance(string, Comment)
        and (string.parent is None or string.parent.name not in _INVISIBLE_PARENTS)
    )
    for email in _EMAIL.findall(visible_text):
        if not email.lower().endswith(_ASSET_SUFFIXES):
            page.emails.append(email.lower())
    for phone in _INTL_PHONE.findall(visible_text):
        if 8 <= sum(char.isdigit() for char in phone) <= 15:
            page.phones.append(clean_text(phone, 40))
    for address in soup.find_all("address"):
        text = clean_text(address.get_text(" "), 300)
        if text:
            page.addresses.append(text)


# --- Main content -------------------------------------------------------------


def _decompose_all(tags: list[Tag]) -> None:
    """Remove ``tags`` from the tree, skipping ones already removed with a parent."""
    for tag in tags:
        if not tag.decomposed:
            tag.decompose()


def _remove_noise(soup: BeautifulSoup) -> None:
    """Strip scripts, styles, navigation, overlays and other non-content markup."""
    for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()
    _decompose_all(soup.find_all(NOISE_TAGS))
    _decompose_all(soup.find_all(attrs={"role": "navigation"}))
    _decompose_all(soup.find_all(attrs={"aria-hidden": "true"}))
    # Cookie banners and newsletter pop-ups: only small containers are removed
    # so that a hint on <body> or a page wrapper cannot wipe the whole page.
    for tag in soup.find_all(["div", "section", "aside"]):
        if tag.decomposed:
            continue
        hint = f"{_attr(tag, 'id')} {_attr(tag, 'class')}"
        if _OVERLAY_HINT.search(hint) and len(tag.get_text(" ", strip=True)) < 1500:
            tag.decompose()


def _content_root(soup: BeautifulSoup) -> Tag | BeautifulSoup:
    """Prefer the semantic main-content element when it holds real content."""
    for candidate in (soup.find("main"), soup.find(attrs={"role": "main"})):
        if isinstance(candidate, Tag) and len(candidate.get_text(" ", strip=True)) >= 200:
            return candidate
    return soup.body or soup


def _extract_text_lines(root: Tag | BeautifulSoup) -> list[str]:
    """Return de-duplicated, whitespace-normalised text lines from ``root``.

    Line breaks are inserted only at block-element boundaries so that inline
    markup (``<a>``, ``<strong>``) does not split sentences apart.
    """
    for line_break in root.find_all("br"):
        line_break.replace_with("\n")
    for block in root.find_all(BLOCK_TAGS):
        if block.name in HEADING_LEVELS:
            block.insert(0, "# ")
        block.insert_before("\n")
        block.append("\n")

    lines: list[str] = []
    for raw_line in root.get_text(" ").split("\n"):
        line = _SPACE_BEFORE_PUNCTUATION.sub(r"\1", " ".join(raw_line.split()))
        if len(line) > 1 and line != "#":
            lines.append(line)
    return unique(lines)


# --- Public API ---------------------------------------------------------------


def parse_html(markup: bytes | str, url: str, charset: str | None = None) -> ParsedPage:
    """Parse one HTML document into a ``ParsedPage``.

    Args:
        markup: Raw HTML. Bytes are preferred so the encoding can be detected.
        url: Final URL of the page, used to resolve relative links.
        charset: Charset declared in the HTTP ``Content-Type`` header, if any.
    """
    if isinstance(markup, bytes):
        soup = BeautifulSoup(markup, "html.parser", from_encoding=charset)
    else:
        soup = BeautifulSoup(markup, "html.parser")
    page = ParsedPage(url=url)

    # 1. Metadata.
    if soup.title:
        page.title = clean_text(soup.title.get_text(" "), 300) or None
    page.title = page.title or _meta_content(soup, "og:title", "twitter:title")
    page.meta_description = _meta_content(
        soup, "description", "og:description", "twitter:description"
    )
    keywords = _meta_content(soup, "keywords")
    page.meta_keywords = unique([clean_text(k, 60) for k in (keywords or "").split(",")], 20)
    page.site_name = _meta_content(soup, "og:site_name", "application-name")
    html_tag = soup.find("html")
    if isinstance(html_tag, Tag):
        page.language = clean_text(_attr(html_tag, "lang"), 20) or None

    # 2. Signals that must be read before noise (scripts, nav, footer) is removed.
    _parse_json_ld(soup, page)
    _parse_links(soup, page, url)
    _parse_service_sections(soup, page)
    _parse_contacts(soup, page)
    page.image_alts = unique(
        [
            alt
            for image in soup.find_all("img", alt=True)
            if len(alt := clean_text(_attr(image, "alt"), 150)) > 2
        ],
        MAX_IMAGE_ALTS,
    )

    # 3. Clean the document, then read headings and body text.
    _remove_noise(soup)
    for level in HEADING_LEVELS:
        texts = [clean_text(heading.get_text(" ")) for heading in soup.find_all(level)]
        page.headings[level] = unique(texts, MAX_HEADINGS_PER_LEVEL)
    page.text_lines = _extract_text_lines(_content_root(soup))

    # 4. De-duplicate and cap list-valued fields.
    page.navigation_links = unique(page.navigation_links, MAX_LINKS)
    page.internal_links = unique(page.internal_links, MAX_LINKS * 2)
    page.social_links = unique(page.social_links, 15)
    page.emails = unique(page.emails, 5)
    page.phones = unique(page.phones, 5)
    page.addresses = unique(page.addresses, 3)
    page.product_service_mentions = unique(page.product_service_mentions, MAX_SERVICE_MENTIONS)
    return page
