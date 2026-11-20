"""Application exception hierarchy.

Every exception maps to exactly one HTTP status code and one stable,
machine-readable error code. Messages are written for API consumers and must
never contain stack traces, credentials or internal network details.
"""


class AppError(Exception):
    """Base class for all errors that are safe to report to API clients."""

    status_code: int = 500
    code: str = "internal_error"
    default_message: str = "An unexpected internal error occurred."

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)


# --- Request / destination errors -------------------------------------------


class InvalidURLError(AppError):
    """The supplied URL is syntactically invalid or not an HTTP(S) URL."""

    status_code = 400
    code = "invalid_url"
    default_message = "The provided URL is not a valid public HTTP(S) URL."


class ForbiddenDestinationError(AppError):
    """The URL points to a private, internal or otherwise prohibited destination."""

    status_code = 403
    code = "forbidden_destination"
    default_message = "The destination is private, internal or otherwise prohibited."


class RobotsDisallowedError(AppError):
    """The website's robots.txt does not allow this crawler to fetch the page."""

    status_code = 403
    code = "robots_disallowed"
    default_message = "The website's robots.txt does not allow automated access to this page."


# --- Website fetching errors ------------------------------------------------


class WebsiteFetchError(AppError):
    """The website could not be fetched or returned an unusable response."""

    status_code = 502
    code = "website_fetch_failed"
    default_message = "The website could not be fetched."


class EmptyContentError(WebsiteFetchError):
    """The website was fetched but contains no analysable text content."""

    code = "empty_content"
    default_message = (
        "The website returned no analysable text content. "
        "It may be empty or rendered entirely with JavaScript."
    )


class WebsiteTimeoutError(AppError):
    """The website did not respond within the configured time budget."""

    status_code = 504
    code = "website_timeout"
    default_message = "The website did not respond in time."


# --- LLM errors -------------------------------------------------------------


class LLMError(AppError):
    """The upstream LLM provider failed.

    ``retryable`` tells the retry loop whether another attempt can help, and
    ``retry_after`` carries the provider's requested delay in seconds, if any.
    """

    status_code = 502
    code = "llm_error"
    default_message = "The AI provider failed to generate the analysis."

    def __init__(
        self,
        message: str | None = None,
        *,
        retryable: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


class LLMRateLimitError(LLMError):
    """The LLM provider rate-limited the request."""

    code = "llm_rate_limited"
    default_message = "The AI provider is rate-limiting requests. Please retry shortly."


class LLMResponseError(LLMError):
    """The LLM returned malformed, truncated or schema-invalid output."""

    code = "llm_invalid_response"
    default_message = "The AI provider returned a response that could not be validated."


class LLMTimeoutError(LLMError):
    """The LLM provider did not answer within the configured timeout."""

    status_code = 504
    code = "llm_timeout"
    default_message = "The AI provider did not respond in time."


class LLMConfigurationError(AppError):
    """The selected LLM provider is not configured (for example, no API key)."""

    status_code = 503
    code = "llm_not_configured"
    default_message = "The AI provider is not configured on this server."
