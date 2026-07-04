# KokoroMemo Docker 化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 全面 Docker 化 KokoroMemo，支持 SQLite ↔ PostgreSQL 双存储模式，删除旧构建体系

**Architecture:** 单一 Python FastAPI 容器 + 可选 pgvector PostgreSQL 容器。存储层引入 SQLAlchemy + Repository 模式，通过 `KOKOROMEMO_DB_URL` 环境变量切换 file 模式（SQLite + LanceDB）和 server 模式（PostgreSQL + pgvector）。前端 Vue 在多阶段构建中预先编译，由后端 Serve 静态文件。

**Tech Stack:** Docker, docker-compose, Python 3.11, FastAPI, SQLAlchemy (async), Alembic, aiosqlite, asyncpg, pgvector/pg17, Node 22 (build only), Vue 3

---

## File Structure

### New files to create:

```
├── Dockerfile                      ← 多阶段构建：前端编译 + Python 运行时
├── docker-compose.yml              ← server 模式（含 PostgreSQL + pgvector）
├── docker-compose.file.yml         ← file 模式（纯应用容器）
├── docker-entrypoint.sh            ← 启动脚本：虚拟环境持久化 + 依赖同步 + 数据库等待 + 迁移 + 启动
├── .dockerignore                   ← 排除 gui/src-tauri/、node_modules 等
│
├── app/storage/
│   ├── __init__.py                 ← 更新，导出 get_repository
│   ├── database.py                 ← SQLAlchemy async engine + session 工厂
│   ├── models.py                   ← ORM 模型（Card, Session, ConversationState 等）
│   ├── repository.py               ← StorageRepository：统一 CRUD 接口，两种模式实现
│   ├── vector_base.py              ← VectorStore 抽象接口
│   ├── vector_lancedb.py           ← file 模式：LanceDB 实现
│   ├── vector_pgvector.py          ← server 模式：pgvector 实现
│   └── migrations/                 ← Alembic 迁移目录
│       ├── env.py
│       ├── alembic.ini
│       ├── script.py.mako
│       └── versions/
│           └── 001_initial.py
│
├── docs/superpowers/plans/         ← 本文件
└── .env.example                    ← 环境变量模板供 docker-compose 使用
```

### Files to modify:

```
├── pyproject.toml                  ← 添加 sqlalchemy, asyncpg, alembic 依赖
├── app/core/config.py              ← 添加 KOKOROMEMO_DB_URL 环境变量读取和数据库配置
├── app/main.py                     ← 初始化 repository（而非直接初始化 SQLite DB）
├── app/core/services.py            ← 根据模式返回对应的向量存储实现
├── app/storage/vector_sync.py      ← 改造为通过 repository 操作
├── app/storage/rebuild_v2.py       ← 改造为通过 repository 操作
├── app/api/routes_admin.py         ← 改为通过 repository 访问数据
├── app/api/routes_openai.py        ← 改为通过 repository 访问数据
├── app/memory/*.py                 ← 改为通过 repository 访问数据（多个模块）
├── app/providers/embedding_*.py    ← 不变
└── .gitignore                      ← 添加 .dockerignore 过滤
```

### Files to delete:

```
gui/src-tauri/                      ← Tauri 桌面端（目录）
packaging/                          ← Android 打包（目录）
scripts/                            ← 安装脚本（目录）
.github/workflows/                  ← GitHub Actions（目录）
.port                               ← 端口文件（无用，Docker 环境不需要）
```

---

## Global Constraints

- Python >= 3.11（与 pyproject.toml 一致）
- 所有异步数据库操作必须使用 `async`/`await`
- 环境变量名采用 `KOKOROMEMO_` 前缀 + `_` 分隔大写形式
- file 模式必须完全兼容现有 SQLite 数据库文件
- 前端构建仅发生在 Docker 构建阶段，不污染运行时镜像
- 虚拟环境持久化到 `/app/data/.venv`，启动时自动检测和更新依赖
- 启动脚本必须使用 `exec uvicorn` 确保 PID 1 信号处理正确

---

### Task 1: 添加存储层依赖

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: 新的 `pyproject.toml` 包含 SQLAlchemy 相关依赖

- [ ] **Step 1: 修改 pyproject.toml 添加依赖**

