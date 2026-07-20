import time
import xml.sax.saxutils

import httpx
import openai

from app.core.app_logger import get_logger

logger = get_logger(__name__)


def escape_ssml(text: str) -> str:
    """Escape special characters for SSML."""
    return xml.sax.saxutils.escape(text)


class AzureTTSService:
    def __init__(self, subscription_key: str, region: str, voice: str) -> None:
        self.subscription_key = subscription_key
        self.region = region
        self.voice = voice
        self.base_url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"

    async def health(self) -> None:
        """Raise if Azure TTS is unreachable."""
        if not self.subscription_key or not self.region:
            raise ValueError("Azure credentials not configured")
        # We can issue a token as a health check
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"https://{self.region}.api.cognitive.microsoft.com/sts/v1.0/issueToken",
                headers={"Ocp-Apim-Subscription-Key": self.subscription_key},
                timeout=5.0,
            )
            r.raise_for_status()

    async def synthesize(
        self, text: str, voice: str | None = None, language: str | None = None
    ) -> bytes:
        """Call Azure TTS API and return MP3 audio bytes."""
        text = text.strip()
        if not text:
            return b""

        req_voice = (voice or self.voice).strip()
        # Default to en-US if lang not specified
        req_lang = language or "en-US"
        # Extract lang from voice if possible (e.g. en-US-AriaNeural -> en-US)
        if len(req_voice.split("-")) >= 2:
            req_lang = "-".join(req_voice.split("-")[:2])

        escaped_text = escape_ssml(text)
        ssml = f"""<speak version='1.0' xml:lang='{req_lang}'>
    <voice xml:lang='{req_lang}' name='{req_voice}'>
        {escaped_text}
    </voice>
</speak>"""

        start_t = time.perf_counter()
        logger.info("[tts-azure] request_start voice=%s chars=%d", req_voice, len(text))

        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.base_url,
                headers={
                    "Ocp-Apim-Subscription-Key": self.subscription_key,
                    "Content-Type": "application/ssml+xml",
                    "X-Microsoft-OutputFormat": "audio-24khz-160kbitrate-mono-mp3",
                    "User-Agent": "FreeLingo",
                },
                content=ssml,
                timeout=30.0,
            )
            response.raise_for_status()
            audio = response.content

        elapsed_ms = (time.perf_counter() - start_t) * 1000
        logger.info(
            "[tts-azure] request_ok voice=%s chars=%d bytes=%d ms=%.1f",
            req_voice,
            len(text),
            len(audio),
            round(elapsed_ms, 1),
        )
        return audio


class KokoroTTSService:
    def __init__(self, base_url: str, voice: str) -> None:
        self.base_url = base_url
        self.voice = voice

    async def health(self) -> None:
        """Raise if Kokoro is unreachable."""
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{self.base_url}/v1/models", timeout=5.0)
            r.raise_for_status()

    async def synthesize(
        self, text: str, voice: str | None = None, language: str | None = None
    ) -> bytes:
        """Call Kokoro-FastAPI and return MP3 audio bytes."""
        _ = language  # Kokoro handles language via the voice model itself
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/v1/audio/speech",
                json={
                    "model": "kokoro",
                    "input": text,
                    "voice": voice or self.voice,
                    "response_format": "mp3",
                },
                timeout=30.0,
            )
            response.raise_for_status()
            return response.content


class OpenAITTSService:
    def __init__(
        self,
        api_key: str,
        model: str,
        voice: str,
        speed: float = 1.0,
        timeout: float | None = None,
    ) -> None:
        self._client = openai.AsyncOpenAI(api_key=api_key)
        self.model = model
        self.voice = voice
        self.speed = speed
        self.timeout = timeout

    async def health(self) -> None:
        """Raise if OpenAI TTS is unreachable (lightweight models list call)."""
        await self._client.models.list()

    async def synthesize(
        self, text: str, voice: str | None = None, language: str | None = None
    ) -> bytes:
        """Call OpenAI TTS API and return MP3 audio bytes."""
        _ = language
        text = text.strip()
        if not text:
            logger.warning("[tts-openai] Empty text received for synthesis")
            return b""

        req_voice = (voice or self.voice).strip()
        input_len = len(text)
        start_t = time.perf_counter()
        logger.info(
            "[tts-openai] request_start model=%s voice=%s chars=%d",
            self.model,
            req_voice,
            input_len,
        )
        request_payload = {
            "model": self.model,
            "voice": req_voice,
            "input": text,
            "response_format": "mp3",
            "speed": self.speed,
        }
        if self.timeout is not None:
            request_payload["timeout"] = self.timeout

        response = await self._client.audio.speech.create(**request_payload)
        audio = response.content
        if not audio:
            raise RuntimeError("OpenAI TTS returned empty audio payload")
        elapsed_ms = (time.perf_counter() - start_t) * 1000
        request_id = getattr(response, "request_id", None)
        if request_id is None:
            headers = getattr(response, "headers", None)
            if headers is not None:
                request_id = headers.get("x-request-id")
        logger.info(
            "[tts-openai] request_ok model=%s voice=%s chars=%d bytes=%d ms=%.1f request_id=%s",
            self.model,
            req_voice,
            input_len,
            len(audio),
            round(elapsed_ms, 1),
            request_id,
        )
        return audio
