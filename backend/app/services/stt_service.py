import base64
import io
import json

import httpx
import openai

from app.core.app_logger import get_logger

logger = get_logger(__name__)


class AzureSTTService:
    def __init__(self, subscription_key: str, region: str) -> None:
        self.subscription_key = subscription_key
        self.region = region
        self.base_url = f"https://{region}.stt.speech.microsoft.com/speech/recognition/conversation/cognitiveservices/v1"

    async def health(self) -> None:
        """Raise if Azure STT is unreachable."""
        if not self.subscription_key or not self.region:
            raise ValueError("Azure credentials not configured")
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"https://{self.region}.api.cognitive.microsoft.com/sts/v1.0/issueToken",
                headers={"Ocp-Apim-Subscription-Key": self.subscription_key},
                timeout=5.0,
            )
            r.raise_for_status()

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        mime_type: str = "audio/wav",
        language: str = "en-US",
    ) -> str:
        """Send audio to Azure STT and return the transcribed text."""
        async with httpx.AsyncClient() as client:
            logger.debug(
                "[stt-azure] POST /stt — %d bytes, filename=%s lang=%s",
                len(audio_bytes),
                filename,
                language,
            )
            response = await client.post(
                self.base_url,
                params={"language": language, "format": "simple"},
                headers={
                    "Ocp-Apim-Subscription-Key": self.subscription_key,
                    "Content-Type": mime_type,
                    "Accept": "application/json",
                },
                content=audio_bytes,
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()
            text = data.get("DisplayText", "").strip()
            logger.info("[stt-azure] Transcribed: %r", text)
            return text

    async def pronunciation_assessment(
        self,
        audio_bytes: bytes,
        reference_text: str,
        filename: str = "audio.wav",
        mime_type: str = "audio/wav",
        language: str = "en-US",
    ) -> dict:
        """Send audio to Azure STT with Pronunciation Assessment headers."""
        pronunciation_params = {
            "ReferenceText": reference_text,
            "GradingSystem": "HundredMark",
            "Granularity": "Phoneme",
            "Dimension": "Comprehensive",
        }
        pronunciation_json = json.dumps(pronunciation_params)
        pronunciation_base64 = base64.b64encode(pronunciation_json.encode("utf-8")).decode("utf-8")

        async with httpx.AsyncClient() as client:
            logger.debug(
                "[stt-azure] Pronunciation Assessment — %d bytes, lang=%s",
                len(audio_bytes),
                language,
            )
            response = await client.post(
                self.base_url,
                params={"language": language, "format": "detailed"},
                headers={
                    "Ocp-Apim-Subscription-Key": self.subscription_key,
                    "Content-Type": mime_type,
                    "Accept": "application/json",
                    "Pronunciation-Assessment": pronunciation_base64,
                },
                content=audio_bytes,
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()
            logger.info("[stt-azure] Pronunciation assessment complete")
            return data


class WhisperSTTService:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def health(self) -> None:
        """Raise if Whisper ASR is unreachable."""
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{self.base_url}/", timeout=5.0)
            r.raise_for_status()

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        mime_type: str = "audio/wav",
        language: str = "en",
    ) -> str:
        """Send audio to Whisper ASR and return the transcribed text."""
        async with httpx.AsyncClient() as client:
            logger.debug(
                "[stt] POST /asr — %d bytes, filename=%s lang=%s",
                len(audio_bytes),
                filename,
                language,
            )
            response = await client.post(
                f"{self.base_url}/asr",
                params={"output": "json", "language": language, "task": "transcribe"},
                files={"audio_file": (filename, audio_bytes, mime_type)},
                timeout=60.0,
            )
            logger.debug("[stt] Response status: %s", response.status_code)
            response.raise_for_status()
            data = response.json()
            # Response: {"text": "...", ...}
            text = data.get("text", "").strip()
            logger.info("[stt] Transcribed: %r", text)
            return text


class OpenAISTTService:
    def __init__(self, api_key: str, model: str) -> None:
        self._client = openai.AsyncOpenAI(api_key=api_key)
        self.model = model

    async def health(self) -> None:
        """Raise if OpenAI STT is unreachable (lightweight models list call)."""
        await self._client.models.list()

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        mime_type: str = "audio/wav",
        language: str = "en",
    ) -> str:
        """Transcribe audio using OpenAI Whisper API."""
        audio_file = (filename, io.BytesIO(audio_bytes), mime_type)
        response = await self._client.audio.transcriptions.create(
            model=self.model,
            file=audio_file,
            language=language,
            timeout=60.0,
        )
        text = response.text.strip()
        logger.info("[stt-openai] Transcribed: %r", text)
        return text
