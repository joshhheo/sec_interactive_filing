function byId(id) {
  return document.getElementById(id);
}

const pendingExplanations = new Map();
let catalog = [];
let activeFiling = '';
let selection;
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function sourceLink(url, label='View source ↗') {
  const link = node('a', label, 'source');
  link.href = url;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  return link;
}
async function request(url, options) {
  const response = await fetch(url, options);
  const result = await response.json();
  if (!response.ok) throw Error(result.error || 'Request failed');
  return result;
}
function jump(id) {
  const index = evidenceIds.indexOf(id);
  if (index !== -1) {
    showEvidence(index);
    return;
  }
  byId('filing').contentWindow.postMessage({type:'reader-jump', id}, '*');
}
function section(title) {
  const element = node('section', undefined, 'section');
  element.append(node('h2', title));
  return element;
}
function showFact(data, url) {
  const fact = data.fact;
  const panel = byId('panel');
  panel.replaceChildren();
  const top = node('div', undefined, 'item-top');
  top.append(node('h1', fact.row_label || fact.title));
  top.append(node('div', data.formatted_amount ?? fact.display_value, 'amount'));
  const context = fact.context;
  const period = context.instant || [context.startdate, context.enddate].filter(Boolean).join(' → ');
  top.append(node('div', period || 'Period unavailable', 'context'));
  if (data.formatted_amount == null) {
    let unit = fact.unit.split(':').pop();
    if (fact.scale !== '0') unit += ' · displayed number × 10^'+fact.scale;
    top.append(node('div', unit + (fact.sign ? ' · negative amount' : ''), 'context'));
  }
  top.append(action('Ask a follow-up', () => setContext({
    fact_id: fact.id, label: fact.row_label || fact.title,
  }, true)));
  panel.append(top);
  if (data.corrections?.length) {
    const amendment = node('details', undefined, 'section');
    amendment.append(node('summary', 'View amendment · Figure corrected'));
    for (const correction of data.corrections) {
      const title = `${correction.form} · ${correction.filed}`;
      amendment.append(node('p', `${title} · ${correction.amount}`));
      amendment.append(openSourceButton(correction, title), sourceLink(correction.source, 'Open on EDGAR ↗'));
    }
    panel.append(amendment);
  }
  const comparison = section('Comparison');
  const baseline = data.comparison;
  if (baseline) {
    const percent = baseline.percent ?? 'N/A';
    const periodEnd = baseline.period_end;
    comparison.append(node('p', `Change from ${periodEnd} (${baseline.basis}): ${baseline.amount_change} (${percent}) · Prior value ${baseline.baseline_amount}`));
    for (const source of baseline.sources) {
      comparison.append(sourceLink(source, 'Prior figure ↗'));
    }
  } else {
    comparison.append(node('p', 'N/A', 'muted'));
  }
  panel.append(comparison);

  const disclosures = section('Relevant context');
  const evidence = new Map();
  const cited = new Set();
  const claims = data.explanation?.claims ?? [];
  for (const passage of data.explanation?.evidence ?? []) {
    evidence.set(passage.id, passage);
  }
  for (const claim of claims) {
    for (const id of claim.source_ids) {
      cited.add(id);
    }
  }

  const notes = data.footnotes.filter(note => !cited.has(note.id));
  for (const note of notes) {
    const block = node('div', undefined, 'claim');
    const excerpt = note.text.trim();
    if (excerpt.length <= 350) {
      block.append(node('p', excerpt));
    } else {
      const details = node('details');
      details.append(node('summary', 'Read linked footnote'), node('p', excerpt));
      block.append(details);
    }
    block.append(passageLink(note, 'Linked footnote'));
    disclosures.append(block);
  }
  for (const claim of claims) {
    disclosures.append(renderClaim(claim, evidence));
  }
  if (data.explanation && !notes.length && !claims.length) {
    disclosures.append(node('p', 'N/A', 'muted'));
  }
  panel.append(disclosures);

  if (!data.explanation) {
    const status = node('div');
    if (pendingExplanations.has(url)) {
      status.append(node('p', 'Checking the filing for relevant passages…', 'muted'));
    } else {
      if (data.error) status.append(node('p', data.error, 'muted'));
      status.append(action(data.error ? 'Retry summary' : 'Generate summary', () => {
        delete data.error;
        loadExplanation(url, data, selection);
        showFact(data, url);
      }));
    }
    disclosures.append(status);
  }
  positionFact(selection.anchor);
}

