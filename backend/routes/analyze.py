"""
routes/analyze.py — POST /api/analyze
Extracts metadata and available formats from a given media URL.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, HttpUrl

from ..downloader import fetch_info

router = APIRouter(tags=["Analyze"])


class AnalyzeRequest(BaseModel):
    url: str
    cookies_file: Optional[str] = None


@router.post("/analyze")
async def analyze_url(req: AnalyzeRequest):
    """
    Extract video metadata and return available quality formats.

    Body:
        url (str): The media URL (YouTube / Instagram / Facebook).
        cookies_file (str, optional): Path to a Netscape cookies.txt file.

    Returns:
        { success: bool, data: MediaInfo }
    """
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL is required.")

    try:
        info = await fetch_info(url, req.cookies_file)
        return {"success": True, "data": info}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Internal error: {str(exc)}")
