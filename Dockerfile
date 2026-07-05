# ---- Stage 1: Build Frontend ----
FROM node:22-alpine AS frontend
WORKDIR /build
COPY gui/package.json gui/package-lock.json ./
RUN npm config set registry https://registry.npmmirror.com && \
    npm ci --ignore-scripts
COPY gui/ .
RUN npm run build

# ---- Stage 2: Final ----
FROM python:3.11-slim

# 使用国内 Debian 镜像源加速
RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev libc6-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 复制项目源码和依赖清单
COPY app/ /app/app/
COPY pyproject.toml requirements.txt /app/

# 使用国内 PyPI 镜像源加速
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
    pip install --no-cache-dir -e ".[full]"

COPY docker-entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# 复制前端构建产物
COPY --from=frontend /build/dist/ /app/gui/dist/

# 创建挂载点
RUN mkdir -p /app/config /app/data
VOLUME ["/app/config", "/app/data"]

EXPOSE 14514
ENTRYPOINT ["/app/entrypoint.sh"]
