const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const template = fs.readFileSync(path.join(__dirname, '../contracts/templates/upload.html'), 'utf8');
const source = [...template.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(match => match[1]).find(script => script.includes('uploadForm.addEventListener'));
function setup(fetch) {
  const nodes = new Map();
  class Element {
    constructor() {
      this.textContent = ''; this.style = {}; this.dataset = {}; this.events = {};
      this.hidden = true; this.disabled = false; this.attrs = {};
      const classes = new Set();
      this.classList = {add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c), toggle: (c, yes) => yes ? classes.add(c) : classes.delete(c)};
    }
    addEventListener(event, handler) { this.events[event] = handler; }
    setAttribute(key, value) { this.attrs[key] = value; }
    removeAttribute(key) { delete this.attrs[key]; }
    querySelector(selector) {
      if (selector === 'strong') return strong;
      if (selector.includes('csrfmiddlewaretoken')) return {value: 'test-token'};
      if (selector.includes('title')) return {value: 'Invalid upload'};
      return null;
    }
  }
  const strong = new Element(); strong.nextElementSibling = new Element();
  const get = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  get('contract-file').files = [{name:'invalid_upload.pdf',type:'application/pdf',size:203}];
  get('upload-btn-label').textContent = 'Upload & Encrypt';
  let submissions = 0;
  const context = {
    document: {getElementById: get, querySelector: () => null},
    window: {setTimeout: () => 1, clearTimeout() {}},
    AbortController, FormData: class {append() {}},
    HTMLFormElement: {prototype: {submit() {submissions++;}}}, fetch,
  };
  vm.runInNewContext(source, context);
  return {get, strong, submissions: () => submissions, submit: () => get('upload-form').events.submit({preventDefault() {}})};
}
(async () => {
  const invalid = setup(async () => ({ok:false,json:async () => ({success:false,error:'invalid_file',message:'This file is not a PDF.'})}));
  await invalid.submit();
  assert.equal(invalid.strong.textContent, 'This file is not a PDF.');
  assert.equal(invalid.get('upload-btn-spinner').hidden, true);
  assert.equal(invalid.get('upload-btn-label').textContent, 'Upload & Encrypt');
  assert.equal(invalid.submissions(), 0);
  assert.equal(invalid.get('upload-zone').style.display, 'block');
  const failed = setup(async () => {throw new Error('Network failure');});
  await failed.submit();
  assert.equal(failed.get('upload-btn').disabled, false);
  assert.equal(failed.get('upload-btn-spinner').hidden, true);
  assert.equal(failed.submissions(), 0);
  const timeout = setup(async () => {const error = new Error(); error.name = 'AbortError'; throw error;});
  await timeout.submit();
  assert.match(timeout.strong.textContent, /timed out/);
  assert.equal(timeout.get('upload-btn').disabled, false);
  const valid = setup(async () => ({ok:true,json:async () => ({success:true,duplicates:[]})}));
  await valid.submit();
  assert.equal(valid.submissions(), 1);
  assert.equal(valid.get('upload-btn-spinner').hidden, false);
  assert.equal(valid.get('upload-btn-label').textContent, 'Processing...');
  const duplicate = setup(async () => ({ok:true,json:async () => ({success:true,duplicates:[{title:'Existing PDF',next_version:2,match_type:'same_name'}]})}));
  await duplicate.submit();
  assert.equal(duplicate.get('duplicate-modal').classList.contains('open'), true);
  assert.equal(duplicate.get('upload-btn-spinner').hidden, true);
  assert.equal(duplicate.submissions(), 0);
  console.log('PASS: invalid-file message, button recovery, network failure, timeout, processing spinner, and duplicate modal');
})().catch(error => {console.error(error); process.exitCode=1;});
