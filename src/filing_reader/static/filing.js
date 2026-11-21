/* Runs inside the opaque-origin sandbox. Never reads parent application state. */
const pathParts = location.pathname.split('/');
const filingId = pathParts[3];
function reportSelection() {
  const selected = getSelection();
  const text = selected?.toString().replace(/\s+/g, ' ').trim();
  if (!text) return false;
  const range = selected.getRangeAt(0);
  const bounds = range.getBoundingClientRect();
  parent.postMessage({
    type: 'reader-selection', filingId, text: text.slice(0, 600),
    anchor: {left: bounds.left, top: bounds.top, bottom: bounds.bottom},
  }, '*');
  return true;
}

document.addEventListener('pointerup', reportSelection);
document.addEventListener('keyup', event => {
  if (event.shiftKey && event.key.startsWith('Arrow')) reportSelection();
});

function selectFact(element) {
  const previous = document.querySelector('.reader-selected');
  if (previous) {
    previous.classList.remove('reader-selected');
  }
  element.classList.add('reader-selected');
  const bounds = element.getBoundingClientRect();
  parent.postMessage({
    type: 'reader-fact',
    id: element.dataset.readerFact,
    filingId,
    anchor: {left: bounds.left, top: bounds.top, bottom: bounds.bottom},
  }, '*');
}
document.addEventListener('click', event => {
  if (event.target.closest('.edgars-scrollbar')) return;
  if (getSelection()?.toString().trim()) return;
  const element = event.target.closest('[data-reader-fact]');
  if (element) {
    event.preventDefault();
    selectFact(element);
  } else {
    parent.postMessage({type: 'reader-dismiss', filingId}, '*');
  }
});
document.addEventListener('keydown', event => {
  if ((event.key === 'Enter' || event.key === ' ') && event.target.matches('[data-reader-fact]')) {
    event.preventDefault();
    selectFact(event.target);
  }
});
function filingTarget(id) {
  if (typeof id !== 'string' || !/^[\w:.-]{1,160}$/.test(id)) return null;
  const element = document.getElementById(id);
  if (id.startsWith('reader-passage-')) return element?.nextElementSibling || element;
  return element;
}

let readingPosition;
let highlightedEvidence = [];
window.addEventListener('message', event => {
  if (event.source !== parent) return;
  const message = event.data;
  if (message?.type === 'reader-evidence' || message?.type === 'reader-clear-evidence') {
    if (message.filingId !== filingId) return;
    for (const element of highlightedEvidence) {
      element.classList.remove('edgars-evidence', 'edgars-evidence-active');
    }
    highlightedEvidence = [];
    if (message.type === 'reader-clear-evidence') {
      if (readingPosition && message.restore === true) {
        window.scrollTo({...readingPosition, behavior:'instant'});
      }
      readingPosition = undefined;
      return;
    }
    if (!Array.isArray(message.ids)) return;
    const ids = message.ids.slice(0, 20);
    const active = ids.includes(message.active) ? filingTarget(message.active) : null;
    highlightedEvidence = ids.map(filingTarget).filter(Boolean);
    if (!highlightedEvidence.length) return;
    readingPosition ??= {top:window.scrollY, left:window.scrollX};
    for (const element of highlightedEvidence) element.classList.add('edgars-evidence');
    if (active) {
      active.classList.add('edgars-evidence-active');
      active.scrollIntoView({behavior:'instant', block:'center'});
    }
    return;
  }
  if (event.data?.type === 'reader-restore-scroll') {
    const {top} = event.data;
    if (event.data.filingId === filingId && Number.isFinite(top) && top >= 0) {
      window.scrollTo({top, behavior:'instant'});
    }
    return;
  }
  if (event.data?.type !== 'reader-jump') return;
  const element = filingTarget(event.data.id);
  if (!element) return;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const nearby = Math.abs(element.getBoundingClientRect().top) < innerHeight * 1.5;
  element.scrollIntoView({behavior: !reduced && nearby ? 'smooth' : 'instant', block:'center'});
  if (!reduced) element.animate([{backgroundColor:'#f6df88'}, {backgroundColor:'transparent'}], {duration:1000});
});