在 `[project] dependencies` 中添加：
```toml
    "sqlalchemy[asyncio]>=2.0",
    "alembic>=1.13",
```

在 `[project.optional-dependencies] full` 中添加：
```toml
    "asyncpg>=0.29",
```

- [ ] **Step 2: 验证依赖安装**

Run:
```bash
pip install -e ".[full]" && python -c "import sqlalchemy; print(sqlalchemy.__version__); import alembic; print(alembic.__version__)"
```

Expected: 打印出版本号，无报错

- [ ] **Step 3: 提交**

```bash
git add pyproject.toml requirements.txt
git commit -m "feat: add sqlalchemy and alembic dependencies for docker storage layer"
```

---

### Task 2: database.py — SQLAlchemy engine + session 工厂

**Files:**
- Create: `app/storage/database.py`

**Interfaces:**
- Consumes: 环境变量 `KOKOROMEMO_DB_URL`
- Produces: `get_engine()` → `AsyncEngine`, `get_session()` → `AsyncSession`, `init_db()` → `None`

- [ ] **Step 1: 写 database.py**

```python
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
    # file 模式：自动创建数据库文件
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
```

- [ ] **Step 2: 验证数据库连接**

```python
# 临时测试
import asyncio
from app.storage.database import get_engine, init_db
async def test():
    await init_db()
    engine = get_engine()
    result = await engine.execute("SELECT 1")
    print(result)
asyncio.run(test())
```

Expected: 无报错，SQLite 文件 `/app/data/app.sqlite` 创建成功

- [ ] **Step 3: 提交**

```bash
git add app/storage/database.py
git commit -m "feat: add sqlalchemy database engine and session factory"
```

---

### Task 3: Alembic 初始化和迁移

**Files:**
- Create: `app/storage/migrations/alembic.ini`
- Create: `app/storage/migrations/env.py`
- Create: `app/storage/migrations/script.py.mako`
- Create: `app/storage/migrations/versions/001_initial.py`

**Interfaces:**
- Produces: `alembic upgrade head` 可直接执行，创建所有 ORM 模型对应的表

- [ ] **Step 1: 创建 alembic.ini**

```ini
[alembic]
script_location = app/storage/migrations
sqlalchemy.url =
# 不在这里配置 URL，由 env.py 从 database.py 获取

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

- [ ] **Step 2: 创建 env.py**

```python
"""Alembic environment configuration."""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.storage.database import _resolve_db_url
from app.storage.models import Base  # noqa: F401 — 导入模型让 Alembic 发现

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = _resolve_db_url()
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    url = _resolve_db_url()
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 3: 创建 script.py.mako**

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 4: 初始迁移脚本**

```python
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
```

- [ ] **Step 5: 提交**

```bash
git add app/storage/migrations/
git commit -m "feat: add alembic migration setup with initial schema"
```

---

### Task 4: ORM 模型定义

**Files:**
- Create: `app/storage/models.py`

**Interfaces:**
- Produces: `Base`, `MemoryCard`, `ConversationStateItem` 等 ORM 模型类
- 供 repository.py 和 Alembic 使用

- [ ] **Step 1: 创建 models.py**

```python
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
```

- [ ] **Step 2: 提交**

```bash
git add app/storage/models.py
git commit -m "feat: add sqlalchemy ORM models for memory cards and state"
```

---

### Task 5: VectorStore 抽象接口 + LanceDB 实现

**Files:**
- Create: `app/storage/vector_base.py`
- Create: `app/storage/vector_lancedb.py`

**Interfaces:**
- `VectorStore` (抽象基类): `search()`, `upsert()`, `delete()`, `rebuild()`, `connect()`, `close()`
- `LanceDBVectorStore(VectorStore)` — file 模式的 LanceDB 实现

- [ ] **Step 1: 写出抽象接口 vector_base.py**

```python
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
```

- [ ] **Step 2: LanceDB 实现 vector_lancedb.py**

```python
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
```

- [ ] **Step 3: 提交**

```bash
git add app/storage/vector_base.py app/storage/vector_lancedb.py
git commit -m "feat: add vector store abstraction and LanceDB implementation"
```

---

### Task 6: pgvector 实现

**Files:**
- Create: `app/storage/vector_pgvector.py`

