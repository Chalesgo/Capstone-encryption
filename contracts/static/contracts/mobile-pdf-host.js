(() => {
  const viewer = new URL(document.currentScript.dataset.viewer, location.href);
  let overlay, frame, previousFocus, previousOverflow;
  window.sealGuardPdfFrameUrl = (url, contractId = null, title = '') => {
    if (!url) return url;
    const source = new URL(url, location.href);
    source.hash = '';
    const target = new URL(viewer);
    target.searchParams.set('file', source.href);
    target.searchParams.set('embedded', '1');
    const inferredContract = source.pathname.match(/^\/contract\/(\d+)\/(?:preview|download)\/$/);
    const selectedContract = contractId || inferredContract?.[1];
    if (selectedContract) target.searchParams.set('contract', selectedContract);
    if (title) target.searchParams.set('title', title);
    return target.href;
  };
  function close() {
    if (!overlay || overlay.hidden) return;
    overlay.hidden = true;
    frame.src = 'about:blank';
    document.body.style.overflow = previousOverflow;
    previousFocus?.focus();
  }
  window.SealGuardPdf = {
    close,
    open(url, contractId = null, title = '', details = false) {
      // Desktop uses the original SealGuard side-panel layout. The full-screen
      // canvas is reserved for compact/mobile layouts.
      if (!url || !matchMedia('(max-width: 900px)').matches) return false;
      if (!overlay) {
        overlay = document.createElement('div');
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.setAttribute('aria-label', 'PDF viewer');
        overlay.style.cssText = 'position:fixed;inset:0;z-index:10000;background:white;';
        overlay.hidden = true;
        frame = document.createElement('iframe');
        frame.title = 'SealGuard PDF viewer';
        frame.style.cssText = 'width:100%;height:100%;border:0;display:block;';
        overlay.append(frame);
        document.body.append(overlay);
      }
      if (overlay.hidden) {
        previousFocus = document.activeElement;
        previousOverflow = document.body.style.overflow;
      }
      const fullScreenUrl = new URL(window.sealGuardPdfFrameUrl(url, contractId, title));
      console.info('[SealGuard PDF] viewer_open', {contractId, url, details});
      const sourcePath = new URL(url, location.href).pathname;
      const inferredContract = sourcePath.match(/^\/contract\/(\d+)\/(?:preview|download)\/$/);
      const selectedContract = contractId || inferredContract?.[1];
      if (selectedContract) fullScreenUrl.searchParams.set('contract', selectedContract);
      fullScreenUrl.searchParams.delete('embedded');
      if (details) fullScreenUrl.searchParams.set('details', '1');
      frame.src = fullScreenUrl.href;
      overlay.hidden = false;
      document.body.style.overflow = 'hidden';
      frame.focus();
      return true;
    }
  };
  window.addEventListener('message', event => {
    if (event.origin !== location.origin || event.data?.type !== 'sealguard-pdf-close') return;
    if (frame && event.source === frame.contentWindow) { close(); return; }
    for (const [id, handler] of [['pdf-frame', 'closePdfPreview'], ['audit-pdf-frame', 'closeAuditPdf'], ['public-pdf-frame', 'closePublicPdfPreview']]) {
      const embedded = document.getElementById(id);
      if (embedded && event.source === embedded.contentWindow && typeof window[handler] === 'function') { window[handler](); return; }
    }
  });
  document.addEventListener('click', event => {
    const link = event.target.closest('#doc-preview-open-link');
    if (link && window.SealGuardPdf.open(link.href)) event.preventDefault();
  });
  window.addEventListener('pagehide', close);
})();
