"""
downloader.py — Core yt-dlp wrapper for VidSnap.

Handles:
- Multi-platform metadata extraction (YouTube, Instagram, Facebook)
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

# Default cookies file path for cloud deployment (Render/AWS)
COOKIES_PATH = Path(__file__).parent.parent / "cookies.txt"

# In-memory job progress store  { job_id: { status, percent, speed, eta, ... } }
progress_store: Dict[str, dict] = {}

# TLS Impersonation Target (bypasses cloud IP bot detection using curl_cffi)
try:
    from yt_dlp.networking.impersonate import ImpersonateTarget
    IMPERSONATE_TARGET = ImpersonateTarget.from_str("chrome-120:macos-14")
except Exception:
    IMPERSONATE_TARGET = None

# Platform detection patterns
PLATFORM_PATTERNS = {
    "youtube": re.compile(r"(youtube\.com|youtu\.be)", re.IGNORECASE),
    "instagram": re.compile(r"instagram\.com", re.IGNORECASE),
    "facebook": re.compile(r"(facebook\.com|fb\.com|fb\.watch)", re.IGNORECASE),
}

# Quality tier definitions — ordered best-to-worst
QUALITY_TIERS = [
    {
        "format_id": "best_auto",
        "label": "Best (Auto)",
        "selector": "bestvideo+bestaudio/b/best",
        "is_audio": False,
    },
    {
        "format_id": "4k",
        "label": "4K (2160p)",
        "selector": "bestvideo[height<=2160]+bestaudio/bestvideo[height<=2160]/b/best",
        "is_audio": False,
    },
    {
        "format_id": "1080p",
        "label": "1080p Full HD",
        "selector": "bestvideo[height<=1080]+bestaudio/bestvideo[height<=1080]/b/best",
        "is_audio": False,
    },
    {
        "format_id": "720p",
        "label": "720p HD",
        "selector": "bestvideo[height<=720]+bestaudio/bestvideo[height<=720]/b/best",
        "is_audio": False,
    },
    {
        "format_id": "480p",
        "label": "480p",
        "selector": "bestvideo[height<=480]+bestaudio/bestvideo[height<=480]/b/best",
        "is_audio": False,
    },
    {
        "format_id": "360p",
        "label": "360p",
        "selector": "bestvideo[height<=360]+bestaudio/bestvideo[height<=360]/b/best",
        "is_audio": False,
    },
    {
        "format_id": "hd",
        "label": "HD Quality",
        "selector": "hd/bestvideo+bestaudio/b/best",
        "is_audio": False,
    },
    {
        "format_id": "sd",
        "label": "SD Quality",
        "selector": "sd/bestvideo+bestaudio/b/best",
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
    Converts Facebook share links (facebook.com/share/...) to m.facebook.com
    which bypasses Facebook login wall blocks.
    """
    url = url.strip()
    if "facebook.com" in url.lower() and "m.facebook.com" not in url.lower():
        if "/share/" in url.lower() or "fb.watch" in url.lower():
            url = re.sub(r"https?://(www\.|web\.)?facebook\.com", "https://m.facebook.com", url, flags=re.IGNORECASE)
    return url


def detect_platform(url: str) -> str:
    """Return 'youtube', 'instagram', 'facebook', or 'unknown'."""
    for platform, pattern in PLATFORM_PATTERNS.items():
        if pattern.search(url):
            return platform
    return "unknown"


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
                "Only YouTube, Instagram, and Facebook links are supported."
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
# yt-dlp Options Builder
# ---------------------------------------------------------------------------

