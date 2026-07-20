import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status

from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.models.user import User
from app.schemas.tts_stt import PronunciationAssessmentResponse, STTResponse
from app.services.pronunciation_service import evaluate_pronunciation_with_llm
from app.services.stt_service import AzureSTTService

router = APIRouter(prefix="/api", tags=["stt"])


@router.post("/stt", response_model=STTResponse)
@limiter.limit("20/minute")
async def speech_to_text(
    request: Request,
    audio: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> STTResponse:
    """Proxy STT request to configured STT service. Returns transcribed text."""
    stt_service = getattr(request.app.state, "stt_service", None)
    if stt_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="STT service is not enabled",
        )
    audio_bytes = await audio.read()
    if len(audio_bytes) > 50 * 1024 * 1024:  # 50 MB
        raise HTTPException(status_code=413, detail="Audio file too large (max 50 MB)")

    text = await stt_service.transcribe(
        audio_bytes,
        audio.filename or "audio.webm",
        mime_type=audio.content_type or "audio/webm",
    )
    return STTResponse(text=text)


def _normalize(text: str) -> str:
    return re.sub(r"[\p{P}\p{S}\s]+", "", text.strip().lower())


@router.post("/stt/pronunciation", response_model=PronunciationAssessmentResponse)
@limiter.limit("20/minute")
async def pronunciation_assessment(
    request: Request,
    reference_text: str = Form(...),
    language: str = Form("en-US"),
    audio: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> PronunciationAssessmentResponse:
    """Assess pronunciation using Azure STT (if available) with LLM fallback."""
    stt_service = getattr(request.app.state, "stt_service", None)
    if stt_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="STT service is not enabled",
        )
    audio_bytes = await audio.read()
    if len(audio_bytes) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Audio file too large (max 50 MB)")

    if isinstance(stt_service, AzureSTTService):
        try:
            data = await stt_service.pronunciation_assessment(
                audio_bytes,
                reference_text=reference_text,
                filename=audio.filename or "audio.wav",
                mime_type=audio.content_type or "audio/wav",
                language=language,
            )
            # Parse Azure Pronunciation Assessment response
            pronunciation_result = data.get("NBest", [{}])[0].get("PronunciationAssessment", {})
            words = data.get("NBest", [{}])[0].get("Words", [])
            return PronunciationAssessmentResponse(
                text=data.get("DisplayText", "").strip(),
                accuracy_score=pronunciation_result.get("AccuracyScore"),
                pronunciation_score=pronunciation_result.get("PronScore"),
                completeness_score=pronunciation_result.get("CompletenessScore"),
                fluency_score=pronunciation_result.get("FluencyScore"),
                words=words,
            )
        except Exception:
            # Fallback if Azure fails or if not Azure
            pass

    # Fallback cascade: Use standard transcription
    text = await stt_service.transcribe(
        audio_bytes,
        audio.filename or "audio.webm",
        mime_type=audio.content_type or "audio/webm",
        language=language.split("-")[0] if "-" in language else language,
    )

    # Exact match check
    if _normalize(text) == _normalize(reference_text):
        return PronunciationAssessmentResponse(
            text=text,
            pronunciation_score=100.0,
            accuracy_score=100.0,
            completeness_score=100.0,
            fluency_score=100.0,
        )

    # If not exact match, ask LLM
    try:
        eval_res = await evaluate_pronunciation_with_llm(reference_text, text, language)
        score = 80.0 if eval_res.is_acceptable else 30.0
        return PronunciationAssessmentResponse(
            text=text,
            pronunciation_score=score,
            accuracy_score=score,
            completeness_score=100.0,
            fluency_score=100.0,
        )
    except Exception:
        # Graceful error / default text fallback
        return PronunciationAssessmentResponse(text=text)
