// Runs in the booking page's own JavaScript world (manifest: "world": "MAIN")
// before any of its scripts. It only does anything in tabs this extension
// opened to check flight times: those are opened with #tigerair-finder in the
// URL, remembered in sessionStorage so it survives the site's own redirects.
// In those tabs it watches the page's GraphQL answers and hands the flight
// search result to relay.js. Normal browsing of the booking site is untouched.
(() => {
  const MARK = 'tigerair-finder';
  try {
    if (location.hash.includes(MARK)) sessionStorage.setItem(MARK, '1');
    if (sessionStorage.getItem(MARK) !== '1') return;
  } catch {
    return;
  }

  const API = 'api-book.tigerairtw.com/graphql';
  const KEY = '"appFlightSearchResult"';
  const post = (text) => {
    if (typeof text === 'string' && text.includes(KEY)) window.postMessage({ source: MARK, text }, location.origin);
  };
  const urlOf = (input) => {
    try {
      return input instanceof Request ? input.url : String(input);
    } catch {
      return '';
    }
  };

  const origFetch = window.fetch;
  window.fetch = function (...args) {
    const p = origFetch.apply(this, args);
    if (urlOf(args[0]).includes(API)) {
      p.then((resp) => resp.clone().text()).then(post).catch(() => {});
    }
    return p;
  };

  const { open, send } = XMLHttpRequest.prototype;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this.__tigerairFinderUrl = urlOf(url);
    return open.call(this, method, url, ...rest);
  };
  XMLHttpRequest.prototype.send = function (...args) {
    if ((this.__tigerairFinderUrl || '').includes(API)) {
      this.addEventListener('load', () => {
        try {
          if (this.responseType === '' || this.responseType === 'text') post(this.responseText);
          else if (this.responseType === 'json') post(JSON.stringify(this.response));
        } catch {
          // ignore
        }
      });
    }
    return send.apply(this, args);
  };
})();
