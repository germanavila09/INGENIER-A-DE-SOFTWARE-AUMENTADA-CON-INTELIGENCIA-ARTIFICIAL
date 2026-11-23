"""revisión HITL vigente por contenido de la HU, no por versión

Revision ID: d8e2b1c4a7f9
Revises: c4f1a2b3d5e6
Create Date: 2026-11-22
"""

import sqlalchemy as sa

from alembic import op

revision = "d8e2b1c4a7f9"
down_revision = "c4f1a2b3d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("story_reviews") as batch:
        batch.add_column(sa.Column("content_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("applied_hash", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("story_reviews") as batch:
        batch.drop_column("applied_hash")
        batch.drop_column("content_hash")
