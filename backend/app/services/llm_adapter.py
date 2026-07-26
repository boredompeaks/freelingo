from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator

import anthropic as _anthropic
from openai import AsyncOpenAI
from pydantic import BaseModel

from app.core.app_logger import get_logger
from app.core.config import settings
from app.services.prompts.common import (
    ANTHROPIC_SYSTEM_ONLY_TRIGGER,
    JSON_ONLY_INSTRUCTION,
    STRUCTURED_OUTPUT_RETRY_PROMPT,
)

import os
import sys

logger = get_logger(__name__)

MAX_RETRIES = 2
RETRY_DELAY_SECONDS = 0 if (os.getenv("PYTEST_CURRENT_TEST") or "pytest" in sys.modules) else 2
REQUEST_TIMEOUT = 120.0
FALLBACK_TIMEOUT = 3.0

MAX_CONTEXT_TOKENS = {
    "openai": 128000,
    "anthropic": 200000,
    "deepseek": 128000,
    "ollama": 8192,
    "vertex": 1048576,
}


class LLMError(Exception):
    pass


class LLMTimeoutError(LLMError):
    pass


class LLMUnavailableError(LLMError):
    pass


class LLMResponseError(LLMError):
    def __init__(self, message: str, raw_response: str | None = None):
        super().__init__(message)
        self.raw_response = raw_response


class LLMContextOverflowError(LLMError):
    pass


class NormalizedDelta:
    def __init__(self, content: str | None = None):
        self.content = content


class NormalizedChoice:
    def __init__(self, content: str | None = None):
        self.delta = NormalizedDelta(content)


class NormalizedStreamChunk:
    def __init__(self, content: str | None = None, usage: object | None = None):
        self.choices = [NormalizedChoice(content)] if content is not None else []
        self.usage = usage


class LLMStream:
    """Wraps an async LLM stream and captures token usage defensively.

    Usage fields (prompt_tokens, completion_tokens, total_tokens) remain None
    if the provider does not return them — callers must always treat them as
    optional and must never raise on their absence.

    The wrapper normalizes Anthropic/OpenAI stream chunks into standard OpenAI
    structure (chunk.choices[0].delta.content) and filters out usage-only chunks
    (choices=[]) so callers that access choice indices never receive an IndexError.
    """

    def __init__(self, stream: object) -> None:
        self._stream = stream
        self.prompt_tokens: int | None = None
        self.completion_tokens: int | None = None
        self.total_tokens: int | None = None

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        async for chunk in self._stream:  # type: ignore[union-attr]
            chunk_type = getattr(chunk, "type", None)
            if chunk_type is not None and not hasattr(chunk, "choices"):
                if chunk_type == "content_block_delta":
                    delta = getattr(chunk, "delta", None)
                    text = getattr(delta, "text", None) if delta else None
                    if text:
                        yield NormalizedStreamChunk(content=text)
                    continue
                elif chunk_type in ("message_start", "message_delta"):
                    usage = getattr(chunk, "usage", None) or getattr(
                        getattr(chunk, "message", None), "usage", None
                    )
                    if usage:
                        pt = getattr(usage, "input_tokens", None)
                        ct = getattr(usage, "output_tokens", None)
                        if pt is not None:
                            self.prompt_tokens = pt
                        if ct is not None:
                            self.completion_tokens = ct
                        if self.prompt_tokens and self.completion_tokens:
                            self.total_tokens = self.prompt_tokens + self.completion_tokens
                    continue
                else:
                    continue

            # Defensively capture usage from every chunk (the final one for
            # OpenAI-compatible streams, or any event for other providers).
            try:
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    pt = getattr(usage, "prompt_tokens", None)
                    ct = getattr(usage, "completion_tokens", None)
                    tt = getattr(usage, "total_tokens", None)
                    if pt is not None:
                        self.prompt_tokens = pt
                    if ct is not None:
                        self.completion_tokens = ct
                    if tt is not None:
                        self.total_tokens = tt
                    elif self.prompt_tokens is not None and self.completion_tokens is not None:
                        self.total_tokens = self.prompt_tokens + self.completion_tokens
            except Exception:
                pass

            # Skip usage-only chunks (choices=[]) to prevent IndexError in
            # callers that do chunk.choices[0].delta.content.
            choices = getattr(chunk, "choices", None)
            if choices is not None and len(choices) == 0:
                continue

            yield chunk


