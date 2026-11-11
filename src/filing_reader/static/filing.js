/* Runs inside the opaque-origin sandbox. Never reads parent application state. */
function selectFact(element) {
  document.querySelector('.reader-selected')?.classList.remove('reader-selected');
  element.classList.add('reader-selected');
  parent.postMessage({type:'reader-fact', id:element.dataset.readerFact}, '*');
}
document.addEventListener('click', event => {
  const element = event.target.closest('[data-reader-fact]');
  if (element) { event.preventDefault(); selectFact(element); }
});
document.addEventListener('keydown', event => {
  if ((event.key === 'Enter' || event.key === ' ') && event.target.matches('[data-reader-fact]')) {
    event.preventDefault(); selectFact(event.target);
  }
});
window.addEventListener('message', event => {
  if (event.source !== parent || event.data?.type !== 'reader-jump') return;
  if (!/^reader-(fact|passage)-\d+$/.test(event.data.id)) return;
  const element = document.getElementById(event.data.id);
  element?.scrollIntoView({behavior:'smooth', block:'center'});
  const fact = element?.querySelector('[data-reader-fact]');
  if (fact) selectFact(fact);
});
const facts = Array.from(document.querySelectorAll('[data-reader-fact]')).map(element => ({
  id:element.dataset.readerFact, tag:element.getAttribute('name'), value:element.textContent
}));
parent.postMessage({type:'reader-ready', facts}, '*');
