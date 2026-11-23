"""revisión multiagente y decisiones HITL

Revision ID: c4f1a2b3d5e6
Revises: a0353ffe7d0e
Create Date: 2026-11-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4f1a2b3d5e6"
down_revision: Union[str, None] = "a0353ffe7d0e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "story_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("story_id", sa.Integer(), sa.ForeignKey("user_stories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("story_version", sa.Integer(), nullable=False),
        sa.Column("applied_version", sa.Integer(), nullable=True),
        sa.Column("agent_run_id", sa.Integer(), sa.ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("level", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("quality", sa.Float(), nullable=True),
        sa.Column("story_type", sa.String(24), nullable=True),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("questions", sa.JSON(), nullable=False),
        sa.Column("proposal", sa.JSON(), nullable=True),
        sa.Column("estimation", sa.JSON(), nullable=True),
        sa.Column("agents", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_story_reviews_project_id", "story_reviews", ["project_id"])
    op.create_index("ix_story_reviews_story_id", "story_reviews", ["story_id"])
    op.create_index("ix_story_reviews_status", "story_reviews", ["status"])
    op.create_table(
        "hitl_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("review_id", sa.Integer(), sa.ForeignKey("story_reviews.id", ondelete="CASCADE"), nullable=False),
        sa.Column("story_id", sa.Integer(), sa.ForeignKey("user_stories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("applied_proposal", sa.Boolean(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=True),
        sa.Column("role", sa.String(32), nullable=True),
        sa.Column("decided_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_hitl_decisions_review_id", "hitl_decisions", ["review_id"])
    op.create_index("ix_hitl_decisions_story_id", "hitl_decisions", ["story_id"])


def downgrade() -> None:
    op.drop_table("hitl_decisions")
    op.drop_table("story_reviews")
