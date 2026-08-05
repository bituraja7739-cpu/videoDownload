"""
downloader.py — Core yt-dlp wrapper for VidSnap.

Handles:
- Multi-platform metadata extraction (Instagram, Facebook)
- Full quality-tier selection
- Async/sync downloads to temp storage
- Live streaming via subprocess stdout pipe
- In-memory progress tracking (SSE-ready)
- Comprehensive error classification
"""

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import AsyncGenerator, Dict, Optional

import yt_dlp

# ---------------------------------------------------------------------------
# Constants & Storage
# ---------------------------------------------------------------------------

TEMP_DIR = Path(tempfile.gettempdir()) / "vidsnap_downloads"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# In-memory job progress store  { job_id: { status, percent, speed, eta, ... } }
progress_store: Dict[str, dict] = {}

# Platform detection patterns
PLATFORM_PATTERNS = {
    "instagram": re.compile(r"instagram\.com", re.IGNORECASE),
    "facebook": re.compile(r"(facebook\.com|fb\.com|fb\.watch)", re.IGNORECASE),
}

# Quality tier definitions — ordered best-to-worst (prioritizing H.264 / AVC1 for 100% Windows/iOS player compatibility)
QUALITY_TIERS = [
    {
        "format_id": "best_auto",
        "label": "Best (Auto)",
        "selector": "bestvideo[vcodec^=avc1]+bestaudio/bestvideo[vcodec^=h264]+bestaudio/bestvideo+bestaudio/b/best",
        "is_audio": False,
    },
    {
        "format_id": "4k",
        "label": "4K (2160p)",
        "selector": "bestvideo[height<=2160][vcodec^=avc1]+bestaudio/bestvideo[height<=2160]+bestaudio/b/best",
        "is_audio": False,
    },
    {
        "format_id": "1080p",
        "label": "1080p Full HD",
        "selector": "bestvideo[height<=1080][vcodec^=avc1]+bestaudio/bestvideo[height<=1080]+bestaudio/b/best",
        "is_audio": False,
    },
    {
        "format_id": "720p",
        "label": "720p HD",
        "selector": "bestvideo[height<=720][vcodec^=avc1]+bestaudio/bestvideo[height<=720]+bestaudio/b/best",
        "is_audio": False,
    },
    {
        "format_id": "480p",
        "label": "480p",
        "selector": "bestvideo[height<=480][vcodec^=avc1]+bestaudio/bestvideo[height<=480]+bestaudio/b/best",
        "is_audio": False,
    },
    {
        "format_id": "360p",
        "label": "360p",
        "selector": "bestvideo[height<=360][vcodec^=avc1]+bestaudio/bestvideo[height<=360]+bestaudio/b/best",
        "is_audio": False,
    },
    {
        "format_id": "hd",
        "label": "HD Quality",
        "selector": "hd[vcodec^=avc1]/bestvideo[vcodec^=avc1]+bestaudio/b/best",
        "is_audio": False,
    },
    {
        "format_id": "sd",
        "label": "SD Quality",
        "selector": "sd[vcodec^=avc1]/bestvideo[vcodec^=avc1]+bestaudio/b/best",
        "is_audio": False,
    },
    {
        "format_id": "audio_best",
        "label": "Audio MP3 (Best Quality)",
        "selector": "bestaudio/best",
        "is_audio": True,
    },
    {
        "format_id": "audio_128k",
        "label": "Audio MP3 (128 kbps)",
        "selector": "bestaudio[abr<=128]/bestaudio",
        "is_audio": True,
    },
]

# Common User-Agent to avoid bot detection
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_url(url: str) -> str:
    """
    Normalize platform URLs for optimal extraction.
    Converts Facebook URLs to m.facebook.com for mobile HTML scraping.
    """
    url = url.strip()
    if ("facebook.com" in url.lower() or "fb.watch" in url.lower()) and "m.facebook.com" not in url.lower():
        url = re.sub(r"https?://(www\.|web\.|mbasic\.)?facebook\.com", "https://m.facebook.com", url, flags=re.IGNORECASE)
    return url


def detect_platform(url: str) -> str:
    """Return 'instagram', 'facebook', or 'unknown'."""
    for platform, pattern in PLATFORM_PATTERNS.items():
        if pattern.search(url):
            return platform
    return "unknown"


