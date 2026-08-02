FROM python:3.11-slim

WORKDIR /app

# Install system deps required for yt-dlp/ffmpeg
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg ca-certificates curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Ensure yt-dlp is installed (in addition to requirements)
RUN pip install --no-cache-dir yt-dlp

COPY . .

# EXPOSE is informational only for Docker; Render provides $PORT at runtime
EXPOSE 8000

# Use $PORT provided by Render — expand it with sh -c
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1"]
