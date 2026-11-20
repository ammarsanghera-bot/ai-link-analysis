"""Illustrative payloads for the OpenAPI documentation.

These show the *shape* of responses. The business, competitors and figures are
fictional and were written by hand; they are not captured LLM output.
"""

from typing import Any

EXAMPLE_REQUEST = {"url": "https://www.brightleaf-accounting.example"}

EXAMPLE_RESPONSE: dict[str, Any] = {
    "success": True,
    "website": "https://www.brightleaf-accounting.example",
    "brand": {
        "name": "BrightLeaf Accounting",
        "category": "Accounting and bookkeeping services for small businesses",
        "summary": (
            "BrightLeaf Accounting provides online bookkeeping, payroll and tax return "
            "services to freelancers and small businesses on fixed monthly plans."
        ),
    },
    "analysis": {
        "competitor_analysis": {
            "brand_name": "BrightLeaf Accounting",
            "brand_overview": (
                "An online accounting practice offering bookkeeping, payroll and tax returns "
                "to freelancers and small businesses for a fixed monthly fee."
            ),
            "business_category": "Accounting and bookkeeping services for small businesses",
            "main_products_services": ["Bookkeeping", "Payroll", "Self-assessment tax returns"],
            "verified_information": [
                "The website offers bookkeeping, payroll and tax return services.",
                "Pricing is presented as fixed monthly plans.",
            ],
            "competitors": [
                {
                    "name": "Example Ledger Co",
                    "relevance": "Targets the same freelancer and small-business audience.",
                    "strengths": ["Widely recognised brand in online accounting"],
                    "weaknesses_or_gaps": ["Less emphasis on a dedicated named accountant"],
                    "source_classification": "ai_suggested",
                },
                {
                    "name": "Sample Books Online",
                    "relevance": "Offers subscription bookkeeping to small businesses.",
                    "strengths": ["Broad software integrations"],
                    "weaknesses_or_gaps": ["Tax return filing is sold as a separate add-on"],
                    "source_classification": "ai_suggested",
                },
                {
                    "name": "Demo Tax Partners",
                    "relevance": "Competes for self-assessment tax return customers.",
                    "strengths": ["Strong focus on tax advice"],
                    "weaknesses_or_gaps": ["No bundled payroll service"],
                    "source_classification": "ai_suggested",
                },
            ],
            "strengths": [
                "Fixed monthly pricing is stated up front, which reduces cost uncertainty.",
                "Bookkeeping, payroll and tax returns are available from a single provider.",
            ],
            "opportunities": [
                "Position as the all-in-one fixed-fee option for first-time freelancers.",
            ],
            "inferred_insights": [
                "The plan structure suggests a focus on price-sensitive sole traders.",
            ],
            "research_status": "ai_suggested_unverified",
            "research_note": (
                "No external competitor research was performed. The competitors listed are "
                "AI-generated suggestions based on the model's general knowledge and the "
                "website's content; they have not been verified. Their strengths and "
                "weaknesses are general observations, not researched facts. Further "
                "competitor research is required before relying on them."
            ),
        },
        "target_search_queries": [
            {
                "query": "online accountant for freelancers",
                "recommended_platforms": ["Google", "LinkedIn"],
                "search_intent": "commercial",
                "business_relevance": "Matches the core fixed-fee offer for freelancers.",
                "target_audience": "Freelancers choosing their first accountant",
                "data_source": "ai_generated_suggestion",
            },
            {
                "query": "fixed fee bookkeeping service small business",
                "recommended_platforms": ["Google"],
                "search_intent": "transactional",
                "business_relevance": "Reflects the fixed monthly plans shown on the website.",
                "target_audience": "Small-business owners comparing providers",
                "data_source": "ai_generated_suggestion",
            },
            {
                "query": "how to file a self assessment tax return",
                "recommended_platforms": ["Google", "Facebook"],
                "search_intent": "informational",
                "business_relevance": "Attracts people who may outsource their tax return.",
                "target_audience": "Sole traders filing for the first time",
                "data_source": "ai_generated_suggestion",
            },
            {
                "query": "payroll service for small teams",
                "recommended_platforms": ["Google", "LinkedIn"],
                "search_intent": "commercial",
                "business_relevance": "Promotes the payroll service to growing businesses.",
                "target_audience": "Owners hiring their first employees",
                "data_source": "ai_generated_suggestion",
            },
            {
                "query": "freelancer bookkeeping tips",
                "recommended_platforms": ["Instagram", "X (Twitter)"],
                "search_intent": "informational",
                "business_relevance": "Supports awareness content that leads to the service.",
                "target_audience": "Freelancers managing their own books",
                "data_source": "ai_generated_suggestion",
            },
        ],
        "seo_improvements": {
            "primary_keyword": "online accountant for small business",
            "secondary_keywords": ["fixed fee bookkeeping", "small business payroll service"],
            "long_tail_keywords": ["fixed monthly fee accountant for freelancers"],
            "business_keywords": ["bookkeeping", "payroll", "tax returns"],
            "recommended_title": "Online Accountant for Small Business | BrightLeaf Accounting",
            "title_explanation": (
                "Leads with the primary keyword and ends with the brand, replacing the "
                "current title, which contains only the brand name."
            ),
            "meta_description": (
                "BrightLeaf Accounting handles bookkeeping, payroll and tax returns for "
                "freelancers and small businesses on simple fixed monthly plans."
            ),
            "meta_description_business_summary": (
                "Fixed-fee online accounting for freelancers and small businesses."
            ),
            "meta_description_keywords": ["bookkeeping", "payroll", "tax returns"],
            "supporting_content": [
                {
                    "content_type": "service_landing_page",
                    "title": "Fixed-Fee Bookkeeping for Small Businesses",
                    "description": "Dedicated page explaining what the bookkeeping plan covers.",
                    "target_keyword": "fixed fee bookkeeping",
                },
                {
                    "content_type": "faq",
                    "title": "What is included in each monthly plan?",
                    "description": "Answers the most common pre-sales pricing question.",
                    "target_keyword": "accountant monthly plan",
                },
            ],
            "current_title": "BrightLeaf Accounting",
            "current_meta_description": None,
            "data_source": "ai_generated_suggestion",
        },
        "top_services": [
            {
                "name": "Bookkeeping",
                "description": "Ongoing recording and reconciliation of business transactions.",
                "target_industry": "Small businesses across sectors",
                "target_customer": "Freelancers and small-business owners",
                "customer_benefit": "Accurate books without doing the work in-house.",
                "source_classification": "extracted",
                "evidence": "Monthly bookkeeping",
            },
            {
                "name": "Payroll",
                "description": "Payslip processing and payroll submissions for small teams.",
                "target_industry": "Small employers",
                "target_customer": "Businesses with employees",
                "customer_benefit": "Staff are paid correctly and on time.",
                "source_classification": "extracted",
                "evidence": "Payroll for small teams",
            },
            {
                "name": "Self-assessment tax returns",
                "description": "Preparation and filing of personal tax returns.",
                "target_industry": "Self-employed professionals",
                "target_customer": "Sole traders",
                "customer_benefit": "Returns are filed on time with fewer errors.",
                "source_classification": "extracted",
                "evidence": "Tax returns filed for you",
            },
            {
                "name": "Year-end accounts",
                "description": "Preparation of annual accounts, implied by the plans offered.",
                "target_industry": "Small limited companies",
                "target_customer": "Company directors",
                "customer_benefit": "Statutory obligations are met without extra providers.",
                "source_classification": "inferred",
                "evidence": "",
            },
            {
                "name": "Business start-up advisory",
                "description": "Guidance on structure and registration for new businesses.",
                "target_industry": "New businesses",
                "target_customer": "First-time founders",
                "customer_benefit": "Fewer costly mistakes when starting out.",
                "source_classification": "suggested_opportunity",
                "evidence": "",
            },
        ],
        "target_audience": {
            "primary_audience": ["Freelancers and sole traders"],
            "secondary_audience": ["Small limited companies with a few employees"],
            "industries": ["Professional services", "Creative services"],
            "business_types": ["Sole traders", "Micro-businesses"],
            "customer_profiles": [
                "A newly self-employed designer who wants tax handled for a predictable fee."
            ],
            "geographic_focus": [],
            "customer_needs": ["Predictable accounting costs", "Timely tax filing"],
            "customer_challenges": ["Limited time for bookkeeping", "Unfamiliar tax rules"],
            "potential_interests": ["Invoicing tools", "Cash-flow management"],
            "marketing_segments": ["Freelancers in their first year of trading"],
        },
    },
    "warnings": [
        "Competitors are AI-generated suggestions and have not been verified by external "
        "research.",
        "Some top_services entries are suggested opportunities, not services the website "
        "currently offers; see each item's source_classification.",
    ],
    "missing_information": [
        "The website does not state which geographic area is served.",
        "No meta description is set on the homepage.",
    ],
    "metadata": {
        "analysis_timestamp": "2026-11-19T09:30:00Z",
        "llm_provider": "openai",
        "llm_model": "gpt-6.1-terra",
        "token_usage": {"input_tokens": 6120, "output_tokens": 3480},
        "processing_time_ms": 18450,
        "cached": False,
        "extraction_metadata": {
            "final_url": "https://www.brightleaf-accounting.example/",
            "pages_analyzed": [
                "https://www.brightleaf-accounting.example/",
                "https://www.brightleaf-accounting.example/services",
                "https://www.brightleaf-accounting.example/about",
            ],
            "content_characters": 9840,
            "content_truncated": False,
            "robots_txt_respected": True,
            "social_links": ["https://www.linkedin.com/company/brightleaf-accounting-example"],
            "contact_details": {
                "emails": ["hello@brightleaf-accounting.example"],
                "phones": [],
                "addresses": [],
            },
        },
        "source_classification": {
            "analysis.competitor_analysis.competitors": (
                "Each competitor carries source_classification: 'verified' (backed by "
                "external research evidence) or 'ai_suggested' (unverified AI-generated "
                "suggestion)."
            ),
            "analysis.target_search_queries": (
                "AI-generated keyword suggestions. No search-volume, keyword-difficulty or "
                "ranking data was used."
            ),
        },
    },
}


def error_example(code: str, message: str) -> dict[str, Any]:
    """Build an example error body for the OpenAPI documentation."""
    return {
        "success": False,
        "error": {"code": code, "message": message},
        "request_id": "3f6c1f0a9d8e4b7c8a1b2c3d4e5f6a7b",
    }
