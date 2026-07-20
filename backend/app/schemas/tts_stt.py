from pydantic import BaseModel, Field


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    voice: str | None = None


class STTResponse(BaseModel):
    text: str


class PronunciationAssessmentResponse(BaseModel):
    text: str
    accuracy_score: float | None = None
    pronunciation_score: float | None = None
    completeness_score: float | None = None
    fluency_score: float | None = None
    words: list[dict] | None = None