def detect_content_type(url: str, info: dict) -> str:
    """
    Detects if the content is a reel, story, photo, video, or carousel.
    Returns one of: 'reel', 'story', 'photo', 'video', 'carousel'.
    """
    url_lower = url.lower()
    
    # Instagram detection
    if "instagram.com" in url_lower:
        if "/reel/" in url_lower:
            return "reel"
        elif "/stories/" in url_lower:
            return "story"
        elif "/p/" in url_lower:
            if info.get('vcodec') and info.get('vcodec') != 'none':
                return "video"
            return "photo"

    # Facebook detection
    if "facebook.com" in url_lower or "fb.watch" in url_lower:
        if "/reel/" in url_lower:
            return "reel"
        elif "/stories/" in url_lower:
            return "story"
        elif "/photo" in url_lower:
            return "photo"
        elif "/watch/" in url_lower or "/videos/" in url_lower:
            return "video"
            
    # Fallbacks
    _type = info.get('_type')
    if _type in ['url', 'url_transparent', 'playlist', 'multi_video']:
        if _type == 'playlist':
            return 'carousel'
        return _type
        
    ext = info.get('ext', '')
    if ext in ('jpg', 'jpeg', 'png', 'webp'):
        return "photo"
        
    return "video"


def find_ffmpeg() -> Optional[str]:
    """
    Locate the FFmpeg binary.
    Checks system PATH first, then common Windows install locations.
    Returns the full path to the binary, or None if not found.
    """
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg

    common_paths = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
        os.path.expanduser(r"~\ffmpeg\bin\ffmpeg.exe"),
        # Linux / macOS common paths
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
    ]
    for path in common_paths:
        if os.path.isfile(path):
            return path
    return None


def check_ffmpeg() -> bool:
    """Return True if FFmpeg is available."""
    return find_ffmpeg() is not None


def get_ytdlp_version() -> str:
    """Return the installed yt-dlp version string."""
    try:
        return yt_dlp.version.__version__
    except Exception:
        return "unknown"


def get_format_tier(format_id: str) -> dict:
    """Look up a quality tier by format_id; falls back to best_auto."""
    for tier in QUALITY_TIERS:
        if tier["format_id"] == format_id:
            return tier
    return QUALITY_TIERS[0]


# ---------------------------------------------------------------------------
# Error Classification
# ---------------------------------------------------------------------------

def classify_error(exc: Exception) -> dict:
    """
    Map a raw yt-dlp (or other) exception into a structured error dict:
      { code: str, message: str }
    """
    msg = str(exc).lower()

    if "private" in msg:
        return {
            "code": "private",
            "message": "This video is private and cannot be accessed.",
        }
    if any(k in msg for k in ("login", "sign in", "authentication required", "not logged in")):
        return {
            "code": "login_required",
            "message": f"This video is private or age-restricted on the platform. [Raw Error: {str(exc)}]",
        }
    if any(k in msg for k in ("429", "rate limit", "too many requests")):
        return {
            "code": "rate_limit",
            "message": "You are being rate-limited by the platform. Please wait a minute and try again.",
        }
    if any(k in msg for k in ("ffmpeg", "ffprobe")):
        return {
            "code": "ffmpeg_missing",
            "message": (
                "FFmpeg is not installed or could not be found. "
                "Please install FFmpeg and add it to your system PATH."
            ),
        }
    if "requested format" in msg or ("format" in msg and "unavailable" in msg):
        return {
            "code": "format_unavailable",
            "message": "The requested quality is not available for this video. Try a lower resolution.",
        }
    if any(k in msg for k in ("unsupported url", "no suitable", "unable to extract", "no video formats")):
        return {
            "code": "unsupported_url",
            "message": (
                "This URL is not supported or could not be parsed. "
                "Only Instagram and Facebook links are supported."
            ),
        }
    if any(k in msg for k in ("copyright", "blocked", "not available in your country")):
        return {
            "code": "geo_blocked",
            "message": "This content is geo-restricted or blocked in your region.",
        }
    if any(k in msg for k in ("404", "not found", "does not exist")):
        return {
            "code": "not_found",
            "message": "Video not found. It may have been deleted or the URL is incorrect.",
        }

    return {
        "code": "unknown",
        "message": f"An unexpected error occurred: {str(exc)[:300]}",
    }


# ---------------------------------------------------------------------------
# Format Selector & yt-dlp Options Builder
# ---------------------------------------------------------------------------

def _build_format_selector(format_id: str) -> str:
    """
    Build format selector string for yt-dlp.
    Explicitly rejects AV1 (av01) streams and prioritizes H.264 (avc1) / VP9 + AAC (mp4a) audio.
    """
    if format_id in ("bestaudio/mp3", "audio_best", "audio_128k") or "audio" in format_id:
        return "bestaudio/best"
    # Fallback chain explicitly excluding AV1 and prioritizing H.264 video + AAC audio for Facebook/Instagram
    return f"{format_id}[vcodec!^=av01][vcodec!^=av1][vcodec^=avc1]+bestaudio[acodec^=mp4a]/bestvideo[vcodec!^=av01][vcodec!^=av1][vcodec^=avc1]+bestaudio/bestvideo[vcodec!^=av01][vcodec!^=av1]+bestaudio/best[ext=mp4]/best"


