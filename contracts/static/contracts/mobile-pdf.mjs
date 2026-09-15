import * as pdfjs from './pdfjs/pdf.mjs';

pdfjs.GlobalWorkerOptions.workerSrc = new URL('./pdfjs/pdf.worker.mjs', import.meta.url).href;
const get = id => document.getElementById(id);
const params = new URLSearchParams(location.search);
const status = get('status'), scroller = get('pages'), stack = get('page-stack');
const controller = new AbortController();
const records = [];
let pdf, bytes, loadingTask, filename = 'document.pdf';
let pageNumber = 1, zoom = 1, painting = false, scheduled = false, paintPending = false, pinch = null;
let versionsLoading = false, versionsLoaded = false;
const integrityIcons = new Map();
const viewerStartedAt = performance.now();
const pdfDebug = (stage, details = {}) => console.info('[SealGuard PDF]', stage, {
  ...details, elapsed_ms: Math.round(performance.now() - viewerStartedAt),
});

function setIntegrityIcon(icon, valid, pending = false) {
  const state = valid === true ? 'valid' : valid === false ? 'invalid' : 'pending';
  const description = valid === true ? 'Integrity check passed' : valid === false
    ? 'Integrity check failed: fingerprint or previous-version link mismatch, or file unreadable'
    : pending ? 'Checking version integrity' : 'Integrity not checked';
  icon.className = 'version-integrity-icon ' + state;
  icon.textContent = valid === true ? '✓' : valid === false ? '✕' : '…';
  icon.title = description;
  icon.setAttribute('aria-label', description);
}


function setTitle(title) {
  get('document-title').textContent = title;
  get('details-title').textContent = title;
  get('document-title').title = title;
  document.title = title + ' — SealGuard';
}
setTitle(params.get('title') || 'Document');

function panel(mode) {
  const open = Boolean(mode), details = mode === 'details';
  document.body.classList.toggle('versions-open', mode === 'versions');
  document.body.classList.toggle('details-open', details);
  get('version-sheet').inert = mode !== 'versions';
  get('pdf-sidebar').inert = !details;
  get('versions-toggle').setAttribute('aria-expanded', String(mode === 'versions'));
  get('details-toggle').setAttribute('aria-expanded', String(details));
  if (open) get(details ? 'details-versions' : 'sheet-versions').append(get('version-content'));
  scroller.inert = open;
  document.querySelector('.floating-controls').inert = open;
  document.querySelector('.corner-actions').inert = open;
  (open ? get(details ? 'close-details' : 'close-versions') : get('details-toggle')).focus();
  if (open && !versionsLoaded) loadVersions();
}
function sheet(open) { panel(open ? 'versions' : null); if (!open) get('versions-toggle').focus(); }
get('details-toggle').onclick = () => panel('details');
get('close-details').onclick = () => panel(null);
get('details-backdrop').onclick = () => panel(null);
get('versions-toggle').onclick = () => sheet(true);
get('close-versions').onclick = () => sheet(false);
get('version-backdrop').onclick = () => sheet(false);
get('integrity-help-toggle').onclick = () => {
  const help = get('integrity-help-panel');
  const open = help.hidden;
  help.hidden = !open;
  get('integrity-help-toggle').setAttribute('aria-expanded', String(open));
};
get('retry-versions').onclick = () => loadVersions();
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') {
    if (document.body.classList.contains('versions-open') || document.body.classList.contains('details-open')) panel(null);
    else get('close').click();
  }
  if (event.key === 'Tab' && (document.body.classList.contains('versions-open') || document.body.classList.contains('details-open'))) {
    const activePanel = get(document.body.classList.contains('details-open') ? 'pdf-sidebar' : 'version-sheet');
    const items = [...activePanel.querySelectorAll('button:not([hidden]):not(:disabled)')];
    const first = items[0], last = items.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
});

