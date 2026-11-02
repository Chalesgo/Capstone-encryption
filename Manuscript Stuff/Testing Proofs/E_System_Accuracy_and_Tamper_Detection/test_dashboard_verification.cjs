const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
class Element {
  constructor() { this.children = []; this.events = {}; this.hidden = false; this.textContent = ''; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; this.textContent = ''; }
  addEventListener(name, callback) { this.events[name] = callback; }
}
const window = {}, calls = [], pending = [];
let events = [];
const context = {
  window,
  document: {createElement: () => new Element()},
  fetch: async url => {
    calls.push(url);
    if (url.includes('verification-log/')) {
      return {ok: true, json: async () => ({note: '<script>unsafe</script>', debug_log: '[FAIL] Modified vector'})};
    }
    if (url.includes('/99/')) return new Promise(resolve => pending.push(resolve));
    return {ok: true, json: async () => ({selected_result: events.selectedResult, events})};
  },
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../contracts/static/contracts/dashboard-verification.js'), 'utf8'), context);
const resultElement = new Element(), section = new Element(), list = new Element();
const load = (logId, initialResult = '') => window.SealGuardAuditVerification.load({logId, initialResult, resultElement, section, list});
const hostWindow = {addEventListener() {}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../contracts/static/contracts/mobile-pdf-host.js'), 'utf8'), {
  window: hostWindow, URL,
  location: {href: 'http://test/dashboard/', origin: 'http://test'},
  document: {currentScript: {dataset: {viewer: '/static/contracts/mobile-pdf.html'}}, addEventListener() {}},
});
assert.equal(new URL(hostWindow.sealGuardPdfFrameUrl('/contract/1/preview/', 1, 'Report', 42)).searchParams.get('audit_log'), '42');
assert.equal(new URL(hostWindow.sealGuardPdfFrameUrl('/contract/1/preview/', 1, 'Contract')).searchParams.has('audit_log'), false);
(async () => {
  events = [{id: 1, result: 'Authentic', verified_at: 'Oct 2'}, {id: 2, result: 'Possible Modification', verified_at: 'Oct 1', is_selected: true}];
  events.selectedResult = 'Authentic';
  await load(1);
  assert.equal(resultElement.textContent, 'Authentic');
  assert.equal(section.hidden, true);
  assert.equal(list.children.length, 0);
  events.selectedResult = 'Possible Modification';
  await load(2);
  assert.equal(resultElement.textContent, 'Possible Modification');
  assert.equal(section.hidden, false);
  assert.equal(list.children.length, 1);
  assert.equal(calls.filter(url => url.includes('verification-log/')).length, 0);
  const item = list.children[0];
  item.open = true;
  await item.events.toggle();
  const detail = item.children[1];
  assert.equal(detail.children[0].textContent, '<script>unsafe</script>');
  assert.equal(detail.children[1].textContent, '[FAIL] Modified vector');
  item.open = false;
  await item.events.toggle();
  item.open = true;
  await item.events.toggle();
  assert.equal(calls.filter(url => url.includes('verification-log/')).length, 1);
  const staleRequest = load(99, 'Possible Modification');
  events.selectedResult = 'Authentic';
  await load(1);
  pending.shift()({ok: true, json: async () => ({selected_result: 'Possible Modification', events})});
  await staleRequest;
  assert.equal(section.hidden, true);
  assert.equal(resultElement.textContent, 'Authentic');
  await load(null);
  assert.equal(resultElement.textContent, 'Not evaluated');
  assert.equal(section.hidden, true);
  console.log('PASS: result display, authentic exclusion, lazy traces, safe text, cached expansion, stale-request isolation, and unevaluated activity');
})().catch(error => { console.error(error); process.exitCode = 1; });