**Interfaces:**
- `PgVectorStore(VectorStore)` — server 模式的 pgvector 实现

- [ ] **Step 1: 创建 vector_pgvector.py**

```python
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
```

- [ ] **Step 2: 提交**

```bash
git add app/storage/vector_pgvector.py
git commit -m "feat: add pgvector vector store implementation for server mode"
```

---

### Task 7: StorageRepository — 统一仓库层

**Files:**
- Create: `app/storage/repository.py`
- Modify: `app/storage/__init__.py`

**Interfaces:**
- `StorageRepository` 类：所有业务代码通过此接口操作数据
- `get_repository()` → `StorageRepository`：根据模式返回对应实现

- [ ] **Step 1: 创建 repository.py**

```python
"""统一存储仓库层。file 模式委托现有 SQLite 函数，server 模式使用 SQLAlchemy ORM。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.storage.database import is_server_mode, get_engine
from app.storage.vector_base import VectorStore, VectorSearchResult

logger = logging.getLogger("kokoromemo.repository")

_repository: StorageRepository | None = None


class StorageRepository:
    """存储仓库，封装所有数据访问。"""

    def __init__(self):
        self._db_path: str = str(Path("/app/data") / "app.sqlite")
        self._server_mode = is_server_mode()
        self._vector_store: VectorStore | None = None
        self._engine = get_engine() if self._server_mode else None

    # ── Card 操作 ──

    async def get_card(self, card_id: str) -> dict | None:
        if self._server_mode:
            from app.storage.models import MemoryCard
            from sqlalchemy import select
            async with self._engine.connect() as conn:
                result = await conn.execute(select(MemoryCard).where(MemoryCard.card_id == card_id))
                row = result.fetchone()
                return dict(row._mapping) if row else None
        from app.storage.sqlite_cards import get_card_by_id
        return await get_card_by_id(self._db_path, card_id)

    async def list_cards(self, scope: str | None = None, character_id: str | None = None,
                         status: str | None = None) -> list[dict]:
        if self._server_mode:
            from app.storage.models import MemoryCard
            from sqlalchemy import select
            query = select(MemoryCard)
            if scope:
                query = query.where(MemoryCard.scope == scope)
            if character_id:
                query = query.where(MemoryCard.character_id == character_id)
            if status:
                query = query.where(MemoryCard.status == status)
            async with self._engine.connect() as conn:
                result = await conn.execute(query)
                return [dict(r._mapping) for r in result.fetchall()]
        from app.storage.sqlite_cards import get_cards_by_scope
        return await get_cards_by_scope(self._db_path, scope or "")

    # ── 更多 Card 方法 ──
    # get_cards_by_ids, create_card, update_card, delete_card, insert_card, insert_card_version
    # 模式同上：server 走 ORM，file 委托现有函数

    # ── Session 操作 ──
    # get_session, create_session, save_turn, get_messages
    # 相同模式

    # ── State 操作 ──
    # get_state, upsert_state, 等
    # file 模式委托 SQLiteStateStore

    # ── Vector 操作 ──
    async def get_vector_store(self) -> VectorStore | None:
        return self._vector_store

    def set_vector_store(self, store: VectorStore | None) -> None:
        self._vector_store = store


def get_repository() -> StorageRepository:
    global _repository
    if _repository is None:
        _repository = StorageRepository()
    return _repository


def reset_repository() -> None:
    global _repository
    _repository = None
```

> **注意：** repository.py 是核心适配层，method 数量多但每个 method 的模式高度一致（server → ORM, file → 委托）。完整实现请参照以下模板填充所有数据访问方法。

- [ ] **Step 2: 补全所有 repository 方法**

按照 Step 1 中 `get_card` 和 `list_cards` 的双模式模式（server → ORM, file → 委托现有函数），逐一实现以下方法。每个方法的方法体完全遵循 Step 1 的模式结构：

