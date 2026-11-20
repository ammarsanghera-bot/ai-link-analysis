"""Shared fixtures and fakes.

No test touches the network: website responses, DNS resolution and LLM
provider APIs are all replaced with in-process fakes.
"""

import copy
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import get_analysis_service
from app.main import create_app
from app.services.analysis_service import AnalysisService
from app.services.competitor_service import CompetitorService
from app.services.llm_service import LLMService
from app.services.website_extractor import WebsiteExtractor
from app.utils.cache import TTLCache

SITE_URL = "https://acmecloud.example"
SITE_HOST = "acmecloud.example"
PUBLIC_IP = "93.184.216.34"
OPENAI_KEY = "sk-test-secret-key-1234567890"
GEMINI_KEY = "AIzaTestSecretKey1234567890"

HOME_HTML = """<!doctype html>
<html lang="en">
<head>
  <title>Acme Cloud Consulting | Cloud Migration &amp; DevOps Experts</title>
  <meta name="description" content="Acme Cloud Consulting helps mid-sized companies migrate to the cloud, automate DevOps and cut infrastructure costs.">
  <meta name="keywords" content="cloud migration, devops consulting, kubernetes">
  <meta property="og:site_name" content="Acme Cloud Consulting">
  <script type="application/ld+json">
  {"@context": "https://schema.org", "@type": "Organization", "name": "Acme Cloud Consulting",
   "telephone": "+1 415 555 0134",
   "address": {"@type": "PostalAddress", "streetAddress": "500 Market Street",
               "addressLocality": "San Francisco", "addressCountry": "US"},
   "sameAs": ["https://www.linkedin.com/company/acme-cloud"]}
  </script>
  <style>.hidden { display: none; }</style>
  <script>var trackingSecret = "do-not-extract-this";</script>
</head>
<body>
  <header>
    <nav>
      <a href="/">Home</a>
      <a href="/services">Services</a>
      <a href="/about">About Us</a>
      <a href="/blog">Blog</a>
      <a href="/login">Login</a>
      <a href="/contact">Contact</a>
    </nav>
    <h1>Cloud migration without the downtime</h1>
  </header>
  <main>
    <section id="services">
      <h2>Our Services</h2>
      <div class="service-card"><h3>Cloud Migration</h3>
        <p>We move your workloads to AWS, Azure or Google Cloud with <strong>zero downtime</strong>.</p></div>
      <div class="service-card"><h3>DevOps Automation</h3>
        <p>CI/CD pipelines and infrastructure as code for faster releases.</p></div>
      <div class="service-card"><h3>Kubernetes Consulting</h3>
        <p>Design, deploy and operate production Kubernetes clusters.</p></div>
    </section>
    <section>
      <h2>Why Acme</h2>
      <p>Trusted by fintech and healthcare companies across North America.</p>
      <img src="/team.jpg" alt="Acme engineers reviewing a cloud architecture diagram">
      <img src="/spacer.gif" alt="">
    </section>
    <div id="cookie-banner">We use cookies to improve your experience.</div>
  </main>
  <footer>
    <p>Contact us: <a href="mailto:hello@acmecloud.example">hello@acmecloud.example</a>
       or <a href="tel:+14155550134">+1 415 555 0134</a></p>
    <a href="https://twitter.com/acmecloud">Twitter</a>
    <a href="https://www.facebook.com/acmecloud">Facebook</a>
    <a href="/privacy-policy">Privacy</a>
  </footer>
</body>
</html>"""

SERVICES_HTML = """<html><head><title>Services | Acme Cloud Consulting</title></head><body>
<nav><a href="/">Home</a></nav>
<main><h1>Services</h1>
<h2>Cost Optimisation Audits</h2>
<p>A two-week review of your cloud bill that identifies savings in compute, storage and licensing.</p>
<p>Trusted by fintech and healthcare companies across North America.</p>
</main></body></html>"""

ABOUT_HTML = """<html><head><title>About | Acme Cloud Consulting</title></head><body>
<main><h1>About Acme</h1>
<p>Founded by former platform engineers, Acme Cloud Consulting is based in San Francisco.</p>
</main></body></html>"""

CONTACT_HTML = """<html><head><title>Contact</title></head><body>
<main><h1>Contact</h1><p>Book a free 30-minute cloud assessment call with our team.</p></main>
</body></html>"""

DEFAULT_PAGES = {
    "/": HOME_HTML,
    "/services": SERVICES_HTML,
    "/about": ABOUT_HTML,
    "/contact": CONTACT_HTML,
}

