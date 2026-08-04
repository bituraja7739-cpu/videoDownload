"""
routes/download.py — Download endpoints.

GET  /api/get-links       — PRIMARY: Extract CDN URLs → browser downloads directly (zero server bandwidth)
GET  /api/stream          — Fallback: FFmpeg merge pipeline for unsupported formats
POST /api/start-download  — Background job for server-side download.
GET  /api/fetch/{job_id}  — Retrieve a completed server-side download.
"""

import asyncio
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
    extract_direct_links,
    get_stream_info,
    stream_via_ffmpeg,
    get_format_tier,
)

router = APIRouter(tags=["Download"])


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/get-links  ← PRIMARY ENDPOINT (Cloud-Safe, Zero Server Bandwidth)
#
# Architecture:
#   1. yt-dlp extracts direct CDN video/audio URLs from Facebook/Instagram
#   2. Returns { video_url, audio_url, title, ext } JSON to the frontend
#   3. Browser downloads directly from the CDN — Render server transfers 0 bytes
#   4. Completely bypasses Render IP blocks since the actual download comes from
#      the USER's IP address, not the server's IP!
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/get-links")
async def get_download_links(
    url:       str           = Query(..., description="Video page URL"),
    format_id: str           = Query("best_auto", description="Quality tier"),
):
    """
    Extract direct CDN download URLs for a video and return as JSON.
    The browser then downloads directly from the CDN — zero server bandwidth used.
    This completely avoids IP blocks on cloud servers like Render.
    """
    if not url:
        raise HTTPException(status_code=400, detail="URL is required.")

    try:
        links = await asyncio.to_thread(extract_direct_links, url.strip(), format_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to extract links: {exc}")

    return links


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/proxy-download  ← AUTO FILE DOWNLOAD PROXY (No New Tabs)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/proxy-download")
async def proxy_download(
    url:   str = Query(..., description="Direct CDN URL"),
    title: str = Query("video", description="Desired filename"),
    ext:   str = Query("mp4", description="File extension"),
):
    """
    Proxies direct CDN media URL with Content-Disposition: attachment header.
    Forces browser to trigger AUTO FILE DOWNLOAD directly to disk instead of opening in a new tab.
    """
    if not url:
        raise HTTPException(status_code=400, detail="URL is required.")

    import requests

    raw_title = title.strip() or "video"
    clean_title = re.sub(r'[\\/*?:"<>|]', '_', raw_title)[:80].strip() or "video"
    ascii_name  = f"{clean_title}.{ext}"
    utf8_name   = quote(f"{raw_title}.{ext}")
    content_disposition = f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{utf8_name}'

    try:
        req = requests.get(
            url,
            stream=True,
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch CDN stream: {exc}")

    if req.status_code >= 400:
        raise HTTPException(status_code=req.status_code, detail="CDN stream returned error.")

    media_type = req.headers.get("Content-Type", "video/mp4")
    content_length = req.headers.get("Content-Length")

    headers = {
        "Content-Disposition": content_disposition,
        "Content-Type": media_type,
        "X-Accel-Buffering": "no",
        "Cache-Control": "no-cache",
    }
    if content_length:
        headers["Content-Length"] = content_length

    def iter_chunks():
        try:
            for chunk in req.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk
        finally:
            req.close()

    return StreamingResponse(iter_chunks(), media_type=media_type, headers=headers)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/stream   ← FALLBACK (for FFmpeg merging when needed)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/stream")
async def stream_download(
    url:          str           = Query(..., description="Video page URL"),
    format_id:    str           = Query("best_auto", description="Quality tier"),
    cookies_file: Optional[str] = Query(None),
):
    """
    Stream merged video+audio via FFmpeg pipeline (fallback for unsupported CDN formats).
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

    ascii_title = re.sub(r'[^\x00-\x7F]+', '', raw_title)
    safe_ascii  = re.sub(r'[\\/*?:"<>|]', "_", ascii_title)[:80].strip()
    ascii_name  = f"{safe_ascii}.{ext}" if safe_ascii else f"vidsnap_download.{ext}"
    utf8_name   = quote(f"{raw_title}.{ext}")
    content_disposition = f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{utf8_name}'
    media_type = "audio/mpeg" if is_audio else "video/mp4"

    if info.get("mode") in ("single", "audio") and info.get("single_url"):
        return RedirectResponse(url=info["single_url"])

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