function passageLink(passage, label) {
  if (!passage.local) return sourceLink(passage.source, label + ' ↗');
  const button = node('button', label, 'link-button');
  button.onclick = () => jump(passage.id);
  return button;
}

function openSourceButton(source, title) {
  if (source.document_id == null) {
    return passageLink(source, source.local ? 'Go to passage' : 'Open full document');
  }
  const button = node('button', 'Open full document', 'link-button');
  button.onclick = () => {
    const dialog = byId('source-dialog');
    byId('source-title').textContent = title;
    const anchor = source.anchor ?? (new URL(source.source).hash.slice(1));
    const fragment = anchor ? '#' + encodeURIComponent(anchor) : '';
    byId('source-frame').src = `/api/filings/${activeFiling}/sources/${source.document_id}${fragment}`;
    dialog.showModal();
  };
  return button;
}

byId('close-source').onclick = () => byId('source-dialog').close();
byId('source-dialog').onclose = () => byId('source-frame').removeAttribute('src');

function renderClaim(claim, evidence) {
  const block = node('div', undefined, 'claim');
  block.append(node('p', claim.explanation));

  const sources = node('details');
  const ids = [...new Set(claim.source_ids)];
  sources.append(node('summary', `View sources (${ids.length})`));
  for (const id of ids) {
    const passage = evidence.get(id);
    let title = 'Selected filing';
    if (passage.filed) {
      title = `${passage.form || '8-K'} · ${passage.filed}`;
    }
    if (passage.exhibit && passage.exhibit !== passage.form) {
      title += ` · ${passage.exhibit}`;
    }
    if (passage.heading) {
      title += ` · ${passage.heading}`;
    }

    const excerpt = node('article', undefined, 'source-excerpt');
    excerpt.append(node('strong', title));
    excerpt.append(node('blockquote', passage.text));
    excerpt.append(openSourceButton(passage, title), sourceLink(passage.source, 'Open on EDGAR ↗'));
    sources.append(excerpt);
  }
  block.append(sources);
  return block;
}

async function loadExplanation(url, data, selected) {
  if (!pendingExplanations.has(url)) {
    const promise = request(url + '/explain', {
      method: 'POST',
      headers: {'X-Reader-Request': '1'},
    }).finally(() => pendingExplanations.delete(url));
    pendingExplanations.set(url, promise);
  }
  try {
    data.explanation = await pendingExplanations.get(url);
  } catch (error) {
    data.error = error.message;
  }
  if (selection === selected) showFact(data, url);
}

function closeFact() {
  selection = undefined;
  byId('fact-popup').hidden = true;
}

function positionFact(anchor) {
  const popup = byId('fact-popup');
  const frame = byId('filing').getBoundingClientRect();
  const margin = 12;
  const left = frame.left + anchor.left;
  const bottom = frame.top + anchor.bottom + margin;
  const above = frame.top + anchor.top - popup.offsetHeight - margin;
  const top = bottom + popup.offsetHeight <= window.innerHeight ? bottom : above;
  const maxLeft = window.innerWidth - popup.offsetWidth - margin;
  const maxTop = window.innerHeight - popup.offsetHeight - margin;
  popup.style.left = `${Math.max(margin, Math.min(left, maxLeft))}px`;
  popup.style.top = `${Math.max(margin, Math.min(top, maxTop))}px`;
}

byId('close-fact').onclick = closeFact;
window.addEventListener('resize', closeFact);
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !byId('source-dialog').open) closeFact();
});