async function loadVersions() {
  if (versionsLoading) return;
  const contractId = params.get('contract');
  if (!contractId || !/^\d+$/.test(contractId)) {
    get('version-message').textContent = 'This preview has no linked revision history.';
    return;
  }
  versionsLoading = true;
  const startedAt = performance.now();
  pdfDebug('version_metadata_started', {contract_id: contractId});
  get('retry-versions').hidden = true;
  get('version-message').textContent = 'Loading versions…';
  try {
    const response = await fetch('/contract/' + contractId + '/version-history/?metadata_only=1', {credentials:'same-origin', signal:controller.signal});
    if (!response.ok) throw new Error();
    const data = await response.json();
    if (!Array.isArray(data.versions)) throw new Error();
    pdfDebug('version_metadata_completed', {
      contract_id: contractId,
      version_count: data.versions.length,
      duration_ms: Math.round(performance.now() - startedAt),
    });
    if (data.title) setTitle(data.title);
    get('details-created').textContent = data.created || '—';
    get('details-encrypted').textContent = data.encrypted || '—';
    get('details-verified').textContent = data.verified || 'Not yet verified';
    get('details-uploader').textContent = 'Unknown account';
    get('details-version').textContent = 'Opened document';
    const source = new URL(params.get('file'), location.origin);
    source.hash = '';
    const fragment = document.createDocumentFragment();
    integrityIcons.clear();
    for (const version of data.versions) {
      if (!version.preview_url) continue;
      const selected = [version.preview_url, version.file_url].some(url => url && new URL(url, location.origin).href === source.href)
        || (version.is_current && /^\/contract\/\d+\/(preview|download)\/$/.test(source.pathname));
      const button = document.createElement('button');
      button.className = 'version-option';
      button.setAttribute('aria-current', String(selected));
      const icon = document.createElement('span');
      icon.setAttribute('role', 'img');
      setIntegrityIcon(icon, null);
      integrityIcons.set(version.version_number, icon);
      const details = document.createElement('div');
      details.className = 'version-option-details';
      const label = document.createElement('strong');
      label.textContent = 'v' + version.version_number + ' — ' + (version.source || 'Document version') + (version.is_current ? ' (Current)' : '');
      const date = document.createElement('span');
      date.className = 'version-option-date';
      date.textContent = version.created_at;
      const account = document.createElement('span');
      account.className = 'version-option-account';
      account.textContent = 'Added by ' + (version.created_by || 'Unknown account');
      details.append(label, date, account);
      button.append(icon, details);
      if (selected) {
        get('versions-toggle').textContent = 'v' + version.version_number;
        get('details-version').textContent = 'v' + version.version_number;
        get('details-uploader').textContent = version.created_by || 'Unknown account';
      }
      button.onclick = () => {
        if (selected) { sheet(false); return; }
        const target = new URL(location.href);
        const file = new URL(version.preview_url, location.origin);
        if (file.origin !== location.origin) return;
        target.searchParams.set('file', file.href);
        target.searchParams.set('title', data.title || get('document-title').textContent);
        target.searchParams.set('version', version.version_number);
        if (document.body.classList.contains('details-open')) target.searchParams.set('details', '1');
        else target.searchParams.delete('details');
        location.replace(target.href);
      };
      fragment.append(button);
    }
    get('version-list').replaceChildren(fragment);
    versionsLoaded = true;
    get('version-message').textContent = data.versions.length ? '' : 'No saved versions available.';
  } catch (_) {
    pdfDebug('version_metadata_failed', {contract_id: contractId, duration_ms: Math.round(performance.now() - startedAt)});
    get('version-message').textContent = 'Version history could not be loaded. Please try again.';
    get('retry-versions').hidden = false;
  } finally { versionsLoading = false; }
}
if (params.get('version')) get('versions-toggle').textContent = 'v' + params.get('version');
loadVersions();
if (params.get('details') === '1') panel('details');