class LLMAdapter:
    def __init__(self):
        try:
            self.provider = settings.LLM_PROVIDER
            if self.provider == "ollama":
                self.client = AsyncOpenAI(
                    base_url=f"{settings.OLLAMA_BASE_URL}/v1",
                    api_key="ollama",
                )
                self.model = settings.OLLAMA_MODEL
            elif self.provider == "openai":
                self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
                self.model = settings.OPENAI_MODEL
            elif self.provider == "deepseek":
                self.client = AsyncOpenAI(
                    base_url="https://api.deepseek.com/v1",
                    api_key=settings.DEEPSEEK_API_KEY,
                )
                self.model = settings.DEEPSEEK_MODEL
            elif self.provider == "vertex":
                location = (
                    settings.VERTEX_LOCATION
                    if (settings.VERTEX_LOCATION and settings.VERTEX_LOCATION != "global")
                    else "us-central1"
                )
                base_url = f"https://{location}-aiplatform.googleapis.com/v1/projects/{settings.VERTEX_PROJECT_ID}/locations/{location}/endpoints/openapi"
                self.client = AsyncOpenAI(
                    base_url=base_url,
                    api_key="dummy_vertex_token",
                )
                self.model = settings.VERTEX_MODEL
                self._credentials = None
            elif self.provider == "anthropic":
                self._anthropic = _anthropic.AsyncAnthropic(
                    api_key=settings.ANTHROPIC_API_KEY,
                    # Retries are handled by _call_with_retry; disable the SDK's
                    # built-in retry logic to avoid exponential back-off stacking.
                    max_retries=0,
                )
                self.client = None
                self.model = settings.ANTHROPIC_MODEL
        except Exception as e:
            logger.error(f"Error initializing LLMAdapter: {e}")
            raise

    def _get_vertex_token(self) -> str:
        try:
            if not getattr(self, "_credentials", None):
                # 1. Try inline service account JSON
                if settings.VERTEX_SERVICE_ACCOUNT_JSON:
                    import json

                    from google.oauth2 import service_account

                    info = json.loads(settings.VERTEX_SERVICE_ACCOUNT_JSON)
                    self._credentials = service_account.Credentials.from_service_account_info(
                        info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
                    )
                # 2. Try service account JSON file path
                elif settings.VERTEX_SERVICE_ACCOUNT_JSON_PATH:
                    from google.oauth2 import service_account

                    self._credentials = service_account.Credentials.from_service_account_file(
                        settings.VERTEX_SERVICE_ACCOUNT_JSON_PATH,
                        scopes=["https://www.googleapis.com/auth/cloud-platform"],
                    )
                # 3. Fallback to Application Default Credentials (ADC)
                else:
                    from google.auth import default as auth_default

                    self._credentials, _ = auth_default(
                        scopes=["https://www.googleapis.com/auth/cloud-platform"]
                    )

            # Refresh token if not present or expired/valid is False
            if self._credentials and not self._credentials.valid:
                import google.auth.transport.requests

                request = google.auth.transport.requests.Request()
                self._credentials.refresh(request)

            if self._credentials and self._credentials.token:
                return self._credentials.token
            return ""
        except Exception as e:
            logger.error(f"Error fetching Vertex AI token: {e}")
            raise LLMUnavailableError(f"Vertex AI authentication failed: {e}") from e

    async def _call_with_retry(self, fn, *args, **kwargs):
        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                return await fn(*args, **kwargs)
            except LLMError as e:
                # Already a typed LLM error raised by a provider-specific
                # handler (e.g. _anthropic_chat). Preserve the type instead of
                # re-wrapping into a generic LLMError.
                last_error = e
            except Exception as e:
                if type(e).__name__ in ("TimeoutError", "AsyncTimeoutError"):
                    last_error = LLMTimeoutError(
                        f"{self.provider} timed out after {REQUEST_TIMEOUT}s"
                    )
                else:
                    error_msg = str(e)
                    if "connection" in error_msg.lower():
                        last_error = LLMUnavailableError(
                            f"{self.provider} is unreachable. Check that the service is running."
                        )
                    elif "rate" in error_msg.lower():
                        last_error = LLMUnavailableError(
                            f"{self.provider} rate limit exceeded. Try again later."
                        )
                    else:
                        last_error = LLMError(f"{self.provider} error: {error_msg}")

            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY_SECONDS * (attempt + 1))

        raise last_error

    def _get_fallback_providers(self) -> list[str]:
        candidates = []
        if settings.OPENAI_API_KEY and self.provider != "openai":
            candidates.append("openai")
        if settings.ANTHROPIC_API_KEY and self.provider != "anthropic":
            candidates.append("anthropic")
        if settings.DEEPSEEK_API_KEY and self.provider != "deepseek":
            candidates.append("deepseek")
        if self.provider != "ollama":
            candidates.append("ollama")
        return candidates

    async def _do_fallback_chat(self, fb_provider: str, messages: list[dict], stream: bool = False):
        if fb_provider == "anthropic":
            if not settings.ANTHROPIC_API_KEY:
                raise LLMUnavailableError("Anthropic API key not set")
            client = _anthropic.AsyncAnthropic(
                api_key=settings.ANTHROPIC_API_KEY,
                max_retries=0,
            )
            return await self._anthropic_chat_with_client(client, settings.ANTHROPIC_MODEL, messages, stream)

        base_url = None
        api_key = "ollama"
        model = settings.OLLAMA_MODEL

        if fb_provider == "openai":
            if not settings.OPENAI_API_KEY:
                raise LLMUnavailableError("OpenAI API key not set")
            base_url = None
            api_key = settings.OPENAI_API_KEY
            model = settings.OPENAI_MODEL
        elif fb_provider == "deepseek":
            if not settings.DEEPSEEK_API_KEY:
                raise LLMUnavailableError("DeepSeek API key not set")
            base_url = "https://api.deepseek.com/v1"
            api_key = settings.DEEPSEEK_API_KEY
            model = settings.DEEPSEEK_MODEL
        elif fb_provider == "ollama":
            base_url = f"{settings.OLLAMA_BASE_URL}/v1"
            api_key = "ollama"
            model = settings.OLLAMA_MODEL

        fb_client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        extra: dict = {}
        if stream:
            extra["stream_options"] = {"include_usage": True}

        response = await fb_client.chat.completions.create(
            model=model,
            messages=messages,
            stream=stream,
            timeout=settings.LLM_FALLBACK_TIMEOUT,
            **extra,
        )
        if stream:
            return LLMStream(response)
        content = response.choices[0].message.content
        if not content:
            raise LLMResponseError("Fallback LLM returned empty response")
        return content

    async def chat(self, messages: list[dict], stream: bool = False) -> str | AsyncGenerator:
        return await self._call_with_retry(self._do_chat, messages, stream)

    async def _do_chat(self, messages: list[dict], stream: bool = False):
        try:
            if self.provider == "anthropic":
                result = await self._anthropic_chat(messages, stream)
                return LLMStream(result) if stream else result

            if self.provider == "vertex":
                token = self._get_vertex_token()
                if self.client:
                    self.client.api_key = token

            extra: dict = {}
            if stream:
                extra["stream_options"] = {"include_usage": True}

            if not self.client:
                raise LLMUnavailableError("LLM client not initialized")

            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=stream,
                timeout=REQUEST_TIMEOUT,
                **extra,
            )
            if stream:
                return LLMStream(response)

            content = response.choices[0].message.content
            if not content:
                raise LLMResponseError("LLM returned empty response")
            return content
        except Exception as e:
            logger.warning(
                f"Primary LLM provider '{self.provider}' failed ({type(e).__name__}: {e}). Attempting fallbacks..."
            )
            for fb_provider in self._get_fallback_providers():
                try:
                    logger.info(f"Attempting LLM fallback with '{fb_provider}'")
                    return await self._do_fallback_chat(fb_provider, messages, stream)
                except Exception as fb_err:
                    logger.warning(f"LLM fallback provider '{fb_provider}' failed: {fb_err}")

            if isinstance(e, LLMError) or type(e).__name__ in ("TimeoutError", "AsyncTimeoutError"):
                raise e
            logger.error(f"Error in _do_chat: {e}")
            raise LLMError(f"LLM chat call failed: {e}") from e

    async def structured_output(self, messages: list[dict], schema: type[BaseModel]) -> BaseModel:
        # Use JSON mode for all providers — more reliable across versions
        return await self._structured_via_json(messages, schema)

    async def _structured_via_json(
        self, messages: list[dict], schema: type[BaseModel]
    ) -> BaseModel:
        messages_with_format = messages + [
            {
                "role": "system",
                "content": JSON_ONLY_INSTRUCTION,
            }
        ]

        raw = await self._call_with_retry(self._do_chat, messages_with_format, False)

        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        try:
            return schema.model_validate(json.loads(cleaned))
        except (json.JSONDecodeError, ValueError) as e:
            retry_messages = messages_with_format + [
                {
                    "role": "user",
                    "content": STRUCTURED_OUTPUT_RETRY_PROMPT.format(error=str(e)),
                }
            ]
            try:
                raw2 = await self._call_with_retry(self._do_chat, retry_messages, False)
                cleaned2 = (
                    raw2.strip()
                    .removeprefix("```json")
                    .removeprefix("```")
                    .removesuffix("```")
                    .strip()
                )
                return schema.model_validate(json.loads(cleaned2))
            except Exception as e2:
                raise LLMResponseError(
                    f"Failed to parse JSON after retry: {str(e2)}",
                    raw_response=raw2 if "raw2" in locals() else raw,
                ) from e2

    async def _anthropic_chat(self, messages: list[dict], stream: bool = False):
        return await self._anthropic_chat_with_client(self._anthropic, self.model, messages, stream)

    async def _anthropic_chat_with_client(self, client_instance, model_name: str, messages: list[dict], stream: bool = False):
        system_parts = [m["content"] for m in messages if m["role"] == "system"]
        system = "\n\n".join(system_parts) if system_parts else None
        user_messages = [m for m in messages if m["role"] != "system"]

        if not user_messages:
            user_messages = [{"role": "user", "content": ANTHROPIC_SYSTEM_ONLY_TRIGGER}]

        kwargs: dict = dict(
            model=model_name,
            messages=user_messages,
            max_tokens=4096,
            stream=stream,
            timeout=REQUEST_TIMEOUT,
        )
        if system is not None:
            kwargs["system"] = system

        try:
            response = await client_instance.messages.create(**kwargs)
        except _anthropic.APITimeoutError as e:
            raise LLMTimeoutError(f"anthropic timed out after {REQUEST_TIMEOUT}s") from e
        except _anthropic.APIConnectionError as e:
            raise LLMUnavailableError(
                "anthropic is unreachable. Check that the service is running."
            ) from e
        except _anthropic.RateLimitError as e:
            raise LLMUnavailableError("anthropic rate limit exceeded. Try again later.") from e
        except _anthropic.APIStatusError as e:
            raise LLMError(f"anthropic error: {e}") from e

        if stream:
            return response
        content = response.content[0].text if response.content else ""
        if not content:
            raise LLMResponseError("Anthropic returned empty response")
        return content


llm_adapter = LLMAdapter()


def parse_llm_json(raw: str) -> dict:
    """Strip optional code fences and parse JSON from LLM output."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        parts = cleaned.split("```")
        # parts[1] is the content inside the fences (may start with "json\n")
        cleaned = parts[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    return json.loads(cleaned.strip())
