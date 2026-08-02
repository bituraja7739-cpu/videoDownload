"""
routes/progress.py — GET /api/progress/{job_id}
Server-Sent Events (SSE) endpoint for real-time download progress.
"""

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ..downloader import progress_store

router = APIRouter(tags=["Progress"])


@router.get("/progress/{job_id}")
async def get_progress(job_id: str):
    """
    SSE stream for download progress of a specific job.
    Sends events every 400 ms until status is 'ready' or 'error'.

    Event data shape:
        { status, percent, percent_str, speed, eta, downloaded_bytes?, total_bytes? }
    """

    async def event_stream():
        while True:
            state = progress_store.get(job_id, {"status": "pending", "percent": "0", "percent_str": "0%"})
            payload = json.dumps(state)
            yield f"data: {payload}\n\n"

            terminal = state.get("status") in ("ready", "error")
            if terminal:
                break

            await asyncio.sleep(0.4)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