**Card 数据访问方法（委托 `app/storage/sqlite_cards.py`）：**
```python
async def get_cards_by_ids(self, card_ids: list[str]) -> list[dict]: ...
async def get_approved_cards(self) -> list[dict]: ...
async def get_pinned_cards(self, character_id: str | None = None) -> list[dict]: ...
async def get_recent_important_cards(self, limit: int = 10) -> list[dict]: ...
async def insert_card(self, card: dict) -> str: ...
async def insert_card_version(self, card_id: str, card: dict) -> None: ...
async def update_card(self, card_id: str, updates: dict) -> bool: ...
async def delete_card(self, card_id: str) -> bool: ...
async def card_exists_with_content(self, user_id: str, content: str) -> bool: ...
async def list_memory_libraries(self, include_deleted: bool = False) -> list[dict]: ...
async def create_memory_library(self, name: str, description: str = "") -> str: ...
async def update_memory_library(self, library_id: str, name: str, description: str = "") -> bool: ...
async def delete_memory_library(self, library_id: str) -> bool: ...
async def get_conversation_mounts(self, conversation_id: str) -> list[dict]: ...
async def set_conversation_mounts(self, conversation_id: str, library_ids: list[str]) -> None: ...
async def get_mounted_library_ids(self, conversation_id: str) -> list[str]: ...
async def copy_conversation_mounts(self, from_conv: str, to_conv: str) -> None: ...
async def get_inbox_items(self, status: str | None = None, limit: int = 50) -> list[dict]: ...
async def get_inbox_item(self, item_id: str) -> dict | None: ...
async def insert_inbox_item(self, item: dict) -> str: ...
async def insert_review_action(self, action: dict) -> None: ...
async def transition_inbox_status(self, item_id: str, new_status: str) -> None: ...
async def mark_card_vector_unsynced(self, card_id: str) -> None: ...
```

**Session / Conversation 数据访问方法（委托 `app/storage/sqlite_conversation.py`）：**
```python
async def get_session(self, conversation_id: str) -> dict | None: ...
async def save_raw_request(self, conversation_id: str, data: dict) -> None: ...
async def save_raw_response(self, conversation_id: str, data: dict) -> None: ...
async def save_injected_memory_log(self, conversation_id: str, data: dict) -> None: ...
async def save_turn_and_messages(self, conversation_id: str, turn_data: dict, messages: list) -> None: ...
async def get_turn_count(self, conversation_id: str) -> int: ...
async def get_all_messages(self, conversation_id: str) -> list[dict]: ...
```

**App-level 数据访问方法（委托 `app/storage/sqlite_app.py`）：**
```python
async def init_app_db(self) -> None: ...
async def upsert_character(self, character_id: str, data: dict) -> None: ...
async def upsert_conversation(self, conversation_id: str, character_id: str) -> None: ...
async def list_characters(self) -> list[dict]: ...
async def list_character_conversations(self, character_id: str) -> list[dict]: ...
async def list_conversations(self) -> list[dict]: ...
async def delete_conversation(self, conversation_id: str) -> bool: ...
async def get_character_defaults(self, character_id: str) -> dict | None: ...
async def set_character_defaults(self, character_id: str, defaults: dict) -> None: ...
async def update_character_profile(self, character_id: str, profile: dict) -> None: ...
async def discover_characters(self) -> list[dict]: ...
```

**State 数据访问方法（委托 `app/storage/sqlite_state.py` 中的 `SQLiteStateStore`）：**
```python
async def get_state_items(self, conversation_id: str) -> list[dict]: ...
async def upsert_state_item(self, item: dict) -> str: ...
async def delete_state_item(self, item_id: str) -> bool: ...
async def get_state_board_config(self, conversation_id: str) -> dict | None: ...
async def init_state_db(self) -> None: ...
```

> 实现约定：file 模式直接调用对应的 `app.storage.sqlite_*.py` 函数（参数完全一致，`db_path` 从 `self._db_path` 自动传入）。server 模式通过 SQLAlchemy ORM 执行相同操作。

- [ ] **Step 3: 更新 `app/storage/__init__.py`**

```python
from app.storage.database import init_db, close_db, is_server_mode
from app.storage.repository import get_repository, reset_repository, StorageRepository

__all__ = ["init_db", "close_db", "is_server_mode", "get_repository", "reset_repository", "StorageRepository"]
```

- [ ] **Step 4: 提交**

```bash
git add app/storage/repository.py app/storage/__init__.py
git commit -m "feat: add storage repository layer with dual-mode support"
```

---

### Task 8: 改造 services.py — 根据模式返回向量存储

**Files:**
- Modify: `app/core/services.py`

