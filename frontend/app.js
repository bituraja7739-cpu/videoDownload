'use strict';

// ─────────────────────────────────────────────────────────────
// DOM Elements
// ─────────────────────────────────────────────────────────────
const urlInput       = document.getElementById('url-input');
const searchForm     = document.getElementById('search-form');
const btnText        = document.getElementById('btn-text');
const downloadBtn    = document.getElementById('download-btn');

const loadingBox     = document.getElementById('loading-box');
const errorBox       = document.getElementById('error-box');
const errorMsg       = document.getElementById('error-msg');
const resultCard     = document.getElementById('result-card');

const vidThumb       = document.getElementById('vid-thumb');
const vidDuration    = document.getElementById('vid-duration');
const vidPlatform    = document.getElementById('vid-platform');
const vidTitle       = document.getElementById('vid-title');
const vidContentType = document.getElementById('vid-content-type');
const vidUploader    = document.getElementById('vid-uploader');
const vidViews       = document.getElementById('vid-views');

const dlProgress     = document.getElementById('dl-progress');
const dlStatusText   = document.getElementById('dl-status-text');
const dlPercentText  = document.getElementById('dl-percent-text');
const dlBarWrap      = document.getElementById('dl-bar-wrap');
const dlBarFill      = document.getElementById('dl-bar-fill');
const dlSpeed        = document.getElementById('dl-speed');
const dlEta          = document.getElementById('dl-eta');

const qualityTbody    = document.getElementById('quality-tbody');

// ─────────────────────────────────────────────────────────────
// State
// ─────────────────────────────────────────────────────────────
let currentUrl  = '';
let isAnalyzing = false;

// ─────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────
function fmtDuration(s) {
  if (!s) return '';
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  return h > 0
    ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
    : `${m}:${String(sec).padStart(2, '0')}`;
}

function fmtViews(n) {
  if (!n) return '';
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B views`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M views`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)}K views`;
  return `${n} views`;
}

function fmtSize(bytes) {
  if (!bytes) return '~';
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`;
  return `${(bytes / 1e3).toFixed(0)} KB`;
}

function detectPlatform(url) {
  if (/instagram\.com/i.test(url))                  return 'instagram';
  if (/facebook\.com|fb\.com|fb\.watch/i.test(url)) return 'facebook';
  return 'unknown';
}

function platformTagClass(p) {
  return { instagram: 'tag-instagram', facebook: 'tag-facebook' }[p] || 'tag-unknown';
}

// ─────────────────────────────────────────────────────────────
// UI Control
// ─────────────────────────────────────────────────────────────
function hideAll() {
  loadingBox.classList.add('hidden');
  errorBox.classList.add('hidden');
  resultCard.classList.add('hidden');
  dlProgress.classList.add('hidden');
}

function showError(msg) {
  hideAll();
  errorMsg.textContent = msg;
  errorBox.classList.remove('hidden');
  document.getElementById('result-section').scrollIntoView({ behavior: 'smooth' });
}

function setAnalyzing(v) {
  isAnalyzing = v;
  downloadBtn.disabled = v;
  btnText.textContent  = v ? 'Analyzing…' : 'GO';
}

function highlightTab(url) {
  const tabIg = document.getElementById('tab-ig');
  const tabFb = document.getElementById('tab-fb');
  if (tabIg) tabIg.classList.remove('active');
  if (tabFb) tabFb.classList.remove('active');

  const p = detectPlatform(url);
  if (p === 'facebook' && tabFb) {
    tabFb.classList.add('active');
  } else if (tabIg) {
    tabIg.classList.add('active');
  }
}

// ─────────────────────────────────────────────────────────────
// Content Type & Quality Badges & Table
// ─────────────────────────────────────────────────────────────
function contentTypeBadge(type) {
  if (!type) return '';
  const map = {
    'reel': { icon: '🎬', label: 'Reel' },
    'story': { icon: '📱', label: 'Story' },
    'photo': { icon: '📷', label: 'Photo' },
    'video': { icon: '▶️', label: 'Video' },
    'igtv': { icon: '📺', label: 'IGTV' },
    'carousel': { icon: '🎠', label: 'Carousel' }
  };
  const t = map[type.toLowerCase()];
  if (!t) return '';
  return `<span class="tag-${type.toLowerCase()}">${t.icon} ${t.label}</span>`;
}

