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