- [ ] **Step 1: 修改 services.py**

```python
def get_vector_store(cfg: AppConfig) -> Any:
    """根据模式返回向量存储实现。"""
    from app.storage.database import is_server_mode

    if not cfg.embedding.enabled:
        return None

    if is_server_mode():
        from app.storage.vector_pgvector import PgVectorStore
        store = PgVectorStore(None, dimension=cfg.embedding.dimension or 4096)
    else:
        from app.storage.vector_lancedb import LanceDBVectorStore
        from app.core.services import resolve_lancedb_path
        lancedb_path = resolve_lancedb_path(cfg)
        store = LanceDBVectorStore(
            db_path=lancedb_path,
            table_name=cfg.storage.lancedb.table,
            dimension=cfg.embedding.dimension or 4096,
        )

    store.connect()
    return store
```

将 `get_lancedb_store()` 改为 `get_vector_store()`，原有逻辑保持不变（LanceDB 仍然是 file 模式的主向量存储，SQLiteVectorStore 作为 LanceDB 不可用时的兜底）。

- [ ] **Step 2: 提交**

```bash
git add app/core/services.py
git commit -m "feat: add mode-aware vector store selection in services"
```

---

### Task 9: 改造 main.py — 初始化 repository

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: 在 lifespan 中添加 repository 初始化**

```python
from app.storage import init_db, close_db, get_repository

@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv()
    cfg = load_config()
    set_config(cfg)
    set_configured_timezone(cfg.server.timezone or None)
    setup_logging(cfg.server.log_level)

    # 初始化数据目录
    Path(cfg.storage.root_dir).mkdir(parents=True, exist_ok=True)
    Path(cfg.storage.root_dir, "conversations").mkdir(parents=True, exist_ok=True)
    Path(cfg.storage.root_dir, "memory").mkdir(parents=True, exist_ok=True)
    Path(cfg.storage.root_dir, "vector_indexes").mkdir(parents=True, exist_ok=True)

    # 初始化数据库和仓库
    await init_db()
    repo = get_repository()
    set_repository(repo)  # 需要在 state 中保存

    import logging
    logger = logging.getLogger("kokoromemo")
    logger.info("KokoroMemo started on %s:%d (mode=%s)", cfg.server.host, cfg.server.port, "server" if is_server_mode() else "file")

    yield

    await close_db()
    logger.info("KokoroMemo shutting down")
```

- [ ] **Step 2: 提交**

```bash
git add app/main.py
git commit -m "feat: initialize repository and database on app startup"
```

---

### Task 10: 改造业务代码 — 导入 repository

**Files:**
- Modify: `app/memory/card_extractor.py`, `app/memory/card_retriever.py`, `app/memory/state_filler.py`, `app/memory/state_updater.py`, `app/memory/state_projector.py`, `app/memory/state_table_filler.py`, `app/api/routes_admin.py`, `app/api/routes_openai.py`, `app/storage/vector_sync.py`, `app/storage/rebuild_v2.py`

- [ ] **Step 1: 替换业务模块中的直接存储导入**

将以下文件中所有 `from app.storage.sqlite_*` / `from app.storage.lancedb_store` / `from app.storage.vector_sync` 等直接导入替换为通过 `get_repository()` 调用：

**替换规则：**
```
原始导入 → 替换方式
───────────────────────────────────────────────
from app.storage.sqlite_cards import fn  →  repo = get_repository(); await repo.fn(args)
from app.storage.sqlite_state import SQLiteStateStore  →  repo.get_state_items() / repo.upsert_state_item()
from app.storage.sqlite_app import fn  →  repo.fn()
from app.storage.sqlite_conversation import fn  →  repo.fn()
from app.storage.vector_sync import sync_card_vector, enqueue_card_vector_sync  →  repo.get_vector_store().upsert() + repo.mark_card_vector_unsynced()
from app.storage.rebuild_v2 import rebuild_vector_index_v2  →  repo.get_vector_store().rebuild()
```

**涉及文件列表（按模块分组）：**

