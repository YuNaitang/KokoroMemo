"""SQLAlchemy async engine and session management."""
from __future__ import annotations

import os
from pathlib import Path
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine, AsyncEngine

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _resolve_db_url() -> str:
    """返回 SQLAlchemy 连接 URL。KOKOROMEMO_DB_URL 为空则走 SQLite file 模式。"""
    url = os.getenv("KOKOROMEMO_DB_URL", "").strip()
    if url:
        return url
    # file 模式：SQLite 存储在 data 目录
    db_dir = Path(os.getenv("KOKOROMEMO_DATA_DIR", "/app/data"))
    db_dir.mkdir(parents=True, exist_ok=True)
    sqlite_path = db_dir / "app.sqlite"
    return f"sqlite+aiosqlite:///{sqlite_path}"


def is_server_mode() -> bool:
    """是否为 server 模式（即配置了 PostgreSQL URL）。"""
    url = os.getenv("KOKOROMEMO_DB_URL", "").strip()
    return bool(url) and url.startswith("postgresql")


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        url = _resolve_db_url()
        if url.startswith("sqlite"):
            _engine = create_async_engine(url, echo=False, connect_args={"check_same_thread": False})
        else:
            _engine = create_async_engine(url, echo=False, pool_size=5, max_overflow=10)
    return _engine


async def init_db() -> None:
    """初始化数据库连接和 session 工厂。在应用启动时调用。"""
    global _sessionmaker
    engine = get_engine()
    _sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    # file 模式：使用 legacy init 机制创建表结构
    if not is_server_mode():
        from app.storage.sqlite_app import init_app_db
        from app.storage.sqlite_cards import init_cards_db
        from app.storage.sqlite_conversation import init_chat_db
        from app.storage.sqlite_state import init_state_db
        db_path = str(Path(os.getenv("KOKOROMEMO_DATA_DIR", "/app/data")) / "app.sqlite")
        await init_app_db(db_path)
        await init_cards_db(db_path)
        await init_chat_db(db_path)
        await init_state_db(db_path)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """获取数据库 session（作为 FastAPI dependency 使用）。"""
    global _sessionmaker
    if _sessionmaker is None:
        await init_db()
    async with _sessionmaker() as session:  # type: ignore[union-attr]
        yield session


async def close_db() -> None:
    """关闭数据库连接。在应用关闭时调用。"""
    global _engine, _sessionmaker
    if _engine:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None
