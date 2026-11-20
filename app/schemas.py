"""Pydantic schemas for the API and for the LLM's structured output.

Two families of models live here:

* ``*Draft`` models describe exactly what the LLM must generate. Their JSON
  Schema is sent to the provider as the structured-output contract, so every
  field is required and descriptions double as generation instructions.
* Response models extend the drafts with fields the *application* controls
  (provenance labels, research status, extraction facts). The LLM can never
  set those, which keeps "verified" labels trustworthy.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

AI_SUGGESTION = "ai_generated_suggestion"

# --- Enumerations -------------------------------------------------------------


class Platform(StrEnum):
    GOOGLE = "Google"
    FACEBOOK = "Facebook"
    INSTAGRAM = "Instagram"
    LINKEDIN = "LinkedIn"
    X = "X (Twitter)"


class SearchIntent(StrEnum):
    INFORMATIONAL = "informational"
    NAVIGATIONAL = "navigational"
    COMMERCIAL = "commercial"
    TRANSACTIONAL = "transactional"


class ServiceSource(StrEnum):
    EXTRACTED = "extracted"
    INFERRED = "inferred"
    SUGGESTED_OPPORTUNITY = "suggested_opportunity"


class CompetitorSource(StrEnum):
    VERIFIED = "verified"
    AI_SUGGESTED = "ai_suggested"


class ResearchStatus(StrEnum):
    EXTERNAL_RESEARCH = "external_research"
    AI_SUGGESTED_UNVERIFIED = "ai_suggested_unverified"
    INSUFFICIENT_INFORMATION = "insufficient_information"


class ContentType(StrEnum):
    SERVICE_LANDING_PAGE = "service_landing_page"
    INDUSTRY_PAGE = "industry_page"
    FAQ = "faq"
    BLOG_TOPIC = "blog_topic"
    LOCATION_PAGE = "location_page"
    BUSINESS_ARTICLE = "business_article"


_PLATFORM_ALIASES = {
    "x": Platform.X,
    "twitter": Platform.X,
    "x/twitter": Platform.X,
    "twitter/x": Platform.X,
    **{platform.value.lower(): platform for platform in Platform},
}

# --- Request ------------------------------------------------------------------


class LinkAnalysisRequest(BaseModel):
    """Request body for ``POST /link-analysis``."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={"example": {"url": "https://example.com"}},
    )

    url: str = Field(
        min_length=1,
        max_length=2048,
        description="Publicly accessible website URL, including http:// or https://.",
    )


# --- LLM draft models (structured-output contract) ----------------------------