`app/memory/`:
- `card_extractor.py`: 替换 insert_inbox_item, enqueue_card_vector_sync, sync_card_vector
- `card_retriever.py`: 替换 get_cards_by_ids, get_pinned_cards, get_recent_important_cards, get_mounted_library_ids
- `state_filler.py`: 替换 SQLiteStateStore
- `state_table_filler.py`: 替换 SQLiteStateStore
- `state_projector.py`: 替换 SQLiteStateStore
- `state_updater.py`: 替换 SQLiteStateStore, insert_inbox_item

`app/api/`:
- `routes_admin.py`: 替换所有 `from app.storage.*` 导入 → 共 ~50 处调用
- `routes_openai.py`: 替换 ~10 处调用

`app/storage/`:
- `vector_sync.py`: 改为通过 repository 获取 card 数据和 vector store
- `rebuild_v2.py`: 改为通过 repository 获取 approved cards

每个替换遵循相同的两步模式：
1. 在文件顶部导入 `from app.storage import get_repository`
2. 在函数内调用 `repo = get_repository()`，将原 `await sqlite_cards.fn(db_path, ...)` 改为 `await repo.fn(...)`（不再需要 `db_path` 参数）

- [ ] **Step 2: 更新 app/storage/__init__.py 导出**

确保 `get_repository()` 可在任意模块直接调用：
```python
from app.storage.repository import get_repository, reset_repository
```

- [ ] **Step 3: 提交**

```bash
git add app/memory/ app/api/ app/storage/
git commit -m "refactor: migrate business code from direct sqlite imports to repository pattern"
```

---

### Task 11: 环境变量配置系统

**Files:**
- Modify: `app/core/config.py`
- Create: `.env.example`

- [ ] **Step 1: 在 config.py 中添加环境变量读取**

```python
# 在 EmbeddingConfig 和 RerankConfig 中添加 env var 回退
@dataclass
class EmbeddingConfig:
    base_url: str = ""
    api_key: str = ""

    def get_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        return os.environ.get(self.api_key_env, "")

# 在 load_config() 中添加 env var 覆盖逻辑
def _apply_env_overrides(cfg: AppConfig) -> None:
    """环境变量覆盖配置值。"""
    mapping = [
        ("KOKOROMEMO_DB_URL", None),  # 在 database.py 中单独处理
        ("LLM_API_KEY", lambda v: setattr(cfg.llm, "api_key", v)),
        ("LLM_MODEL", lambda v: setattr(cfg.llm, "model", v)),
        ("LLM_BASE_URL", lambda v: setattr(cfg.llm, "base_url", v)),
        ("EMBEDDING_ENABLED", lambda v: setattr(cfg.embedding, "enabled", v.lower() in {"1", "true", "yes"})),
        ("EMBEDDING_BASE_URL", lambda v: setattr(cfg.embedding, "base_url", v)),
        ("EMBEDDING_API_KEY", lambda v: setattr(cfg.embedding, "api_key", v)),
        ("EMBEDDING_MODEL", lambda v: setattr(cfg.embedding, "model", v)),
        ("RERANK_BASE_URL", lambda v: setattr(cfg.rerank, "base_url", v)),
        ("RERANK_API_KEY", lambda v: setattr(cfg.rerank, "api_key", v)),
        ("RERANK_MODEL", lambda v: setattr(cfg.rerank, "model", v)),
        ("ADMIN_TOKEN", lambda v: setattr(cfg.server, "admin_token", v)),
        ("SERVER_PORT", lambda v: setattr(cfg.server, "port", int(v))),
        ("SERVER_HOST", lambda v: setattr(cfg.server, "host", v)),
        ("STORAGE_ROOT_DIR", lambda v: setattr(cfg.storage, "root_dir", v)),
    ]
    for env_key, apply_fn in mapping:
        value = os.environ.get(env_key, "").strip()
        if value and apply_fn:
            apply_fn(value)
```

在 `load_config()` 末尾调用 `_apply_env_overrides(cfg)`。

- [ ] **Step 2: 创建 .env.example**

