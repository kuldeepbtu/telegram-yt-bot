FROM python:3.12-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /install /usr/local
COPY bot_session.py .
RUN mkdir -p /tmp/ytbot_downloads
ENV DOWNLOAD_DIR=/tmp/ytbot_downloads MAX_CONCURRENT=3
CMD ["python", "bot_session.py"]
