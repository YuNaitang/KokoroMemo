"""Abstract vector store interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class VectorSearchResult:
    card_id: str
    score: float
    content: str | None


class VectorStore(ABC):
    @abstractmethod
    async def connect(self) -> None:
        ...

    @abstractmethod
    async def close(self) -> None:
        ...

    @abstractmethod
    async def upsert(self, card_id: str, vector: list[float], text: str, metadata: dict | None = None) -> None:
        ...

    @abstractmethod
    async def delete(self, card_id: str) -> None:
        ...

    @abstractmethod
    async def search(self, query_vector: list[float], top_k: int = 10) -> list[VectorSearchResult]:
        ...

    @abstractmethod
    async def rebuild(self, cards: list[dict], embed_func) -> dict:
        ...
