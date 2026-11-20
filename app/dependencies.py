"""Dependency wiring for the FastAPI application."""

import httpx
from fastapi import Request

from app.config import Settings
from app.services.analysis_service import AnalysisService
from app.services.competitor_service import CompetitorService
from app.services.llm_service import LLMService
from app.services.website_extractor import WebsiteExtractor
from app.utils.cache import TTLCache


def build_analysis_service(settings: Settings, llm_client: httpx.AsyncClient) -> AnalysisService:
    """Compose the analysis service and its collaborators."""
    return AnalysisService(
        settings=settings,
        extractor=WebsiteExtractor(settings),
        llm_service=LLMService(settings, llm_client),
        competitor_service=CompetitorService(),
        cache=TTLCache(settings.cache_ttl_seconds, settings.cache_max_entries),
    )


def get_app_settings(request: Request) -> Settings:
    """FastAPI dependency: the settings the application was created with."""
    return request.app.state.settings


def get_analysis_service(request: Request) -> AnalysisService:
    """FastAPI dependency: the shared analysis service built at startup."""
    return request.app.state.analysis_service
