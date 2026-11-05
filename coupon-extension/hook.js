// Runs in the page's own JavaScript world at document_start. While a claim
// run is active (content.js sets the sessionStorage flag) it answers the
// site's confirm() with OK and turns alert() into a message for content.js,
// so a native dialog can't freeze the page in the middle of a run.
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
})();
