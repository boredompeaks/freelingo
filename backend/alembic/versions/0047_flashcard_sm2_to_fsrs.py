"""Migrate flashcard columns from SM-2 to FSRS v5

Drops: ease_factor, interval, repetitions
Adds: stability, difficulty, state, reps, lapses, last_review, scheduled_days

All existing cards are reset to state=0 (New) with FSRS defaults.

Revision ID: 0047_flashcard_sm2_to_fsrs
Revises: 0046_feedback_read_states
Create Date: 2026-07-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0047_flashcard_sm2_to_fsrs"
down_revision: str | None = "0046_feedback_read_states"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add new FSRS columns with defaults first (so existing rows get sensible values)
    op.add_column(
        "flashcards",
        sa.Column("stability", sa.Float(), nullable=False, server_default="0.0"),
    )
    op.add_column(
        "flashcards",
        sa.Column("difficulty", sa.Float(), nullable=False, server_default="0.0"),
    )
    op.add_column(
        "flashcards",
        sa.Column("state", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "flashcards",
        sa.Column("reps", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "flashcards",
        sa.Column("lapses", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "flashcards",
        sa.Column("last_review", sa.Date(), nullable=True),
    )
    op.add_column(
        "flashcards",
        sa.Column("scheduled_days", sa.Integer(), nullable=False, server_default="0"),
    )

    # Drop old SM-2 columns
    op.drop_column("flashcards", "ease_factor")
    op.drop_column("flashcards", "interval")
    op.drop_column("flashcards", "repetitions")


def downgrade() -> None:
    # Re-add SM-2 columns
    op.add_column(
        "flashcards",
        sa.Column("ease_factor", sa.Float(), nullable=False, server_default="2.5"),
    )
    op.add_column(
        "flashcards",
        sa.Column("interval", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "flashcards",
        sa.Column("repetitions", sa.Integer(), nullable=False, server_default="0"),
    )

    # Drop FSRS columns
    op.drop_column("flashcards", "stability")
    op.drop_column("flashcards", "difficulty")
    op.drop_column("flashcards", "state")
    op.drop_column("flashcards", "reps")
    op.drop_column("flashcards", "lapses")
    op.drop_column("flashcards", "last_review")
    op.drop_column("flashcards", "scheduled_days")
