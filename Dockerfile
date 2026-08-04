# Dockerfile for VidSnap — FastAPI + yt-dlp + FFmpeg

FROM python:3.11-slim

# Prevent Python from writing .pyc files & enable unbuffered stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

# Install FFmpeg, Node.js, and system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port
EXPOSE 8000

# Run Gunicorn optimized for 512MB memory limit on Render Free Tier:
# -w 1 (single worker process keeps RAM < 200MB)
# --threads 4 (handles concurrent requests safely)
# --max-requests 200 (automatically recycles worker memory to prevent leaks)
CMD ["sh", "-c", "gunicorn -w 1 --threads 4 --max-requests 200 --max-requests-jitter 20 -k uvicorn.workers.UvicornWorker backend.main:app --bind 0.0.0.0:${PORT:-8000}"]
