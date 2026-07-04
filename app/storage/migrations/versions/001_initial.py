"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-07-04
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # memory_cards 表
    op.create_table(
        "memory_cards",
        sa.Column("card_id", sa.String(), nullable=False),
        sa.Column("library_id", sa.String(), nullable=False, server_default="lib_default"),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("character_id", sa.String(), nullable=True),
        sa.Column("conversation_id", sa.String(), nullable=True),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("card_type", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("summary", sa.String(), nullable=True),
        sa.Column("importance", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.7"),
        sa.Column("stability", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending_review"),
        sa.Column("is_pinned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_turn_ids_json", sa.String(), nullable=True),
        sa.Column("evidence_text", sa.String(), nullable=True),
        sa.Column("supersedes_card_id", sa.String(), nullable=True),
        sa.Column("embedding_model", sa.String(), nullable=True),
        sa.Column("embedding_dimension", sa.Integer(), nullable=True),
        sa.Column("vector_synced", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("vector_synced_at", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False, server_default=sa.text("datetime('now')")),
        sa.Column("updated_at", sa.String(), nullable=False, server_default=sa.text("datetime('now')")),
        sa.Column("last_accessed_at", sa.String(), nullable=True),
        sa.Column("access_count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("card_id"),
    )
    op.create_index("idx_cards_scope", "memory_cards", ["user_id", "character_id", "scope", "status"])

    # conversation_state_items 表
    op.create_table(
        "conversation_state_items",
        sa.Column("item_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("character_id", sa.String(), nullable=True),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("world_id", sa.String(), nullable=True),
        sa.Column("template_id", sa.String(), nullable=True),
        sa.Column("tab_id", sa.String(), nullable=True),
        sa.Column("field_id", sa.String(), nullable=True),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("item_key", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.7"),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("resolved", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.String(), nullable=False, server_default=sa.text("datetime('now')")),
        sa.Column("updated_at", sa.String(), nullable=False, server_default=sa.text("datetime('now')")),
        sa.PrimaryKeyConstraint("item_id"),
    )
    op.create_index("idx_state_conversation", "conversation_state_items", ["conversation_id", "category"])


def downgrade() -> None:
    op.drop_index("idx_state_conversation", table_name="conversation_state_items")
    op.drop_table("conversation_state_items")
    op.drop_index("idx_cards_scope", table_name="memory_cards")
    op.drop_table("memory_cards")