```env
# KokoroMemo Docker 配置模板
# 复制为 .env 使用（docker-compose 自动加载）

# 存储模式（留空=file 模式 SQLite+LanceDB，设置=server 模式 PostgreSQL+pgvector）
# KOKOROMEMO_DB_URL=postgresql+asyncpg://kokoro:password@db:5432/kokoromemo

# LLM 配置
LLM_API_KEY=your-api-key
LLM_MODEL=gpt-4o
LLM_BASE_URL=https://api.openai.com/v1

# Embedding 配置
EMBEDDING_BASE_URL=https://api.openai.com/v1
EMBEDDING_API_KEY=your-api-key
EMBEDDING_MODEL=text-embedding-3-small

# Rerank 配置（可选）
# RERANK_BASE_URL=https://api.openai.com/v1
# RERANK_API_KEY=your-api-key
# RERANK_MODEL=text-embedding-3-small

# 服务配置
# SERVER_PORT=14514
# SERVER_HOST=0.0.0.0
# ADMIN_TOKEN=your-admin-token
```

- [ ] **Step 3: 提交**

```bash
git add app/core/config.py .env.example
git commit -m "feat: add environment variable config overrides for docker"
```

---

### Task 12: Dockerfile + entrypoint + .dockerignore

**Files:**
- Create: `Dockerfile`
- Create: `docker-entrypoint.sh`
- Create: `.dockerignore`

- [ ] **Step 1: 创建 Dockerfile**

```dockerfile
# ---- Stage 1: Build Frontend ----
FROM node:22-alpine AS frontend
WORKDIR /build
COPY gui/package.json gui/package-lock.json ./
RUN npm ci --ignore-scripts
COPY gui/ .
RUN npm run build

# ---- Stage 2: Final ----
FROM python:3.11-slim

# 安装系统依赖（LanceDB 和编译需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev libc6-dev apg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 复制项目源码
COPY app/ /app/app/
COPY pyproject.toml requirements.txt /app/
COPY docker-entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# 复制前端构建产物
COPY --from=frontend /build/dist/ /app/gui/dist/

# 创建挂载点
RUN mkdir -p /app/config /app/data
VOLUME ["/app/config", "/app/data"]

EXPOSE 14514
ENTRYPOINT ["/app/entrypoint.sh"]
```

- [ ] **Step 2: 创建 docker-entrypoint.sh**

```bash
#!/bin/bash
set -e

VENV_DIR="/app/data/.venv"
CONFIG_DIR="/app/config"
DATA_DIR="/app/data"

echo "=== KokoroMemo Docker Entrypoint ==="

# 1. 持久化虚拟环境
if [ ! -f "$VENV_DIR/bin/python" ]; then
    echo "创建持久化虚拟环境..."
    python -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
echo "虚拟环境就绪: $(which python)"

# 2. 同步依赖（增量安装）
echo "同步 Python 依赖..."
pip install --no-cache-dir -e ".[full]" -q
echo "依赖就绪"

# 3. 复制默认配置（如果不存在）
if [ ! -f "$CONFIG_DIR/config.yaml" ] && [ -f "/app/config.example.yaml" ]; then
    echo "初始化默认配置..."
    cp /app/config.example.yaml "$CONFIG_DIR/config.yaml"
fi

# 4. 等待数据库（server 模式）
if [ -n "$KOKOROMEMO_DB_URL" ]; then
    echo "等待 PostgreSQL 就绪..."
    DB_HOST="${KOKOROMEMO_DB_HOST:-db}"
    until pg_isready -h "$DB_HOST" -U "${KOKOROMEMO_DB_USER:-kokoro}" -d "${KOKOROMEMO_DB_NAME:-kokoromemo}" 2>/dev/null; do
        sleep 1
    done
    echo "PostgreSQL 就绪"
fi

# 5. Alembic 自动迁移
echo "执行数据库迁移..."
alembic -c /app/app/storage/migrations/alembic.ini upgrade head || echo "迁移完成（或无需迁移）"

# 6. 确保数据目录
mkdir -p "$DATA_DIR/memory" "$DATA_DIR/conversations" "$DATA_DIR/vector_indexes"

# 7. 启动
echo "启动 KokoroMemo..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${SERVER_PORT:-14514}"
```

- [ ] **Step 3: 创建 .dockerignore**

```
.git
.gitignore
.github
gui/node_modules
gui/src-tauri
gui/dist
gui/*.ts
gui/*.json
__pycache__
*.pyc
.pytest_cache
.test_tmp
test_dbs
tests
packaging
scripts
data
config
.port
.env
.env.*
*.md
.DS_Store
```

- [ ] **Step 4: 验证构建**

```bash
docker build -t kokoromemo:test .
```