function qualityBadge(fmt) {
  if (fmt.is_audio) {
    return `<span class="qual-badge">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#c084fc" stroke-width="2" aria-hidden="true">
        <path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>
      </svg>
      <span>${fmt.label}</span>
      <span class="qual-tag tag-mp3">MP3</span>
    </span>`;
  }
  const h = fmt.height || 0;
  let tag = `<span class="qual-tag tag-sd">SD</span>`;
  if (h >= 2160)     tag = `<span class="qual-tag tag-4k">4K</span>`;
  else if (h >= 720)  tag = `<span class="qual-tag tag-hd">HD</span>`;
  return `<span class="qual-badge"><span>${fmt.label}</span> ${tag}</span>`;
}

function buildTable(formats) {
  qualityTbody.innerHTML = '';

  formats.forEach(fmt => {
    const tr = document.createElement('tr');
    tr.id = `row-${fmt.format_id}`;
    tr.innerHTML = `
      <td>${qualityBadge(fmt)}</td>
      <td class="fmt-cell">${fmt.is_audio ? 'MP3' : 'MP4'}</td>
      <td class="size-cell">${fmtSize(fmt.filesize)}</td>
      <td class="text-right">
        <button
          class="dl-row-btn"
          id="dl-btn-${fmt.format_id}"
          data-format-id="${fmt.format_id}"
          aria-label="Download ${fmt.label}"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="7 10 12 15 17 10"/>
            <line x1="12" y1="15" x2="12" y2="3"/>
          </svg>
          <span>Download</span>
        </button>
      </td>
    `;
    qualityTbody.appendChild(tr);
  });

  qualityTbody.querySelectorAll('.dl-row-btn').forEach(btn => {
    btn.addEventListener('click', () => triggerChromeDownload(btn.dataset.formatId, btn));
  });
}

// ─────────────────────────────────────────────────────────────
// Analyze URL
// ─────────────────────────────────────────────────────────────
async function analyzeUrl(url) {
  currentUrl = url.trim();
  setAnalyzing(true);
  hideAll();
  loadingBox.classList.remove('hidden');
  highlightTab(currentUrl);

  document.getElementById('result-section').scrollIntoView({ behavior: 'smooth' });

  try {
    const res  = await fetch('/api/analyze', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ url: currentUrl }),
    });
    const json = await res.json();
    if (!res.ok) throw new Error(json.detail || 'Could not fetch media info.');

    const d = json.data;

    vidThumb.src = d.thumbnail || '';
    vidThumb.alt = d.title || 'Media Preview';
    vidDuration.textContent = fmtDuration(d.duration);
    vidDuration.style.display = d.duration ? 'inline-block' : 'none';
    vidTitle.textContent    = d.title || 'Downloaded Media';
    vidUploader.textContent = d.uploader ? `By ${d.uploader.startsWith('@') ? d.uploader : '@' + d.uploader}` : '';
    vidViews.textContent    = fmtViews(d.view_count);

    const plat = d.platform || detectPlatform(currentUrl);
    vidPlatform.textContent = plat.charAt(0).toUpperCase() + plat.slice(1);
    vidPlatform.className   = `platform-tag ${platformTagClass(plat)}`;

    if (d.content_type && vidContentType) {
      vidContentType.innerHTML = contentTypeBadge(d.content_type);
      vidContentType.style.display = 'inline-block';
    } else if (vidContentType) {
      vidContentType.style.display = 'none';
    }

    buildTable(d.formats || []);

    loadingBox.classList.add('hidden');
    resultCard.classList.remove('hidden');

  } catch (err) {
    showError(err.message || 'Unable to extract link. Please check the URL and try again.');
  } finally {
    setAnalyzing(false);
  }
}

