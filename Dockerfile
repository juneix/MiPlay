# 使用原生 Python 轻量镜像
FROM python:3.12-slim

# 设置工作目录
WORKDIR /app

# 安装必要的系统运行时 (ffmpeg)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# 1. 先复制依赖配置文件
COPY pyproject.toml ./

# 2. 安装依赖 (利用 Docker 缓存层)
# 注意：这里安装会尝试查找源码，如果只想安装依赖可以配合 --no-deps 或者使用临时目录
# 为了极简，我们直接复制整个目录并安装
COPY . .
RUN pip install --no-cache-dir .

# 默认环境变量配置
ENV DLNA_PORT=8200
ENV WEB_PORT=8300
ENV PYTHONUNBUFFERED=1

# 声明端口
EXPOSE 8200 8300

# 启动应用
CMD ["python", "miair.py"]