"""LLM provider integration.

OpenAI is the primary provider and Google Gemini the alternative; either can
be selected with ``LLM_PROVIDER``. Both are called over their public REST APIs
with HTTPX, which keeps the dependency footprint small and makes the calls
trivial to mock in tests. Adding a provider means implementing one
``LLMProvider`` subclass and registering it in ``PROVIDERS``.

Only the selected provider needs an API key. Keys are read from settings,
sent only in request headers and never logged.
"""

import asyncio
import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ValidationError

from app.config import ProviderName, Settings
from app.exceptions import (
    LLMConfigurationError,
    LLMError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
)
from app.schemas import LLMAnalysisDraft

logger = logging.getLogger(__name__)

SCHEMA_NAME = "website_link_analysis"
MAX_RETRY_DELAY_SECONDS = 10.0

_SECRET_PATTERN = re.compile(r"sk-[\w\-*.]{6,}|AIza[\w\-]{10,}|Bearer\s+\S+")
_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)
# JSON Schema keywords that strict structured-output modes reject or ignore.
_UNSUPPORTED_SCHEMA_KEYS = frozenset({"title", "default", "examples", "minLength", "maxLength"})


# --- Schema and output helpers ------------------------------------------------


def build_strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Convert a Pydantic model into a provider-friendly strict JSON Schema.

    Structured-output modes accept only a subset of JSON Schema. This inlines
    ``$ref`` definitions, marks every property as required, forbids additional
    properties and drops keywords providers do not enforce. Constraints removed
    here (such as string lengths) are still enforced locally by Pydantic.
    """
    root = model.model_json_schema()
    definitions = root.pop("$defs", {})

    def transform(node: Any) -> Any:
        if isinstance(node, list):
            return [transform(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            target = definitions[node["$ref"].rsplit("/", 1)[-1]]
            siblings = {key: value for key, value in node.items() if key != "$ref"}
            return transform({**target, **siblings})

        result: dict[str, Any] = {}
        for key, value in node.items():
            if key == "properties":
                # Property *names* may legitimately be "title" or "default".
                result[key] = {name: transform(schema) for name, schema in value.items()}
            elif key not in _UNSUPPORTED_SCHEMA_KEYS:
                result[key] = transform(value)
        if result.get("type") == "object" and "properties" in result:
            result["required"] = list(result["properties"])
            result["additionalProperties"] = False
        return result

    return transform(root)


def parse_analysis_json(text: str) -> LLMAnalysisDraft:
    """Parse and validate the model's JSON output.

    Raises:
        LLMResponseError: the output is not JSON or does not match the schema.
    """
    cleaned = _CODE_FENCE.sub("", text.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    try:
        if start == -1 or end <= start:
            raise ValueError("no JSON object found")
        payload = json.loads(cleaned[start : end + 1])
    except ValueError as exc:
        raise LLMResponseError(
            "The AI provider returned a response that is not valid JSON.", retryable=True
        ) from exc

    try:
        return LLMAnalysisDraft.model_validate(payload)
    except ValidationError as exc:
        # Log which fields failed, never the generated values themselves.
        problems = [
            f"{'.'.join(str(part) for part in error['loc'])}: {error['type']}"
            for error in exc.errors()[:10]
        ]
        logger.warning("llm output failed schema validation", extra={"problems": problems})
        raise LLMResponseError(
            "The AI provider's response did not match the required analysis structure.",
            retryable=True,
        ) from exc


def _redact(text: str, limit: int = 300) -> str:
    """Mask anything that looks like a credential and truncate for logging."""
    return _SECRET_PATTERN.sub("[REDACTED]", text)[:limit]


def _provider_error_detail(response: httpx.Response) -> str:
    """Summarise a provider error body for logs (codes and message, redacted)."""
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return _redact(response.text)
    if isinstance(error, dict):
        fields = (error.get(key) for key in ("code", "type", "status", "message"))
        return _redact(" | ".join(str(value) for value in fields if value))
    return _redact(str(error))


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return max(0.0, float(response.headers["retry-after"]))
    except (KeyError, ValueError):
        return None


# --- Providers ----------------------------------------------------------------


@dataclass(frozen=True)
class LLMCompletion:
    """Raw text returned by a provider, with the token usage it reported."""

    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class LLMResult:
    """A validated analysis draft plus details of the call that produced it."""

    draft: LLMAnalysisDraft
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    used_fallback: bool = False


class LLMProvider(ABC):
    """Common HTTP plumbing and error mapping for LLM REST APIs."""

    name: ClassVar[ProviderName]

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        client: httpx.AsyncClient,
        timeout: float,
        max_output_tokens: int,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._timeout = timeout
        self._max_output_tokens = max_output_tokens

    @abstractmethod
    async def complete_json(
        self, system_prompt: str, user_prompt: str, schema: dict[str, Any]
    ) -> LLMCompletion:
        """Request a JSON completion constrained by ``schema``."""

    async def _post_json(
        self, url: str, headers: dict[str, str], payload: dict[str, Any]
    ) -> dict[str, Any]:
        """POST ``payload`` and return the decoded JSON body, mapping failures."""
        try:
            response = await self._client.post(
                url, headers=headers, json=payload, timeout=self._timeout
            )
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError() from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "llm provider unreachable",
                extra={"provider": self.name, "error_type": type(exc).__name__},
            )
            raise LLMError("The AI provider could not be reached.", retryable=True) from exc

        if response.status_code >= 400:
            raise self._http_error(response)
        try:
            body = response.json()
        except ValueError as exc:
            raise LLMResponseError(retryable=True) from exc
        if not isinstance(body, dict):
            raise LLMResponseError(retryable=True)
        return body

    def _http_error(self, response: httpx.Response) -> LLMError:
        """Map a provider HTTP error to an exception with a client-safe message."""
        status = response.status_code
        detail = _provider_error_detail(response)
        logger.warning(
            "llm provider returned an error",
            extra={
                "provider": self.name,
                "model": self.model,
                "status_code": status,
                "provider_error": detail,
            },
        )
        lowered = detail.lower()
        if status in (401, 403) or "api_key_invalid" in lowered or "api key not valid" in lowered:
            return LLMError("The AI provider rejected the server's API credentials.")
        if status == 404:
            return LLMError("The configured AI model was not found at the provider.")
        if status == 429:
            if "insufficient_quota" in lowered:
                return LLMRateLimitError("The AI provider account has exhausted its quota.")
            return LLMRateLimitError(retryable=True, retry_after=_retry_after(response))
        if status >= 500:
            return LLMError("The AI provider is temporarily unavailable.", retryable=True)
        return LLMError("The AI provider rejected the analysis request.")


class OpenAIProvider(LLMProvider):
    """OpenAI Responses API with strict JSON-schema structured output."""

    name = "openai"

    def __init__(self, *, reasoning_effort: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._reasoning_effort = reasoning_effort

    async def complete_json(
        self, system_prompt: str, user_prompt: str, schema: dict[str, Any]
    ) -> LLMCompletion:
        payload: dict[str, Any] = {
            "model": self.model,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": SCHEMA_NAME,
                    "schema": schema,
                    "strict": True,
                }
            },
            "max_output_tokens": self._max_output_tokens,
            "store": False,
        }
        if self._reasoning_effort:
            payload["reasoning"] = {"effort": self._reasoning_effort}

        data = await self._post_json(
            f"{self._base_url}/responses",
            {"Authorization": f"Bearer {self._api_key}"},
            payload,
        )

        if data.get("status") == "incomplete":
            reason = (data.get("incomplete_details") or {}).get("reason", "unknown")
            raise LLMResponseError(
                f"The AI response was cut off before completion ({reason}). "
                "Increase LLM_MAX_OUTPUT_TOKENS or lower the reasoning effort."
            )

        texts: list[str] = []
        for item in data.get("output") or []:
            if item.get("type") != "message":
                continue
            for part in item.get("content") or []:
                if part.get("type") == "refusal":
                    raise LLMResponseError("The AI provider declined to analyse this website.")
                if part.get("type") == "output_text":
                    texts.append(part.get("text") or "")
        if not "".join(texts).strip():
            raise LLMResponseError("The AI provider returned an empty response.", retryable=True)

        usage = data.get("usage") or {}
        return LLMCompletion(
            text="".join(texts),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )


class GeminiProvider(LLMProvider):
    """Google Gemini ``generateContent`` API with JSON-schema structured output."""

    name = "gemini"

    async def complete_json(
        self, system_prompt: str, user_prompt: str, schema: dict[str, Any]
    ) -> LLMCompletion:
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
                "maxOutputTokens": self._max_output_tokens,
            },
        }
        data = await self._post_json(
            f"{self._base_url}/models/{quote(self.model, safe='')}:generateContent",
            {"x-goog-api-key": self._api_key},
            payload,
        )

        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMResponseError("The AI provider declined to analyse this website.")
        candidate = candidates[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(part.get("text") or "" for part in parts if not part.get("thought"))
        finish_reason = candidate.get("finishReason")
        if finish_reason == "MAX_TOKENS":
            raise LLMResponseError(
                "The AI response was cut off before completion (MAX_TOKENS). "
                "Increase LLM_MAX_OUTPUT_TOKENS."
            )
        if not text.strip():
            if finish_reason not in (None, "STOP"):
                raise LLMResponseError("The AI provider declined to analyse this website.")
            raise LLMResponseError("The AI provider returned an empty response.", retryable=True)

        usage = data.get("usageMetadata") or {}
        visible, thoughts = usage.get("candidatesTokenCount"), usage.get("thoughtsTokenCount")
        output_tokens = None if visible is None else visible + (thoughts or 0)
        return LLMCompletion(
            text=text,
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=output_tokens,
        )


PROVIDERS: dict[ProviderName, type[LLMProvider]] = {
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
}


# --- Service ------------------------------------------------------------------


class LLMService:
    """Generates the validated analysis with retries and an optional fallback provider."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client
        self._schema = build_strict_json_schema(LLMAnalysisDraft)

    @property
    def provider_name(self) -> ProviderName:
        return self._settings.llm_provider

    @property
    def model_name(self) -> str:
        return self._settings.model_for(self.provider_name)

    @property
    def is_configured(self) -> bool:
        return self._settings.api_key_for(self.provider_name) is not None

    def ensure_configured(self) -> None:
        """Fail fast when the selected provider has no API key.

        Raises:
            LLMConfigurationError: the API key for ``LLM_PROVIDER`` is missing.
        """
        if not self.is_configured:
            raise LLMConfigurationError(
                f"The AI provider '{self.provider_name}' is not configured: set "
                f"{self.provider_name.upper()}_API_KEY on the server."
            )

    def _build_provider(self, name: ProviderName) -> LLMProvider | None:
        """Instantiate provider ``name``, or return ``None`` if it has no API key."""
        api_key = self._settings.api_key_for(name)
        if api_key is None:
            return None
        options: dict[str, Any] = {
            "api_key": api_key,
            "model": self._settings.model_for(name),
            "base_url": getattr(self._settings, f"{name}_base_url"),
            "client": self._client,
            "timeout": self._settings.llm_timeout_seconds,
            "max_output_tokens": self._settings.llm_max_output_tokens,
        }
        if name == "openai":
            options["reasoning_effort"] = self._settings.openai_reasoning_effort
        return PROVIDERS[name](**options)

    def _fallback_provider(self) -> LLMProvider | None:
        name = self._settings.llm_fallback_provider
        if name is None or name == self.provider_name:
            return None
        return self._build_provider(name)

    async def generate_analysis(self, system_prompt: str, user_prompt: str) -> LLMResult:
        """Generate and validate the analysis, falling back if configured.

        Raises:
            LLMConfigurationError: the selected provider has no API key.
            LLMError: the provider(s) failed or returned unusable output.
        """
        self.ensure_configured()
        primary = self._build_provider(self.provider_name)
        assert primary is not None  # guaranteed by ensure_configured()
        try:
            return await self._generate_with_retries(primary, system_prompt, user_prompt)
        except LLMError as primary_error:
            fallback = self._fallback_provider()
            if fallback is None:
                raise
            logger.warning(
                "primary llm provider failed; using fallback",
                extra={"primary": primary.name, "fallback": fallback.name},
            )
            try:
                return await self._generate_with_retries(
                    fallback, system_prompt, user_prompt, used_fallback=True
                )
            except LLMError as fallback_error:
                raise fallback_error from primary_error

    async def _generate_with_retries(
        self,
        provider: LLMProvider,
        system_prompt: str,
        user_prompt: str,
        *,
        used_fallback: bool = False,
    ) -> LLMResult:
        """Call ``provider``, retrying transient failures with exponential backoff."""
        attempts = self._settings.llm_max_retries + 1
        context = {"provider": provider.name, "model": provider.model}
        for attempt in range(1, attempts + 1):
            try:
                completion = await provider.complete_json(system_prompt, user_prompt, self._schema)
                draft = parse_analysis_json(completion.text)
            except LLMError as exc:
                log_extra = {**context, "attempt": attempt, "error_code": exc.code}
                if not exc.retryable or attempt == attempts:
                    logger.error("llm request failed", extra=log_extra)
                    raise
                backoff = self._settings.llm_retry_backoff_seconds * 2 ** (attempt - 1)
                delay = exc.retry_after if exc.retry_after is not None else backoff
                logger.warning("llm request failed; retrying", extra=log_extra)
                await asyncio.sleep(min(delay, MAX_RETRY_DELAY_SECONDS))
                continue

            logger.info(
                "llm analysis generated",
                extra={
                    **context,
                    "attempt": attempt,
                    "input_tokens": completion.input_tokens,
                    "output_tokens": completion.output_tokens,
                },
            )
            return LLMResult(
                draft=draft,
                provider=provider.name,
                model=provider.model,
                input_tokens=completion.input_tokens,
                output_tokens=completion.output_tokens,
                used_fallback=used_fallback,
            )
        raise LLMError()  # pragma: no cover - the loop always returns or raises
