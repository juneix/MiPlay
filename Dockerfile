# Stage 1: Build 阶段 - 负责编译和安装依赖
FROM python:3.12.13-slim AS builder

WORKDIR /app

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 复制依赖定义文件
COPY pyproject.toml uv.lock ./

# 安装依赖到本地目录，不保留缓存，不安装 uv 自身到最终镜像
RUN uv pip install --no-cache --target /app/deps -r pyproject.toml

# Stage 2: Final 阶段 - 生产运行镜像
FROM python:3.12.13-slim

WORKDIR /app

# 1. 引入必要工具 (FFmpeg - 使用静态编译版本，体积小且无动态库冲突)
COPY --from=mwader/static-ffmpeg:6.1 /ffmpeg /ffprobe /usr/local/bin/

# 2. 基础配置
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MIPLAY_HOST= \
    WEB_PORT=8300 \
    PYTHONPATH="/app/deps"

# 3. 从 builder 阶段复制已安装的 Python 依赖
COPY --from=builder /app/deps /app/deps

# 4. 复制项目源码
COPY miplay/ ./miplay/
COPY miplay.py ./

# 5. 初始化配置目录
RUN mkdir -p /app/conf

EXPOSE 8300

# 6. 运行命令
CMD ["python", "-m", "miplay.cli", "serve", "--conf-path", "/app/conf"]