# What a well-behaved LLM returns for the Acme website (the "draft" schema).
VALID_ANALYSIS: dict[str, Any] = {
    "brand": {
        "name": "Acme Cloud Consulting",
        "category": "Cloud consulting and DevOps services",
        "summary": "Acme Cloud Consulting helps mid-sized companies migrate to the cloud.",
    },
    "competitor_analysis": {
        "brand_overview": "A consultancy offering cloud migration and DevOps automation.",
        "main_products_services": ["Cloud Migration", "DevOps Automation"],
        "verified_information": ["The website lists Cloud Migration as a service."],
        "competitors": [
            {
                "name": "Globex Cloud",
                "relevance": "Offers cloud migration to the same mid-market audience.",
                "strengths": ["Large delivery team"],
                "weaknesses_or_gaps": ["Less focus on mid-sized companies"],
            },
            {
                "name": "Initech Systems",
                "relevance": "Competes on DevOps consulting.",
                "strengths": ["Broad service catalogue"],
                "weaknesses_or_gaps": ["Generalist positioning"],
            },
            {
                "name": "Umbrella DevOps",
                "relevance": "Specialises in Kubernetes consulting.",
                "strengths": ["Kubernetes expertise"],
                "weaknesses_or_gaps": ["No migration offering"],
            },
            {
                "name": "Acme Cloud Consulting Inc.",
                "relevance": "This is the analysed brand and must be filtered out.",
                "strengths": [],
                "weaknesses_or_gaps": [],
            },
        ],
        "strengths": ["Zero-downtime migration promise stated on the homepage."],
        "opportunities": ["Position as the migration specialist for fintech."],
        "inferred_insights": ["Likely targets companies without in-house platform teams."],
    },
    "target_search_queries": [
        {
            "query": f"cloud migration consulting {index}",
            "recommended_platforms": ["Google", "linkedin", "Twitter"],
            "search_intent": "commercial",
            "business_relevance": "Matches the core migration service.",
            "target_audience": "CTOs at mid-sized companies",
        }
        for index in range(1, 6)
    ],
    "seo_improvements": {
        "primary_keyword": "cloud migration consulting",
        "secondary_keywords": ["devops automation services"],
        "long_tail_keywords": ["zero downtime cloud migration for fintech"],
        "business_keywords": ["cloud consulting"],
        "recommended_title": "Cloud Migration Consulting | Acme Cloud Consulting",
        "title_explanation": "Leads with the primary keyword, then the brand.",
        "meta_description": "Acme Cloud Consulting migrates mid-sized companies to the cloud.",
        "meta_description_business_summary": "Cloud migration for mid-sized companies.",
        "meta_description_keywords": ["cloud migration"],
        "supporting_content": [
            {
                "content_type": "service_landing_page",
                "title": "Zero-Downtime Cloud Migration",
                "description": "Landing page for the migration service.",
                "target_keyword": "zero downtime cloud migration",
            }
        ],
    },
    "top_services": [
        {
            "name": "Cloud Migration",
            "description": "Moving workloads to AWS, Azure or Google Cloud.",
            "target_industry": "Fintech and healthcare",
            "target_customer": "Mid-sized companies",
            "customer_benefit": "Migration with zero downtime.",
            "source_classification": "extracted",
            "evidence": "We move your workloads to AWS, Azure or Google Cloud",
        },
        {
            "name": "DevOps Automation",
            "description": "CI/CD pipelines and infrastructure as code.",
            "target_industry": "Software companies",
            "target_customer": "Engineering teams",
            "customer_benefit": "Faster releases.",
            "source_classification": "extracted",
            "evidence": "CI/CD pipelines and infrastructure as code",
        },
        {
            "name": "Kubernetes Consulting",
            "description": "Design and operation of Kubernetes clusters.",
            "target_industry": "Software companies",
            "target_customer": "Platform teams",
            "customer_benefit": "Reliable production clusters.",
            "source_classification": "extracted",
            "evidence": "production Kubernetes clusters",
        },
        {
            "name": "Blockchain Development",
            "description": "A service the website never mentions.",
            "target_industry": "Finance",
            "target_customer": "Startups",
            "customer_benefit": "Unknown.",
            "source_classification": "extracted",
            "evidence": "smart contract engineering",
        },
        {
            "name": "Managed Cloud Operations",
            "description": "Ongoing management after migration.",
            "target_industry": "Mid-market",
            "target_customer": "Companies without platform teams",
            "customer_benefit": "Predictable operations.",
            "source_classification": "suggested_opportunity",
            "evidence": "",
        },
    ],
    "target_audience": {
        "primary_audience": ["CTOs at mid-sized companies"],
        "secondary_audience": ["Engineering managers"],
        "industries": ["Fintech", "Healthcare"],
        "business_types": ["Mid-sized companies"],
        "customer_profiles": ["A CTO planning a data-centre exit."],
        "geographic_focus": ["North America"],
        "customer_needs": ["Migrate without downtime"],
        "customer_challenges": ["Limited in-house cloud expertise"],
        "potential_interests": ["Infrastructure as code"],
        "marketing_segments": ["Fintech scale-ups"],
    },
    "missing_information": ["Pricing is not stated on the website."],
}


def valid_analysis() -> dict[str, Any]:
    """Return a fresh, mutable copy of the valid LLM output."""
    return copy.deepcopy(VALID_ANALYSIS)