async function chooseFact(id, anchor) {
  const selected = {anchor};
  selection = selected;
  const url = `/api/filings/${activeFiling}/facts/${id}`;
  byId('panel').replaceChildren(node('p', 'Loading sourced details…', 'section'));
  byId('fact-popup').hidden = false;
  positionFact(anchor);
  try {
    const data = await request(url);
    if (selection !== selected) return;
    showFact(data, url);
    if (!data.explanation && pendingExplanations.has(url)) {
      await loadExplanation(url, data, selected);
    }
  } catch (error) {
    if (selection === selected) byId('panel').replaceChildren(node('p', error.message, 'error'));
  }
}
function openFiling(id) {
  const filing = catalog.find(item => item.id === id);
  if (!filing) return;
  const previous = filingHistory.find(item => item.id === id && item.accession === filing.accession);
  filingHistory = [previous ?? {id, accession: filing.accession, sections: []},
    ...filingHistory.filter(item => item.id !== id)].slice(0, 30);
  saveFilingHistory();
  renderRecentShortcuts();
  showWorkspacePage('viewer');
  if (filing.id === activeFiling) {
    byId('report-title').focus();
    return;
  }
  clearEvidence(false);
  activeFiling = filing.id;
  pendingJump = null;
  answerRun++;
  byId('answer').hidden = true;
  setContext(null);
  closeFact();
  byId('search-status').textContent = '';
  const status = byId('document-status');
  const frame = byId('filing');
  status.hidden = false;
  const amendments = byId('amendments');
  amendments.replaceChildren();
  if (filing.amendments?.length) {
    const details = node('details', undefined, 'section');
    details.append(node('summary', `View amendments (${filing.amendments.length})`));
    for (const amendment of filing.amendments) {
      const title = `${amendment.form} · ${amendment.filed}`;
      const entry = node('div', undefined, 'source-excerpt');
      entry.append(node('strong', title));
      entry.append(openSourceButton(amendment, title), sourceLink(amendment.source, 'Open on EDGAR ↗'));
      details.append(entry);
    }
    amendments.append(details);
  }

  byId('report-title').textContent = `${filing.company} · FY${filing.year} ${filing.fiscal_period} · ${filing.form}`;
  byId('report-title').focus();
  byId('report-dates').textContent = `Period ended ${filing.period_end} · Filed ${filing.filed}`;
  status.textContent = 'Opening filing…';
  frame.src = `/api/filings/${activeFiling}/document`;
}

window.addEventListener('message', event => {
  const frame = byId('filing');
  if (event.source !== frame.contentWindow) {
    return;
  }
  const message = event.data;
  if (message?.filingId !== activeFiling) {
    return;
  }
  if (message.type === 'reader-dismiss') {
    closeFact();
  }
  const entry = filingHistory.find(item => item.id === activeFiling);
  if (message.type === 'reader-scroll' && entry && !evidenceIds.length && !byId('viewer').hidden &&
      byId('document-status').hidden && Number.isFinite(message.top) && message.top >= 0) {
    entry.top = message.top;
  }
  if (message.type === 'reader-ready') {
    byId('document-status').hidden = true;
    if (entry && Array.isArray(message.sections)) {
      entry.sections = message.sections.filter(item =>
        typeof item?.label === 'string' && item.label.length <= 85 && validAnchor(item.id)).slice(0, 20);
      saveFilingHistory();
      if (!byId('filings-page').hidden) renderFilingHistory();
    }
    if (pendingJump) {
      jump(pendingJump);
      pendingJump = null;
    } else if (entry?.top > 0) {
      frame.contentWindow.postMessage({type:'reader-restore-scroll', filingId:activeFiling, top:entry.top}, '*');
    }
  }
  if (message.type === 'reader-fact' || message.type === 'reader-selection') {
    const anchor = message.anchor;
    if (anchor && [anchor.left, anchor.top, anchor.bottom].every(Number.isFinite)) {
      if (message.type === 'reader-fact' && /^reader-fact-\d+$/.test(message.id)) {
        chooseFact(message.id, anchor);
      } else if (message.type === 'reader-selection' && typeof message.text === 'string') {
        inspectPassage(message.text.slice(0, 600), anchor);
      }
    }
  }
});

let pendingJump;
let questionContext;
let answerRun = 0;

function validAnchor(id) {
  return typeof id === 'string' && /^[\w:.-]{1,160}$/.test(id);
}

