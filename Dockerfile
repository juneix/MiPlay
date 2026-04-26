FROM python:3.12.13-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /uvx /bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && mkdir -p /app/conf \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1
ENV MIPLAY_HOST=
ENV WEB_PORT=8300
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml ./
RUN uv pip install --system -r pyproject.toml

COPY miplay/ ./miplay/
COPY miplay.py ./
COPY config-example.json ./
COPY README.md ./

EXPOSE 8300

CMD ["python", "-m", "miplay.cli", "serve", "--conf-path", "/app/conf"]