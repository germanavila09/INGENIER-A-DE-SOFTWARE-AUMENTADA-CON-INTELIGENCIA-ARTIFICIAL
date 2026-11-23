"""criterios de aceptacion en markdown

Convierte user_stories.acceptance_criteria de JSON (lista de strings) a TEXT (markdown).
Las listas existentes pasan a viñetas "- item". El downgrade hace la operación inversa.

Revision ID: b7e2c9d41f10
Revises: 8d3ae1fb534a
Create Date: 2026-10-10
"""

import json
import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7e2c9d41f10"
down_revision: str | None = "8d3ae1fb534a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _as_list(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, bytes):
        raw = raw.decode()
    if isinstance(raw, str):
        return json.loads(raw) if raw.strip() else []
    return list(raw)


def _to_list(markdown: str | None) -> list[str]:
    """Extrae los items de una lista markdown (quita el prefijo de checkbox si existe)."""
    if not markdown:
        return []
    items = []
    for line in markdown.splitlines():
        match = re.match(r"^\s*[-*]\s+(?:\[[ xX]\]\s+)?(.*\S)\s*$", line)
        if match:
            items.append(match.group(1))
    return items


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, acceptance_criteria FROM user_stories")).fetchall()

    op.add_column("user_stories", sa.Column("acceptance_criteria_md", sa.Text(), nullable=True))
    update = sa.text("UPDATE user_stories SET acceptance_criteria_md = :md WHERE id = :id")
    for story_id, raw in rows:
        items = _as_list(raw)
        md = "\n".join(f"- {item}" for item in items) if items else None
        conn.execute(update, {"md": md, "id": story_id})

    with op.batch_alter_table("user_stories") as batch:
        batch.drop_column("acceptance_criteria")
        batch.alter_column("acceptance_criteria_md", new_column_name="acceptance_criteria", nullable=True)


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, acceptance_criteria FROM user_stories")).fetchall()

    op.add_column("user_stories", sa.Column("acceptance_criteria_json", sa.JSON(), nullable=True))
    update = sa.text("UPDATE user_stories SET acceptance_criteria_json = :items WHERE id = :id").bindparams(
        sa.bindparam("items", type_=sa.JSON())
    )
    for story_id, markdown in rows:
        conn.execute(update, {"items": _to_list(markdown), "id": story_id})

    with op.batch_alter_table("user_stories") as batch:
        batch.drop_column("acceptance_criteria")
        batch.alter_column("acceptance_criteria_json", new_column_name="acceptance_criteria", nullable=False)
