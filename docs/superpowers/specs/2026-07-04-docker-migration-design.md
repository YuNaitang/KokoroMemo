# KokoroMemo Docker 化设计文档

> **日期：** 2026-07-04
> **状态：** 已批准
> **目标：** 将 KokoroMemo 从多构建（Tauri/Android/SSH）全面迁移到 Docker 单部署方案，并重构存储层以支持 SQLite ↔ PostgreSQL 双模式切换。

---

## 1. 目标与范围

### 1.1 目标
- 完整 Docker 化：所有部署方式统一为 Docker 容器
- 双存储模式：`file`（默认，SQLite + LanceDB）和 `server`（PostgreSQL + pgvector），通过环境变量切换
- 删除旧构建体系：Tauri 桌面端、Android 打包、安装脚本、GitHub Actions
- Python 虚拟环境持久化到数据卷，重启免重复下载依赖
- 支持 `docker run`（单容器）和 `docker-compose`（含 PostgreSQL）

### 1.2 非目标
- 不引入 WebSocket 之外的实时通知
- 不改造核心记忆系统（memory/）的业务逻辑
- 不添加 Kubernetes 支持（依然可用，但非设计目标）

---

## 2. 整体架构

```
┌───────────────────────────────────────┐
│          Docker 容器                   │
│                                       │
│  ┌───────────────────────────────┐    │
│  │  Uvicorn (FastAPI)            │    │
│  │  ┌─────────────────────────┐  │    │
│  │  │  API Routes             │  │    │
│  │  │  /v1  /admin  /ws       │  │    │
│  │  └──────────┬──────────────┘  │    │
│  │             │                  │    │
│  │  ┌──────────▼──────────────┐  │    │
│  │  │  Memory System          │  │    │
│  │  │  (不改造)                │  │    │
│  │  └──────────┬──────────────┘  │    │
│  │             │                  │    │
│  │  ┌──────────▼──────────────┐  │    │
│  │  │  Storage Layer          │  │    │
│  │  │  SQLAlchemy (关系)       │  │    │
│  │  │  + Vector 抽象接口       │  │    │
│  │  └─────────────────────────┘  │    │
│  └───────────────────────────────┘    │
└───────────────────┬───────────────────┘
                    │ 模式切换
     ┌──────────────┴──────────────┐
     │ file (默认)                 │ server (docker-compose)
     │                             │
     │ SQLAlchemy + SQLite         │ SQLAlchemy + PostgreSQL
     │ LanceDB (向量)              │ pgvector (向量)
```

### 2.1 模式切换

由 `KOKOROMEMO_DB_URL` 环境变量控制：
- **未设置** → `file` 模式，SQLite + LanceDB（兼容当前所有数据）
- **设置** → `server` 模式，SQLAlchemy + asyncpg + pgvector

---

## 3. 目录结构

```
/path/to/kokoromemo/         ← 挂载根目录，对应容器内 /app
├── config/
│   └── config.yaml          ← 配置文件（只读挂载）
└── data/
    ├── .venv/               ← Python 虚拟环境（持久化，免重复下载）
    ├── app.sqlite           ← file 模式关系数据库
    ├── vector_indexes/      ← file 模式 LanceDB 向量存储
    ├── memory/
    └── conversations/

# 容器内路径
/app/config/                 → config 挂载点
/app/data/                   → data 挂载点
/app/app/                    → Python 源码
/app/gui/dist/               → 构建好的前端静态文件
```

---

## 4. 存储层重构

### 4.1 新增文件

```
app/storage/
├── database.py          ← SQLAlchemy async engine + session 工厂
├── models.py            ← ORM 模型 (Card, Session, State, ...)
├── migrations/          ← Alembic 迁移目录
├── repository.py        ← 仓库层，统一 CRUD 接口，业务代码只通过此层操作数据
├── vector_base.py       ← 向量存储抽象接口（search / upsert / delete / rebuild）
├── vector_lancedb.py    ← file 模式：LanceDB 实现
└── vector_pgvector.py   ← server 模式：pgvector 实现
```

