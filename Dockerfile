# 极致精简：原生 Python 镜像
FROM python:3.12-slim

WORKDIR /app

# 安装必要的运行时依赖 (ffmpeg 仍然保留，用于音频处理的库依赖)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖定义
COPY pyproject.toml ./

# 使用原生 pip 安装依赖
RUN pip install --no-cache-dir .

# 复制源码
COPY . .

# 环境变量配置
ENV DLNA_PORT=8200
ENV WEB_PORT=8300
# Plex 模拟播放器端口
ENV PLEX_PORT=32500
ENV PYTHONUNBUFFERED=1

# 暴露端口 (Host 网络下仅作文档参考)
EXPOSE 8200 8300 32500

# 启动
CMD ["python", "miair.py"]