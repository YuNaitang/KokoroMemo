"""LanceDB vector store implementation."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import numpy as np
from app.storage.vector_base import VectorStore, VectorSearchResult

logger = logging.getLogger("kokoromemo.vector_lancedb")


class LanceDBVectorStore(VectorStore):
    def __init__(self, db_path: str, table_name: str = "memories", dimension: int = 4096):
        self.db_path = db_path
        self.table_name = table_name
        self.dimension = dimension
        self._table: Any = None

    async def connect(self) -> None:
        """初始化 LanceDB 连接（在 executor 中运行以兼容阻塞 API）。"""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._connect_sync)

    def _connect_sync(self) -> None:
        import lancedb
        db = lancedb.connect(self.db_path)
        try:
            self._table = db.open_table(self.table_name)
        except Exception:
            from lancedb.pydantic import LanceModel, Vector
            class CardModel(LanceModel):
                card_id: str
                vector: Vector(self.dimension)
                text: str

            self._table = db.create_table(self.table_name, schema=CardModel, exist_ok=True)

    async def close(self) -> None:
        self._table = None

    async def upsert(self, card_id: str, vector: list[float], text: str, metadata: dict | None = None) -> None:
        if self._table is None:
            return
        loop = asyncio.get_running_loop()
        data = {"card_id": card_id, "vector": vector, "text": text}
        if metadata:
            data.update(metadata)
        await loop.run_in_executor(None, lambda: self._table.merge_insert("card_id").when_matched_update_all().execute([data]))

    async def delete(self, card_id: str) -> None:
        if self._table is None:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: self._table.delete(f"card_id = '{card_id}'"))

    async def search(self, query_vector: list[float], top_k: int = 10) -> list[VectorSearchResult]:
        if self._table is None:
            return []
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None,
            lambda: self._table.search(np.array(query_vector, dtype=np.float32)).limit(top_k).to_list(),
        )
        return [
            VectorSearchResult(card_id=r["card_id"], score=r["_distance"], content=r.get("text"))
            for r in results
        ]

    async def rebuild(self, cards: list[dict], embed_func) -> dict:
        """删除旧表并用最新卡片重建向量索引。"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self._rebuild_sync(cards, embed_func))

    def _rebuild_sync(self, cards: list[dict], embed_func) -> dict:
        import lancedb
        db = lancedb.connect(self.db_path)
        db.drop_table(self.table_name, ignore_missing=True)
        self._connect_sync()
        count = 0
        for card in cards:
            vec = embed_func(card.get("content", ""))
            if vec:
                self._table.add([{"card_id": card["card_id"], "vector": vec, "text": card.get("content", "")}])
                count += 1
        return {"rebuilt": count, "total": len(cards)}