class _Draft(BaseModel):
    """Base for LLM-generated models: tolerant of extra keys, trims strings."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class BrandInfo(_Draft):
    name: str = Field(
        min_length=1, description="Brand or company name exactly as written on the website."
    )
    category: str = Field(description="Main business category, as specific as the content allows.")
    summary: str = Field(
        description="Factual 2-3 sentence business overview grounded in the website content."
    )


class CompetitorDraft(_Draft):
    name: str = Field(min_length=1, description="Name of a real company you are confident exists.")
    relevance: str = Field(
        description="Why it competes with the brand: overlapping offer, audience or market."
    )
    strengths: list[str] = Field(
        description="Generally known strengths, phrased as general knowledge, not verified facts."
    )
    weaknesses_or_gaps: list[str] = Field(
        description="Likely weaknesses or market gaps the analysed brand could exploit."
    )


class CompetitorAnalysisDraft(_Draft):
    brand_overview: str = Field(
        description="What the business does and for whom, based only on the website content."
    )
    main_products_services: list[str] = Field(
        description="Products and services explicitly offered on the website."
    )
    verified_information: list[str] = Field(
        description="Facts stated directly on the website. No assumptions."
    )
    competitors: list[CompetitorDraft] = Field(
        max_length=5,
        description=(
            "Relevant competitors. Aim for at least three when you can name them reliably; "
            "return fewer rather than guessing."
        ),
    )
    strengths: list[str] = Field(
        description="The analysed brand's competitive advantages, tied to evidence on the website."
    )
    opportunities: list[str] = Field(
        description="Market positioning opportunities specific to this brand."
    )
    inferred_insights: list[str] = Field(
        description="Reasoned assumptions that go beyond what the website states."
    )

    @field_validator("competitors", mode="before")
    @classmethod
    def _limit_competitors(cls, value: Any) -> Any:
        return value[:5] if isinstance(value, list) else value


class SearchQueryDraft(_Draft):
    query: str = Field(min_length=1, description="Search query a potential customer would type.")
    recommended_platforms: list[Platform] = Field(
        min_length=1, description="Platforms where this query is most relevant."
    )
    search_intent: SearchIntent
    business_relevance: str = Field(
        description="Why this query matters for this specific business."
    )
    target_audience: str = Field(description="Who is likely to search for this.")

    @field_validator("recommended_platforms", mode="before")
    @classmethod
    def _normalise_platforms(cls, value: Any) -> Any:
        """Map common spellings such as ``Twitter`` or ``linkedin`` to the enum."""
        if not isinstance(value, list):
            return value
        normalised = [
            _PLATFORM_ALIASES.get(item.strip().lower(), item) if isinstance(item, str) else item
            for item in value
        ]
        return list(dict.fromkeys(normalised))


class SupportingContentIdea(_Draft):
    content_type: ContentType
    title: str = Field(min_length=1, description="Working title of the page or article.")
    description: str = Field(description="What the content covers and why it helps this business.")
    target_keyword: str = Field(description="Main keyword the content should target.")


class SEOImprovementsDraft(_Draft):
    primary_keyword: str = Field(min_length=1, description="Single most important keyword.")
    secondary_keywords: list[str]
    long_tail_keywords: list[str]
    business_keywords: list[str] = Field(
        description="Keywords describing the business, its industry and its offer."
    )
    recommended_title: str = Field(
        min_length=1,
        description="Improved SEO title of about 50-60 characters containing the primary keyword.",
    )
    title_explanation: str = Field(
        description="How the recommended title structure improves on the current title."
    )
    meta_description: str = Field(
        min_length=1,
        description="Recommended meta description of about 150-160 characters.",
    )
    meta_description_business_summary: str = Field(
        description="One-sentence business summary the meta description conveys."
    )
    meta_description_keywords: list[str] = Field(
        description="Keywords included in the recommended meta description."
    )
    supporting_content: list[SupportingContentIdea] = Field(
        description=(
            "Supporting content ideas: service landing pages, industry pages, FAQs, blog "
            "topics, business articles, and location pages only when a location is identifiable."
        )
    )


class ServiceItem(_Draft):
    name: str = Field(min_length=1)
    description: str
    target_industry: str
    target_customer: str
    customer_benefit: str = Field(description="Main benefit for the customer.")
    source_classification: ServiceSource = Field(
        description=(
            "'extracted' if the service is stated on the website, 'inferred' if implied by "
            "the content, 'suggested_opportunity' if it is not offered today."
        )
    )
    evidence: str = Field(
        description=(
            "Short phrase copied from the website supporting this service. "
            "Empty string for suggested opportunities."
        )
    )


class TargetAudience(_Draft):
    primary_audience: list[str]
    secondary_audience: list[str]
    industries: list[str] = Field(description="Industry segments the business serves.")
    business_types: list[str] = Field(
        description="Types of organisations or consumers served (e.g. SMEs, agencies)."
    )
    customer_profiles: list[str] = Field(
        description="Short persona descriptions: role, context and goal."
    )
    geographic_focus: list[str] = Field(
        description="Locations or markets named on the website. Empty if not identifiable."
    )
    customer_needs: list[str]
    customer_challenges: list[str]
    potential_interests: list[str]
    marketing_segments: list[str] = Field(
        description="Recommended audience segments for marketing campaigns."
    )


class LLMAnalysisDraft(_Draft):
    """Complete structured output expected from the LLM."""

    brand: BrandInfo
    competitor_analysis: CompetitorAnalysisDraft
    target_search_queries: list[SearchQueryDraft] = Field(
        max_length=5, description="The top five target search queries."
    )
    seo_improvements: SEOImprovementsDraft
    top_services: list[ServiceItem] = Field(
        max_length=5,
        description=(
            "The top five services: existing services first, then clearly labelled "
            "suggested opportunities if fewer than five exist."
        ),
    )
    target_audience: TargetAudience
    missing_information: list[str] = Field(
        description="Important information that could not be found on the website."
    )

    @field_validator("target_search_queries", "top_services", mode="before")
    @classmethod
    def _limit_to_five(cls, value: Any) -> Any:
        return value[:5] if isinstance(value, list) else value


# --- Response models ----------------------------------------------------------


class Competitor(CompetitorDraft):
    source_classification: CompetitorSource = Field(
        description=(
            "'verified' only when backed by external research evidence; otherwise "
            "'ai_suggested' (from the model's general knowledge, not verified)."
        )
    )


class CompetitorAnalysis(BaseModel):
    """Section 1: competitor vs. your brand details."""

    brand_name: str
    brand_overview: str
    business_category: str
    main_products_services: list[str]
    verified_information: list[str] = Field(description="Facts stated on the analysed website.")
    competitors: list[Competitor]
    strengths: list[str] = Field(description="Brand competitive advantages (inferred insights).")
    opportunities: list[str] = Field(
        description="Market positioning opportunities (inferred insights)."
    )
    inferred_insights: list[str]
    research_status: ResearchStatus
    research_note: str = Field(description="How the competitor information was obtained.")


class SearchQuery(SearchQueryDraft):
    """Section 2 item: one target search query."""

    data_source: Literal["ai_generated_suggestion"] = Field(
        default=AI_SUGGESTION,
        description="Always an AI-generated suggestion; no search-volume or ranking data is used.",
    )


class SEOImprovements(SEOImprovementsDraft):
    """Section 3: SEO improvements."""

    current_title: str | None = Field(description="Title currently used by the website.")
    current_meta_description: str | None = Field(
        description="Meta description currently used by the website."
    )
    data_source: Literal["ai_generated_suggestion"] = Field(
        default=AI_SUGGESTION,
        description="Recommendations are AI-generated and not backed by SEO tool data.",
    )


class Analysis(BaseModel):
    """The five required analysis sections."""

    competitor_analysis: CompetitorAnalysis
    target_search_queries: list[SearchQuery]
    seo_improvements: SEOImprovements
    top_services: list[ServiceItem]
    target_audience: TargetAudience


class ContactDetails(BaseModel):
    emails: list[str] = []
    phones: list[str] = []
    addresses: list[str] = []


class ExtractionMetadata(BaseModel):
    """Facts about what was fetched from the website (extracted, not generated)."""

    final_url: str
    pages_analyzed: list[str]
    content_characters: int
    content_truncated: bool
    robots_txt_respected: bool = True
    social_links: list[str] = []
    contact_details: ContactDetails = ContactDetails()


class TokenUsage(BaseModel):
    """Token counts reported by the LLM provider for the successful call."""

    input_tokens: int | None = None
    output_tokens: int | None = None


class AnalysisMetadata(BaseModel):
    analysis_timestamp: datetime
    llm_provider: str
    llm_model: str
    token_usage: TokenUsage
    processing_time_ms: int
    cached: bool = False
    extraction_metadata: ExtractionMetadata
    source_classification: dict[str, str] = Field(
        description="Explains, per section, whether data is extracted, inferred or AI-suggested."
    )


class LinkAnalysisResponse(BaseModel):
    """Successful response of ``POST /link-analysis``."""

    success: Literal[True] = True
    website: str
    brand: BrandInfo
    analysis: Analysis
    warnings: list[str] = Field(
        default=[], description="Caveats about data quality or coverage of this analysis."
    )
    missing_information: list[str] = Field(
        default=[], description="Information the model could not find on the website."
    )
    metadata: AnalysisMetadata


# --- Errors and service endpoints ---------------------------------------------


class ErrorDetail(BaseModel):
    code: str = Field(description="Stable machine-readable error code.")
    message: str = Field(description="Human-readable explanation.")
    details: list[dict[str, Any]] | None = Field(
        default=None, description="Field-level validation errors, when applicable."
    )


class ErrorResponse(BaseModel):
    """Error envelope returned for every non-2xx response."""

    success: Literal[False] = False
    error: ErrorDetail
    request_id: str | None = None


class ServiceInfo(BaseModel):
    """Response of ``GET /``."""

    name: str
    version: str
    description: str
    documentation: str
    endpoints: dict[str, str]


class HealthResponse(BaseModel):
    """Response of ``GET /health``."""

    status: Literal["ok"] = "ok"
    version: str
    environment: str
    llm_provider: str
    llm_model: str
    llm_configured: bool = Field(
        description="Whether an API key is present for the selected LLM provider."
    )
    timestamp: datetime
