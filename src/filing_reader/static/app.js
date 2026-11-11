const $ = id => document.getElementById(id);
const factCache = new Map();
const pendingExplanations = new Map();
let catalog = [], activeFiling = '', activeFact = '', filingFacts = [];
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function sourceLink(url, label='View source ↗') {
  const link = node('a', label, 'source');
  link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer';
  return link;
}
async function request(url, options) {
  const response = await fetch(url, options);
  const result = await response.json();
  if (!response.ok) throw Error(result.error || 'Request failed');
  return result;
}
function jump(id) { $('filing').contentWindow.postMessage({type:'reader-jump', id}, '*'); }
function section(title, subtitle) {
  const element = node('section', undefined, 'section');
  const heading = node('h2', title);
  if (subtitle) heading.append(node('span', subtitle));
  element.append(heading); return element;
}
function showFact(data, cached=false) {
  const fact = data.fact;
  const panel = $('panel'); panel.replaceChildren();
  $('cache-status').textContent = cached ? 'REUSED' : 'SOURCE DATA';
  const top = node('div', undefined, 'item-top');
  top.append(node('div', 'Reported value · '+activeFiling, 'eyebrow'), node('h1', fact.title));
  top.append(node('div', fact.display_value, 'amount'));
  const context = fact.context;
  const period = context.instant || [context.startdate, context.enddate].filter(Boolean).join(' → ');
  top.append(node('div', period || 'Period unavailable', 'context'));
  let unit = fact.unit.split(':').pop();
  if (fact.scale !== '0') unit += ' · displayed number × 10^'+fact.scale;
  top.append(node('div', unit + (fact.sign ? ' · negative amount' : ''), 'context'));
  if (context.dimensions?.length) {
    top.append(node('p', 'Dimension-specific fact: this may describe a segment or category rather than the entire company.', 'muted'));
  }
  top.append(sourceLink(fact.source, 'Reported value in SEC filing ↗')); panel.append(top);
  const definition = section('What this item means', '01');
  if (!fact.definitions.length) definition.append(node('p', 'No definition was found in the loaded taxonomy or company documentation. We have not inferred one.', 'muted'));
  for (const item of fact.definitions) {
    definition.append(node('div', item.text, 'definition'));
    definition.append(sourceLink(item.source, item.role === 'schema-documentation' ? 'Company schema documentation ↗' : 'Definition source ↗'));
  }
  panel.append(definition);
  const disclosures = section('Related disclosure passages', '02');
  disclosures.append(node('p', 'Text-search suggestions from this filing. Relevance and completeness are not guaranteed.', 'muted'));
  if (!data.disclosures.length) disclosures.append(node('p', 'No matching passages found. This does not establish that no related disclosure exists.', 'muted'));
  for (const passage of data.disclosures) {
    const card = node('div', undefined, 'evidence');
    card.append(node('h3', passage.heading));
    const excerpt = node('pre', passage.text.length > 1800 ? passage.text.slice(0,1800)+'…' : passage.text);
    card.append(excerpt);
    if (passage.text.length > 1800) {
      const expand = node('button', 'Show full passage', 'link-button');
      expand.onclick = () => { excerpt.textContent = passage.text; expand.remove(); };
      card.append(expand);
    }
    const actions = node('div', undefined, 'actions');
    const button = node('button', 'Go to passage', 'link-button'); button.onclick = () => jump(passage.id);
    actions.append(button, sourceLink(passage.source, 'SEC source ↗')); card.append(actions); disclosures.append(card);
  }
  panel.append(disclosures);
  const explanation = section('Why these passages may matter', '03');
  explanation.append(node('p', 'Optional local-model explanation. Sources load without running the model. Generated results are saved for reuse.', 'muted'));
  const filingId = activeFiling, factId = activeFact, key = filingId+'/'+factId;
  if (data.explanation) renderExplanation(explanation, data.explanation);
  else {
    const button = node('button', pendingExplanations.has(key) ? 'Generating…' : 'Explain connections', 'primary');
    button.disabled = pendingExplanations.has(key) || !data.disclosures.length;
    button.onclick = async () => {
      button.disabled = true; button.textContent = 'Generating locally…';
      const promise = request(`/api/filings/${filingId}/facts/${factId}/explain`, {method:'POST', headers:{'X-Reader-Request':'1'}});
      pendingExplanations.set(key, promise);
      try {
        data.explanation = await promise;
        if (activeFiling===filingId && activeFact===factId) showFact(data, true);
      } catch(error) {
        if (activeFiling===filingId && activeFact===factId) {
          explanation.append(node('p', error.message, 'error')); button.disabled=false; button.textContent='Retry explanation';
        }
      } finally { pendingExplanations.delete(key); }
    };
    explanation.append(button);
  }
  panel.append(explanation);
  const technical = node('details', undefined, 'technical');
  technical.append(node('summary','Technical details'));
  technical.append(node('pre', JSON.stringify({tag:fact.tag, context:fact.context, format:fact.format},null,2)));
  panel.append(technical);
}
function renderExplanation(container, answer) {
  container.append(node('p', answer.status, 'muted'));
  for (const claim of answer.claims) {
    const block=node('div',undefined,'claim'); block.append(node('p',claim.explanation));
    claim.source_ids.forEach((id,index) => {
      const button=node('button',`Source ${index+1}`, 'link-button'); button.onclick=()=>jump(id); block.append(button,document.createTextNode('  '));
    });
    container.append(block);
  }
  if (!answer.claims.length) container.append(node('p','No supported explanation was established.','muted'));
  container.append(node('span', 'Saved · '+answer.model, 'badge'));
}
async function chooseFact(id) {
  const filingId = activeFiling, key = filingId+'/'+id;
  activeFact=id;
  const cached=factCache.has(key);
  if (!cached) factCache.set(key, request(`/api/filings/${filingId}/facts/${id}`));
  $('panel').replaceChildren(node('p','Loading sourced details…','section'));
  try {
    const data=await factCache.get(key);
    if (activeFiling===filingId && activeFact===id) showFact(data,cached);
  } catch(error) {
    factCache.delete(key);
    if (activeFiling===filingId && activeFact===id) $('panel').replaceChildren(node('p',error.message,'error'));
  }
}
function openFiling() {
  activeFiling=$('company').value+'-'+$('year').value; activeFact=''; filingFacts=[];
  const filing=catalog.find(item=>item.id===activeFiling);
  $('search-results').hidden=true; $('fact-search').value=''; $('search-status').textContent='';
  $('document-status').hidden=false;
  $('panel').replaceChildren(node('div','Select a number in the filing to explore its meaning and sources.','empty'));
  $('cache-status').textContent='';
  $('report-title').textContent=`${filing.company} · ${filing.year} Form 10-K`;
  $('report-dates').textContent=filing.available ? `Year ended ${filing.period_end} · Filed ${filing.filed}` : 'Not prepared';
  $('original').href=filing.source || '#'; $('original').hidden=!filing.available;
  $('filing-status').textContent=filing.warnings?.length ? 'Some source definitions unavailable' : '';
  $('document-status').textContent=filing.available ? 'Opening annual filing…' : 'This filing is not prepared. Run the preparation command in the setup guide.';
  $('filing').src=filing.available ? `/api/filings/${activeFiling}/document` : 'about:blank';
}
window.addEventListener('message',event=>{
  if(event.source!==$('filing').contentWindow) return;
  if(event.data?.type==='reader-ready' && Array.isArray(event.data.facts)) {
    filingFacts=event.data.facts; $('document-status').hidden=true;
    $('filing-status').textContent=`${filingFacts.length.toLocaleString()} selectable values`;
  }
  if(event.data?.type==='reader-fact' && /^reader-fact-\d+$/.test(event.data.id)) chooseFact(event.data.id);
});
$('company').onchange=openFiling; $('year').onchange=openFiling;
$('fact-search').oninput=()=>{
  const query=$('fact-search').value.toLowerCase().replace(/\s/g,'');
  const results=$('search-results'); results.replaceChildren(); results.hidden=!query;
  if(!query) { $('search-status').textContent=''; return; }
  const matches=filingFacts.filter(item=>item.tag?.toLowerCase().includes(query));
  $('search-status').textContent=`${matches.length} matches · first 30 shown`;
  for(const item of matches.slice(0,30)) {
    const title=item.tag.split(':').pop().replace(/([a-z])([A-Z])/g,'$1 $2');
    const button=node('button',title+' — '+item.value); button.onclick=()=>{jump(item.id);results.hidden=true;};results.append(button);
  }
};
request('/api/filings').then(items=>{catalog=items;openFiling();}).catch(error=>{$('document-status').textContent=error.message;});