def build_ydl_opts(
    format_selector: str,
    output_template: str,
    is_audio_only: bool = False,
    job_id: Optional[str] = None,
    cookies_file: Optional[str] = None,
) -> dict:
    """
    Build a complete yt-dlp options dict with MP4 remux/recode fallback.
    """
    # Build format string if format_id passed
    fmt_str = _build_format_selector(format_selector) if not ("+" in format_selector or "/" in format_selector) else format_selector

    opts: dict = {
        "format": fmt_str,
        "outtmpl": output_template,
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
        "file_access_retries": 3,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "concurrent_fragment_downloads": 4,
    }

    # FFmpeg location
    ffmpeg_bin = find_ffmpeg()
    if ffmpeg_bin:
        opts["ffmpeg_location"] = os.path.dirname(ffmpeg_bin)

    # Always merge & force-recode video into universally compatible H.264 (libx264) + AAC MP4 with faststart moov atom
    if not is_audio_only:
        opts["merge_output_format"] = "mp4"
        opts["recode_video"] = "mp4"
        opts["postprocessor_args"] = {
            "ffmpeg": [
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
                "-c:a", "aac",
                "-b:a", "192k",
                "-movflags", "+faststart",
            ]
        }
        opts["postprocessors"] = [
            {
                "key": "FFmpegVideoConvertor",
                "preferedformat": "mp4",
            }
        ]

    # Audio extraction post-processor
    if is_audio_only:
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "0",  # VBR best
            }
        ]

    # Cookies
    if cookies_file and os.path.isfile(cookies_file):
        opts["cookiefile"] = cookies_file

    # Progress hook (for SSE tracking)
    if job_id:
        def _hook(d: dict) -> None:
            status = d.get("status")
            if status == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                percent_raw = (downloaded / total * 100) if total else 0
                progress_store[job_id] = {
                    "status": "downloading",
                    "percent": f"{percent_raw:.1f}",
                    "percent_str": d.get("_percent_str", "0%").strip(),
                    "speed": d.get("_speed_str", "N/A").strip(),
                    "eta": d.get("_eta_str", "N/A").strip(),
                    "downloaded_bytes": downloaded,
                    "total_bytes": total,
                }
            elif status == "finished":
                progress_store[job_id] = {
                    "status": "processing",
                    "percent": "100",
                    "percent_str": "100%",
                    "speed": "N/A",
                    "eta": "0s",
                }

        opts["progress_hooks"] = [_hook]

    return opts


# Alias for internal or external callers
_build_ydl_opts = build_ydl_opts


# ---------------------------------------------------------------------------
# Direct HTML Fallback Extractor for Facebook
# ---------------------------------------------------------------------------