function controls() {
  get('previous').disabled = !pdf || pageNumber <= 1;
  get('next').disabled = !pdf || pageNumber >= records.length;
  get('page-label').textContent = pdf ? 'Page ' + pageNumber + ' of ' + pdf.numPages : 'Loading pages…';
}
function layout() {
  const width = Math.max(160, scroller.clientWidth - 24) * zoom;
  stack.style.width = width + 'px';
  for (const record of records) {
    record.box.style.width = width + 'px';
    record.box.style.height = width * record.ratio + 'px';
  }
}
function currentRecord(y = scroller.scrollTop + scroller.clientHeight * .35) {
  return records.find(record => record.box.offsetTop + stack.offsetTop + record.box.offsetHeight > y) || records.at(-1);
}
function updatePage() {
  const record = currentRecord();
  if (record) pageNumber = record.number;
  controls();
}
function schedulePaint() {
  if (scheduled) return;
  scheduled = true;
  requestAnimationFrame(() => { scheduled = false; updatePage(); paint(); });
}
async function paint() {
  if (painting) { paintPending = true; return; }
  if (pinch || !pdf || controller.signal.aborted) return;
  paintPending = false;
  painting = true;
  try {
    for (const record of records) {
      if (pinch || controller.signal.aborted) break;
      const top = record.box.offsetTop + stack.offsetTop;
      const nearby = top < scroller.scrollTop + scroller.clientHeight * 2 && top + record.box.offsetHeight > scroller.scrollTop - scroller.clientHeight;
      if (!nearby) {
        // Keep layout placeholders, but release offscreen bitmap memory.
        if (record.canvas) { record.canvas.width = 0; record.canvas.height = 0; record.canvas.remove(); record.canvas = null; record.scale = null; }
        continue;
      }
      const width = record.box.clientWidth;
      if (record.scale === width || record.failed) continue;
      try {
        const page = await pdf.getPage(record.number);
        const viewport = page.getViewport({scale:width / page.getViewport({scale:1}).width});
        const ratio = Math.min(devicePixelRatio || 1, 2, Math.sqrt(4000000 / (viewport.width * viewport.height)));
        const canvas = document.createElement('canvas');
        canvas.width = Math.floor(viewport.width * ratio);
        canvas.height = Math.floor(viewport.height * ratio);
        await page.render({canvasContext:canvas.getContext('2d'),viewport,transform:[ratio,0,0,ratio,0,0]}).promise;
        if (record.canvas) { record.canvas.width = 0; record.canvas.height = 0; record.canvas.remove(); }
        record.canvas = canvas;
        record.box.append(canvas);
        record.scale = width;
      } catch (_) {
        record.failed = true;
        record.box.setAttribute('aria-label', 'Page ' + record.number + ' could not be displayed. Download the PDF to view it.');
      }
    }
  } finally {
    painting = false;
    if (!controller.signal.aborted && !pinch && (paintPending || records.some(record => !record.failed && record.scale !== record.box.clientWidth && Math.abs(record.box.offsetTop + stack.offsetTop - scroller.scrollTop) < scroller.clientHeight))) schedulePaint();
  }
}
scroller.addEventListener('scroll', schedulePaint, {passive:true});
function goPage(number) {
  const record = records[number - 1];
  if (record) scroller.scrollTo({top:record.box.offsetTop + stack.offsetTop - 12, behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
}
get('previous').onclick = () => goPage(Math.max(1, pageNumber - 1));
get('next').onclick = () => goPage(Math.min(records.length, pageNumber + 1));

function touches(event) {
  const [a,b] = event.touches;
  const rect = scroller.getBoundingClientRect();
  return {distance:Math.hypot(a.clientX-b.clientX,a.clientY-b.clientY),x:(a.clientX+b.clientX)/2-rect.left,y:(a.clientY+b.clientY)/2-rect.top};
}
scroller.addEventListener('touchstart', event => {
  if (event.touches.length !== 2 || !records.length) return;
  event.preventDefault();
  const point = touches(event);
  const record = currentRecord(scroller.scrollTop + point.y);
  pinch = {distance:Math.max(1,point.distance),zoom,record,fraction:(scroller.scrollTop+point.y-record.box.offsetTop-stack.offsetTop)/record.box.offsetHeight,x:(scroller.scrollLeft+point.x)/zoom};
}, {passive:false});
scroller.addEventListener('touchmove', event => {
  if (!pinch || event.touches.length !== 2) return;
  event.preventDefault();
  const point = touches(event);
  zoom = Math.max(1,Math.min(4,pinch.zoom*point.distance/pinch.distance));
  layout();
  scroller.scrollTop = pinch.record.box.offsetTop + stack.offsetTop + pinch.fraction*pinch.record.box.offsetHeight-point.y;
  scroller.scrollLeft = pinch.x*zoom-point.x;
  updatePage();
}, {passive:false});
function endPinch(event) { if (pinch && event.touches.length < 2) { pinch = null; schedulePaint(); } }
scroller.addEventListener('touchend', endPinch);
scroller.addEventListener('touchcancel', endPinch);
let resizeTimer;
window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { layout(); goPage(pageNumber); schedulePaint(); },150); });
get('close').onclick = () => parent.postMessage({type:'sealguard-pdf-close'}, location.origin);
if (params.get('embedded') === '1') get('close').hidden = true;
get('download').onclick = () => {
  if (!bytes) return;
  const url = URL.createObjectURL(new Blob([bytes],{type:'application/pdf'}));
  const link = document.createElement('a');
  link.href = url; link.download = filename;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url),60000);
};
window.addEventListener('pagehide', () => { controller.abort(); loadingTask?.destroy(); });