def build_ydl_opts(
    format_selector: str,
    output_template: str,
    is_audio_only: bool,
    job_id: Optional[str] = None,
    cookies_file: Optional[str] = None,
) -> dict:
    """
    Build a complete yt-dlp options dict.

    Args:
        format_selector:  yt-dlp format string (e.g. 'bestvideo+bestaudio/best')
        output_template:  outtmpl path (e.g. '/tmp/vidsnap/abc/download.%(ext)s')
        is_audio_only:    Whether to post-process to MP3
        job_id:           If provided, attaches a progress hook to update progress_store
        cookies_file:     Optional path to a Netscape cookies.txt file
    """
    # Determine if cookies are available
    # IMPORTANT: android_vr client is skipped by yt-dlp when cookies are present.
    # So we use TWO different strategies:
    #   - WITH cookies: use 'web' client (supports cookies, works on cloud IPs)
    #   - WITHOUT cookies: use 'android_vr' (bypasses bot detection, no login needed)
    _has_cookies = (
        (cookies_file and os.path.isfile(cookies_file))
        or (COOKIES_PATH.exists() and COOKIES_PATH.is_file() and COOKIES_PATH.stat().st_size > 200)
    )
    _player_clients = ["web", "web_creator"] if _has_cookies else ["android_vr", "web_embedded", "mweb"]

    opts: dict = {
        "format": format_selector,
        "outtmpl": output_template,
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
        "file_access_retries": 3,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "concurrent_fragment_downloads": 4,
        "extractor_args": {
            "youtube": {
                "player_client": ["android_vr"],
            }
        },
    }
    if IMPERSONATE_TARGET:
        opts["impersonate"] = IMPERSONATE_TARGET

    # FFmpeg location
    ffmpeg_bin = find_ffmpeg()
    if ffmpeg_bin:
        opts["ffmpeg_location"] = os.path.dirname(ffmpeg_bin)

    # Always merge video+audio into mp4 (requires FFmpeg)
    if not is_audio_only:
        opts["merge_output_format"] = "mp4"
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
    elif COOKIES_PATH.exists() and COOKIES_PATH.is_file():
        opts["cookiefile"] = str(COOKIES_PATH)

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


# ---------------------------------------------------------------------------
# Metadata / Info Extraction
# ---------------------------------------------------------------------------

