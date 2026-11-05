// Runs in the page's own JavaScript world at document_start.
//
// 1. While a claim run is active (content.js sets the sessionStorage flag) it
//    answers the site's confirm() with OK and turns alert() into a message
//    for content.js, so a native dialog can't freeze the page mid-run.
// 2. A small bridge so content.js can ask the myship page to reload its
//    coupon list over AJAX (the page's own loadCouponView()), which is much
//    faster than reloading the whole page. Only these fixed commands exist.
(() => {
  const FLAG = '__couponAutoRun';
  const active = () => { try { return !!sessionStorage.getItem(FLAG); } catch (e) { return false; } };
  const emit = (kind, msg) => {
    try {
      document.dispatchEvent(new CustomEvent('__coupon_dialog', { detail: JSON.stringify({ kind, msg: String(msg ?? '') }) }));
    } catch (e) { /* ignore */ }
  };
  const nativeAlert = window.alert;
  const nativeConfirm = window.confirm;
  window.alert = function (m) {
    if (active()) { emit('alert', m); return undefined; }
    return nativeAlert.apply(this, arguments);
  };
  window.confirm = function (m) {
    if (active()) { emit('confirm', m); return true; }
    return nativeConfirm.apply(this, arguments);
  };

  // content.js dispatches the event and reads the result attribute right
  // after: dispatchEvent is synchronous, so the answer is already there.
  document.addEventListener('__coupon_cmd', (e) => {
    let result = 'fail';
    try {
      const c = JSON.parse(e.detail);
      const canLoad = typeof window.loadCouponView === 'function' && !!document.getElementById('tabPanel');
      if (c.cmd === 'ping') {
        result = canLoad ? 'ok' : 'fail';
      } else if (c.cmd === 'loadList' && canLoad) {
        const $ = window.jQuery;
        if ($) {
          const s = $('#currentPageStatus');
          s.data('viewType', String(c.viewType));
          s.data('pageSize', Number(c.pageSize));
          s.data('pageIndex', Number(c.pageIndex));
          s.data('jsact', Number(c.pageIndex) > 1 ? 'page' : 'size');
        }
        document.querySelectorAll('[data-viewtype]').forEach((a) => {
          a.classList.toggle('active', a.getAttribute('data-viewtype') === String(c.viewType));
        });
        window.loadCouponView();
        result = 'ok';
      }
    } catch (err) { /* ignore */ }
    document.documentElement.setAttribute('data-coupon-cmd-result', result);
  });
})();
