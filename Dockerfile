FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY . .
RUN pip install --no-cache-dir .

ENV MIPLAY_HOST=
ENV WEB_PORT=8300
ENV PYTHONUNBUFFERED=1

EXPOSE 8300

CMD ["miplay", "serve", "--conf-path", "/app/conf"]
