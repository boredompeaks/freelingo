from datetime import date, timedelta

import pytest


async def _seed_plan(db_session, user_id: int):
    """Create a minimal active A1 plan so flashcard endpoints have a study_plan_id."""
    from tests.conftest import make_study_plan

    return await make_study_plan(
        db_session,
        user_id=user_id,
        cefr_level="A1",
        target_language="en-US",
        goals=["grammar"],
        duration_weeks=4,
        days_per_week=4,
        current_unit="",
        generated_plan={},
        is_active=True,
    )


from app.services.flashcard_fsrs import Rating, State, fsrs_retrievability, fsrs_update


class MockCard:
    """Mock card with FSRS fields for unit-testing the algorithm."""

    def __init__(
        self,
        stability=0.0,
        difficulty=0.0,
        state=0,
        reps=0,
        lapses=0,
        last_review=None,
        scheduled_days=0,
    ):
        self.stability = stability
        self.difficulty = difficulty
        self.state = state
        self.reps = reps
        self.lapses = lapses
        self.last_review = last_review
        self.scheduled_days = scheduled_days
        self.next_review = date.today()


# ---------------------------------------------------------------------------
# FSRS algorithm unit tests
# ---------------------------------------------------------------------------


def test_fsrs_good_on_new_card():
    """Rating Good on a new card should transition to Learning with a scheduled interval."""
    card = MockCard()
    card = fsrs_update(card, Rating.Good)
    assert card.state == State.Learning
    assert card.reps == 1
    assert card.stability > 0
    assert card.scheduled_days >= 1
    assert card.next_review >= date.today()


def test_fsrs_easy_on_new_card():
    """Rating Easy on a new card should transition directly to Review."""
    card = MockCard()
    card = fsrs_update(card, Rating.Easy)
    assert card.state == State.Review
    assert card.reps == 1
    assert card.stability > 0
    assert card.scheduled_days >= 1


def test_fsrs_again_on_new_card():
    """Rating Again on a new card should stay in Learning with short interval."""
    card = MockCard()
    card = fsrs_update(card, Rating.Again)
    assert card.state == State.Learning
    assert card.reps == 1
    assert card.scheduled_days == 1


def test_fsrs_hard_on_new_card():
    """Rating Hard on a new card should stay in Learning."""
    card = MockCard()
    card = fsrs_update(card, Rating.Hard)
    assert card.state == State.Learning
    assert card.reps == 1


def test_fsrs_again_on_review_transitions_to_relearning():
    """Rating Again on a Review card should transition to Relearning and increment lapses."""
    card = MockCard(
        stability=10.0,
        difficulty=5.0,
        state=State.Review,
        reps=5,
        lapses=0,
        last_review=date.today() - timedelta(days=5),
    )
    card = fsrs_update(card, Rating.Again)
    assert card.state == State.Relearning
    assert card.lapses == 1
    assert card.reps == 6
    assert card.scheduled_days == 1


def test_fsrs_good_on_review_stays_review():
    """Rating Good on a Review card should stay in Review with updated stability."""
    card = MockCard(
        stability=10.0,
        difficulty=5.0,
        state=State.Review,
        reps=5,
        lapses=0,
        last_review=date.today() - timedelta(days=5),
    )
    old_stability = card.stability
    card = fsrs_update(card, Rating.Good)
    assert card.state == State.Review
    assert card.reps == 6
    assert card.stability != old_stability  # stability should change


def test_fsrs_good_on_relearning_transitions_to_review():
    """Rating Good on a Relearning card should transition back to Review."""
    card = MockCard(
        stability=2.0,
        difficulty=6.0,
        state=State.Relearning,
        reps=6,
        lapses=1,
        last_review=date.today() - timedelta(days=1),
    )
    card = fsrs_update(card, Rating.Good)
    assert card.state == State.Review
    assert card.reps == 7


def test_fsrs_again_on_relearning_stays_relearning():
    """Rating Again on a Relearning card should keep it in Relearning."""
    card = MockCard(
        stability=2.0,
        difficulty=6.0,
        state=State.Relearning,
        reps=6,
        lapses=1,
        last_review=date.today() - timedelta(days=1),
    )
    card = fsrs_update(card, Rating.Again)
    assert card.state == State.Relearning
    assert card.scheduled_days == 1


def test_fsrs_retrievability_no_review():
    """Retrievability should be 1.0 when the card has never been reviewed."""
    card = MockCard()
    r = fsrs_retrievability(card)
    assert r == 1.0


def test_fsrs_retrievability_after_review():
    """Retrievability should be < 1.0 after some days have elapsed since review."""
    card = MockCard(
        stability=5.0,
        last_review=date.today() - timedelta(days=10),
    )
    r = fsrs_retrievability(card)
    assert 0.0 < r < 1.0


def test_fsrs_retrievability_same_day():
    """Retrievability should be 1.0 on the day of review."""
    card = MockCard(
        stability=5.0,
        last_review=date.today(),
    )
    r = fsrs_retrievability(card)
    assert r == 1.0


def test_fsrs_difficulty_clamped():
    """Difficulty should always be clamped between 1.0 and 10.0."""
    card = MockCard()
    card = fsrs_update(card, Rating.Again)
    assert 1.0 <= card.difficulty <= 10.0

    card2 = MockCard()
    card2 = fsrs_update(card2, Rating.Easy)
    assert 1.0 <= card2.difficulty <= 10.0


def test_fsrs_next_review_is_future():
    """next_review should always be today or in the future after an update."""
    card = MockCard(
        stability=10.0,
        difficulty=5.0,
        state=State.Review,
        reps=5,
        last_review=date.today() - timedelta(days=5),
    )
    card = fsrs_update(card, Rating.Good)
    assert card.next_review >= date.today()