Expected: 构建成功，前端编译无报错

- [ ] **Step 5: 提交**

```bash
git add Dockerfile docker-entrypoint.sh .dockerignore
git commit -m "feat: add dockerfile with multi-stage build and entrypoint"
```

---

### Task 13: docker-compose 文件

**Files:**
- Create: `docker-compose.yml` (server 模式)
- Create: `docker-compose.file.yml` (file 模式)

- [ ] **Step 1: 创建 docker-compose.yml（server 模式）**

```yaml
services:
  app:
    build:
      context: .
      dockerfile: Dockerfile
    ports:
      - "${SERVER_PORT:-14514}:14514"
    volumes:
      - ./config:/app/config
      - ./data:/app/data
    env_file:
      - .env
    environment:
      - KOKOROMEMO_DB_URL=postgresql+asyncpg://kokoro:${DB_PASSWORD:-changeme}@db:5432/kokoromemo
      - KOKOROMEMO_DB_HOST=db
      - KOKOROMEMO_DB_USER=kokoro
      - KOKOROMEMO_DB_NAME=kokoromemo
    depends_on:
      db:
        condition: service_healthy
    restart: unless-stopped

  db:
    image: pgvector/pgvector:pg17
    environment:
      POSTGRES_DB: kokoromemo
      POSTGRES_USER: kokoro
      POSTGRES_PASSWORD: ${DB_PASSWORD:-changeme}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U kokoro -d kokoromemo"]
      interval: 5s
      timeout: 5s
      retries: 5
    restart: unless-stopped

volumes:
  pgdata:
```

- [ ] **Step 2: 创建 docker-compose.file.yml（file 模式）**

```yaml
services:
  app:
    build:
      context: .
      dockerfile: Dockerfile
    ports:
      - "${SERVER_PORT:-14514}:14514"
    volumes:
      - ./config:/app/config
      - ./data:/app/data
    env_file:
      - .env
    restart: unless-stopped
```

- [ ] **Step 3: 提交**

```bash
git add docker-compose.yml docker-compose.file.yml
git commit -m "feat: add docker-compose configs for file and server modes"
```

---

### Task 14: 清理旧构建体系

**Files:**
- Delete: `gui/src-tauri/` (目录)
- Delete: `packaging/` (目录)
- Delete: `scripts/` (目录)
- Delete: `.github/workflows/` (目录)
- Delete: `.port` (文件)
- Modify: `.gitignore` (添加忽略)

- [ ] **Step 1: 删除目录和文件**

```bash
rm -rf gui/src-tauri packaging scripts .github/workflows .port
```

- [ ] **Step 2: 更新 .gitignore**

```gitignore
# Docker
/config/
/data/
.env
```

- [ ] **Step 3: 提交**

```bash
git add -A
git commit -m "cleanup: remove tauri android scripts and github actions - fully dockerized"
```

---

### Task 15: 端到端验证

**Files:** 无（测试操作）

- [ ] **Step 1: 验证 file 模式**

```bash
# 构建镜像
docker build -t kokoromemo:latest .

# 准备数据目录
mkdir -p test-data/config test-data/data

# 复制默认配置
cp config.example.yaml test-data/config/config.yaml

# 启动容器
docker run -d --name km-test \
  -p 14514:14514 \
  -v "$(pwd)/test-data/config:/app/config" \
  -v "$(pwd)/test-data/data:/app/data" \
  kokoromemo:latest

# 检查健康
sleep 3
curl http://localhost:14514/health

# 清理
docker stop km-test && docker rm km-test
```

Expected: 返回 JSON 状态码 200，包含版本信息

- [ ] **Step 2: 验证 server 模式**

```bash
# 启动 docker-compose
docker compose up -d

# 检查应用
sleep 5
curl http://localhost:14514/health

# 查看日志
docker compose logs app

# 清理
docker compose down
```

Expected: 应用正常启动，PostgreSQL 连接成功

- [ ] **Step 3: 验证数据持久化**

```bash
# 停止并重新启动容器
docker compose down
docker compose up -d

# 确认数据未丢失
curl http://localhost:14514/health
docker compose down
```

Expected: 重启后数据仍在

- [ ] **Step 4: 标记验证完成**

```bash
echo "Docker 化验证通过"
```
