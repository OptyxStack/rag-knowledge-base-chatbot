"""Seed llm_model and llm_fallback_model in app_config

Revision ID: 009
Revises: 008
Create Date: 2025-03-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO app_config (id, key, value) SELECT gen_random_uuid(), 'llm_model', 'gpt-4o-mini' "
            "WHERE NOT EXISTS (SELECT 1 FROM app_config WHERE key = 'llm_model')"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO app_config (id, key, value) SELECT gen_random_uuid(), 'llm_fallback_model', 'gpt-3.5-turbo' "
            "WHERE NOT EXISTS (SELECT 1 FROM app_config WHERE key = 'llm_fallback_model')"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM app_config WHERE key IN ('llm_model', 'llm_fallback_model')"))