function action(label, callback, className = 'link-button') {
  const button = node('button', label, className);
  button.type = 'button';
  button.onclick = () => callback(button);
  return button;
}

function setContext(context, focus = false) {
  questionContext = context;
  byId('context-label').textContent = context?.label ?? '';
  byId('question-context').hidden = !context;
  if (focus) {
    closeFact();
    byId('question').focus();
  }
}
byId('clear-context').onclick = () => setContext(null);

const historyKey = 'edgars-filing-history-v1';
let filingHistory = [];
try {
  const saved = JSON.parse(localStorage.getItem(historyKey) || '[]');
  if (Array.isArray(saved)) filingHistory = saved;
} catch {
  // Reading history can still work for this tab when storage is unavailable.
}

function saveFilingHistory() {
  try {
    localStorage.setItem(historyKey, JSON.stringify(filingHistory));
  } catch {
    // Keep the current tab's history in memory.
  }
}
window.addEventListener('pagehide', saveFilingHistory);

function renderRecentShortcuts() {
  const shortcuts = byId('recent-shortcuts');
  shortcuts.replaceChildren();
  for (const entry of filingHistory) {
    const filing = catalog.find(item => item.id === entry.id && item.accession === entry.accession);
    if (!filing) continue;
    shortcuts.append(action(filingLabel(filing), () => openFiling(filing.id), 'filing-result'));
    if (shortcuts.childElementCount === 3) break;
  }
  if (!shortcuts.childElementCount) shortcuts.append(node('small', 'No recent filings yet.'));
}

function renderFilingHistory() {
  const list = byId('filing-history');
  list.replaceChildren();
  for (const entry of filingHistory) {
    const filing = catalog.find(item => item.id === entry.id && item.accession === entry.accession);
    if (!filing) continue;
    const row = node('div', undefined, 'history-row');
    const header = node('div', undefined, 'history-header');
    const name = action(filingLabel(filing), () => openFiling(filing.id), 'filing-result');
    const remove = action('Remove', () => {
      filingHistory = filingHistory.filter(item => item.id !== entry.id);
      saveFilingHistory();
      renderRecentShortcuts();
      renderFilingHistory();
      byId('filings-title').focus();
    });
    remove.setAttribute('aria-label', `Remove ${filingLabel(filing)} from recent filings`);
    header.append(name, remove);
    row.append(header);
    if (entry.sections.length) {
      const sections = node('details', undefined, 'history-sections');
      sections.append(node('summary', 'Sections'));
      for (const section of entry.sections) {
        sections.append(action(section.label, () => {
          const needsLoad = activeFiling !== filing.id || !byId('document-status').hidden;
          openFiling(filing.id);
          if (needsLoad) pendingJump = section.id;
          else jump(section.id);
        }, 'link-button'));
      }
      row.append(sections);
    }
    list.append(row);
  }
  if (!list.childElementCount) {
    list.append(node('p', 'Filings you open will appear here.'), action('Search filings', showSearchPage));
  }
}

let evidenceIds = [];
let evidenceIndex = 0;

function showEvidence(index) {
  evidenceIndex = index;
  byId('evidence-position').textContent = `Evidence ${index + 1} of ${evidenceIds.length}`;
  byId('previous-evidence').disabled = index === 0;
  byId('next-evidence').disabled = index === evidenceIds.length - 1;
  byId('filing').contentWindow.postMessage({
    type:'reader-evidence', filingId:activeFiling, ids:evidenceIds, active:evidenceIds[index],
  }, '*');
}

function clearEvidence(restore = true) {
  if (!evidenceIds.length) return;
  evidenceIds = [];
  byId('evidence-tools').hidden = true;
  byId('filing').contentWindow.postMessage({
    type:'reader-clear-evidence', filingId:activeFiling, restore,
  }, '*');
}

byId('previous-evidence').onclick = () => showEvidence(evidenceIndex - 1);
byId('next-evidence').onclick = () => showEvidence(evidenceIndex + 1);
byId('exit-evidence').onclick = () => {
  clearEvidence();
  byId('question').focus();
};