def fetch_info_sync(url: str, cookies_file: Optional[str] = None) -> dict:
    """
    Synchronously extract video metadata with yt-dlp.
    3-Stage Failover Pipeline:
      Stage 1: android_vr & web_embedded (no cookies) -> bypasses bot detection on cloud IPs
      Stage 2: mweb & android (no cookies) -> mobile failover
      Stage 3: web & web_creator (with cookies if available) -> authenticated fallback
    """
    url = normalize_url(url)

    _ck_file = cookies_file if (cookies_file and os.path.isfile(cookies_file)) else None

    stage1_opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "extractor_args": {
            "youtube": {
                "player_client": ["android_vr"],
            }
        },
    }
    if IMPERSONATE_TARGET:
        stage1_opts["impersonate"] = IMPERSONATE_TARGET

    info = None
    # Stage 1: Cloud Bypass (android_vr / web_embedded)
    try:
        with yt_dlp.YoutubeDL(stage1_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        pass

    # Stage 2: Mobile Failover (mweb / android)
    if not info:
        try:
            stage2_opts = dict(stage1_opts)
            stage2_opts["extractor_args"] = {"youtube": {"player_client": ["mweb", "android"]}}
            with yt_dlp.YoutubeDL(stage2_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception:
            pass

    # Stage 3: Authenticated Web Fallback (with cookies if available)
    if not info:
        try:
            stage3_opts = dict(stage1_opts)
            stage3_opts["extractor_args"] = {"youtube": {"player_client": ["web", "web_creator"]}}
            if _ck_file:
                stage3_opts["cookiefile"] = _ck_file
            with yt_dlp.YoutubeDL(stage3_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:
            err = classify_error(exc)
            raise ValueError(err["message"]) from exc

    if not info:
        raise ValueError("Could not extract video information from this URL.")

    # Build available video format list (deduplicated by height)
    # Map each detected height to the closest QUALITY_TIER format_id
    # so the download endpoint uses the correct yt-dlp selector
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

    # Find size of best audio stream to add to video-only streams
    best_audio_size = 0
    for f in raw_formats:
        vcodec = (f.get("vcodec") or "none").lower()
        acodec = (f.get("acodec") or "none").lower()
        if vcodec in ("none", "") and acodec not in ("none", ""):
            sz = f.get("filesize") or f.get("filesize_approx") or 0
            if sz > best_audio_size:
                best_audio_size = sz

    for fmt in reversed(raw_formats):  # reversed = highest quality first
        h = fmt.get("height")
        vcodec = fmt.get("vcodec", "none")
        acodec = (fmt.get("acodec") or "none").lower()

        # Only proper video streams (not audio-only, not storyboards)
        if h and h > 0 and vcodec and vcodec not in ("none", None) and h not in seen_heights:
            seen_heights.add(h)
            label_suffix = ""
            if h >= 2160:   label_suffix = " · 4K"
            elif h >= 1080: label_suffix = " · Full HD"
            elif h >= 720:  label_suffix = " · HD"

            # Map to nearest tier (for yt-dlp format selector)
            tier_id = "best_auto"
            for threshold in sorted(HEIGHT_TO_TIER.keys(), reverse=True):
                if h >= threshold:
                    tier_id = HEIGHT_TO_TIER[threshold]
                    break

            v_size = fmt.get("filesize") or fmt.get("filesize_approx") or 0
            # If video-only format (acodec == 'none'), add audio size for accurate total size
            total_size = (v_size + best_audio_size) if (acodec in ("none", "") and v_size > 0) else (v_size or None)

            video_formats.append(
                {
                    "format_id": tier_id,   # ← uses correct selector on download
                    "label": f"{h}p{label_suffix}",
                    "ext": "mp4",
                    "height": h,
                    "filesize": total_size,
                    "is_audio": False,
                }
            )

    # Standard audio tiers (always appended)
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

    # If no specific video formats found, fall back to standard tiers
    if not video_formats:
        video_formats = [
            {"format_id": "best_auto", "label": "Best Available", "ext": "mp4", "height": None, "filesize": None, "is_audio": False},
            {"format_id": "720p", "label": "720p HD", "ext": "mp4", "height": 720, "filesize": None, "is_audio": False},
            {"format_id": "480p", "label": "480p", "ext": "mp4", "height": 480, "filesize": None, "is_audio": False},
            {"format_id": "360p", "label": "360p", "ext": "mp4", "height": 360, "filesize": None, "is_audio": False},
        ]

    return {
        "title": info.get("title", "Unknown Title"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "platform": detect_platform(url),
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
# The browser downloads directly from YouTube CDN — zero Render server bandwidth!
# ---------------------------------------------------------------------------

def extract_direct_links(url: str, format_id: str = "best_auto") -> dict:
    """
    Use yt-dlp to extract direct CDN streaming URLs for a video.
    Returns JSON with { video_url, audio_url, title, ext, needs_merge, filesize }.

    The key innovation: the SERVER only extracts URLs (fast, ~1-2 sec).
    The BROWSER then downloads directly from YouTube/Facebook CDN.
    This means:
      - Render server uses zero bandwidth for actual video transfer
      - YouTube IP blocks don't affect actual download (user's IP is used)
      - No server disk space consumed
    """
    url = normalize_url(url)
    tier = get_format_tier(format_id)
    is_audio = tier["is_audio"]

    # Stage 1: Pure android_vr Cloud Bypass (no cookies, zero bot detection)
    opts_s1 = {
        "quiet":        True,
        "no_warnings":  True,
        "noplaylist":   True,
        "socket_timeout": 30,
        "extractor_args": {
            "youtube": {
                "player_client": ["android_vr"],
            }
        },
    }
    if IMPERSONATE_TARGET:
        opts_s1["impersonate"] = IMPERSONATE_TARGET

    # Stage 2: Mobile Failover
    opts_s2 = {
        "quiet":        True,
        "no_warnings":  True,
        "noplaylist":   True,
        "socket_timeout": 30,
        "extractor_args": {
            "youtube": {
                "player_client": ["mweb", "android"],
            }
        },
    }

    # Stage 3: Authenticated Web Fallback
    opts_s3 = {
        "quiet":        True,
        "no_warnings":  True,
        "noplaylist":   True,
        "socket_timeout": 30,
        "extractor_args": {
            "youtube": {
                "player_client": ["web", "web_creator"],
            }
        },
    }

    def _try_extract(client_opts: dict) -> Optional[dict]:
        try:
            with yt_dlp.YoutubeDL(client_opts) as ydl:
                return ydl.extract_info(url, download=False)
        except Exception:
            return None

    # Stage 1 attempt
    info = _try_extract(opts_s1)

    # Stage 2 attempt (mobile)
    if not info:
        info = _try_extract(opts_s2)

    # Stage 3 attempt (cookies / web)
    if not info:
        info = _try_extract(opts_s3)

    if not info:
        raise ValueError("Could not extract video. The video may be unavailable or restricted.")

    title     = info.get("title", "video")
    thumbnail = info.get("thumbnail", "")
    duration  = info.get("duration", 0)
    platform  = detect_platform(url)

    # ── Audio-only request ────────────────────────────────────────────────────
    if is_audio:
        # Find best audio-only format with a direct URL
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
                "ext":         "webm",          # audio stream
                "needs_merge": False,
                "video_url":   best_audio["url"],
                "audio_url":   None,
                "filesize":    best_audio.get("filesize") or best_audio.get("filesize_approx"),
            }
        raise ValueError("No direct audio stream URL found for this video.")

    # ── Video request ─────────────────────────────────────────────────────────
    # Get requested height cap from format_id
    height_caps = {
        "4k": 2160, "1080p": 1080, "720p": 720,
        "480p": 480, "360p": 360, "best_auto": 9999,
    }
    max_h = height_caps.get(format_id, 9999)

    # Look for a combined (muxed) stream first — browser can download as-is
    best_muxed  = None
    best_mux_h  = 0
    best_video  = None  # video-only
    best_v_h    = 0
    best_audio  = None  # audio-only companion
    best_abr    = 0

    for fmt in (info.get("formats") or []):
        vcodec = (fmt.get("vcodec") or "none").lower()
        acodec = (fmt.get("acodec") or "none").lower()
        h      = fmt.get("height") or 0
        direct = fmt.get("url", "")
        if not direct.startswith("http"):
            continue

        has_video = vcodec not in ("none", "")
        has_audio = acodec not in ("none", "")

        if has_video and has_audio and h <= max_h and h > best_mux_h:
            best_mux_h  = h
            best_muxed  = fmt

        if has_video and not has_audio and h <= max_h and h > best_v_h:
            best_v_h   = h
            best_video = fmt

        if not has_video and has_audio:
            abr = fmt.get("abr") or 0
            if abr > best_abr:
                best_abr   = abr
                best_audio = fmt

    # ── Case 1: Combined (muxed) stream — browser downloads directly ──────────
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

    # ── Case 2: Separate DASH streams — return both URLs to frontend ──────────
    # Frontend will download video + audio separately, then use ffmpeg.wasm or
    # simply offer the video-only stream with a note.
    if best_video:
        v_size = best_video.get("filesize") or best_video.get("filesize_approx") or 0
        a_size = (best_audio.get("filesize") or best_audio.get("filesize_approx") or 0) if best_audio else 0
        return {
            "title":       title,
            "thumbnail":   thumbnail,
            "duration":    duration,
            "platform":    platform,
            "ext":         best_video.get("ext", "mp4"),
            "needs_merge": True,   # Frontend uses /api/stream for merging
            "video_url":   best_video["url"],
            "audio_url":   best_audio["url"] if best_audio else None,
            "video_height": best_v_h,
            "filesize":    (v_size + a_size) or None,
        }

    raise ValueError("No downloadable video stream found for this URL and quality.")



# Live Streaming — FFmpeg Pipeline (instant browser dialog, no server storage)
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
        "extractor_args": {
            "youtube": {
                "player_client": ["android_vr", "web_embedded", "mweb", "android", "web"],
            }
        },
    }
    if cookies_file and os.path.isfile(cookies_file):
        opts["cookiefile"] = cookies_file
    elif COOKIES_PATH.exists() and COOKIES_PATH.is_file():
        opts["cookiefile"] = str(COOKIES_PATH)

    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except Exception as exc:
            if "facebook.com" in url.lower() and "m.facebook.com" not in url.lower():
                alt_url = re.sub(r"https?://(www\.|web\.)?facebook\.com", "https://m.facebook.com", url, flags=re.IGNORECASE)
                try:
                    info = ydl.extract_info(alt_url, download=False)
                except Exception as alt_exc:
                    err = classify_error(alt_exc)
                    raise ValueError(err["message"]) from alt_exc
            else:
                try:
                    alt_opts = dict(opts)
                    alt_opts["extractor_args"] = {"youtube": {"player_client": ["tv", "mweb", "web"]}}
                    with yt_dlp.YoutubeDL(alt_opts) as alt_ydl:
                        info = alt_ydl.extract_info(url, download=False)
                except Exception as alt_exc:
                    err = classify_error(alt_exc)
                    raise ValueError(err["message"]) from alt_exc

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

    Uses -movflags frag_keyframe+empty_moov so the browser can start the
    download immediately without knowing the total file size.
    Streams in 64-KB chunks.
    """
    ffmpeg_bin = find_ffmpeg() or "ffmpeg"
    mode       = stream_info.get("mode", "single")

    if mode == "separate":
        v_url  = stream_info["video_url"]
        a_url  = stream_info["audio_url"]
        v_hdrs = _headers_str(stream_info.get("video_headers", {"User-Agent": _UA}))
        a_hdrs = _headers_str(stream_info.get("audio_headers", {"User-Agent": _UA}))
        vcodec = stream_info.get("vcodec", "")

        # If video codec is H.264 (avc1), copy video directly.
        # If video codec is VP9/AV1, re-encode video to libx264 fast to guarantee MP4 compatibility
        v_encoder = ["-c:v", "copy"] if ("avc" in vcodec or "h264" in vcodec) else ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23"]

        reconnect_opts = [
            "-reconnect", "1",
            "-reconnect_at_eof", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
        ]

        cmd = [
            ffmpeg_bin,
            "-loglevel",  "error",
            # Video input
            *reconnect_opts,
            "-headers",   v_hdrs,
            "-i",         v_url,
            # Audio input
            *reconnect_opts,
            "-headers",   a_hdrs,
            "-i",         a_url,
            # Explicit stream mapping: stream 0:v (video) and stream 1:a (audio)
            "-map",       "0:v:0",
            "-map",       "1:a:0",
            # Encoding
            *v_encoder,
            "-c:a",       "aac",
            "-b:a",       "192k",
            # Fragmented MP4 for immediate browser download streaming
            "-movflags",  "frag_keyframe+empty_moov+default_base_moof",
            "-f",         "mp4",
            "pipe:1",
        ]

    elif mode == "audio":
        # ── Convert audio stream to MP3 ──────────────────────────────────────
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
            "-vn",                       # drop video (if any)
            "-c:a",       "libmp3lame",
            "-q:a",       "2",           # VBR ~190 kbps
            "-f",         "mp3",
            "pipe:1",
        ]

    else:
        # ── Re-mux pre-muxed stream to fragmented MP4 ────────────────────────
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

    # ── Run FFmpeg with standard subprocess.Popen to prevent Windows asyncio NotImplementedError ──
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )

    try:
        while True:
            chunk = await asyncio.to_thread(process.stdout.read, 65536)   # 64 KB
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
