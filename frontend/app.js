'use strict';

// ─────────────────────────────────────────────────────────────
// DOM Elements
// ─────────────────────────────────────────────────────────────
const urlInput     = document.getElementById('url-input');
const searchForm   = document.getElementById('search-form');
const btnText      = document.getElementById('btn-text');
const downloadBtn  = document.getElementById('download-btn');

const loadingBox   = document.getElementById('loading-box');
const errorBox     = document.getElementById('error-box');
const errorMsg     = document.getElementById('error-msg');
const resultCard   = document.getElementById('result-card');

const vidThumb     = document.getElementById('vid-thumb');
const vidDuration  = document.getElementById('vid-duration');
const vidPlatform  = document.getElementById('vid-platform');
const vidTitle     = document.getElementById('vid-title');
const vidUploader  = document.getElementById('vid-uploader');
const vidViews     = document.getElementById('vid-views');

const dlProgress    = document.getElementById('dl-progress');
const dlStatusText  = document.getElementById('dl-status-text');
const dlPercentText = document.getElementById('dl-percent-text');
const dlBarWrap     = document.getElementById('dl-bar-wrap');
const dlBarFill     = document.getElementById('dl-bar-fill');
const dlSpeed       = document.getElementById('dl-speed');
const dlEta         = document.getElementById('dl-eta');

const qualityTbody  = document.getElementById('quality-tbody');

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
  if (/youtube\.com|youtu\.be/i.test(url))           return 'youtube';
  if (/instagram\.com/i.test(url))                   return 'instagram';
  if (/facebook\.com|fb\.com|fb\.watch/i.test(url))  return 'facebook';
  return 'unknown';
}

function platformTagClass(p) {
  return { youtube: 'tag-youtube', instagram: 'tag-instagram', facebook: 'tag-facebook' }[p] || 'tag-unknown';
}

// ─────────────────────────────────────────────────────────────
// UI Control
// ─────────────────────────────────────────────────────────────
function hideAll() {
  loadingBox.hidden = true;
  errorBox.hidden   = true;
  resultCard.hidden = true;
  dlProgress.hidden = true;
}

function showError(msg) {
  hideAll();
  errorMsg.textContent = msg;
  errorBox.hidden = false;
  document.getElementById('result-section').scrollIntoView({ behavior: 'smooth' });
}

function setAnalyzing(v) {
  isAnalyzing = v;
  downloadBtn.disabled = v;
  btnText.textContent  = v ? 'Analyzing…' : 'Download';
}

function highlightTab(url) {
  ['tab-yt', 'tab-ig', 'tab-fb'].forEach(id => document.getElementById(id).classList.remove('active'));
  const map = { youtube: 'tab-yt', instagram: 'tab-ig', facebook: 'tab-fb' };
  const p = detectPlatform(url);
  if (map[p]) document.getElementById(map[p]).classList.add('active');
}

// ─────────────────────────────────────────────────────────────
// Quality Badges & Table
// ─────────────────────────────────────────────────────────────
function qualityBadge(fmt) {
  if (fmt.is_audio) {
    return `<span class="qual-badge">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#553c9a" stroke-width="2" aria-hidden="true">
        <path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>
      </svg>
      ${fmt.label} <span class="qual-tag tag-mp3">MP3</span>
    </span>`;
  }
  const h = fmt.height || 0;
  let tag = `<span class="qual-tag tag-sd">SD</span>`;
  if (h >= 2160)    tag = `<span class="qual-tag tag-4k">4K</span>`;
  else if (h >= 720) tag = `<span class="qual-tag tag-hd">HD</span>`;
  return `<span class="qual-badge">${fmt.label} ${tag}</span>`;
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
      <td>
        <button
          class="dl-row-btn"
          id="dl-btn-${fmt.format_id}"
          data-format-id="${fmt.format_id}"
          aria-label="Download ${fmt.label}"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="7 10 12 15 17 10"/>
            <line x1="12" y1="15" x2="12" y2="3"/>
          </svg>
          Download
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
  loadingBox.hidden = false;
  highlightTab(currentUrl);

  document.getElementById('result-section').scrollIntoView({ behavior: 'smooth' });

  try {
    const res  = await fetch('/api/analyze', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ url: currentUrl }),
    });
    const json = await res.json();
    if (!res.ok) throw new Error(json.detail || 'Could not fetch video info.');

    const d = json.data;

    vidThumb.src = d.thumbnail || '';
    vidThumb.alt = d.title;
    vidDuration.textContent = fmtDuration(d.duration);
    vidDuration.hidden      = !d.duration;
    vidTitle.textContent    = d.title;
    vidUploader.textContent = d.uploader || '';
    vidViews.textContent    = fmtViews(d.view_count);

    const plat = d.platform || 'unknown';
    vidPlatform.textContent = plat.charAt(0).toUpperCase() + plat.slice(1);
    vidPlatform.className   = `platform-tag ${platformTagClass(plat)}`;

    buildTable(d.formats || []);

    loadingBox.hidden  = true;
    resultCard.hidden  = false;

  } catch (err) {
    showError(err.message || 'Something went wrong. Please try again.');
  } finally {
    setAnalyzing(false);
  }
}

// ─────────────────────────────────────────────────────────────
// Trigger Chrome Download Manager Directly
// Opens Chrome's native Download Manager with Live MB & Progress!
// ─────────────────────────────────────────────────────────────
function triggerChromeDownload(formatId, btnEl) {
  if (!currentUrl) return;

  // Show status banner
  dlProgress.hidden = false;
  dlStatusText.textContent  = '⬇ Download sent to Chrome Download Manager!';
  dlPercentText.textContent = '';
  dlBarFill.style.width     = '100%';
  dlBarFill.style.background = 'linear-gradient(90deg, #0085CF, #01C5C9)';
  dlSpeed.textContent = 'Check your browser downloads bar (top-right in Chrome) for live MB & speed!';
  dlEta.textContent   = '';
  dlProgress.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

  // Direct trigger link
  const streamUrl = `/api/stream?url=${encodeURIComponent(currentUrl)}&format_id=${encodeURIComponent(formatId)}`;

  const a = document.createElement('a');
  a.href = streamUrl;
  a.download = '';
  document.body.appendChild(a);
  a.click();
  a.remove();

  setTimeout(() => {
    dlProgress.hidden = true;
  }, 6000);
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
  const params = new URLSearchParams(window.location.search);
  const pre    = params.get('url');
  if (pre) { urlInput.value = pre; analyzeUrl(pre); }
});