def extract_facebook_direct_html(url: str) -> dict:
    """
    Direct HTML fallback extractor for Facebook Videos & Reels.
    Parses mobile HTML page source to extract HD/SD playable URLs when yt-dlp fails.
    """
    import requests

    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    clean_url = url.strip()
    m_url = re.sub(r"https?://(www\.|web\.|mbasic\.)?facebook\.com", "https://m.facebook.com", clean_url, flags=re.IGNORECASE)

    try:
        resp = requests.get(m_url, headers=headers, timeout=15)
        html = resp.text
    except Exception as exc:
        raise ValueError(f"Could not connect to Facebook server: {exc}")

    patterns = [
        r'"playable_url_quality_hd"\s*:\s*"(https?:[^\"]+)"',
        r'"playable_url"\s*:\s*"(https?:[^\"]+)"',
        r'"hd_src"\s*:\s*"(https?:[^\"]+)"',
        r'"sd_src"\s*:\s*"(https?:[^\"]+)"',
        r'"browser_native_hd_url"\s*:\s*"(https?:[^\"]+)"',
        r'"browser_native_sd_url"\s*:\s*"(https?:[^\"]+)"',
        r'videoUrl\s*:\s*"(https?:[^\"]+)"',
        r'src\s*:\s*"(https?:[^\"]+\.mp4[^\"]*)"',
    ]

    urls_found = []
    for pat in patterns:
        matches = re.findall(pat, html)
        for m in matches:
            decoded = m.replace("\\/", "/").replace("\\u0025", "%").replace("&amp;", "&")
            if decoded.startswith("http") and decoded not in urls_found:
                urls_found.append(decoded)

    title_match = (
        re.search(r'<meta property="og:title" content="(.*?)"', html) or
        re.search(r'<title>(.*?)</title>', html) or
        re.search(r'"title"\s*:\s*"(.*?)"', html)
    )
    thumb_match = (
        re.search(r'<meta property="og:image" content="(.*?)"', html) or
        re.search(r'"preferred_thumbnail"\s*:\s*{\s*"image"\s*:\s*{\s*"uri"\s*:\s*"(https?:[^\"]+)"', html)
    )

    raw_title = title_match.group(1) if title_match else "Facebook Video"
    title = re.sub(r'\s*\|\s*Facebook$', '', raw_title, flags=re.IGNORECASE).strip() or "Facebook Video"
    thumbnail = thumb_match.group(1).replace("\\/", "/") if thumb_match else ""

    if not urls_found:
        raise ValueError("Could not extract Facebook video stream. The video may be private, restricted, or deleted.")

    formats = []
    if len(urls_found) >= 2:
        formats.append({"format_id": "720p", "label": "720p HD", "ext": "mp4", "height": 720, "filesize": None, "url": urls_found[0], "vcodec": "h264", "acodec": "aac", "is_audio": False})
        formats.append({"format_id": "360p", "label": "360p SD", "ext": "mp4", "height": 360, "filesize": None, "url": urls_found[1], "vcodec": "h264", "acodec": "aac", "is_audio": False})
    else:
        formats.append({"format_id": "best_auto", "label": "Best Available Quality", "ext": "mp4", "height": 720, "filesize": None, "url": urls_found[0], "vcodec": "h264", "acodec": "aac", "is_audio": False})

    # Add MP3 audio format option
    formats.append({"format_id": "audio_best", "label": "Audio MP3 (Best Quality)", "ext": "mp3", "height": None, "filesize": None, "url": urls_found[0], "vcodec": "none", "acodec": "mp3", "is_audio": True})

    content_type = "reel" if "/reel/" in url.lower() else ("story" if "/stories/" in url.lower() else "video")

    return {
        "title": title,
        "thumbnail": thumbnail,
        "duration": 0,
        "platform": "facebook",
        "content_type": content_type,
        "webpage_url": url,
        "uploader": "Facebook Video",
        "view_count": None,
        "formats": formats,
        "url": formats[0]["url"]
    }


# ---------------------------------------------------------------------------
# Metadata / Info Extraction
# ---------------------------------------------------------------------------