### 4.2 保留文件（file 模式实现不动）

- `sqlite_app.py` — 保留，通过 repository 包装
- `sqlite_cards.py` — 保留，通过 repository 包装
- `sqlite_conversation.py` — 保留
- `sqlite_state.py` — 保留
- `sqlite_vector_store.py` — 保留（SQLite 兜底向量搜索）
- `lancedb_store.py` — 保留（LanceDB 实现）
- `vector_sync.py` — 改造为通过 repository 操作
- `rebuild_v2.py` — 改造为通过 repository 操作

### 4.3 repository.py 接口设计

```python
class StorageRepository:
    # Card 操作
    async def get_card(self, card_id: str) -> Card | None
    async def list_cards(self, scope: str, character_id: str | None = None) -> list[Card]
    async def create_card(self, card: CardCreate) -> Card
    async def update_card(self, card_id: str, update: CardUpdate) -> Card | None
    async def delete_card(self, card_id: str) -> bool

    # Session 操作
    async def get_session(self, session_id: str) -> Session | None
    async def create_session(self, session: SessionCreate) -> Session

    # State 操作
    async def get_state(self, session_id: str) -> State | None
    async def upsert_state(self, state: StateUpdate) -> State

    # Vector 操作（委托给 vector_base 实现）
    async def search_vectors(self, query: list[float], top_k: int) -> list[VectorResult]
    async def upsert_vector(self, card_id: str, vector: list[float], text: str) -> None
```

业务代码（`memory/` 目录下）**只依赖** `repository.py`，不直接触及底层实现。

### 4.4 切换逻辑

```python
# database.py
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

db_url = os.getenv("KOKOROMEMO_DB_URL")  # None → file 模式
if db_url:
    engine = create_async_engine(db_url)
    # server 模式
else:
    # file 模式：使用现有的 SQLite / LanceDB 文件路径
    sqlite_path = "/app/data/app.sqlite"
    engine = create_async_engine(f"sqlite+aiosqlite:///{sqlite_path}")
```

---

## 5. Dockerfile

```dockerfile
# ---- Stage 1: Build Frontend ----
FROM node:22-alpine AS frontend
WORKDIR /build
COPY gui/package.json gui/package-lock.json ./
RUN npm ci
COPY gui/ .
RUN npm run build

# ---- Stage 2: Final ----
FROM python:3.11-slim

# 安装系统依赖（LanceDB 需要底层库）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 复制源码
COPY app/ /app/app/
COPY pyproject.toml requirements.txt /app/
COPY docker-entrypoint.sh /app/entrypoint.sh

# 复制前端产物
COPY --from=frontend /build/dist/ /app/gui/dist/

EXPOSE 14514
ENTRYPOINT ["/app/entrypoint.sh"]
```

## 6. Entrypoint 脚本

```bash
#!/bin/bash
set -e

VENV_DIR="/app/data/.venv"

# 1. 持久化虚拟环境
if [ ! -f "$VENV_DIR/bin/python" ]; then
    python -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"

# 2. 同步依赖
pip install --no-cache-dir -e ".[full]" -q

# 3. 等待数据库（server 模式）
if [ -n "$KOKOROMEMO_DB_URL" ]; then
    until pg_isready -h "${KOKOROMEMO_DB_HOST:-localhost}" -U "${KOKOROMEMO_DB_USER:-kokoro}" -d "${KOKOROMEMO_DB_NAME:-kokoromemo}" 2>/dev/null; do
        sleep 1
    done
fi

# 4. Alembic 自动迁移
alembic upgrade head

# 5. 确保数据目录存在
mkdir -p /app/data/memory /app/data/conversations

# 6. 启动
exec uvicorn app.main:app --host 0.0.0.0 --port 14514
```

---

## 7. docker-compose

### 7.1 file 模式（纯应用）

```yaml
# docker-compose.file.yml
services:
  app:
    build: .
    ports:
      - "14514:14514"
    volumes:
      - ./config:/app/config
      - ./data:/app/data
    environment:
      - LLM_API_KEY=${LLM_API_KEY}
```

