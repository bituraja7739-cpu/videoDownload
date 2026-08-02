import os
import sys
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch

def create_project_report(output_filename="VidSnap_Technical_Project_Report.pdf"):
    doc = SimpleDocTemplate(
        output_filename,
        pagesize=letter,
        rightMargin=0.5 * inch,
        leftMargin=0.5 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch
    )

    styles = getSampleStyleSheet()
    
    # Custom styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=24,
        leading=28,
        textColor=colors.HexColor('#0085CF'),
        alignment=0,
        spaceAfter=10
    )
    
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=12,
        leading=16,
        textColor=colors.HexColor('#4A5568'),
        spaceAfter=15
    )

    h1_style = ParagraphStyle(
        'H1Style',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=16,
        leading=20,
        textColor=colors.HexColor('#1A202C'),
        spaceBefore=14,
        spaceAfter=6
    )

    h2_style = ParagraphStyle(
        'H2Style',
        parent=styles['Heading3'],
        fontName='Helvetica-Bold',
        fontSize=12,
        leading=16,
        textColor=colors.HexColor('#2B6CB0'),
        spaceBefore=10,
        spaceAfter=4
    )

    body_style = ParagraphStyle(
        'BodyStyle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9.5,
        leading=13.5,
        textColor=colors.HexColor('#2D3748'),
        spaceAfter=6
    )

    bullet_style = ParagraphStyle(
        'BulletStyle',
        parent=body_style,
        leftIndent=15,
        spaceAfter=4
    )

    code_style = ParagraphStyle(
        'CodeStyle',
        parent=styles['Normal'],
        fontName='Courier',
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor('#1A202C'),
        backColor=colors.HexColor('#EDF2F7'),
        borderColor=colors.HexColor('#CBD5E0'),
        borderWidth=0.5,
        borderPadding=6,
        spaceBefore=4,
        spaceAfter=6
    )

    story = []

    # Title & Header Banner
    story.append(Paragraph("VidSnap — Technical Architecture & Maintenance Report", title_style))
    story.append(Paragraph("<b>Version:</b> 2.0 | <b>Core Stack:</b> FastAPI, Python 3.11, yt-dlp, FFmpeg | <b>Date:</b> August 2026", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#0085CF'), spaceAfter=12))

    # Section 1: Executive Summary
    story.append(Paragraph("1. Executive Summary & Core Requirements", h1_style))
    story.append(Paragraph(
        "VidSnap is a high-performance, multi-platform media downloader designed to fetch and stream video and audio "
        "content from YouTube, Facebook, and Instagram. The platform emphasizes instant browser-native downloads, "
        "full resolution support (360p up to 4K), accurate file size estimation, and robust server stability under high traffic.",
        body_style
    ))
    story.append(Paragraph("<b>Key Application Capabilities:</b>", body_style))
    story.append(Paragraph("• <b>Multi-Platform Extraction:</b> Seamless support for YouTube, Facebook (Posts, Reels, Watch), and Instagram.", bullet_style))
    story.append(Paragraph("• <b>Adaptive Stream Merging:</b> Merges separate high-res video and audio tracks (1080p, 4K) into single playable MP4 files.", bullet_style))
    story.append(Paragraph("• <b>Chrome Download Manager Integration:</b> Direct CDN redirecting and streaming with live MB progress display.", bullet_style))
    story.append(Paragraph("• <b>Zero Storage Overhead:</b> Real-time FFmpeg pipe streaming prevents server disk bloat.", bullet_style))

    # Section 2: Architecture & Format Selector Core Logic
    story.append(Paragraph("2. Core Technical Architecture & Fixes Implemented", h1_style))
    story.append(Paragraph(
        "During development, several critical challenges regarding adaptive media streaming, Windows process execution, "
        "and HTTP header limits were identified and permanently resolved. Below is the technical breakdown:",
        body_style
    ))

    # Issue 1
    story.append(Paragraph("A. Adaptive Stream Merging (Audio + Video Fix)", h2_style))
    story.append(Paragraph(
        "<b>Problem:</b> High-resolution videos (1080p, 4K) on YouTube and Facebook use DASH adaptive streaming where video and audio "
        "are hosted as separate files. Downloading directly often resulted in audio-only output.",
        body_style
    ))
    story.append(Paragraph(
        "<b>Architectural Solution:</b> Implemented a dynamic format selector (<i>_build_format_selector</i>) that pairs requested video quality "
        "with the best available audio track using: <code>bestvideo[height<=1080]+bestaudio/b/best</code>. Additionally, explicit FFmpeg "
        "stream mapping <code>-map 0:v:0 -map 1:a:0</code> forces input 0 as video and input 1 as audio.",
        body_style
    ))

    # Issue 2
    story.append(Paragraph("B. Chrome Native Download Manager Integration", h2_style))
    story.append(Paragraph(
        "<b>Problem:</b> Downloads previously completed internally on the server before transferring to Chrome, leaving the user with "
        "no native download progress tracker.",
        body_style
    ))
    story.append(Paragraph(
        "<b>Solution:</b> The endpoint <code>/api/stream</code> issues a <i>RedirectResponse</i> for single/pre-muxed streams (Facebook, Instagram, YouTube 360p/720p) "
        "direct to CDN, allowing Chrome's native Download Manager to immediately open and display live progress (e.g. <i>0.9 / 834 MB • 1 hour left</i>).",
        body_style
    ))

    # Issue 3
    story.append(Paragraph("C. Windows Async Subprocess & Event Loop Handling", h2_style))
    story.append(Paragraph(
        "<b>Problem:</b> Running <i>asyncio.create_subprocess_exec</i> inside Starlette StreamingResponse iterators on Windows threw <code>NotImplementedError</code>.",
        body_style
    ))
    story.append(Paragraph(
        "<b>Solution:</b> Replaced async subprocess creation with standard <code>subprocess.Popen</code> and wrapped output chunk reads inside <code>asyncio.to_thread(process.stdout.read, 65536)</code>. "
        "This completely eliminates Windows event loop crashes.",
        body_style
    ))

    # Issue 4
    story.append(Paragraph("D. Unicode & Emoji Filename Header Safety", h2_style))
    story.append(Paragraph(
        "<b>Problem:</b> Video titles containing emojis (🔗, 🎬) caused Starlette HTTP header encoding crashes (<code>UnicodeEncodeError: 'latin-1'</code>).",
        body_style
    ))
    story.append(Paragraph(
        "<b>Solution:</b> Applied RFC 5987 UTF-8 header encoding: <code>filename*=UTF-8''{quoted_title}</code> with an ASCII fallback parameter, ensuring 100% safety for all international characters.",
        body_style
    ))

    # Issue 5
    story.append(Paragraph("E. Accurate File Size Calculation", h2_style))
    story.append(Paragraph(
        "<b>Problem:</b> UI table displayed video-only stream sizes (e.g. 13.3 MB for 1080p) while actual download was 33 MB.",
        body_style
    ))
    story.append(Paragraph(
        "<b>Solution:</b> Updated <i>fetch_info_sync</i> to add <code>video_filesize + best_audio_filesize</code> for DASH formats so the quality table renders exact total file sizes.",
        body_style
    ))

    # Issue 6
    story.append(Paragraph("F. Facebook Share Link Mobile Fallback", h2_style))
    story.append(Paragraph(
        "<b>Problem:</b> Desktop Facebook share links (<code>facebook.com/share/v/...</code>) returned <i>Cannot parse data</i> due to Facebook login walls.",
        body_style
    ))
    story.append(Paragraph(
        "<b>Solution:</b> Added <i>normalize_url</i> helper and fallback handling to convert share links to <code>m.facebook.com</code>, bypassing login restrictions.",
        body_style
    ))

    story.append(PageBreak())

    # Section 3: Key Code Snippets & Options
    story.append(Paragraph("3. Essential Code Architecture Reference", h1_style))
    story.append(Paragraph("<b>yt-dlp Options Builder (downloader.py):</b>", h2_style))
    story.append(Paragraph(
"""opts = {
    "format": format_selector,          # bestvideo+bestaudio/b/best
    "outtmpl": output_template,
    "quiet": True,
    "ffmpeg_location": os.path.dirname(find_ffmpeg()),
    "merge_output_format": "mp4",       # Automatic FFmpeg merging
    "postprocessors": [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}]
}""", code_style))

    story.append(Paragraph("<b>FFmpeg Live Streaming Command (downloader.py):</b>", h2_style))
    story.append(Paragraph(
"""cmd = [
    ffmpeg_bin, "-loglevel", "error",
    "-reconnect", "1", "-reconnect_at_eof", "1", "-reconnect_streamed", "1",
    "-headers", v_headers, "-i", video_url,
    "-headers", a_headers, "-i", audio_url,
    "-map", "0:v:0", "-map", "1:a:0",
    "-c:v", "copy" if "avc" in vcodec else "libx264",
    "-c:a", "aac", "-b:a", "192k",
    "-movflags", "frag_keyframe+empty_moov+default_base_moof",
    "-f", "mp4", "pipe:1"
]""", code_style))

    # Section 4: API Routes Specification
    story.append(Paragraph("4. Backend API Routes Specification", h1_style))
    
    api_data = [
        [Paragraph('<b>Endpoint</b>', body_style), Paragraph('<b>Method</b>', body_style), Paragraph('<b>Description</b>', body_style)],
        [Paragraph('<code>/api/analyze</code>', body_style), Paragraph('POST', body_style), Paragraph('Extracts metadata (title, thumb, duration, quality formats).', body_style)],
        [Paragraph('<code>/api/stream</code>', body_style), Paragraph('GET', body_style), Paragraph('Triggers Chrome Download Manager (redirects or FFmpeg stream).', body_style)],
        [Paragraph('<code>/api/start-download</code>', body_style), Paragraph('POST', body_style), Paragraph('Initiates background server download job.', body_style)],
        [Paragraph('<code>/api/progress/{id}</code>', body_style), Paragraph('GET (SSE)', body_style), Paragraph('Streams live SSE progress (% percentage, speed, ETA).', body_style)],
        [Paragraph('<code>/api/fetch/{id}</code>', body_style), Paragraph('GET', body_style), Paragraph('Retrieves completed job download file and cleans up temp.', body_style)],
    ]
    t = Table(api_data, colWidths=[1.5*inch, 0.8*inch, 4.7*inch])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#EDF2F7')),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E0')),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('PADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(t)

    story.append(Spacer(1, 10))

    # Section 5: Future Maintenance & Troubleshooting
    story.append(Paragraph("5. Future Maintenance & Troubleshooting Guide", h1_style))
    
    maint_data = [
        [Paragraph('<b>Issue Scenario</b>', body_style), Paragraph('<b>Root Cause</b>', body_style), Paragraph('<b>Action / Solution</b>', body_style)],
        [
            Paragraph('Facebook links show <i>Cannot parse data</i>', body_style),
            Paragraph('Facebook updated page HTML / login wall active', body_style),
            Paragraph('Run <code>pip install -U yt-dlp</code> to get latest extractor fixes.', body_style)
        ],
        [
            Paragraph('Private Video error', body_style),
            Paragraph('Video requires login or cookies', body_style),
            Paragraph('Provide <code>cookies.txt</code> in request parameters.', body_style)
        ],
        [
            Paragraph('Chrome download stops mid-video', body_style),
            Paragraph('CDN socket timeout', body_style),
            Paragraph('Verify FFmpeg <code>-reconnect 1</code> flags are present in <i>downloader.py</i>.', body_style)
        ],
        [
            Paragraph('Server disk filling up', body_style),
            Paragraph('Temp files accumulated', body_style),
            Paragraph('Ensure <i>cleanup_job</i> is invoked after file fetch.', body_style)
        ],
    ]
    t2 = Table(maint_data, colWidths=[2.0*inch, 2.0*inch, 3.0*inch])
    t2.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#EDF2F7')),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E0')),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('PADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(t2)

    story.append(Spacer(1, 10))

    # Section 6: Production Deployment
    story.append(Paragraph("6. Production Deployment Instructions", h1_style))
    story.append(Paragraph("<b>1. Install Dependencies:</b>", h2_style))
    story.append(Paragraph("<code>pip install fastapi uvicorn gunicorn yt-dlp pydantic reportlab</code>", code_style))
    story.append(Paragraph("<b>2. Run Gunicorn with 4 CPU Workers:</b>", h2_style))
    story.append(Paragraph("<code>gunicorn -w 4 -k uvicorn.workers.UvicornWorker backend.main:app --bind 0.0.0.0:8000</code>", code_style))
    story.append(Paragraph("<b>3. Nginx Reverse Proxy Header Setup:</b>", h2_style))
    story.append(Paragraph("Ensure <code>proxy_buffering off;</code> and <code>proxy_set_header X-Real-IP $remote_addr;</code> are set in Nginx location block.", body_style))

    doc.build(story)
    print(f"PDF Project Report generated successfully: {output_filename}")

if __name__ == "__main__":
    create_project_report()
