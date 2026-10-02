/* Apply the saved preference before the page paints. Embedded viewers inherit
 * the surrounding page's scale, so they must not multiply it a second time. */
(() => {
  'use strict';
  // The page is scaled with CSS zoom, which multiplies `position: fixed`
  // offsets but not getBoundingClientRect()/clientX/innerWidth. Convert those
  // viewport pixels to CSS pixels before assigning left/top.
  window.sgCssPx = value => {
    const zoom = parseFloat(document.documentElement.style.zoom) || 1;
    return (value / zoom) + 'px';
  };
  if (window.self !== window.top) return;
  const key = 'sealguard.displaySize';
  const levels = [100, 110, 125, 150, 175, 200];
  let size = 100;
  let select;
  const normalize = value => levels.includes(Number(value)) ? Number(value) : 100;
  function apply(value) {
    size = normalize(value);
    document.documentElement.style.zoom = String(size / 100);
    if (select) select.value = String(size);
    window.dispatchEvent(new Event('sealguard:display-size'));
  }
  try { size = normalize(localStorage.getItem(key)); } catch (_) { /* Storage may be blocked. */ }
  apply(size);
  function save(value) {
    apply(value);
    try { localStorage.setItem(key, String(size)); } catch (_) { /* Live control still works. */ }
  }
  window.addEventListener('storage', event => {
    if (event.key === key || event.key === null) apply(event.newValue);
  });
  document.addEventListener('DOMContentLoaded', () => {
    // The standalone PDF viewer keeps its own corner actions where this button sits.
    if (document.documentElement.hasAttribute('data-sg-pdf-viewer')) return;
    const slot = document.querySelector('[data-display-size-slot]');
    const adminTools = document.getElementById('user-tools');
    const control = document.createElement(slot ? 'section' : 'details');
    control.id = 'sg-display-size';
    if (slot) control.classList.add('sg-size-inline');
    else if (adminTools) control.classList.add('sg-size-admin');
    control.innerHTML = `
      ${slot ? '' : '<summary title="Change display size" aria-label="Display size"><span aria-hidden="true">Aa</span></summary>'}
      <div class="sg-size-panel">
        <label for="sg-size-select">Display Size</label>
        <div class="sg-size-controls">
          <select id="sg-size-select">
            ${levels.map(level => `<option value="${level}">${level}%</option>`).join('')}
          </select>
          <button type="button" aria-label="Reset display size to 100%" title="Reset to 100%">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 11a9 9 0 1 1 2.6 7.4"/><path d="M3 4v7h7"/></svg>
          </button>
        </div>
      </div>`;
    (slot || adminTools || document.body).appendChild(control);
    select = control.querySelector('select');
    select.value = String(size);
    select.addEventListener('change', () => save(select.value));
    control.querySelector('button').addEventListener('click', () => save(100));
    if (!slot) {
      document.addEventListener('pointerdown', event => {
        if (!control.contains(event.target)) control.open = false;
      });
      document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && control.open) {
          control.open = false;
          control.querySelector('summary').focus();
        }
      });
      // Step aside while a PDF viewer is on screen so its close/download
      // buttons in the same corner stay reachable.
      const viewerOnScreen = frame => {
        if (!/mobile-pdf\.html/.test(frame.getAttribute('src') || '')) return false;
        const box = frame.getBoundingClientRect();
        return box.width > 0 && box.height > 0 && box.left < innerWidth && box.right > 0
          && box.top < innerHeight && box.bottom > 0;
      };
      let pending = false;
      const sync = () => {
        if (pending) return;
        pending = true;
        requestAnimationFrame(() => {
          pending = false;
          const viewerOpen = [...document.querySelectorAll('iframe')].some(viewerOnScreen);
          if (viewerOpen === control.hidden) return;
          if (viewerOpen) control.open = false;
          control.hidden = viewerOpen;
        });
      };
      new MutationObserver(sync).observe(document.body, {
        subtree: true, childList: true,
        attributes: true, attributeFilter: ['src', 'class', 'style', 'hidden', 'aria-hidden'],
      });
      ['transitionend', 'animationend'].forEach(type => document.addEventListener(type, sync, true));
      window.addEventListener('resize', sync);
      sync();
    }
  });
})();