def test_fsrs_invalid_rating_raises():
    """Passing an invalid rating value should raise ValueError."""
    card = MockCard()
    with pytest.raises(ValueError, match="Invalid rating"):
        fsrs_update(card, 0)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Invalid rating"):
        fsrs_update(card, 5)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Router / endpoint integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_flashcard(client, test_user, db_session):
    user, headers = test_user
    await _seed_plan(db_session, user.id)

    response = await client.post(
        "/api/flashcards",
        headers=headers,
        json={
            "word": "hello",
            "definition": "a greeting",
            "example_sentence": "Hello, how are you?",
            "translation": "hola",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["word"] == "hello"
    assert data["stability"] == 0.0
    assert data["state"] == 0  # New


@pytest.mark.asyncio
async def test_get_due_flashcards(client, test_user, db_session):
    user, headers = test_user

    from app.models.flashcard import Flashcard

    plan = await _seed_plan(db_session, user.id)

    card = Flashcard(
        user_id=user.id,
        study_plan_id=plan.id,
        word="test",
        definition="a test",
        example_sentence="This is a test.",
        translation="prueba",
    )
    db_session.add(card)
    await db_session.commit()

    response = await client.get("/api/flashcards/due", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data["due"]) == 1
    assert data["total"] == 1


@pytest.mark.asyncio
async def test_review_flashcard(client, test_user, db_session):
    user, headers = test_user

    from app.models.flashcard import Flashcard

    plan = await _seed_plan(db_session, user.id)

    card = Flashcard(
        user_id=user.id,
        study_plan_id=plan.id,
        word="test",
        definition="a test",
        example_sentence="Test.",
        translation="prueba",
    )
    db_session.add(card)
    await db_session.commit()

    response = await client.post(
        f"/api/flashcards/{card.id}/review",
        headers=headers,
        json={"rating": 3},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["reps"] == 1
    assert data["state"] == State.Learning
    assert data["stability"] > 0


@pytest.mark.asyncio
async def test_get_vocabulary_flashcards(client, test_user, db_session):
    user, headers = test_user
    plan = await _seed_plan(db_session, user.id)

    from app.models.flashcard import Flashcard

    # from_text card
    card_vocab = Flashcard(
        user_id=user.id,
        study_plan_id=plan.id,
        word="ephemeral",
        definition="lasting a very short time",
        example_sentence="The joy was ephemeral.",
        translation="efímero",
        source="from_text",
    )
    # regular card (should not appear in vocabulary endpoint)
    card_regular = Flashcard(
        user_id=user.id,
        study_plan_id=plan.id,
        word="run",
        definition="to move fast",
        example_sentence="I run every day.",
        translation="correr",
    )
    db_session.add(card_vocab)
    db_session.add(card_regular)
    await db_session.commit()

    response = await client.get("/api/flashcards/vocabulary", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["word"] == "ephemeral"
    assert data["items"][0]["source"] == "from_text"


@pytest.mark.asyncio
async def test_delete_flashcard(client, test_user, db_session):
    user, headers = test_user

    from app.models.flashcard import Flashcard

    plan = await _seed_plan(db_session, user.id)

    card = Flashcard(
        user_id=user.id,
        study_plan_id=plan.id,
        word="obsolete",
        definition="no longer in use",
        example_sentence="This word is obsolete.",
        translation="obsoleto",
        source="from_text",
    )
    db_session.add(card)
    await db_session.commit()

    response = await client.delete(f"/api/flashcards/{card.id}", headers=headers)
    assert response.status_code == 204

    # Confirm deletion
    response = await client.get("/api/flashcards/vocabulary", headers=headers)
    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert response.json()["items"] == []


@pytest.mark.asyncio
async def test_delete_flashcard_not_found(client, test_user):
    _, headers = test_user
    response = await client.delete("/api/flashcards/99999", headers=headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_flashcard_other_user(client, test_user, db_session):
    user, headers = test_user

    from app.core.security import hash_password
    from app.models.flashcard import Flashcard
    from app.models.user import User

    other_user = User(
        username="other",
        email="other@example.com",
        display_name="Other",
        hashed_password=hash_password("pass"),
        role="user",
        native_language="es",
        is_active=True,
    )
    db_session.add(other_user)
    await db_session.commit()
    await db_session.refresh(other_user)

    other_plan = await _seed_plan(db_session, other_user.id)

    card = Flashcard(
        user_id=other_user.id,
        study_plan_id=other_plan.id,
        word="word",
        definition="def",
        example_sentence="example",
        translation="traducción",
    )
    db_session.add(card)
    await db_session.commit()

    response = await client.delete(f"/api/flashcards/{card.id}", headers=headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_flashcard_from_word(client, test_user, db_session, monkeypatch):
    _, headers = test_user
    await _seed_plan(db_session, test_user[0].id)

    from app.schemas.flashcards import FlashcardCreate

    async def mock_lookup_word(**kwargs):  # noqa: ANN002
        return FlashcardCreate(
            word=kwargs["word"],
            definition="lasting a very short time",
            example_sentence="The fame was fleeting.",
            translation="efímero",
        )

    import app.routers.flashcards as fc_router

    monkeypatch.setattr(fc_router, "lookup_word", mock_lookup_word)

    response = await client.post(
        "/api/flashcards/from-word",
        headers=headers,
        json={
            "word": "fleeting",
            "context": "The fame was fleeting.",
            "cefr_level": "B2",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["word"] == "fleeting"
    assert data["source"] == "from_text"
    assert data["definition"] == "lasting a very short time"
