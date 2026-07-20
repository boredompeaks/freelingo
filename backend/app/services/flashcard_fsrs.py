"""FSRS v5 spaced-repetition service for flashcards.

Replaces the legacy SM-2 algorithm with the Free Spaced Repetition Scheduler (FSRS) v5.
Also contains flashcard generation and word lookup helpers previously in flashcard_sm2.
"""

from __future__ import annotations

import math
import re
from datetime import date, timedelta
from enum import IntEnum
from typing import TYPE_CHECKING

from app.schemas.flashcards import (
    FlashcardCreate,
    FlashcardGenerateResponse,
    GeneratedFlashcard,
)
from app.services.language_helpers import get_language_name, get_native_language_name
from app.services.llm_adapter import llm_adapter
from app.services.prompts import flashcards as flashcard_prompts
from app.services.prompts.common import get_language_prompt_overlay
from app.services.prompts.flashcards import (
    build_flashcard_generation_prompt,
    build_word_lookup_prompt,
)

if TYPE_CHECKING:
    from app.models.flashcard import Flashcard

FLASHCARD_GEN_PROMPT = flashcard_prompts.FLASHCARD_GEN_PROMPT
WORD_LOOKUP_PROMPT = flashcard_prompts.WORD_LOOKUP_PROMPT

# ---------------------------------------------------------------------------
# FSRS v5 enums
# ---------------------------------------------------------------------------


class Rating(IntEnum):
    """User rating for a flashcard review."""

    Again = 1
    Hard = 2
    Good = 3
    Easy = 4


class State(IntEnum):
    """Card state in the FSRS scheduler."""

    New = 0
    Learning = 1
    Review = 2
    Relearning = 3


# ---------------------------------------------------------------------------
# FSRS v5 optimal default weights (w0 – w18)
# ---------------------------------------------------------------------------

FSRS_WEIGHTS: list[float] = [
    0.4072,  # w0
    1.1829,  # w1
    3.1262,  # w2
    15.4722,  # w3
    7.2102,  # w4
    0.5316,  # w5
    1.0651,  # w6
    0.0589,  # w7
    1.5947,  # w8
    0.2753,  # w9
    1.0278,  # w10
    1.9523,  # w11
    0.1132,  # w12
    0.2955,  # w13
    2.2584,  # w14
    0.2353,  # w15
    2.9466,  # w16
    0.5130,  # w17
    0.5600,  # w18
]

# ---------------------------------------------------------------------------
# Internal FSRS helpers
# ---------------------------------------------------------------------------


def _initial_stability(rating: Rating) -> float:
    """Return initial stability for *rating* on a new card (w0–w3)."""
    try:
        return FSRS_WEIGHTS[rating.value - 1]
    except IndexError, AttributeError:
        return FSRS_WEIGHTS[0]


def _initial_difficulty(rating: Rating) -> float:
    """Return initial difficulty for *rating* on a new card."""
    try:
        w4, w5 = FSRS_WEIGHTS[4], FSRS_WEIGHTS[5]
        d = w4 - math.exp(w5 * (rating.value - 1)) + 1.0
        return max(1.0, min(10.0, d))
    except Exception:
        return 5.0


def _next_difficulty(d: float, rating: Rating) -> float:
    """Compute updated difficulty after a review."""
    try:
        w6, w7 = FSRS_WEIGHTS[6], FSRS_WEIGHTS[7]
        delta = -w6 * (rating.value - 3)
        d_new = d + delta * (w7 * (10.0 - d))
        return max(1.0, min(10.0, d_new))
    except Exception:
        return max(1.0, min(10.0, d))


def _next_recall_stability(s: float, d: float, r: float, rating: Rating) -> float:
    """Compute new stability after a successful recall (rating ≥ Hard)."""
    try:
        w8, w9, w10, w11 = (
            FSRS_WEIGHTS[8],
            FSRS_WEIGHTS[9],
            FSRS_WEIGHTS[10],
            FSRS_WEIGHTS[11],
        )
        hard_penalty = FSRS_WEIGHTS[15] if rating == Rating.Hard else 1.0
        easy_bonus = FSRS_WEIGHTS[16] if rating == Rating.Easy else 1.0
        new_s = s * (
            1.0
            + math.exp(w8)
            * (11.0 - d)
            * math.pow(s, -w9)
            * (math.exp((1.0 - r) * w10) - 1.0)
            * hard_penalty
            * easy_bonus
        )
        return max(0.01, new_s)
    except Exception:
        return max(0.01, s)


def _next_forget_stability(s: float, d: float, r: float) -> float:
    """Compute new stability after a lapse (rating == Again)."""
    try:
        w11, w12, w13, w14 = (
            FSRS_WEIGHTS[11],
            FSRS_WEIGHTS[12],
            FSRS_WEIGHTS[13],
            FSRS_WEIGHTS[14],
        )
        new_s = w11 * math.pow(d, -w12) * (math.pow(s + 1.0, w13) - 1.0) * math.exp((1.0 - r) * w14)
        return max(0.01, min(new_s, s))
    except Exception:
        return max(0.01, 0.5)


# ---------------------------------------------------------------------------
# Public FSRS API
# ---------------------------------------------------------------------------


def fsrs_retrievability(card: Flashcard) -> float:
    """Calculate current retrievability R for *card*.

    R = (1 + elapsed / (9 * S))^(-1)

    Returns 1.0 when the card has never been reviewed.
    """
    try:
        if card.last_review is None:
            return 1.0
        elapsed = (date.today() - card.last_review).days
        if elapsed <= 0:
            return 1.0
        stability = card.stability if card.stability and card.stability > 0 else 0.01
        return math.pow(1.0 + elapsed / (9.0 * stability), -1.0)
    except Exception:
        return 1.0


