// Isolated-world half of hook.js: page scripts can't reach chrome.runtime, so
// hook.js posts the captured JSON here and this forwards it to the extension.
// The background only accepts it from tabs it opened itself.
window.addEventListener('message', (e) => {
  if (e.source !== window || !e.data || e.data.source !== 'tigerair-finder' || typeof e.data.text !== 'string') return;
  chrome.runtime.sendMessage({ type: 'flightSearchResult', text: e.data.text }).catch(() => {});
});