function showAnswer(result, target) {
  target.hidden = false;
  target.replaceChildren();
  if (target === byId('answer')) {
    target.append(action('Close', () => {
      answerRun++;
      target.hidden = true;
      clearEvidence();
    }, 'tray-close'));
    target.append(node('strong', result.question));
  }
  if (result.clarification) {
    target.append(node('p', result.clarification, 'clarification'));
    for (const choice of result.choices ?? []) {
      target.append(action(choice.label, () => {
        byId('question').value = choice.question;
        byId('ask').requestSubmit();
      }, 'clarification-choice'));
    }
    if (!result.choices?.length) {
      target.append(action('Refine question', () => {
        byId('question').focus();
        byId('question').select();
      }));
    }
    return;
  }
  const evidence = new Map(result.evidence.map(item => [item.id, item]));
  const cited = [...new Set(result.claims.flatMap(claim => claim.source_ids))]
    .filter(id => evidence.get(id)?.local && validAnchor(id));
  if (cited.length) {
    const explore = () => {
      closeFact();
      if (target !== byId('answer')) {
        answerRun++;
        showAnswer(result, byId('answer'));
        return;
      }
      evidenceIds = cited;
      byId('evidence-tools').hidden = false;
      showEvidence(0);
    };
    target.append(action('Show evidence in filing', explore));
    if (target === byId('answer')) explore();
  }
  for (const claim of result.claims) target.append(renderClaim(claim, evidence));
  if (!result.claims.length) {
    target.append(node('p', 'I couldn’t find enough support in this filing to answer.'));
  }
  if (result.claims.length) {
    target.append(action('Export evidence', () => {
      const file = new Blob([JSON.stringify(result, null, 2)], {type:'application/json'});
      const url = URL.createObjectURL(file);
      const link = node('a');
      link.href = url;
      link.download = `edgarS-${result.filing_id}.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }));
  }
}

function askFiling(filing, question, context) {
  return request(`/api/filings/${filing}/ask`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Reader-Request': '1'},
    body: JSON.stringify({question, fact_id: context?.fact_id, selection: context?.text}),
  });
}

byId('ask').onsubmit = async event => {
  event.preventDefault();
  const question = byId('question').value.trim();
  if (!question || !activeFiling) return;
  const run = ++answerRun;
  clearEvidence();
  const tray = byId('answer');
  tray.replaceChildren(node('p', 'Looking for evidence in this filing…'));
  tray.hidden = false;
  try {
    const result = await askFiling(activeFiling, question, questionContext);
    if (run === answerRun) showAnswer(result, tray);
  } catch (error) {
    if (run === answerRun) tray.replaceChildren(node('p', error.message, 'error'),
      action('Close', () => { tray.hidden = true; }));
  }
};

function inspectPassage(text, anchor) {
  const selected = {anchor};
  selection = selected;
  const panel = byId('panel');
  const summary = section('Selected passage');
  summary.append(node('blockquote', text));
  const context = {text, label: text};
  const filing = activeFiling;
  const answer = section('Relevant context');
  const explain = action('Explain', async button => {
    button.disabled = true;
    const question = 'Explain the selected passage and its qualifications in plain language.';
    answer.textContent = 'Looking for evidence in this filing…';
    try {
      const result = await askFiling(filing, question, context);
      if (selection === selected) showAnswer({...result, question: text}, answer);
    } catch (error) {
      if (selection === selected) answer.textContent = error.message;
    } finally {
      button.disabled = false;
      if (selection === selected) positionFact(anchor);
    }
  });
  summary.append(explain, action('Ask a follow-up', () => setContext(context, true)));
  panel.replaceChildren(summary, answer);
  byId('fact-popup').hidden = false;
  positionFact(anchor);
}

function filingLabel(filing) {
  return `${filing.company} · FY${filing.year} ${filing.fiscal_period} · ${filing.form}`;
}

const filingQuery = byId('filing-query');
const filingForm = byId('filing-form');
const filedFrom = byId('filed-from');
const filedThrough = byId('filed-through');
const filingSearchResults = byId('filing-search-results');

function searchTokens(text) {
  return text.toLowerCase().match(/[a-z0-9]+/g) || [];
}

function renderFilingSearch() {
  const terms = searchTokens(filingQuery.value);
  const form = filingForm.value;
  const from = filedFrom.value;
  const through = filedThrough.value;
  const status = byId('filing-search-status');
  filingSearchResults.replaceChildren();
  if (from && through && from > through) {
    status.textContent = 'The end date must be on or after the start date.';
    return;
  }
  for (const filing of catalog) {
    if (form && filing.form !== form) continue;
    if ((from && filing.filed < from) || (through && filing.filed > through)) continue;
    const tokens = searchTokens(`${filing.company} ${filing.ticker}`);
    if (!terms.every(term => tokens.some(token => token.startsWith(term)))) continue;
    const result = action(filingLabel(filing), () => openFiling(filing.id), 'filing-result');
    result.append(node('small', `Period ended ${filing.period_end} · Filed ${filing.filed}`));
    filingSearchResults.append(result);
  }
  status.textContent = filingSearchResults.childElementCount
    ? ''
    : catalog.length ? 'No matching filings.' : 'No filings available.';
}

function showWorkspacePage(page) {
  byId('viewer').hidden = page !== 'viewer';
  for (const button of document.querySelectorAll('.rail button[aria-controls]')) {
    const target = button.getAttribute('aria-controls');
    byId(target).hidden = target !== page;
    if (target === page) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  }
}

function renderHomeResume() {
  const filing = catalog.find(item => item.id === activeFiling) ||
    filingHistory.map(entry => catalog.find(item => item.id === entry.id && item.accession === entry.accession)).find(Boolean);
  const resume = byId('home-resume');
  byId('home-continue').hidden = !filing;
  resume.hidden = !filing;
  if (filing) {
    resume.replaceChildren(filingLabel(filing), node('small', 'Resume reading'));
    resume.onclick = () => openFiling(filing.id);
  }
}

function showHome() {
  closeFact();
  showWorkspacePage('home');
  renderHomeResume();
  byId('home-title').focus();
}

function showSearchPage() {
  closeFact();
  showWorkspacePage('search-page');
  byId('resume-filing').hidden = !activeFiling;
  filingQuery.focus();
}

byId('open-home').onclick = showHome;
byId('open-filing-search').onclick = showSearchPage;
byId('open-filings').onclick = () => {
  closeFact();
  showWorkspacePage('filings-page');
  renderFilingHistory();
  byId('filings-title').focus();
};
byId('resume-filing').onclick = () => openFiling(activeFiling);
filingQuery.oninput = renderFilingSearch;
filingForm.onchange = renderFilingSearch;
filedFrom.oninput = renderFilingSearch;
filedThrough.oninput = renderFilingSearch;
byId('filing-search').onsubmit = event => {
  event.preventDefault();
  filingSearchResults.firstElementChild?.click();
};

function renderHomeFeed() {
  const feed = byId('home-filings');
  feed.replaceChildren();
  const latest = [...catalog].sort((a, b) => b.filed.localeCompare(a.filed)).slice(0, 4);
  for (const filing of latest) {
    const row = node('li');
    const button = action(filingLabel(filing), () => openFiling(filing.id), 'home-filing');
    const date = node('time', filing.filed);
    date.dateTime = filing.filed;
    button.append(date);
    row.append(button);
    feed.append(row);
  }
  if (!latest.length) feed.append(node('li', 'No filings available.', 'muted'));
}

async function loadCatalog() {
  try {
    catalog = await request('/api/filings');
    catalog.sort((a, b) => a.company.localeCompare(b.company) || b.period_end.localeCompare(a.period_end));
    for (const company of new Set(catalog.map(filing => `${filing.company} (${filing.ticker})`))) {
      byId('filing-companies').append(new Option(company));
    }
    for (const form of [...new Set(catalog.map(filing => filing.form))].sort()) {
      filingForm.append(new Option(form));
    }
    renderFilingSearch();
    renderHomeFeed();
    renderHomeResume();
    renderRecentShortcuts();
    byId('search-status').textContent = '';
  } catch (error) {
    byId('search-status').textContent = error.message;
  }
}

loadCatalog();