function installScrollbars() {
  const scroller = document.scrollingElement;
  const style = document.createElement('style');
  style.textContent = `
    html { scrollbar-width: none; }
    html::-webkit-scrollbar { display: none; }
    .edgars-evidence { background-color: #fff4c7 !important; outline: 1px solid #d9b858; }
    .edgars-evidence-active { background-color: #ffe69a !important; outline: 2px solid #946800; }
    .edgars-scrollbar {
      appearance: none;
      -webkit-appearance: none;
      position: fixed;
      z-index: 2147483647;
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      background: transparent;
      direction: ltr;
      --thumb-color: #858585;
    }
    .edgars-scrollbar[hidden] { display: none; }
    .edgars-scrollbar:hover, .edgars-scrollbar:active { --thumb-color: #666; }
    .edgars-scrollbar:focus-visible { outline: 2px solid #306950; outline-offset: -2px; }
    .edgars-scrollbar::-webkit-slider-runnable-track { background: transparent; }
    .edgars-scrollbar::-moz-range-track { background: transparent; }
    .edgars-scrollbar::-moz-range-progress { background: transparent; }
    .edgars-scrollbar::-webkit-slider-thumb {
      -webkit-appearance: none;
      width: var(--thumb-width);
      height: var(--thumb-height);
      border-radius: 999px;
      background: var(--thumb-color);
    }
    .edgars-scrollbar::-moz-range-thumb {
      width: var(--thumb-width);
      height: var(--thumb-height);
      border: 0;
      border-radius: 999px;
      background: var(--thumb-color);
    }
    #edgars-scroll-y {
      writing-mode: vertical-lr;
      top: 0;
      right: 0;
      width: 14px;
      height: 100%;
      --thumb-width: 7px;
      --thumb-height: var(--thumb-length, 24px);
    }
    #edgars-scroll-x {
      bottom: 0;
      left: 0;
      width: 100%;
      height: 14px;
      --thumb-width: var(--thumb-length, 24px);
      --thumb-height: 7px;
    }
    html:has(#edgars-scroll-y:not([hidden])) { padding-right: 14px; }
    html:has(#edgars-scroll-x:not([hidden])) { padding-bottom: 14px; }
    html:has(#edgars-scroll-y:not([hidden])) #edgars-scroll-x { width: calc(100% - 14px); }
    html:has(#edgars-scroll-x:not([hidden])) #edgars-scroll-y { height: calc(100% - 14px); }
    @media (forced-colors: active) {
      .edgars-scrollbar { --thumb-color: ButtonText; }
      .edgars-scrollbar::-webkit-slider-thumb { forced-color-adjust: none; }
      .edgars-scrollbar::-moz-range-thumb { forced-color-adjust: none; }
    }
  `;

  const axes = [
    {id: 'y', label: 'Scroll filing vertically', offset: 'scrollTop', extent: 'scrollHeight', viewport: 'clientHeight'},
    {id: 'x', label: 'Scroll filing horizontally', offset: 'scrollLeft', extent: 'scrollWidth', viewport: 'clientWidth'},
  ];
  for (const axis of axes) {
    const input = document.createElement('input');
    input.type = 'range';
    input.id = 'edgars-scroll-' + axis.id;
    input.className = 'edgars-scrollbar';
    input.hidden = true;
    input.min = '0';
    input.setAttribute('aria-label', axis.label);
    input.addEventListener('input', () => { scroller[axis.offset] = input.valueAsNumber; });
    document.body.append(input);
    axis.input = input;
  }
  document.head.append(style);

  let updatePending = false;
  let geometryChanged = true;
  function scheduleGeometryUpdate() {
    geometryChanged = true;
    syncScrollbars();
  }
  function syncScrollbars() {
    if (updatePending) return;
    updatePending = true;
    requestAnimationFrame(() => {
      updatePending = false;
      if (geometryChanged) {
        geometryChanged = false;
        for (const axis of axes) {
          const {input, extent, viewport} = axis;
          axis.max = Math.max(0, scroller[extent] - scroller[viewport]);
          input.max = String(axis.max);
          input.hidden = axis.max === 0;
        }
        for (const {id, input, extent, viewport} of axes) {
          if (input.hidden) continue;
          const track = id === 'y' ? input.clientHeight : input.clientWidth;
          const proportion = scroller[viewport] / scroller[extent];
          const length = Math.min(track, Math.max(24, track * proportion));
          input.style.setProperty('--thumb-length', `${length}px`);
        }
      }
      for (const {input, offset, max} of axes) {
        if (input.hidden) continue;
        const value = Math.round(scroller[offset]);
        if (input.valueAsNumber !== value) input.value = String(value);
        const label = `${Math.round(100 * input.valueAsNumber / max)}% through filing`;
        if (input.getAttribute('aria-valuetext') !== label) input.setAttribute('aria-valuetext', label);
      }
    });
  }
  window.addEventListener('scroll', syncScrollbars, {passive:true});
  function reportScrollPosition() {
    const top = Math.round(scroller.scrollTop);
    if (scroller.clientHeight > 0) parent.postMessage({type:'reader-scroll', filingId, top}, '*');
  }
  if ('onscrollend' in document) {
    document.addEventListener('scrollend', reportScrollPosition);
  } else {
    let scrollTimer;
    window.addEventListener('scroll', () => {
      clearTimeout(scrollTimer);
      scrollTimer = setTimeout(reportScrollPosition, 150);
    }, {passive:true});
  }
  window.addEventListener('resize', scheduleGeometryUpdate);
  const observer = new ResizeObserver(scheduleGeometryUpdate);
  observer.observe(document.body);
  syncScrollbars();
}

installScrollbars();
const sections = [];
const seenSections = new Set();
for (const link of document.querySelectorAll('a[href^="#"]')) {
  const id = link.getAttribute('href').slice(1);
  const label = link.textContent.replace(/\s+/g, ' ').trim();
  if (!label || label.length > 85 || /^(?:\d+|item\s+\d+\w?\.?|part\s+[ivx]+)$/i.test(label)) continue;
  if (seenSections.has(id) || !document.getElementById(id)) continue;
  seenSections.add(id);
  sections.push({id, label});
  if (sections.length === 20) break;
}
parent.postMessage({type: 'reader-ready', filingId, sections}, '*');

document.addEventListener('keydown', event => {
  if (event.key === 'Escape') {
    parent.postMessage({type: 'reader-dismiss', filingId}, '*');
  }
});