def fetch_info_sync(url: str, cookies_file: Optional[str] = None) -> dict:
    """
    Synchronously extract video metadata with yt-dlp.
    Simple single-pass extraction. Fallbacks to direct HTML parser for Facebook.
    """
    url = normalize_url(url)

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
    }

    if cookies_file and os.path.isfile(cookies_file):
        opts["cookiefile"] = cookies_file

    info = None
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        if "facebook" in url.lower() or "fb.watch" in url.lower():
            try:
                return extract_facebook_direct_html(url)
            except Exception:
                pass
        err = classify_error(exc)
        raise ValueError(err["message"]) from exc

    if not info:
        if "facebook" in url.lower() or "fb.watch" in url.lower():
            try:
                return extract_facebook_direct_html(url)
            except Exception:
                pass
        raise ValueError("Could not extract video information from this URL.")

    # Build available video format list (deduplicated by height)
    # Map each detected height to the closest QUALITY_TIER format_id
    HEIGHT_TO_TIER = {
        2160: "4k",
        1440: "4k",
        1080: "1080p",
        720:  "720p",
        480:  "480p",
        360:  "360p",
        240:  "360p",
        144:  "360p",
    }

    raw_formats = info.get("formats", [])
    seen_heights: set = set()
    video_formats = []

    best_audio_size = 0
    for f in raw_formats:
        vcodec = (f.get("vcodec") or "none").lower()
        acodec = (f.get("acodec") or "none").lower()
        if vcodec in ("none", "") and acodec not in ("none", ""):
            sz = f.get("filesize") or f.get("filesize_approx") or 0
            if sz > best_audio_size:
                best_audio_size = sz

    for fmt in reversed(raw_formats):
        h = fmt.get("height")
        vcodec = fmt.get("vcodec", "none")
        acodec = (fmt.get("acodec") or "none").lower()

        if h and h > 0 and vcodec and vcodec not in ("none", None) and h not in seen_heights:
            seen_heights.add(h)
            label_suffix = ""
            if h >= 2160:   label_suffix = " · 4K"
            elif h >= 1080: label_suffix = " · Full HD"
            elif h >= 720:  label_suffix = " · HD"

            tier_id = "best_auto"
            for threshold in sorted(HEIGHT_TO_TIER.keys(), reverse=True):
                if h >= threshold:
                    tier_id = HEIGHT_TO_TIER[threshold]
                    break

            v_size = fmt.get("filesize") or fmt.get("filesize_approx") or 0
            total_size = (v_size + best_audio_size) if (acodec in ("none", "") and v_size > 0) else (v_size or None)

            video_formats.append(
                {
                    "format_id": tier_id,
                    "label": f"{h}p{label_suffix}",
                    "ext": "mp4",
                    "height": h,
                    "filesize": total_size,
                    "is_audio": False,
                }
            )

    audio_formats = [
        {
            "format_id": "audio_best",
            "label": "Audio MP3 · Best Quality",
            "ext": "mp3",
            "height": None,
            "filesize": best_audio_size or None,
            "is_audio": True,
        },
        {
            "format_id": "audio_128k",
            "label": "Audio MP3 · 128 kbps",
            "ext": "mp3",
            "height": None,
            "filesize": int(best_audio_size * 0.7) if best_audio_size else None,
            "is_audio": True,
        },
    ]

    if not video_formats:
        video_formats = [
            {"format_id": "best_auto", "label": "Best Available", "ext": "mp4", "height": None, "filesize": None, "is_audio": False},
            {"format_id": "720p", "label": "720p HD", "ext": "mp4", "height": 720, "filesize": None, "is_audio": False},
            {"format_id": "480p", "label": "480p", "ext": "mp4", "height": 480, "filesize": None, "is_audio": False},
            {"format_id": "360p", "label": "360p", "ext": "mp4", "height": 360, "filesize": None, "is_audio": False},
        ]

    content_type = detect_content_type(url, info)

    return {
        "title": info.get("title", "Unknown Title"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "platform": detect_platform(url),
        "content_type": content_type,
        "webpage_url": info.get("webpage_url", url),
        "uploader": info.get("uploader") or info.get("channel", "Unknown"),
        "view_count": info.get("view_count"),
        "formats": video_formats + audio_formats,
    }


async def fetch_info(url: str, cookies_file: Optional[str] = None) -> dict:
    """Async wrapper around fetch_info_sync."""
    return await asyncio.to_thread(fetch_info_sync, url, cookies_file)


# ---------------------------------------------------------------------------
# Server-Side Download (temp file)
# ---------------------------------------------------------------------------

def _download_sync(
    url: str,
    format_id: str,
    job_id: str,
    cookies_file: Optional[str] = None,
) -> Path:
    """
    Download media to a temporary directory on the server.
    Returns the Path to the downloaded file.
    Raises ValueError with a clean message on failure.
    """
    job_dir = TEMP_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    if not os.access(job_dir, os.W_OK):
        raise PermissionError(f"Cannot write to temp directory: {job_dir}")

    tier = get_format_tier(format_id)
    format_selector = tier["selector"]
    is_audio = tier["is_audio"]
    output_template = str(job_dir / "download.%(ext)s")

    opts = build_ydl_opts(
        format_selector=format_selector,
        output_template=output_template,
        is_audio_only=is_audio,
        job_id=job_id,
        cookies_file=cookies_file,
    )

    progress_store[job_id] = {"status": "starting", "percent": "0", "percent_str": "0%", "speed": "N/A", "eta": "N/A"}

    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            ydl.download([url])
        except Exception as exc:
            err = classify_error(exc)
            progress_store[job_id] = {"status": "error", **err}
            raise ValueError(err["message"]) from exc

    # Find the output file (pick largest if multiple)
    files = list(job_dir.glob("download.*"))
    if not files:
        raise FileNotFoundError("Download completed but no output file was found.")

    output_file = max(files, key=lambda f: f.stat().st_size)
    progress_store[job_id] = {"status": "ready", "percent": "100", "percent_str": "100%", "filename": output_file.name}
    return output_file


async def download_to_temp(
    url: str,
    format_id: str,
    job_id: str,
    cookies_file: Optional[str] = None,
) -> Path:
    """Async wrapper around _download_sync."""
    return await asyncio.to_thread(_download_sync, url, format_id, job_id, cookies_file)


# ---------------------------------------------------------------------------
# Client-Side Redirect — Extract CDN URLs and return as JSON
# ---------------------------------------------------------------------------

def extract_direct_links(url: str, format_id: str = "best_auto") -> dict:
    """
    Use yt-dlp to extract direct CDN streaming URLs for a video.
    Returns JSON with { video_url, audio_url, title, ext, needs_merge, filesize }.
    """
    url = normalize_url(url)
    tier = get_format_tier(format_id)
    is_audio = tier["is_audio"]

    opts = {
        "quiet":        True,
        "no_warnings":  True,
        "noplaylist":   True,
        "socket_timeout": 30,
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        if "facebook" in url.lower() or "fb.watch" in url.lower():
            try:
                info = extract_facebook_direct_html(url)
            except Exception:
                raise ValueError("Could not extract Facebook video. The video may be private, restricted, or deleted.")
        else:
            raise ValueError("Could not extract video. The video may be unavailable or restricted.")

    if not info:
        if "facebook" in url.lower() or "fb.watch" in url.lower():
            try:
                info = extract_facebook_direct_html(url)
            except Exception:
                raise ValueError("Could not extract Facebook video. The video may be private, restricted, or deleted.")
        else:
            raise ValueError("Could not extract video. The video may be unavailable or restricted.")

    title     = info.get("title", "video")
    thumbnail = info.get("thumbnail", "")
    duration  = info.get("duration", 0)
    platform  = detect_platform(url)

    # ── Audio-only request ────────────────────────────────────────────────────
    if is_audio:
        best_audio = None
        best_abr   = 0
        for fmt in (info.get("formats") or []):
            vcodec = (fmt.get("vcodec") or "none").lower()
            acodec = (fmt.get("acodec") or "none").lower()
            direct = fmt.get("url", "")
            if vcodec in ("none", "") and acodec not in ("none", "") and direct.startswith("http"):
                abr = fmt.get("abr") or 0
                if abr > best_abr:
                    best_abr   = abr
                    best_audio = fmt

        if best_audio:
            return {
                "title":       title,
                "thumbnail":   thumbnail,
                "duration":    duration,
                "platform":    platform,
                "ext":         "mp3",
                "needs_merge": False,
                "video_url":   best_audio["url"],
                "audio_url":   None,
                "filesize":    best_audio.get("filesize") or best_audio.get("filesize_approx"),
            }

        # Fallback for MP3 extraction via server stream
        return {
            "title":       title,
            "thumbnail":   thumbnail,
            "duration":    duration,
            "platform":    platform,
            "ext":         "mp3",
            "needs_merge": True,
            "video_url":   info.get("url", ""),
            "audio_url":   None,
            "filesize":    None,
        }

    # ── Video request ─────────────────────────────────────────────────────────
    height_caps = {
        "4k": 2160, "1080p": 1080, "720p": 720,
        "480p": 480, "360p": 360, "best_auto": 9999,
    }
    max_h = height_caps.get(format_id, 9999)

    best_muxed  = None  # Direct CDN stream (video+audio embedded)
    best_mux_h  = 0
    best_video  = None  # Separate DASH video stream
    best_v_h    = 0
    best_audio  = None  # Separate DASH audio stream
    best_abr    = 0

    formats_list = info.get("formats") or []

    for fmt in formats_list:
        vcodec = (fmt.get("vcodec") or "").lower()
        acodec = (fmt.get("acodec") or "").lower()
        fid    = str(fmt.get("format_id", "")).lower()
        h      = fmt.get("height") or 0
        direct = fmt.get("url", "")
        if not direct.startswith("http"):
            continue

        is_pure_video_only = (acodec == "none" and vcodec not in ("none", "")) and ("dash" in fid or "video" in fid)
        is_pure_audio_only = (vcodec in ("none", "") and acodec not in ("none", "")) or ("audio" in fid and vcodec in ("none", ""))

        # 1. Muxed format (has audio embedded or is standard Instagram/FB video stream)
        if not is_pure_video_only and not is_pure_audio_only:
            is_av1 = "av01" in vcodec or "av1" in vcodec or "vp09" in vcodec or "vp9" in vcodec
            # Score: prefer H.264 (avc1) over AV1/VP9 to ensure Windows Media Player & iOS compatibility
            score = (h if not is_av1 else h - 500)

            best_vcodec = (best_muxed.get("vcodec") or "").lower() if best_muxed else ""
            best_is_av1 = "av01" in best_vcodec or "av1" in best_vcodec or "vp09" in best_vcodec or "vp9" in best_vcodec
            best_score  = (best_mux_h if not best_is_av1 else best_mux_h - 500) if best_muxed else -9999

            if h <= max_h and score > best_score:
                best_mux_h = h
                best_muxed = fmt

        # 2. Pure DASH video stream (if DASH separate streams used)
        if is_pure_video_only and h <= max_h and h >= best_v_h:
            best_v_h   = h
            best_video = fmt

        # 3. Pure DASH audio stream
        if is_pure_audio_only:
            abr = fmt.get("abr") or 0
            if abr >= best_abr:
                best_abr   = abr
                best_audio = fmt

    # Case 1: Direct CDN stream (Instagram/Facebook native combined video+audio) -> MAXIMUM SPEED & FULL DURATION
    if best_muxed:
        return {
            "title":       title,
            "thumbnail":   thumbnail,
            "duration":    duration,
            "platform":    platform,
            "ext":         best_muxed.get("ext", "mp4"),
            "needs_merge": False,
            "video_url":   best_muxed["url"],
            "audio_url":   None,
            "filesize":    best_muxed.get("filesize") or best_muxed.get("filesize_approx"),
        }

    # Case 2: Top-level single media URL (e.g. Instagram Reels direct CDN URL) -> MAXIMUM SPEED & FULL DURATION
    if info.get("url") and info["url"].startswith("http"):
        return {
            "title":       title,
            "thumbnail":   thumbnail,
            "duration":    duration,
            "platform":    platform,
            "ext":         info.get("ext", "mp4"),
            "needs_merge": False,
            "video_url":   info["url"],
            "audio_url":   None,
            "filesize":    info.get("filesize") or info.get("filesize_approx"),
        }

    # Case 3: Separate DASH video + audio streams -> Needs FFmpeg merge
    if best_video:
        v_size = best_video.get("filesize") or best_video.get("filesize_approx") or 0
        a_size = (best_audio.get("filesize") or best_audio.get("filesize_approx") or 0) if best_audio else 0

        return {
            "title":       title,
            "thumbnail":   thumbnail,
            "duration":    duration,
            "platform":    platform,
            "ext":         best_video.get("ext", "mp4"),
            "needs_merge": best_audio is not None,
            "video_url":   best_video["url"],
            "audio_url":   best_audio["url"] if best_audio else None,
            "filesize":    (v_size + a_size) or None,
        }

    raise ValueError("No downloadable video stream found for this URL and quality.")


# ---------------------------------------------------------------------------
# Live Streaming — FFmpeg Pipeline
# ---------------------------------------------------------------------------

def _get_direct_urls_sync(
    url: str,
    format_id: str,
    cookies_file: Optional[str] = None,
) -> dict:
    """
    Extract direct HTTP stream URLs via yt-dlp (no download).
    Returns a stream_info dict consumed by stream_via_ffmpeg().
    """
    url      = normalize_url(url)
    tier     = get_format_tier(format_id)
    is_audio = tier["is_audio"]
    fmt_sel  = tier["selector"]

    opts = {
        "format":       fmt_sel,
        "quiet":        True,
        "no_warnings":  True,
        "noplaylist":   True,
        "socket_timeout": 30,
        "http_headers": {"User-Agent": _UA},
    }
    if cookies_file and os.path.isfile(cookies_file):
        opts["cookiefile"] = cookies_file

    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except Exception as exc:
            err = classify_error(exc)
            raise ValueError(err["message"]) from exc

    if not info:
        raise ValueError("Could not extract stream URLs.")

    title        = info.get("title", "download")
    global_hdrs  = info.get("http_headers", {"User-Agent": _UA})
    requested    = info.get("requested_formats") or []

    # ── Case 1: Two separate streams (video-only + audio-only) ───────────────
    if len(requested) >= 2:
        video_fmt = None
        audio_fmt = None

        for f in requested:
            vcodec    = (f.get("vcodec") or "none").lower()
            acodec    = (f.get("acodec") or "none").lower()
            has_video = vcodec not in ("none", "")
            has_audio = acodec not in ("none", "")

            if has_video and not has_audio:
                video_fmt = f            # pure video stream
            elif has_audio and not has_video:
                audio_fmt = f            # pure audio stream
            elif has_video and has_audio:
                # muxed stream in the list — treat as video
                if video_fmt is None:
                    video_fmt = f

        # Strict fallback: index order (yt-dlp always puts video first)
        if video_fmt is None:
            video_fmt = requested[0]
        if audio_fmt is None:
            audio_fmt = requested[1]

        v_hdrs = video_fmt.get("http_headers") or global_hdrs
        a_hdrs = audio_fmt.get("http_headers") or global_hdrs
        vcodec = (video_fmt.get("vcodec") or "").lower()

        v_size = video_fmt.get("filesize") or video_fmt.get("filesize_approx") or 0
        a_size = audio_fmt.get("filesize") or audio_fmt.get("filesize_approx") or 0
        total_filesize = (v_size + a_size) if (v_size and a_size) else None

        return {
            "mode":           "separate",
            "video_url":      video_fmt["url"],
            "audio_url":      audio_fmt["url"],
            "video_headers":  v_hdrs,
            "audio_headers":  a_hdrs,
            "vcodec":         vcodec,
            "single_url":     None,
            "filesize":       total_filesize,
            "is_audio_only":  False,
            "title":          title,
            "ext":            "mp4",
        }

    # ── Case 2: Single pre-muxed stream (combined video+audio) ──────────────
    fmt_info   = requested[0] if requested else info
    single_url = fmt_info.get("url") or info.get("url", "")
    s_hdrs     = fmt_info.get("http_headers") or global_hdrs
    ext        = fmt_info.get("ext") or "mp4"

    filesize = fmt_info.get("filesize") or fmt_info.get("filesize_approx") or info.get("filesize") or info.get("filesize_approx")

    vcodec = (fmt_info.get("vcodec") or "none").lower()
    has_video = vcodec not in ("none", "")

    if is_audio or not has_video:
        ext = "mp3"

    return {
        "mode":          "single" if has_video else "audio",
        "video_url":     None,
        "audio_url":     None,
        "single_url":    single_url,
        "single_headers": s_hdrs,
        "filesize":      filesize,
        "is_audio_only": is_audio or not has_video,
        "title":         title,
        "ext":           ext,
    }


async def get_stream_info(
    url: str,
    format_id: str,
    cookies_file: Optional[str] = None,
) -> dict:
    """Async wrapper for _get_direct_urls_sync."""
    return await asyncio.to_thread(_get_direct_urls_sync, url, format_id, cookies_file)


def _headers_str(headers: dict) -> str:
    """Convert a headers dict to FFmpeg -headers string format."""
    return "".join(f"{k}: {v}\r\n" for k, v in headers.items())


async def stream_via_ffmpeg(
    stream_info: dict,
) -> AsyncGenerator[bytes, None]:
    """
    FFmpeg streaming pipeline — three modes:

    1. 'separate'  : Two inputs (video URL + audio URL) → merged fragmented MP4
    2. 'single'    : One pre-muxed input → re-mux to fragmented MP4
    3. 'audio'     : One audio input → convert to MP3
    """
    ffmpeg_bin = find_ffmpeg() or "ffmpeg"
    mode       = stream_info.get("mode", "single")

    if mode == "separate":
        v_url  = stream_info["video_url"]
        a_url  = stream_info["audio_url"]
        v_hdrs = _headers_str(stream_info.get("video_headers", {"User-Agent": _UA}))
        a_hdrs = _headers_str(stream_info.get("audio_headers", {"User-Agent": _UA}))
        vcodec = stream_info.get("vcodec", "")

        v_encoder = ["-c:v", "copy"]

        reconnect_opts = [
            "-reconnect", "1",
            "-reconnect_at_eof", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
        ]

        cmd = [
            ffmpeg_bin,
            "-loglevel",  "error",
            *reconnect_opts,
            "-headers",   v_hdrs,
            "-i",         v_url,
            *reconnect_opts,
            "-headers",   a_hdrs,
            "-i",         a_url,
            "-map",       "0:v:0",
            "-map",       "1:a:0",
            *v_encoder,
            "-c:a",       "aac",
            "-b:a",       "192k",
            "-movflags",  "frag_keyframe+empty_moov+default_base_moof",
            "-f",         "mp4",
            "pipe:1",
        ]

    elif mode == "audio":
        s_url  = stream_info["single_url"]
        s_hdrs = _headers_str(stream_info.get("single_headers", {"User-Agent": _UA}))
        reconnect_opts = [
            "-reconnect", "1",
            "-reconnect_at_eof", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
        ]

        cmd = [
            ffmpeg_bin,
            "-loglevel",  "error",
            *reconnect_opts,
            "-headers",   s_hdrs,
            "-i",         s_url,
            "-vn",
            "-c:a",       "libmp3lame",
            "-q:a",       "2",
            "-f",         "mp3",
            "pipe:1",
        ]

    else:
        s_url  = stream_info["single_url"]
        s_hdrs = _headers_str(stream_info.get("single_headers", {"User-Agent": _UA}))
        reconnect_opts = [
            "-reconnect", "1",
            "-reconnect_at_eof", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
        ]

        cmd = [
            ffmpeg_bin,
            "-loglevel",  "error",
            *reconnect_opts,
            "-headers",   s_hdrs,
            "-i",         s_url,
            "-c",         "copy",
            "-movflags",  "frag_keyframe+empty_moov+default_base_moof",
            "-f",         "mp4",
            "pipe:1",
        ]

    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )

    try:
        while True:
            chunk = await asyncio.to_thread(process.stdout.read, 65536)
            if not chunk:
                break
            yield chunk
    finally:
        try:
            process.kill()
        except Exception:
            pass
        try:
            process.wait()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def cleanup_job(job_id: str) -> None:
    """Remove temp files for a completed job and clear progress entry."""
    job_dir = TEMP_DIR / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)
    progress_store.pop(job_id, None)

    # Prune old progress_store entries if dictionary grows larger than 50
    if len(progress_store) > 50:
        keys_to_remove = list(progress_store.keys())[:25]
        for k in keys_to_remove:
            progress_store.pop(k, None)
