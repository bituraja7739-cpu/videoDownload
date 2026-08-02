# Dockerfile for VidSnap — FastAPI + yt-dlp + FFmpeg

FROM python:3.11-slim

# Prevent Python from writing .pyc files & enable unbuffered stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

# Install FFmpeg and system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
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

# Run Gunicorn with Uvicorn worker (binds dynamically to $PORT for Render/Railway/Koyeb)
CMD ["sh", "-c", "gunicorn -w 2 -k uvicorn.workers.UvicornWorker backend.main:app --bind 0.0.0.0:${PORT:-8000}"]
