"""Unit tests for flashcard SM-2 service helpers and LLM-backed functions."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.flashcards import (
    FlashcardCreate,
    FlashcardGenerateResponse,
    GeneratedFlashcard,
)


class TestCleanGeneratedWord:
    def test_strips_quotes(self):
        from app.services.flashcard_fsrs import _clean_generated_word

        assert _clean_generated_word('"hello"') == "hello"
        assert _clean_generated_word("'world'") == "world"

    def test_removes_parenthetical_content(self):
        from app.services.flashcard_fsrs import _clean_generated_word

        assert _clean_generated_word("run (verb)") == "run"
        assert _clean_generated_word("beautiful (adjective)") == "beautiful"
        assert _clean_generated_word("  fast  (adj)  ") == "fast"

    def test_removes_nested_parentheses(self):
        from app.services.flashcard_fsrs import _clean_generated_word

        result = _clean_generated_word("word (some (nested) thing)")
        assert "word" in result
        assert "some" not in result
        assert "nested" not in result

    def test_normalizes_whitespace(self):
        from app.services.flashcard_fsrs import _clean_generated_word

        assert _clean_generated_word("  hello   world  ") == "hello world"

    def test_handles_empty_string(self):
        from app.services.flashcard_fsrs import _clean_generated_word

        assert _clean_generated_word("") == ""


class TestGetLangHint:
    def test_known_language_de(self):
        from app.services.flashcard_fsrs import _get_lang_hint

        hint = _get_lang_hint("de")
        assert "standard german spelling" in hint.lower()

    def test_known_language_fr(self):
        from app.services.flashcard_fsrs import _get_lang_hint

        hint = _get_lang_hint("fr")
        assert "french" in hint.lower()

    def test_known_language_es(self):
        from app.services.flashcard_fsrs import _get_lang_hint

        hint = _get_lang_hint("es")
        assert "spanish" in hint.lower()

    def test_known_language_it(self):
        from app.services.flashcard_fsrs import _get_lang_hint

        hint = _get_lang_hint("it")
        assert "italian" in hint.lower()

    def test_known_language_pt(self):
        from app.services.flashcard_fsrs import _get_lang_hint

        hint = _get_lang_hint("pt")
        assert "portuguese" in hint.lower()

    def test_known_language_en_us(self):
        from app.services.flashcard_fsrs import _get_lang_hint

        hint = _get_lang_hint("en-US")
        assert "american english" in hint.lower()

    def test_known_language_en_gb(self):
        from app.services.flashcard_fsrs import _get_lang_hint

        hint = _get_lang_hint("en-GB")
        assert "british english" in hint.lower()

    def test_iso_fallback_de_de(self):
        from app.services.flashcard_fsrs import _get_lang_hint

        hint = _get_lang_hint("de-DE")
        assert "standard german spelling" in hint.lower()

    def test_iso_fallback_fr_fr(self):
        from app.services.flashcard_fsrs import _get_lang_hint

        hint = _get_lang_hint("fr-FR")
        assert "french" in hint.lower()

    def test_unknown_language_returns_empty(self):
        from app.services.flashcard_fsrs import _get_lang_hint

        assert _get_lang_hint("xx") == ""
        assert _get_lang_hint("") == ""


class TestGenerateFlashcards:
    @pytest.mark.asyncio
    async def test_generates_and_cleans_words(self):
        from app.services.flashcard_fsrs import generate_flashcards

        mock_response = FlashcardGenerateResponse(
            flashcards=[
                GeneratedFlashcard(
                    word='  "run"  ',
                    definition="moverse rapidamente",
                    example_sentence="I like to run in the park.",
                    translation="Me gusta correr en el parque.",
                ),
                GeneratedFlashcard(
                    word="study (verb)",
                    definition="dedicarse al aprendizaje",
                    example_sentence="She studies every day.",
                    translation="Ella estudia todos los dias.",
                ),
            ]
        )

        with patch(
            "app.services.flashcard_fsrs.llm_adapter.structured_output",
            AsyncMock(return_value=mock_response),
        ):
            result = await generate_flashcards(
                topic="daily activities",
                count=2,
                cefr_level="A1",
                native_language="spanish",
                target_language="en-GB",
            )

        assert len(result.flashcards) == 2
        assert result.flashcards[0].word == "run"
        assert result.flashcards[1].word == "study"

    @pytest.mark.asyncio
    async def test_native_language_code_is_humanized_in_prompt(self):
        from app.services.flashcard_fsrs import generate_flashcards

        mock_response = FlashcardGenerateResponse(
            flashcards=[
                GeneratedFlashcard(
                    word="book",
                    definition="libro",
                    example_sentence="I read a book.",
                    translation="libro",
                )
            ]
        )

        with patch(
            "app.services.flashcard_fsrs.llm_adapter.structured_output",
            AsyncMock(return_value=mock_response),
        ) as mock_structured:
            await generate_flashcards(
                topic="objects",
                count=1,
                cefr_level="A1",
                native_language="es",
                target_language="en-GB",
            )

        prompt = mock_structured.await_args.args[0][0]["content"]
        assert "definition in Spanish" in prompt
        assert "student's native language (Spanish)" in prompt


class TestLookupWord:
    @pytest.mark.asyncio
    async def test_lookup_returns_cleaned_flashcard(self):
        from app.services.flashcard_fsrs import lookup_word

        mock_response = FlashcardCreate(
            word='  "library"  ',
            definition="place with books",
            example_sentence="I go to the library to read.",
            translation="biblioteca",
        )

        with patch(
            "app.services.flashcard_fsrs.llm_adapter.structured_output",
            AsyncMock(return_value=mock_response),
        ):
            result = await lookup_word(
                word="library",
                context="I went to the library yesterday.",
                cefr_level="A2",
                native_language="spanish",
                target_language="en-GB",
            )

        assert result.word == "library"
        assert result.definition == "place with books"
        assert "library" in result.example_sentence
        assert result.translation == "biblioteca"

    @pytest.mark.asyncio
    async def test_lookup_falls_back_word_for_context(self):
        from app.services.flashcard_fsrs import lookup_word

        mock_response = FlashcardCreate(
            word="hello",
            definition="greeting",
            example_sentence="Hello, how are you?",
            translation="hola",
        )

        with patch(
            "app.services.flashcard_fsrs.llm_adapter.structured_output",
            AsyncMock(return_value=mock_response),
        ):
            result = await lookup_word(
                word="hello",
                context="",
                cefr_level="A1",
                native_language="spanish",
                target_language="en-GB",
            )

        assert result.word == "hello"
