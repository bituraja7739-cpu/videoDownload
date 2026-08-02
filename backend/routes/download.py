"""
routes/download.py — Download endpoints.

GET  /api/stream          — Two-phase: extract URLs, then FFmpeg→browser pipeline.
POST /api/start-download  — Background job for server-side download.
GET  /api/fetch/{job_id}  — Retrieve a completed server-side download.
"""

import re
import uuid
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel

from ..downloader import (
    cleanup_job,
    download_to_temp,
    get_stream_info,
    stream_via_ffmpeg,
    get_format_tier,
)

router = APIRouter(tags=["Download"])


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/stream   ←  PRIMARY ENDPOINT
#
# Flow (two async phases):
#   Phase 1 — yt-dlp extracts direct video+audio HTTP URLs (no download).
#   Phase 2 — FFmpeg fetches those URLs, merges them, pipes fragmented MP4/MP3
#             directly to the browser as a StreamingResponse.
#
# The browser sees the response headers immediately and opens its native
# Download Manager, showing live MB/speed/ETA from the OS download tracker.
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/stream")
async def stream_download(
    url:          str           = Query(..., description="Video page URL"),
    format_id:    str           = Query("best_auto", description="Quality tier"),
    cookies_file: Optional[str] = Query(None),
):
    """
    Instantly stream video (merged video+audio) or audio (MP3) to the browser.

    If single pre-muxed media URL is available (Facebook, Instagram, combined MP4s):
      Redirects directly to CDN media URL -> Chrome Download Manager opens with 0.9 / 834 MB!
    If separate video+audio streams (YouTube 1080p/4K):
      FFmpeg streams merged MP4 directly to browser with live progress!
    """
    if not url:
        raise HTTPException(status_code=400, detail="URL is required.")

    try:
        info = await get_stream_info(url.strip(), format_id, cookies_file)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to extract stream info: {exc}")

    is_audio  = info.get("is_audio_only", False)
    ext       = "mp3" if is_audio else "mp4"
    raw_title = info.get("title", "download")

    # Clean non-ASCII emojis/special symbols for ASCII header fallback
    ascii_title = re.sub(r'[^\x00-\x7F]+', '', raw_title)
    safe_ascii  = re.sub(r'[\\/*?:"<>|]', "_", ascii_title)[:80].strip()
    ascii_name  = f"{safe_ascii}.{ext}" if safe_ascii else f"vidsnap_download.{ext}"

    # UTF-8 encoded filename for modern browsers (RFC 5987)
    utf8_name   = quote(f"{raw_title}.{ext}")
    content_disposition = f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{utf8_name}'

    media_type = "audio/mpeg" if is_audio else "video/mp4"

    # If single pre-muxed stream URL (Facebook, Instagram, combined MP4), redirect directly
    # Chrome Download Manager gets Content-Length from CDN and displays "0.9 / 834 MB • 1 hour left"!
    if info.get("mode") in ("single", "audio") and info.get("single_url"):
        return RedirectResponse(url=info["single_url"])

    # For separate streams (YouTube 1080p/4K), stream merged MP4 via FFmpeg chunked encoding
    headers = {
        "Content-Disposition": content_disposition,
        "X-Accel-Buffering":   "no",
        "Cache-Control":       "no-cache",
    }

    return StreamingResponse(
        stream_via_ffmpeg(info),
        media_type=media_type,
        headers=headers,
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/start-download  (background job fallback)
# GET  /api/fetch/{job_id}
# ─────────────────────────────────────────────────────────────────────────────

class DownloadRequest(BaseModel):
    url:          str
    format_id:    str
    cookies_file: Optional[str] = None


@router.post("/start-download")
async def start_download(req: DownloadRequest):
    """
    Begin a background server-side download job.
    Returns job_id immediately. Track via GET /api/progress/{job_id}.
    Retrieve file via GET /api/fetch/{job_id} when status='ready'.
    """
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL is required.")

    job_id = str(uuid.uuid4())

    import asyncio
    asyncio.create_task(
        download_to_temp(url, req.format_id, job_id, req.cookies_file)
    )

    return {"success": True, "job_id": job_id}


@router.get("/fetch/{job_id}")
async def fetch_completed_file(job_id: str, background_tasks: BackgroundTasks):
    """
    Retrieve a completed download by job_id.
    Only call once status from /api/progress/{job_id} is 'ready'.
    """
    from ..downloader import progress_store, TEMP_DIR

    state = progress_store.get(job_id)
    if not state:
        raise HTTPException(status_code=404, detail="Job not found or already cleaned up.")
    if state.get("status") == "error":
        raise HTTPException(status_code=422, detail=state.get("message", "Download failed."))
    if state.get("status") != "ready":
        raise HTTPException(status_code=409, detail="Download not yet complete.")

    filename  = state.get("filename", "download.mp4")
    file_path = TEMP_DIR / job_id / filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Output file not found on server.")

    background_tasks.add_task(cleanup_job, job_id)

    ext        = file_path.suffix.lower()
    media_type = "audio/mpeg" if ext == ".mp3" else "video/mp4"

    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/download-report")
async def download_report_pdf():
    """Download the VidSnap Technical Project Report PDF."""
    from pathlib import Path
    pdf_path = Path(__file__).resolve().parent.parent.parent / "VidSnap_Technical_Project_Report.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF report file not found on server.")
    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename="VidSnap_Technical_Project_Report.pdf",
        headers={"Content-Disposition": 'attachment; filename="VidSnap_Technical_Project_Report.pdf"'}
    )