### 7.2 server 模式（含 PostgreSQL + pgvector）

```yaml
# docker-compose.yml
services:
  app:
    build: .
    ports:
      - "14514:14514"
    volumes:
      - ./config:/app/config
      - ./data:/app/data
    environment:
      - KOKOROMEMO_DB_URL=postgresql+asyncpg://kokoro:${DB_PASSWORD}@db:5432/kokoromemo
      - LLM_API_KEY=${LLM_API_KEY}
    depends_on:
      db:
        condition: service_healthy

  db:
    image: pgvector/pgvector:pg17
    environment:
      POSTGRES_DB: kokoromemo
      POSTGRES_USER: kokoro
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U kokoro -d kokoromemo"]
      interval: 5s
      retries: 5

volumes:
  pgdata:
```

---

## 8. 配置系统

### 8.1 环境变量优先级

`KOKOROMEMO_DB_URL` 不再从 `config.yaml` 读取——它是**容器层面的配置**，只在环境变量中设置。

其他配置项的优先顺序（从高到低）：
1. 环境变量（`LLM_API_KEY`、`EMBEDDING_BASE_URL` 等）
2. `config.yaml` 中的值
3. `AppConfig` dataclass 默认值

### 8.2 计划的环境变量映射

```
KOKOROMEMO_DB_URL       → storage 模式切换
KOKOROMEMO_DB_HOST       → db 主机名（healthcheck）
LLM_API_KEY              → llm.api_key
LLM_MODEL                → llm.model
LLM_BASE_URL             → llm.base_url
EMBEDDING_ENABLED        → embedding.enabled
EMBEDDING_BASE_URL       → embedding.base_url
EMBEDDING_API_KEY        → embedding.api_key
EMBEDDING_MODEL          → embedding.model
RERANK_BASE_URL          → rerank.base_url
RERANK_API_KEY           → rerank.api_key
RERANK_MODEL             → rerank.model
ADMIN_TOKEN              → server.admin_token
```

---

## 9. 实施顺序

| 阶段 | 内容 | 涉及文件 |
|---|---|---|
| **1. 存储层重构** | SQLAlchemy 模型 + database.py + Alembic + repository.py + 向量接口 | `app/storage/` 新增文件 |
| **2. 业务代码适配** | 将 `memory/` / `api/` 中对 SQLite 的直接调用改为 repository 调用 | 多个业务模块 |
| **3. Docker 化** | Dockerfile + entrypoint.sh + .dockerignore | 根目录 |
| **4. docker-compose** | 两种模式的 compose 文件 | 根目录 |
| **5. 配置系统** | 环境变量读取 + 优先级逻辑 | `app/core/config.py` |
| **6. 清理** | 删除 Tauri / Android / 脚本 / Actions | 多目录 |
| **7. 验证** | `docker run` + `docker compose up` 跑通 | 端到端 |

---

## 10. 清理清单

删除以下目录和文件：

```
gui/src-tauri/               → Tauri 桌面端
gui/tauri.conf.json          → Tauri 配置
packaging/                   → Android 打包
scripts/                     → 安装脚本
.github/workflows/           → GitHub Actions
.port                        → 端口文件（无用）
```

保留：
- `gui/` — 前端源码，用于 Docker build 阶段
- `tests/` — 测试，继续保持可用
- `config.example.yaml` — 作为使用参考

---

## 11. 关键决策

### 11.1 向量迁移策略
- **file 模式**：保持 LanceDB 不变，现有数据完全兼容
- **server 模式**：启动后首次使用时自动重建 pgvector 索引（从 SQLAlchemy 已批准卡片重新嵌入），不迁移旧的 LanceDB 文件。用户需要在切换模式后触发一次索引重建

### 11.2 Entrypoint 位置
- `docker-entrypoint.sh` 放在项目根目录，不属于 `scripts/`（后者将被清理）

### 11.3 环境变量映射
- 详细映射表在实施阶段（writing-plans）展开，核心原则：环境变量名 → `_` 分隔的大写形式，与 config.py 字段名一一对应
