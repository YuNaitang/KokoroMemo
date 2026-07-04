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
