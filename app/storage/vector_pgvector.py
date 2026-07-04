"""pgvector vector store implementation for server mode."""
from __future__ import annotations

import logging

from sqlalchemy import Column, Float, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.storage.vector_base import VectorStore, VectorSearchResult

logger = logging.getLogger("kokoromemo.vector_pgvector")


class PgVectorBase(DeclarativeBase):
    pass


class PgVectorCard(PgVectorBase):
    """pgvector 表，存储记忆卡片的向量嵌入。"""
    __tablename__ = "memory_vectors"

    card_id: Mapped[str] = mapped_column(String, primary_key=True)
    embedding: Mapped[list[float]] = mapped_column(ARRAY(Float))
    text: Mapped[str] = mapped_column(Text)
    dimension: Mapped[int] = mapped_column(Integer)


class PgVectorStore(VectorStore):
    def __init__(self, session_factory, dimension: int = 4096):
        self._session_factory = session_factory
        self.dimension = dimension
        self._session: AsyncSession | None = None

    async def connect(self) -> None:
        """确保 pgvector 扩展已启用，并创建表。"""
        self._session = self._session_factory()
        async with self._session.begin():
            await self._session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            # 检查是否已创建表
            from sqlalchemy import inspect
            inspector = await self._session.connection()
            tables = await inspector.run_sync(lambda conn: inspect(conn).get_table_names())
            if "memory_vectors" not in tables:
                async with self._session.begin():
                    await self._session.execute(
                        text(f"""
                            CREATE TABLE memory_vectors (
                                card_id TEXT PRIMARY KEY,
                                embedding vector({self.dimension}),
                                text TEXT,
                                dimension INTEGER DEFAULT {self.dimension}
                            )
                        """)
                    )
                    await self._session.execute(
                        text("CREATE INDEX IF NOT EXISTS idx_memory_vectors_embedding ON memory_vectors USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)")
                    )

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def upsert(self, card_id: str, vector: list[float], text: str, metadata: dict | None = None) -> None:
        if not self._session:
            return
        async with self._session.begin():
            await self._session.execute(
                text("""
                    INSERT INTO memory_vectors (card_id, embedding, text, dimension)
                    VALUES (:card_id, :embedding::vector, :text, :dimension)
                    ON CONFLICT (card_id) DO UPDATE SET
                        embedding = EXCLUDED.embedding,
                        text = EXCLUDED.text,
                        dimension = EXCLUDED.dimension
                """),
                {"card_id": card_id, "embedding": str(vector), "text": text, "dimension": self.dimension},
            )

    async def delete(self, card_id: str) -> None:
        if not self._session:
            return
        async with self._session.begin():
            await self._session.execute(
                text("DELETE FROM memory_vectors WHERE card_id = :card_id"),
                {"card_id": card_id},
            )

    async def search(self, query_vector: list[float], top_k: int = 10) -> list[VectorSearchResult]:
        if not self._session:
            return []
        async with self._session.begin():
            result = await self._session.execute(
                text("""
                    SELECT card_id, 1 - (embedding <=> :query::vector) AS score, text
                    FROM memory_vectors
                    ORDER BY embedding <=> :query::vector
                    LIMIT :top_k
                """),
                {"query": str(query_vector), "top_k": top_k},
            )
            rows = result.fetchall()
        return [VectorSearchResult(card_id=r[0], score=float(r[1]), content=r[2]) for r in rows]

    async def rebuild(self, cards: list[dict], embed_func) -> dict:
        """清空并重建 pgvector 索引。"""
        if not self._session:
            return {"rebuilt": 0, "total": len(cards)}
        async with self._session.begin():
            await self._session.execute(text("TRUNCATE memory_vectors"))
        count = 0
        for card in cards:
            vec = embed_func(card.get("content", ""))
            if vec:
                await self.upsert(card["card_id"], vec, card.get("content", ""))
                count += 1
        return {"rebuilt": count, "total": len(cards)}
