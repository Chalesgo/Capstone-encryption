// Isolated viewer behavior checks. Run: node scripts/test_mobile_pdf.cjs
// This models DOM geometry and PDF.js; it does not replace real-phone QA.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');

async function run(versionCount = 250, failHistory = false, failIntegrity = false, autoDetails = false) {
  const nodes = new Map(), frames = [], listeners = {};
  class Element {
    constructor(id = '') {
      this.id = id; this.style = {}; this.children = []; this.attrs = {};
      this.events = {}; this.textContent = ''; this.disabled = false; this.hidden = false;
      this.scrollTop = 0; this.scrollLeft = 0;
      const classes = new Set();
      this.classList = {toggle: (name, value) => value ? classes.add(name) : classes.delete(name), contains: name => classes.has(name)};
    }
    get clientWidth() { return this.id === 'pages' ? 390 : parseFloat(this.style.width) || 366; }
    get clientHeight() { return this.id === 'pages' ? 844 : this.offsetHeight; }
    get offsetHeight() { return parseFloat(this.style.height) || 0; }
    get offsetTop() {
      if (this.id === 'page-stack') return 64;
      if (!this.parent) return 0;
      return this.parent.children.slice(0, this.parent.children.indexOf(this)).reduce((sum, item) => sum + item.offsetHeight + 14, 0);
    }
    append(...items) {
      for (const item of items) {
        if (item.fragment) { this.append(...item.children); continue; }
        this.children.push(item); item.parent = this;
      }
    }
    replaceChildren(...items) { this.children = []; this.append(...items); }
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(item => item !== this); }
    setAttribute(key, value) { this.attrs[key] = value; }
    toggleAttribute(key, value) { if (value) this.attrs[key] = ''; else delete this.attrs[key]; }
    addEventListener(name, callback) { this.events[name] = callback; }
    focus() { document.activeElement = this; }
    click() { this.onclick?.(); }
    getContext() { return {}; }
    getBoundingClientRect() { return {left:0, top:0}; }
    scrollTo({top}) { this.scrollTop = top; this.events.scroll?.(); }
    querySelectorAll() { return this.children; }
  }
  const get = id => { if (!nodes.has(id)) nodes.set(id, new Element(id)); return nodes.get(id); };
  const document = {
    getElementById:get, querySelector:get, body:new Element('body'),
    createElement:() => new Element(),
    createDocumentFragment:() => Object.assign(new Element(), {fragment:true}),
    addEventListener:(name, callback) => { listeners[name] = callback; },
  };
  let destination;
  const location = {origin:'http://test', search:'?file=http%3A%2F%2Ftest%2Fcontract%2F7%2Fpreview%2F&contract=7&title=Contract',
    href:'http://test/viewer?contract=7', replace:url => { destination = new URL(url); }};
  if (autoDetails) location.search += '&details=1';
  const versions = Array.from({length:versionCount}, (_, i) => ({
    version_number:versionCount-i, is_current:i===0, created_by:i ? 'clerk' : 'admin',
    preview_url:'/contract/version/'+(versionCount-i)+'/preview/',
    file_url:'/contract/version/'+(versionCount-i)+'/download/', created_at:'Sep 15, 2026',
    source:'New Revision Upload', valid:i === 0 ? true : i === 1 ? false : null,
  }));
  const pdf = {numPages:20, getPage:async number => ({
    getViewport:({scale}) => ({width:600*scale, height:(number === 2 ? 600 : 800)*scale}),
    render:() => ({promise:Promise.resolve()}),
  })};
  const scope = vm.createContext({
    document, location, URL, URLSearchParams, AbortController, TextDecoder, Uint8Array,
    devicePixelRatio:2, Blob, performance:{now:() => Date.now()}, setTimeout:() => 1, clearTimeout:() => {},
    requestAnimationFrame:callback => frames.push(callback),
    matchMedia:() => ({matches:true}), parent:{postMessage:() => {}},
    window:{addEventListener:() => {}},
    pdfjs:{GlobalWorkerOptions:{},getDocument:() => ({promise:Promise.resolve(pdf), destroy:() => {}})},
    fetch:async url => url.includes('version-history') ? {
      ok:!failHistory && !(failIntegrity && !url.includes('metadata_only')), json:async () => ({title:'01_basic_contract',versions}),
    } : {ok:true,headers:{get:()=>'attachment; filename="contract.pdf"'}, arrayBuffer:async () => Buffer.from('%PDF-fixture')},
  });
  let source = fs.readFileSync(path.join(root,'contracts/static/contracts/mobile-pdf.mjs'),'utf8');
  source = source.replace(/^import .*;\r?\n/, '').replaceAll('import.meta.url', "'http://test/static/mobile-pdf.mjs'");
  await vm.runInContext('(async()=>{'+source+'\nglobalThis.check={records,updatePage,paint,layout,getZoom:()=>zoom};})()',scope);
  async function settle() {
    for (let i=0;i<30;i++) { const callbacks=frames.splice(0); callbacks.forEach(fn=>fn()); await new Promise(resolve=>setImmediate(resolve)); }
  }
  await settle();
  if (autoDetails) {
    assert(document.body.classList.contains('details-open'),'Details entry opens sidebar automatically');
    assert.equal(get('details-uploader').textContent,'admin');
    get('close-details').click();
  }
  const {records,updatePage}=scope.check;
  assert.equal(records.length,20);
  assert.equal(records[1].ratio,1, 'mixed page sizes retain their proportions');
  assert.match(get('page-label').textContent,/Page 1 of 20/);
  get('pages').scrollTop=records[5].box.offsetTop+64;
  updatePage(); assert.match(get('page-label').textContent,/Page 6 of 20/);
  get('next').click(); await settle(); assert.match(get('page-label').textContent,/Page 7 of 20/);
  get('previous').click(); await settle(); assert.match(get('page-label').textContent,/Page 6 of 20/);
  assert(records.filter(record=>record.canvas).length < 10, 'offscreen pages do not retain all canvases');
  const position=get('pages').scrollTop;
  if (!failHistory && !autoDetails) assert.equal(get('version-list').children[0].children[0].textContent,'…','metadata alone must not imply integrity passed');
  get('versions-toggle').click();
  await settle();
  if (!failHistory) {
    assert.equal(get('version-list').children[0].children[0].textContent,'…','viewer must not verify integrity while opening revisions');
    if (versionCount > 1) assert.equal(get('version-list').children[1].children[0].textContent,'…');
    if (versionCount > 2) assert.equal(get('version-list').children[2].children[0].textContent,'…');
  }
  assert(document.body.classList.contains('versions-open'));
  assert.equal(get('version-sheet').inert,false);
  get('close-versions').click();
  await settle();
  assert(!document.body.classList.contains('versions-open'));
  assert.equal(get('pages').scrollTop,position,'closing sheet preserves reading position');
  if (failHistory) {
    assert.equal(get('retry-versions').hidden,false);
    assert.match(get('version-message').textContent,/could not be loaded/);
  } else {
    assert.equal(get('version-list').children.length,versionCount);
    assert.equal(get('versions-toggle').textContent,'v'+versionCount);
    assert.equal(get('document-title').textContent,'01_basic_contract');
    get('version-list').children.at(-1).click();
    assert.equal(destination.searchParams.get('contract'),'7');
    assert.equal(destination.searchParams.get('file'),'http://test/contract/version/1/preview/');
    assert.equal(destination.searchParams.get('version'),'1');
    get('details-toggle').click();
    assert(document.body.classList.contains('details-open'));
    assert.equal(get('version-content').parent,get('details-versions'),'sidebar uses same revision list');
    assert.equal(get('details-uploader').textContent,'admin');
    get('version-list').children.at(-1).click();
    assert.equal(destination.searchParams.get('details'),'1','revision selection keeps sidebar open');
    get('close-details').click();
    assert.equal(get('pages').scrollTop,position);
    assert.equal(get('pages').inert,false);
  }
  const event=(left,right)=>({touches:[{clientX:left,clientY:300},{clientX:right,clientY:300}],preventDefault(){}});
  get('pages').events.touchstart(event(145,245));
  get('pages').events.touchmove(event(95,295));
  assert.equal(scope.check.getZoom(),2);
  assert.equal(records[0].box.clientWidth,732);
  get('pages').events.touchend({touches:[]}); await settle();
  get('pages').events.touchstart(event(95,295));
  get('pages').events.touchmove(event(190,200));
  assert.equal(scope.check.getZoom(),1,'pinch cannot shrink below fit width');
  get('pages').events.touchend({touches:[]}); await settle();
  console.log('PASS: scrolling, page arrows/counter, bounded canvases, pinch, sheet cancellation, '+(failHistory ? 'history error' : versionCount+' revisions and selection'));
}
(async()=>{
  await run();
  await run(2);
  await run(0,true);
  await run(3,false,true);
  await run(2,false,false,true);
  const html=fs.readFileSync(path.join(root,'contracts/static/contracts/mobile-pdf.html'),'utf8');
  assert.match(html, /max-height:50dvh/);
  assert.match(html, /#version-list \{ overflow-y:auto/);
  assert(!html.includes('id="in"') && !html.includes('id="out"'));
  const list=fs.readFileSync(path.join(root,'contracts/templates/list.html'),'utf8');
  const headings=[...list.matchAll(/<th(?:\s[^>]*)?>([\s\S]*?)<\/th>/g)].map(match=>match[1]);
  assert.equal(headings[1],'Document Name');
  assert.equal(headings[2],'Uploaded by');
  assert(!list.includes('folder-heading-help'));
  const title={textContent:''};
  const icon={hiddenAttribute:true,toggleAttribute(name,value){this.hiddenAttribute=value;}};
  const folder={textContent:'Procurement'};
  const headingContext=vm.createContext({currentFolderFilter:7,headingOriginalName:null,document:{getElementById:id=>({'contracts-title':title,'folder-page-icon':icon,'folder-name-7':folder}[id])}});
  const headingCode=list.slice(list.indexOf('function updateFolderHeading()'),list.indexOf('function editFolderHeading()'));
  vm.runInContext(headingCode+';updateFolderHeading();',headingContext);
  assert.equal(icon.hiddenAttribute,false,'folder SVG hidden attribute is removed');
  assert.equal(title.textContent,'Procurement');
  headingContext.currentFolderFilter=null;
  vm.runInContext('updateFolderHeading()',headingContext);
  assert.equal(icon.hiddenAttribute,true);
  assert.equal(title.textContent,'All Documents');
  console.log('PASS: half-screen sheet, no zoom buttons, uploader third column, no folder text label');
  const elements=[];
  const hostWindow={addEventListener(){}};
  const hostDocument={
    currentScript:{dataset:{viewer:'/static/contracts/mobile-pdf.html?v=scroll-2'}},
    activeElement:{focus(){}},body:{style:{},append(){}},addEventListener(){},
    createElement:tag=>{const element={tag,style:{},setAttribute(){},append(){},focus(){}};elements.push(element);return element;},
  };
  const host=vm.createContext({URL,location:{href:'http://test/contracts/',origin:'http://test'},window:hostWindow,document:hostDocument,matchMedia:()=>({matches:true})});
  vm.runInContext(fs.readFileSync(path.join(root,'contracts/static/contracts/mobile-pdf-host.js'),'utf8'),host);
  assert.equal(hostWindow.SealGuardPdf.open('/contract/7/preview/',7,'Purchase document'),true);
  let frameUrl=new URL(elements.find(element=>element.tag==='iframe').src);
  assert.equal(frameUrl.searchParams.get('contract'),'7');
  assert.equal(frameUrl.searchParams.get('title'),'Purchase document');
  assert.equal(frameUrl.searchParams.has('embedded'),false);
  hostWindow.SealGuardPdf.open('/contract/version/42/preview/',7,'Purchase document');
  frameUrl=new URL(elements.find(element=>element.tag==='iframe').src);
  assert.equal(frameUrl.searchParams.get('contract'),'7');
  assert.equal(frameUrl.searchParams.get('file'),'http://test/contract/version/42/preview/');
  hostWindow.SealGuardPdf.open('/contract/7/preview/',7,'Purchase document',true);
  frameUrl=new URL(elements.find(element=>element.tag==='iframe').src);
  assert.equal(frameUrl.searchParams.get('details'),'1');
  console.log('PASS: direct View PDF and historical PDF carry revision context into fullscreen viewer');
})().catch(error=>{ console.error(error); process.exitCode=1; });
