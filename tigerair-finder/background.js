// The toolbar button opens the search page in a full tab (a popup is too
// small for the form and the results table).
const PAGE = chrome.runtime.getURL('search.html');

function openSearch() {
  return chrome.tabs.create({ url: PAGE });
}

chrome.action.onClicked.addListener(openSearch);

chrome.runtime.onInstalled.addListener(({ reason }) => {
  if (reason === 'install') openSearch();
});