# --- Fakes --------------------------------------------------------------------


async def public_resolver(host: str, port: int) -> list[str]:
    """Fake DNS: every hostname resolves to one public address."""
    return [PUBLIC_IP]


class FakeWebsite:
    """In-process website. Maps paths to HTML, a status code or a callable."""

    def __init__(
        self, pages: dict[str, Any] | None = None, robots: str | int = "User-agent: *\nAllow: /\n"
    ) -> None:
        self.pages = dict(DEFAULT_PAGES if pages is None else pages)
        self.robots = robots
        self.requests: list[httpx.Request] = []

    @property
    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        page = self.robots if path == "/robots.txt" else self.pages.get(path, 404)
        if callable(page):
            page = page(request)
        if isinstance(page, BaseException):
            raise page
        if isinstance(page, httpx.Response):
            return page
        if isinstance(page, int):
            return httpx.Response(page, text="error")
        if path == "/robots.txt":
            return httpx.Response(200, text=page)
        return httpx.Response(200, html=page)


class FakeLLM:
    """In-process LLM API. Replays queued ``(status, body)`` replies or exceptions.

    The last queued reply is repeated for any further requests.
    """

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.replies[min(len(self.requests), len(self.replies)) - 1]
        if callable(reply):
            reply = reply(request)
        if isinstance(reply, BaseException):
            raise reply
        status, body = reply
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    def json_body(self, index: int = 0) -> dict[str, Any]:
        return json.loads(self.requests[index].content)


def openai_reply(payload: Any = None, *, status: int = 200) -> tuple[int, dict[str, Any]]:
    """Build an OpenAI Responses API success body wrapping ``payload``."""
    text = payload if isinstance(payload, str) else json.dumps(payload or VALID_ANALYSIS)
    return status, {
        "id": "resp_test",
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            },
        ],
        "usage": {"input_tokens": 5200, "output_tokens": 3100},
    }


def gemini_reply(payload: Any = None, *, finish_reason: str = "STOP") -> tuple[int, dict[str, Any]]:
    """Build a Gemini generateContent success body wrapping ``payload``."""
    text = payload if isinstance(payload, str) else json.dumps(payload or VALID_ANALYSIS)
    return 200, {
        "candidates": [
            {"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": finish_reason}
        ],
        "usageMetadata": {
            "promptTokenCount": 5000,
            "candidatesTokenCount": 2500,
            "thoughtsTokenCount": 500,
        },
    }


def provider_error(status: int, message: str, code: str = "error") -> tuple[int, dict[str, Any]]:
    return status, {"error": {"message": message, "type": code, "code": code}}


# --- Builders and fixtures ----------------------------------------------------


def make_settings(**overrides: Any) -> Settings:
    """Settings isolated from the developer's environment and ``.env`` file."""
    values: dict[str, Any] = {
        "llm_provider": "openai",
        "llm_fallback_provider": None,
        "openai_api_key": OPENAI_KEY,
        "openai_model": "test-openai-model",
        "openai_base_url": "https://api.openai.com/v1",
        "openai_reasoning_effort": None,
        "gemini_api_key": None,
        "gemini_model": "test-gemini-model",
        "gemini_base_url": "https://generativelanguage.googleapis.com/v1beta",
        "llm_max_retries": 2,
        "llm_retry_backoff_seconds": 0,
        "cache_ttl_seconds": 0,
        "log_format": "text",
        "allowed_ports": [80, 443],
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def make_extractor(
    settings: Settings,
    website: Callable[[httpx.Request], httpx.Response],
    resolver: Any = public_resolver,
) -> WebsiteExtractor:
    return WebsiteExtractor(
        settings, transport_factory=lambda: httpx.MockTransport(website), resolver=resolver
    )


def make_service(
    settings: Settings,
    website: Callable[[httpx.Request], httpx.Response],
    llm: Callable[[httpx.Request], httpx.Response],
    resolver: Any = public_resolver,
) -> AnalysisService:
    """Build the real service stack on top of fake website, DNS and LLM transports."""
    return AnalysisService(
        settings=settings,
        extractor=make_extractor(settings, website, resolver),
        llm_service=LLMService(settings, httpx.AsyncClient(transport=httpx.MockTransport(llm))),
        competitor_service=CompetitorService(),
        cache=TTLCache(settings.cache_ttl_seconds, settings.cache_max_entries),
    )


def make_client(settings: Settings, service: AnalysisService) -> TestClient:
    app = create_app(settings)
    app.dependency_overrides[get_analysis_service] = lambda: service
    return TestClient(app)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def website() -> FakeWebsite:
    return FakeWebsite()


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM(openai_reply())


@pytest.fixture
def client(settings: Settings, website: FakeWebsite, llm: FakeLLM) -> TestClient:
    """API client wired to the fake website and a fake OpenAI that always succeeds."""
    return make_client(settings, make_service(settings, website, llm))
