/* Apply the saved preference before the page paints. Embedded viewers inherit
 * the surrounding page's scale, so they must not multiply it a second time. */
(() => {
  'use strict';
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
    const slot = document.querySelector('[data-display-size-slot]');
    const adminTools = document.getElementById('user-tools');
    const control = document.createElement(slot ? 'section' : 'details');
    control.id = 'sg-display-size';
    if (slot) control.classList.add('sg-size-inline');
    else if (adminTools) control.classList.add('sg-size-admin');
    control.innerHTML = `
      ${slot ? '' : '<summary title="Change display size" aria-label="Display size"><span aria-hidden="true">Aa</span></summary>'}
      <div class="sg-size-panel">
        <label for="sg-size-select">Display size</label>
        <select id="sg-size-select" aria-describedby="sg-size-help">
          ${levels.map(level => `<option value="${level}">${level}%${level === 100 ? ' (default)' : ''}</option>`).join('')}
        </select>
        <p id="sg-size-help">Enlarge text and controls. Saved in this browser.</p>
        <button type="button">Reset to 100%</button>
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
    }
  });
})();
