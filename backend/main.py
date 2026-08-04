"""
main.py — VidSnap FastAPI application.

Startup:
    uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

The frontend (index.html / style.css / app.js) is served as static files
from the ../frontend directory, so no separate web server is needed.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .downloader import TEMP_DIR, check_ffmpeg, get_ytdlp_version
from .routes.analyze import router as analyze_router
from .routes.download import router as download_router
from .routes.progress import router as progress_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vidsnap")

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown logic."""
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    ffmpeg_ok = check_ffmpeg()
    ytdlp_ver = get_ytdlp_version()

    from .downloader import check_ffmpeg, get_ytdlp_version
    logger.info("=" * 50)
    logger.info("  VidSnap started")
    logger.info(f"  yt-dlp version  : {ytdlp_ver}")
    logger.info(f"  FFmpeg found    : {'✓' if ffmpeg_ok else '✗  (MP3/merged downloads may fail)'}")
    logger.info(f"  Temp dir        : {TEMP_DIR}")
    logger.info("=" * 50)

    yield

    ffmpeg_ok = check_ffmpeg()
    ytdlp_ver = get_ytdlp_version()

    logger.info("=" * 50)
    logger.info("  VidSnap started")
    logger.info(f"  yt-dlp version : {ytdlp_ver}")
    logger.info(f"  FFmpeg found   : {'✓' if ffmpeg_ok else '✗  (MP3/merged downloads may fail)'}")
    logger.info(f"  Temp dir       : {TEMP_DIR}")
    logger.info("=" * 50)

    yield

    # Shutdown — optionally clean up all temp files
    # shutil.rmtree(TEMP_DIR, ignore_errors=True)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="VidSnap",
    description="Instagram & Facebook Downloader — Reels, Stories, Photos & Videos",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# CORS — allow all origins for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------

app.include_router(analyze_router, prefix="/api")
app.include_router(download_router, prefix="/api")
app.include_router(progress_router, prefix="/api")


@app.get("/api/health", tags=["Health"])
async def health():
    """Health check — confirms FFmpeg availability and yt-dlp version."""
    return {
        "status": "ok",
        "ffmpeg_available": check_ffmpeg(),
        "yt_dlp_version": get_ytdlp_version(),
    }


# ---------------------------------------------------------------------------
# Frontend Static Serving
# ---------------------------------------------------------------------------

if FRONTEND_DIR.exists():
    # Serve CSS/JS assets under /static
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_index():
        return FileResponse(str(FRONTEND_DIR / "index.html"))

    @app.get("/{full_path:path}", include_in_schema=False)
    async def catch_all(full_path: str):
        """SPA catch-all: serve index.html for unknown paths."""
        target = FRONTEND_DIR / full_path
        if target.exists() and target.is_file():
            return FileResponse(str(target))
        return FileResponse(str(FRONTEND_DIR / "index.html"))
