#!/bin/bash
set -e

VENV_DIR="/app/data/.venv"
CONFIG_DIR="/app/config"
DATA_DIR="/app/data"

echo "=== KokoroMemo Docker Entrypoint ==="

# 1. 持久化虚拟环境（系统 site-packages 已在 Docker build 时预装）
if [ ! -f "$VENV_DIR/bin/python" ]; then
    echo "创建持久化虚拟环境（继承系统包）..."
    python -m venv --system-site-packages --without-pip "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"

# 2. 确保项目本身在 venv 中可用（用 .pth 文件跳过 pip）
SITE_PACKAGES="$VENV_DIR/lib/python$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')/site-packages"
if [ ! -f "$SITE_PACKAGES/kokoromemo.pth" ]; then
    echo "注册项目路径到虚拟环境..."
    echo "/app" > "$SITE_PACKAGES/kokoromemo.pth"
fi
echo "环境就绪"

# 3. 复制默认配置（如果不存在）
if [ ! -f "$CONFIG_DIR/config.yaml" ] && [ -f "/app/config.example.yaml" ]; then
    echo "初始化默认配置..."
    cp /app/config.example.yaml "$CONFIG_DIR/config.yaml"
fi

# 4. file 模式：使用旧的 init_*_db 机制（Alembic 仅 server 模式使用）
#    server 模式：等待 PostgreSQL + Alembic 迁移
if [ -n "$KOKOROMEMO_DB_URL" ]; then
    echo "等待 PostgreSQL 就绪..."
    DB_HOST="${KOKOROMEMO_DB_HOST:-db}"
    until pg_isready -h "$DB_HOST" -U "${KOKOROMEMO_DB_USER:-kokoro}" -d "${KOKOROMEMO_DB_NAME:-kokoromemo}" 2>/dev/null; do
        sleep 1
    done
    echo "PostgreSQL 就绪"
    echo "执行数据库迁移..."
    alembic -c /app/app/storage/migrations/alembic.ini upgrade head || echo "迁移完成（或无需迁移）"
fi

# 5. 确保数据目录
mkdir -p "$DATA_DIR/memory" "$DATA_DIR/conversations" "$DATA_DIR/vector_indexes"

# 6. 启动
echo "启动 KokoroMemo..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${SERVER_PORT:-14514}"
