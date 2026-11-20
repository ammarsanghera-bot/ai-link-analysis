"""FastAPI application entry point: wiring, routes and API documentation.

Business logic lives in ``app.services``; this module only exposes it over HTTP.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Body, Depends, FastAPI

from app.config import Settings, get_settings
from app.dependencies import build_analysis_service, get_analysis_service, get_app_settings
from app.error_handlers import register_error_handling
from app.examples import EXAMPLE_REQUEST, EXAMPLE_RESPONSE, error_example
from app.logging_config import configure_logging
from app.schemas import (
    ErrorResponse,
    HealthResponse,
    LinkAnalysisRequest,
    LinkAnalysisResponse,
    ServiceInfo,
)
from app.services.analysis_service import AnalysisService

logger = logging.getLogger(__name__)

API_DESCRIPTION = """
Analyses any publicly accessible website with an LLM and returns structured business,
competitor, SEO, search-query, service and target-audience insights.

**How it works**

1. The URL is validated and checked against SSRF rules (public HTTP/HTTPS only).
2. The homepage and a few key pages are fetched, respecting `robots.txt`.
3. HTML is cleaned and reduced to a compact, de-duplicated content summary.
4. The configured LLM (OpenAI by default, Gemini as alternative) generates the five
   analysis sections as structured JSON, which is validated with Pydantic.

**Reading the results**

Every section states where its data comes from. Facts extracted from the website are kept
apart from AI-generated suggestions and inferred insights; see
`metadata.source_classification`, each competitor's and service's `source_classification`,
and the `warnings` list.
"""

router = APIRouter()


def _error(description: str, code: str, message: str) -> dict[str, Any]:
    """Describe one error response for the OpenAPI schema."""
    return {
        "model": ErrorResponse,
        "description": description,
        "content": {"application/json": {"example": error_example(code, message)}},
    }


LINK_ANALYSIS_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Analysis generated successfully (illustrative example).",
        "content": {"application/json": {"example": EXAMPLE_RESPONSE}},
    },
    400: _error(
        "The URL is malformed or not an HTTP(S) URL.",
        "invalid_url",
        "Only http:// and https:// URLs are supported.",
    ),
    403: _error(
        "The destination is private, internal or disallowed by robots.txt.",
        "forbidden_destination",
        "The URL points to a private network address, which is not allowed.",
    ),
    422: _error(
        "The request body is missing or malformed.",
        "invalid_request",
        "The request body is invalid.",
    ),
    500: _error(
        "Unexpected internal error.", "internal_error", "An unexpected internal error occurred."
    ),
    502: _error(
        "The website could not be fetched, or the LLM provider failed.",
        "website_fetch_failed",
        "The page was not found on the website (HTTP 404).",
    ),
    503: _error(
        "The selected LLM provider has no API key configured.",
        "llm_not_configured",
        "The AI provider 'openai' is not configured: set OPENAI_API_KEY on the server.",
    ),
    504: _error(
        "The website or the LLM provider timed out.",
        "website_timeout",
        "The website did not respond in time.",
    ),
}


@router.get("/", response_model=ServiceInfo, tags=["Service"], summary="Service information")
async def root(settings: Annotated[Settings, Depends(get_app_settings)]) -> ServiceInfo:
    """Return basic information about the API and where to find its documentation."""
    return ServiceInfo(
        name=settings.app_name,
        version=settings.app_version,
        description="Analyses a public website with an LLM and returns structured insights.",
        documentation="/docs",
        endpoints={
            "GET /": "Service information",
            "GET /health": "Health check",
            "POST /link-analysis": "Analyse a website URL",
            "GET /docs": "Interactive API documentation (Swagger UI)",
            "GET /redoc": "API reference (ReDoc)",
        },
    )


@router.get("/health", response_model=HealthResponse, tags=["Service"], summary="Health check")
async def health(settings: Annotated[Settings, Depends(get_app_settings)]) -> HealthResponse:
    """Liveness probe. Also reports whether the selected LLM provider has an API key.

    The check makes no outbound calls, so it stays fast and free.
    """
    return HealthResponse(
        version=settings.app_version,
        environment=settings.environment,
        llm_provider=settings.llm_provider,
        llm_model=settings.model_for(settings.llm_provider),
        llm_configured=settings.api_key_for(settings.llm_provider) is not None,
        timestamp=datetime.now(UTC),
    )


@router.post(
    "/link-analysis",
    response_model=LinkAnalysisResponse,
    responses=LINK_ANALYSIS_RESPONSES,
    tags=["Analysis"],
    summary="Analyse a website URL",
)
async def link_analysis(
    payload: Annotated[
        LinkAnalysisRequest,
        Body(
            openapi_examples={
                "website": {
                    "summary": "Public website",
                    "description": "Any publicly accessible http:// or https:// URL.",
                    "value": {"url": "https://example.com"},
                },
                "business": {
                    "summary": "Business website (illustrative)",
                    "value": EXAMPLE_REQUEST,
                },
            }
        ),
    ],
    service: Annotated[AnalysisService, Depends(get_analysis_service)],
) -> LinkAnalysisResponse:
    """Fetch the website, analyse it with the configured LLM and return five sections:

    1. **Competitor vs. your brand** - overview, competitors, strengths, opportunities.
    2. **Top 5 target search queries** - with platforms, intent and audience.
    3. **SEO improvements** - keywords, title, meta description, supporting content.
    4. **Top 5 services** - each labelled as extracted, inferred or suggested.
    5. **Target audience** - segments, needs, challenges and marketing audiences.

    Each request crawls the website and makes an LLM call, so use a generous client
    timeout: by default the server allows up to 30 seconds for crawling and 90 seconds
    per LLM attempt. Repeated requests for the same URL are served from an in-memory cache.
    """
    return await service.analyze(payload.url)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory.

    Args:
        settings: Optional settings override (used by tests); defaults to the
            environment-based settings.
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # One pooled HTTP client for all LLM calls, closed on shutdown.
        async with httpx.AsyncClient() as llm_client:
            app.state.analysis_service = build_analysis_service(settings, llm_client)
            logger.info(
                "application started",
                extra={
                    "environment": settings.environment,
                    "llm_provider": settings.llm_provider,
                    "llm_model": settings.model_for(settings.llm_provider),
                    "llm_configured": settings.api_key_for(settings.llm_provider) is not None,
                },
            )
            yield
        logger.info("application stopped")

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=API_DESCRIPTION,
        lifespan=lifespan,
        openapi_tags=[
            {"name": "Analysis", "description": "AI-powered website analysis."},
            {"name": "Service", "description": "Service information and health."},
        ],
    )
    app.state.settings = settings
    register_error_handling(app)
    app.include_router(router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    _settings = get_settings()
    # Logging is configured by the application, so uvicorn's own config is disabled.
    uvicorn.run(
        app, host=_settings.host, port=_settings.port, log_config=None, access_log=False
    )