try {
  const fetchStartedAt = performance.now();
  const source = new URL(params.get('file') || '',location.origin);
  if (!params.get('file') || source.origin !== location.origin || !['http:','https:','blob:'].includes(source.protocol)) throw new Error('Invalid source');
  source.hash = '';
  const response = await fetch(source.href,{credentials:'same-origin',signal:controller.signal});
  if (!response.ok) throw new Error('Document unavailable');
  bytes = new Uint8Array(await response.arrayBuffer());
  pdfDebug('pdf_bytes_loaded', {url: source.href, bytes: bytes.byteLength, duration_ms: Math.round(performance.now() - fetchStartedAt)});
  if (!new TextDecoder().decode(bytes.subarray(0,1024)).includes('%PDF-')) throw new Error('Not a PDF');
  const disposition = response.headers.get('Content-Disposition') || '';
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i), plain = disposition.match(/filename="([^"]+)"/i);
  filename = ((encoded ? decodeURIComponent(encoded[1]) : plain?.[1]) || filename).replace(/[\\/]/g,'_');
  if (!params.get('title') && get('document-title').textContent === 'Document') setTitle(filename.replace(/\.pdf$/i,''));
  loadingTask = pdfjs.getDocument({data:bytes.slice(),isEvalSupported:false,
    cMapUrl:new URL('./pdfjs/cmaps/',import.meta.url).href,cMapPacked:true,
    standardFontDataUrl:new URL('./pdfjs/standard_fonts/',import.meta.url).href,
    wasmUrl:new URL('./pdfjs/wasm/',import.meta.url).href});
  pdf = await loadingTask.promise;
  pdfDebug('pdf_document_loaded', {page_count: pdf.numPages, duration_ms: Math.round(performance.now() - fetchStartedAt)});
  get('download').disabled = false;
  const first = await pdf.getPage(1);
  const base = first.getViewport({scale:1});
  for (let number=1;number<=pdf.numPages;number++) {
    const box = document.createElement('section');
    box.className = 'pdf-page'; box.setAttribute('aria-label','Page '+number);
    stack.append(box);
    records.push({number,box,ratio:base.height/base.width,scale:null,canvas:null});
  }
  layout(); controls(); status.textContent = ''; schedulePaint();
  // Resolve mixed page sizes without holding every rendered page in memory.
  for (const record of records) {
    if (controller.signal.aborted) break;
    const page = await pdf.getPage(record.number);
    const viewport = page.getViewport({scale:1});
    record.ratio = viewport.height/viewport.width;
  }
  layout(); schedulePaint();
  pdfDebug('pdf_view_ready', {page_count: pdf.numPages, duration_ms: Math.round(performance.now() - fetchStartedAt)});
} catch (_) {
  pdfDebug('pdf_load_failed');
  status.textContent = 'Unable to open this PDF. Close the viewer and try again, or check that you are signed in.';
}
