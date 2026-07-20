from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field, field_serializer


class FlashcardCreate(BaseModel):
    word: str
    definition: str
    example_sentence: str
    translation: str


class FlashcardBulkCreate(BaseModel):
    flashcards: list[FlashcardCreate]


class FlashcardBulkResponse(BaseModel):
    created: int


class FlashcardReview(BaseModel):
    rating: int = Field(ge=1, le=4)


class FlashcardFromWordRequest(BaseModel):
    word: str
    context: str = ""
    cefr_level: str = "B1"


class FlashcardGenerateRequest(BaseModel):
    topic: str
    count: int = Field(default=5, ge=1, le=20)
    cefr_level: str = "B1"
    target_language: str | None = None


class FlashcardResponse(BaseModel):
    id: int
    user_id: int
    word: str
    definition: str
    example_sentence: str
    translation: str
    source: Optional[str] = None
    stability: float
    difficulty: float
    state: int
    reps: int
    lapses: int
    scheduled_days: int
    last_review: date | None = None
    retrievability: float | None = None
    next_review: date
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("next_review")
    def serialize_next_review(self, v: date, _info: object) -> str:
        return v.isoformat()

    @field_serializer("last_review")
    def serialize_last_review(self, v: date | None, _info: object) -> str | None:
        return v.isoformat() if v else None

    @field_serializer("created_at")
    def serialize_created_at(self, v: datetime, _info: object) -> str:
        return v.isoformat()


class FlashcardListResponse(BaseModel):
    due: list[FlashcardResponse]
    total: int


class VocabularyListResponse(BaseModel):
    items: list[FlashcardResponse]
    total: int
    page: int
    pages: int


class GeneratedFlashcard(BaseModel):
    word: str
    definition: str
    example_sentence: str
    translation: str


class FlashcardGenerateResponse(BaseModel):
    flashcards: list[GeneratedFlashcard]
