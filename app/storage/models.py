"""SQLAlchemy ORM models for KokoroMemo."""
from __future__ import annotations

from sqlalchemy import Column, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class MemoryCard(Base):
    __tablename__ = "memory_cards"

    card_id: Mapped[str] = mapped_column(String, primary_key=True)
    library_id: Mapped[str] = mapped_column(String, default="lib_default")
    user_id: Mapped[str] = mapped_column(String)
    character_id: Mapped[str | None] = mapped_column(String, nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String, nullable=True)
    scope: Mapped[str] = mapped_column(String)
    card_type: Mapped[str] = mapped_column(String)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    content: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(String, nullable=True)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    confidence: Mapped[float] = mapped_column(Float, default=0.7)
    stability: Mapped[float] = mapped_column(Float, default=0.5)
    status: Mapped[str] = mapped_column(String, default="pending_review")
    is_pinned: Mapped[int] = mapped_column(Integer, default=0)
    embedding_model: Mapped[str | None] = mapped_column(String, nullable=True)
    embedding_dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vector_synced: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)


class ConversationStateItem(Base):
    __tablename__ = "conversation_state_items"

    item_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    character_id: Mapped[str | None] = mapped_column(String, nullable=True)
    conversation_id: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String)
    item_key: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    content: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.7)
    resolved: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)