def fsrs_update(card: Flashcard, rating: Rating) -> Flashcard:
    """Apply one FSRS v5 review to *card* and return the mutated card.

    Pure-ish function: mutates the ORM object in-place for SQLAlchemy change tracking.
    """
    try:
        # Validate rating
        if not isinstance(rating, Rating):
            try:
                rating = Rating(int(rating))
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Invalid rating: {rating}. Must be 1–4.") from exc

        current_state = State(card.state) if card.state is not None else State.New
        today = date.today()

        # Compute retrievability before update
        r = fsrs_retrievability(card)

        if current_state in (State.New, State.Learning):
            # --- New / Learning card ---
            card.stability = _initial_stability(rating)
            card.difficulty = _initial_difficulty(rating)

            if rating == Rating.Again:
                card.scheduled_days = 1
                card.state = State.Learning
            elif rating == Rating.Hard:
                card.scheduled_days = 1
                card.state = State.Learning
            elif rating == Rating.Good:
                card.scheduled_days = max(1, round(card.stability))
                card.state = State.Learning
            else:  # Easy
                card.scheduled_days = max(1, round(card.stability))
                card.state = State.Review

            card.reps = (card.reps or 0) + 1
            card.last_review = today
            card.next_review = today + timedelta(days=card.scheduled_days)

        elif current_state == State.Review:
            # --- Review card ---
            if rating == Rating.Again:
                # Lapse
                card.stability = _next_forget_stability(
                    card.stability or 0.01,
                    card.difficulty or 5.0,
                    r,
                )
                card.difficulty = _next_difficulty(card.difficulty or 5.0, rating)
                card.lapses = (card.lapses or 0) + 1
                card.state = State.Relearning
                card.scheduled_days = 1
            else:
                # Successful recall
                card.stability = _next_recall_stability(
                    card.stability or 0.01,
                    card.difficulty or 5.0,
                    r,
                    rating,
                )
                card.difficulty = _next_difficulty(card.difficulty or 5.0, rating)
                card.scheduled_days = max(1, round(card.stability))
                card.state = State.Review

            card.reps = (card.reps or 0) + 1
            card.last_review = today
            card.next_review = today + timedelta(days=card.scheduled_days)

        elif current_state == State.Relearning:
            # --- Relearning card ---
            if rating == Rating.Again:
                card.scheduled_days = 1
                card.state = State.Relearning
            elif rating == Rating.Hard:
                card.scheduled_days = 1
                card.state = State.Relearning
            elif rating == Rating.Good:
                card.scheduled_days = max(1, round(card.stability or 0.01))
                card.state = State.Review
            else:  # Easy
                card.scheduled_days = max(1, round(card.stability or 0.01))
                card.state = State.Review

            card.reps = (card.reps or 0) + 1
            card.last_review = today
            card.next_review = today + timedelta(days=card.scheduled_days)

        # Clamp difficulty
        if card.difficulty is not None:
            card.difficulty = max(1.0, min(10.0, card.difficulty))

        return card

    except ValueError:
        raise
    except Exception as exc:
        raise RuntimeError(f"FSRS update failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Flashcard generation / lookup helpers (moved from flashcard_sm2)
# ---------------------------------------------------------------------------


def _clean_generated_word(value: str) -> str:
    """Strip quotes, parenthetical content, and normalise whitespace."""
    try:
        cleaned = value.strip().strip("\"'")
        cleaned = re.sub(r"\s*\([^)]*\)", "", cleaned)
        return re.sub(r"\s+", " ", cleaned).strip()
    except Exception:
        return value


def _get_lang_hint(target_language: str) -> str:
    """Return the language-specific prompt overlay for *target_language*."""
    try:
        return get_language_prompt_overlay(target_language)
    except Exception:
        return ""


async def generate_flashcards(
    topic: str,
    count: int,
    cefr_level: str,
    native_language: str,
    target_language: str = "en-GB",
) -> FlashcardGenerateResponse:
    """Generate *count* flashcards for *topic* via LLM."""
    try:
        target_language_name = get_language_name(target_language)
        native_language_name = get_native_language_name(native_language)
        language_prompt_overlay = _get_lang_hint(target_language)
        prompt = build_flashcard_generation_prompt(
            topic=topic,
            count=count,
            cefr_level=cefr_level,
            native_language=native_language_name,
            target_language_name=target_language_name,
            language_prompt_overlay=language_prompt_overlay,
        )

        result = await llm_adapter.structured_output(
            [{"role": "system", "content": prompt}],
            FlashcardGenerateResponse,
        )
        result.flashcards = [
            GeneratedFlashcard(
                word=_clean_generated_word(card.word),
                definition=card.definition,
                example_sentence=card.example_sentence,
                translation=card.translation,
            )
            for card in result.flashcards
        ]
        return result
    except Exception:
        raise


async def lookup_word(
    word: str,
    context: str,
    cefr_level: str,
    native_language: str,
    target_language: str = "en-GB",
) -> FlashcardCreate:
    """Look up a single word via LLM and return a FlashcardCreate schema."""
    try:
        target_language_name = get_language_name(target_language)
        native_language_name = get_native_language_name(native_language)
        language_prompt_overlay = _get_lang_hint(target_language)
        prompt = build_word_lookup_prompt(
            word=word,
            context=context or word,
            cefr_level=cefr_level,
            native_language=native_language_name,
            target_language_name=target_language_name,
            language_prompt_overlay=language_prompt_overlay,
        )
        result = await llm_adapter.structured_output(
            [{"role": "system", "content": prompt}],
            FlashcardCreate,
        )
        result.word = _clean_generated_word(result.word)
        return result
    except Exception:
        raise