// ─────────────────────────────────────────────────────────────
// Trigger Download (Auto File Saving, No New Tabs)
// ─────────────────────────────────────────────────────────────
async function triggerChromeDownload(formatId, btnEl) {
  if (!currentUrl) return;

  const origText = btnEl.innerHTML;
  btnEl.disabled    = true;
  btnEl.textContent = 'Getting link…';

  dlProgress.classList.remove('hidden');
  dlStatusText.textContent  = '🔗 Extracting direct download link…';
  dlPercentText.textContent = '';
  dlBarFill.style.width     = '60%';
  dlSpeed.textContent       = 'Connecting to CDN…';
  dlEta.textContent         = '';
  dlProgress.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

  try {
    const res = await fetch(
      `/api/get-links?url=${encodeURIComponent(currentUrl)}&format_id=${encodeURIComponent(formatId)}`
    );
    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.detail || 'Could not extract download link.');
    }

    if (data.needs_merge) {
      dlStatusText.textContent = '⬇ Processing high-quality stream…';
      dlBarFill.style.width    = '100%';
      dlSpeed.textContent      = 'Merging audio + video. Download starting automatically.';

      const streamUrl = `/api/stream?url=${encodeURIComponent(currentUrl)}&format_id=${encodeURIComponent(formatId)}`;
      const a = document.createElement('a');
      a.href = streamUrl;
      a.download = `${data.title || 'media_video'}.mp4`;
      document.body.appendChild(a);
      a.click();
      a.remove();

    } else {
      dlStatusText.textContent = '⬇ Download started automatically!';
      dlBarFill.style.width    = '100%';
      dlSpeed.textContent      = '✅ Saving media file directly to your Downloads folder…';

      const ext  = data.ext || 'mp4';
      const downloadUrl = `/api/proxy-download?url=${encodeURIComponent(data.video_url)}&title=${encodeURIComponent(data.title || 'media_file')}&ext=${encodeURIComponent(ext)}`;

      const a = document.createElement('a');
      a.href     = downloadUrl;
      a.download = `${(data.title || 'media_file').replace(/[\\/*?:"<>|]/g, '_')}.${ext}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
    }

  } catch (err) {
    dlStatusText.textContent = '⬇ Starting download…';
    dlBarFill.style.width    = '100%';
    dlSpeed.textContent      = 'Sending to browser download manager…';

    const streamUrl = `/api/stream?url=${encodeURIComponent(currentUrl)}&format_id=${encodeURIComponent(formatId)}`;
    const a = document.createElement('a');
    a.href = streamUrl;
    a.download = '';
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    btnEl.disabled   = false;
    btnEl.innerHTML  = origText;
    setTimeout(() => { dlProgress.classList.add('hidden'); }, 7000);
  }
}

// ─────────────────────────────────────────────────────────────
// Feature Quick Selection Chips & Platform Tabs
// ─────────────────────────────────────────────────────────────
function initFeatureChips() {
  const tabIg = document.getElementById('tab-ig');
  const tabFb = document.getElementById('tab-fb');

  if (tabIg) {
    tabIg.addEventListener('click', () => {
      tabIg.classList.add('active');
      tabFb?.classList.remove('active');
      urlInput.placeholder = 'Paste Instagram link here (Reel, Story, Photo, Post)...';
      urlInput.focus();
    });
  }

  if (tabFb) {
    tabFb.addEventListener('click', () => {
      tabFb.classList.add('active');
      tabIg?.classList.remove('active');
      urlInput.placeholder = 'Paste Facebook link here (Reel, Video, Watch, Story)...';
      urlInput.focus();
    });
  }

  const chips = document.querySelectorAll('.feature-chip');
  chips.forEach(chip => {
    chip.addEventListener('click', () => {
      chips.forEach(c => c.classList.remove('active'));
      chip.classList.add('active');

      const type = chip.dataset.type;
      const typeNames = {
        reel: 'Instagram Reel',
        photo: 'Instagram Photo',
        story: 'Instagram Story',
        fb_reel: 'Facebook Reel',
        fb_video: 'Facebook Video',
        fb_watch: 'Facebook Watch Video',
        carousel: 'Instagram Carousel Post'
      };

      if (type && type.startsWith('fb_') && tabFb) {
        tabFb.classList.add('active');
        tabIg?.classList.remove('active');
      } else if (tabIg) {
        tabIg.classList.add('active');
        tabFb?.classList.remove('active');
      }

      urlInput.placeholder = `Paste ${typeNames[type] || 'Instagram or Facebook'} link here...`;
      urlInput.focus();
    });
  });
}

// ─────────────────────────────────────────────────────────────
// Event Listeners
// ─────────────────────────────────────────────────────────────
searchForm.addEventListener('submit', e => {
  e.preventDefault();
  const url = urlInput.value.trim();
  if (!url || isAnalyzing) return;
  analyzeUrl(url);
});

urlInput.addEventListener('input', () => highlightTab(urlInput.value));
urlInput.addEventListener('paste', () => setTimeout(() => highlightTab(urlInput.value), 0));

document.addEventListener('DOMContentLoaded', () => {
  urlInput.focus();
  initFeatureChips();
  const params = new URLSearchParams(window.location.search);
  const pre    = params.get('url');
  if (pre) { urlInput.value = pre; analyzeUrl(pre); }
});
