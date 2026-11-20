"""Competitor discovery and research.

The LLM can *suggest* competitors from general knowledge, but a suggestion is
not research. This service keeps the two apart:

* ``CompetitorResearchProvider`` is the extension point for real external
  research (a search API, a business-data API, ...). None is bundled, so the
  default provider returns no evidence.
* ``CompetitorService.finalize`` decides the provenance label of every
  competitor. A competitor is ``verified`` only when it matches evidence
  returned by a research provider; the LLM's own claims are never trusted.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Protocol

from app.schemas import (
    BrandInfo,
    Competitor,
    CompetitorAnalysis,
    CompetitorAnalysisDraft,
    CompetitorSource,
    ResearchStatus,
)
from app.services.website_extractor import ExtractedWebsite

logger = logging.getLogger(__name__)

MIN_COMPETITORS = 3
MAX_COMPETITORS = 5
_COMPANY_SUFFIX = re.compile(r"\b(inc|llc|ltd|limited|gmbh|corp|corporation|co|plc)\b\.?", re.I)

NOTE_UNVERIFIED = (
    "No external competitor research was performed. The competitors listed are AI-generated "
    "suggestions based on the model's general knowledge and the website's content; they have "
    "not been verified. Their strengths and weaknesses are general observations, not "
    "researched facts. Further competitor research is required before relying on them."
)
NOTE_INSUFFICIENT = (
    "No competitors could be suggested with reasonable confidence from the available "
    "website content, and no external competitor research was performed. Further competitor "
    "research is required."
)
NOTE_EXTERNAL = (
    "Competitors labelled 'verified' are backed by external research evidence from the "
    "'{provider}' research provider. Competitors labelled 'ai_suggested' are unverified "
    "AI-generated suggestions."
)


@dataclass(frozen=True)
class CompetitorEvidence:
    """One externally sourced fact identifying a competitor."""

    name: str
    source_url: str
    summary: str


class CompetitorResearchProvider(Protocol):
    """Interface for external competitor research integrations."""

    name: str

    async def research(self, site: ExtractedWebsite) -> list[CompetitorEvidence]:
        """Return evidence about competitors of the business behind ``site``."""
        ...


class NoExternalResearchProvider:
    """Default provider: performs no external research and returns no evidence."""

    name = "none"

    async def research(self, site: ExtractedWebsite) -> list[CompetitorEvidence]:
        return []


@dataclass
class CompetitorResearchContext:
    """Research evidence gathered for one analysis."""

    provider_name: str = "none"
    evidence: list[CompetitorEvidence] = field(default_factory=list)

    @property
    def has_evidence(self) -> bool:
        return bool(self.evidence)


def _normalise_name(name: str) -> str:
    """Normalise a company name for comparison (case, punctuation, legal suffix)."""
    without_suffix = _COMPANY_SUFFIX.sub("", name.lower())
    return re.sub(r"[^a-z0-9]+", "", without_suffix)


class CompetitorService:
    """Coordinates competitor research and labels competitor provenance."""

    def __init__(self, research_provider: CompetitorResearchProvider | None = None) -> None:
        self._provider = research_provider or NoExternalResearchProvider()

    async def gather_research(self, site: ExtractedWebsite) -> CompetitorResearchContext:
        """Run external research; on failure degrade to "no evidence" instead of failing."""
        try:
            evidence = await self._provider.research(site)
        except Exception:
            logger.exception(
                "competitor research provider failed", extra={"provider": self._provider.name}
            )
            evidence = []
        return CompetitorResearchContext(provider_name=self._provider.name, evidence=evidence)

    def build_prompt_section(self, context: CompetitorResearchContext) -> str | None:
        """Render research evidence for the LLM prompt (``None`` when there is none)."""
        if not context.has_evidence:
            return None
        lines = [
            "External research found the following competitors. Prefer these, and base their "
            "strengths and weaknesses on the evidence given:"
        ]
        lines.extend(
            f"- {item.name}: {item.summary} (source: {item.source_url})"
            for item in context.evidence
        )
        lines.append(
            "Any competitor you add beyond this list is an unverified AI-generated suggestion."
        )
        return "\n".join(lines)

    def finalize(
        self,
        draft: CompetitorAnalysisDraft,
        brand: BrandInfo,
        context: CompetitorResearchContext,
    ) -> tuple[CompetitorAnalysis, list[str]]:
        """Turn the LLM's draft into the final section with enforced provenance labels.

        Returns the section and any warnings to surface to the API client.
        """
        verified_names = {_normalise_name(item.name) for item in context.evidence}
        brand_key = _normalise_name(brand.name)
        competitors: list[Competitor] = []
        seen: set[str] = set()

        for candidate in draft.competitors:
            key = _normalise_name(candidate.name)
            # Drop empty names, duplicates and the analysed brand itself.
            if not key or key == brand_key or key in seen:
                continue
            seen.add(key)
            source = (
                CompetitorSource.VERIFIED if key in verified_names else CompetitorSource.AI_SUGGESTED
            )
            competitors.append(Competitor(**candidate.model_dump(), source_classification=source))
            if len(competitors) >= MAX_COMPETITORS:
                break

        has_verified = any(c.source_classification is CompetitorSource.VERIFIED for c in competitors)
        if not competitors:
            status, note = ResearchStatus.INSUFFICIENT_INFORMATION, NOTE_INSUFFICIENT
        elif has_verified:
            status = ResearchStatus.EXTERNAL_RESEARCH
            note = NOTE_EXTERNAL.format(provider=context.provider_name)
        else:
            status, note = ResearchStatus.AI_SUGGESTED_UNVERIFIED, NOTE_UNVERIFIED

        warnings: list[str] = []
        if len(competitors) < MIN_COMPETITORS:
            warnings.append(
                f"Only {len(competitors)} competitor(s) could be suggested with reasonable "
                "confidence; further competitor research is required."
            )
        if competitors and not has_verified:
            warnings.append(
                "Competitors are AI-generated suggestions and have not been verified by "
                "external research."
            )

        section = CompetitorAnalysis(
            brand_name=brand.name,
            brand_overview=draft.brand_overview,
            business_category=brand.category,
            main_products_services=draft.main_products_services,
            verified_information=draft.verified_information,
            competitors=competitors,
            strengths=draft.strengths,
            opportunities=draft.opportunities,
            inferred_insights=draft.inferred_insights,
            research_status=status,
            research_note=note,
        )
        return section, warnings
