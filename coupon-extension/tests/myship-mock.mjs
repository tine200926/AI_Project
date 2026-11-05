// A mock of the myship GetCoupon page, modelled on its real structure:
// the coupon list is loaded over AJAX (loadCouponView -> /mem/_CouponListPage)
// into #tabPanel with pagination, claims go through getCouponTicket ->
// /mem/GetCouponTicket, success shows an alertify dialog, failure shows the
// #getTicketDialog modal, and WaitAndReload locks the button for a countdown.
// The breadcrumb also contains a 「領取優惠券」 link that must not be clicked.

export function createMyship(serverNow) {
  const st = {
    openAt: Infinity,
    claimed: {},          // eacm -> server time
    early: 0,             // claim attempts before openAt
    listRequests: [],     // { pgsize, pgidx, view, at }
    waitOnce: new Set(['A3']), // first claim of these returns WaitAndReload
    maxPageSize: 5,       // server caps the page size, to exercise pagination
  };
  const COUPONS = {
    getAllCoupon: [
      { no: 'A0', name: '新會員 滿199折20元', always: true, claimedAlready: true },
      { no: 'A1', name: '11月1111購物嘉年華-滿350折35元-1' },
      { no: 'A2', name: '11月1111購物嘉年華-免運券' },
      { no: 'A3', name: '11月1111購物嘉年華-滿999折111元' },
      { no: 'A4', name: '11月1111購物嘉年華-滿500折50元' },
      { no: 'A5', name: '11月1111購物嘉年華-滿200折20元' },
      { no: 'A6', name: '11月1111購物嘉年華-滿1500折150元' },
    ],
    getSellerCoupon: [
      { no: 'S1', name: '合作商家 滿300折30元' },
    ],
  };

  function card(c) {
    const done = c.claimedAlready || st.claimed[c.no];
    const btn = done
      ? '<button class="stub-btn stub-btn-disabled btn">已領取</button>'
      : `<button id="GetCouponTicket_${c.no}" class="stub-btn btn btn-primary" onclick="getCouponTicket('CP${c.no}','${c.no}')">領取<span id="TicketTimer_${c.no}"></span></button>`;
    return `<div class="cp"><div class="cp-image cp-discount"><div class="flag">限量</div><div class="cp-image-content"><div class="name">折價券</div></div></div>
<div class="cp-infos-row"><div class="cp-infos"><div class="cp-name">${c.name}</div><div class="cp-date">有效期限：2026.12.01 23:59</div></div>
<div class="cp-ticket-stub no-border">${btn}<div class="stub-rule" style="cursor: pointer;" onclick="void 0">使用規則</div></div></div></div>`;
  }

  function listPage(params) {
    const view = params.get('ViewType') || 'getAllCoupon';
    const pgsize = Math.min(Number(params.get('pgsize')) || 6, st.maxPageSize);
    const all = (COUPONS[view] || []).filter((c) => c.always || serverNow() >= st.openAt);
    const pgcnt = Math.max(1, Math.ceil(all.length / pgsize));
    const pgidx = Math.min(Math.max(1, Number(params.get('pgidx')) || 1), pgcnt);
    st.listRequests.push({ view, pgsize: Number(params.get('pgsize')), pgidx, at: serverNow() });
    const items = all.slice((pgidx - 1) * pgsize, pgidx * pgsize);
    return `<div class="tab-pane active" id="${view}" role="tabpanel">
<div class="cp-list"><input type="hidden" id="CouponTotalCount" value="${all.length}">${items.map(card).join('')}</div>
<div style="display:none;"><input type="hidden" id="pgcnt" value="${pgcnt}"><input type="hidden" id="pgidx" value="${pgidx}"><input type="hidden" id="pgsize" value="${pgsize}"></div>
<div class="pagination"><div class="pagination__pageinfo">第 ${pgidx} / ${pgcnt} 頁，共 ${all.length} 筆</div></div></div>`;
  }

  function claim(params) {
    const no = params.get('Eacm_No');
    const all = [...COUPONS.getAllCoupon, ...COUPONS.getSellerCoupon];
    const c = all.find((x) => x.no === no);
    if (serverNow() < st.openAt) { st.early++; return { ResultCode: false, ResultMessage: '活動尚未開始' }; }
    if (!c || c.claimedAlready || st.claimed[no]) return { ResultCode: false, ResultMessage: '您已領取過此優惠券' };
    if (st.waitOnce.has(no)) {
      st.waitOnce.delete(no);
      return { ResultCode: false, ResultMessage: '目前領取人數眾多，請稍候再試', WaitAndReload: true };
    }
    st.claimed[no] = serverNow();
    return { ResultCode: true, ResultMessage: '領取成功' };
  }

  const page = `<!doctype html><html><head><meta charset="utf-8"><title>領取優惠券</title>
<style>.modal{display:none;position:fixed;inset:20% 30%;background:#fff;border:1px solid #999}
.alertify{position:fixed;top:30%;left:30%;background:#fff;border:1px solid #999;padding:10px}
#divLoadingContent{position:fixed;top:0;left:0;background:#ffc}</style></head>
<body>
<nav class="header-navbar"><div>王*明，歡迎回來</div></nav>
<div class="main-menu"><a href="/mem/MyCoupon"><span class="menu-item">我的優惠券</span></a></div>
<ol class="breadcrumb"><li><a href="/Home">首頁</a></li><li class="breadcrumb-item active"><a href="#" onclick="window.__crumb=(window.__crumb||0)+1;return false;">領取優惠券</a></li></ol>
<h4 class="card-title">領取優惠券</h4>
<ul class="nav nav-pills">
  <li><a class="nav-link active" href="#getAllCoupon" data-viewtype="getAllCoupon" onclick="loadCouponViewByTab(this.getAttribute('data-viewtype'));">全站</a></li>
  <li><a class="nav-link" href="#getSellerCoupon" data-viewtype="getSellerCoupon" onclick="loadCouponViewByTab(this.getAttribute('data-viewtype'));">合作商家</a></li>
</ul>
<div class="tab-content" id="tabPanel"></div>
<div type="hidden" id="currentPageStatus"></div>
<div class="modal fade show" id="getTicketDialog"><div class="modal-dialog"><div class="modal-content">
  <div class="modal-header"><h3 class="modal-title" id="getTicketTitle"></h3>
  <button type="button" class="close rounded-pill" aria-label="Close" onclick="document.getElementById('getTicketDialog').style.display='none'"><i class="bx bx-x"></i></button></div>
  <div class="modal-body"><a id="getTicketMessage"></a></div></div></div></div>
<div class="modal fade" id="loginModal"><div class="modal-body"><button class="btnOPLogin">uniopen 登入/註冊</button></div></div>
<div id="divLoadingContent" style="display:none">資料處理中</div>
<script>
  // Tiny stand-ins for the jQuery / alertify calls the real page makes.
  window.jQuery = window.$ = function (sel) {
    const el = typeof sel === 'string' ? document.querySelector(sel) : sel;
    return { data(k, v) { el.__d = el.__d || {}; if (v === undefined) return el.__d[k]; el.__d[k] = v; return this; } };
  };
  window.alertify = { alert(msg) {
    const s = document.createElement('section');
    s.className = 'alertify';
    s.innerHTML = '<div class="alertify-dialog"><article class="alertify-inner"><p class="alertify-message"></p><nav class="alertify-buttons"><button class="alertify-button alertify-button-ok" id="alertify-ok">確認</button></nav></article></div>';
    s.querySelector('p').textContent = msg;
    s.querySelector('button').onclick = () => s.remove();
    document.body.appendChild(s);
  } };
  let busy = 0;
  function jsShowBusy() { busy++; document.getElementById('divLoadingContent').style.display = 'block'; }
  function jsHideBusy() { busy = Math.max(0, busy - 1); if (!busy) document.getElementById('divLoadingContent').style.display = 'none'; }
  function loadCouponViewByTab(v) { $('#currentPageStatus').data('viewType', v); $('#currentPageStatus').data('pageSize', 6); loadCouponView(); }
  function loadCouponView() {
    const s = $('#currentPageStatus');
    jsShowBusy();
    fetch('/mem/_CouponListPage', { method: 'POST', body: new URLSearchParams({
      pgsize: s.data('pageSize'), pgidx: s.data('pageIndex'), ViewType: s.data('viewType'), jsact: s.data('jsact') || '' }) })
      .then((r) => r.text()).then((h) => { document.getElementById('tabPanel').innerHTML = h; })
      .finally(() => setTimeout(jsHideBusy, 150));
  }
  function getCouponTicket(code, no) {
    jsShowBusy();
    fetch('/mem/GetCouponTicket', { method: 'POST', body: new URLSearchParams({ Coupon_Code: code, Eacm_No: no }) })
      .then((r) => r.json()).then((data) => {
        if (data.ResultCode == false) {
          document.getElementById('getTicketTitle').textContent = '領取失敗';
          document.getElementById('getTicketMessage').textContent = data.ResultMessage;
          document.getElementById('getTicketDialog').style.display = 'block';
        } else {
          alertify.alert(data.ResultMessage);
        }
        if (data.WaitAndReload) {
          const b = document.getElementById('GetCouponTicket_' + no);
          b.classList.remove('stub-btn', 'btn-primary'); b.classList.add('stub-btn-disabled'); b.setAttribute('onclick', 'return false;');
          let count = 3;
          const t = setInterval(() => { count--; document.getElementById('TicketTimer_' + no).textContent = '(' + count + ')';
            if (count <= 0) { clearInterval(t); loadCouponView(); } }, 1000);
        } else {
          loadCouponView();
        }
      }).finally(jsHideBusy);
  }
  document.addEventListener('DOMContentLoaded', () => {
    const s = $('#currentPageStatus');
    s.data('viewType', 'getAllCoupon'); s.data('pageSize', 6); s.data('pageIndex', 1); s.data('jsact', '');
    loadCouponView();
  });
</script></body></html>`;

  async function handle(route, headers) {
    const req = route.request();
    const url = new URL(req.url());
    const body = new URLSearchParams(req.postData() || '');
    if (url.pathname === '/mem/GetCoupon') {
      if (req.method() === 'HEAD') return route.fulfill({ status: 200, headers, body: '' });
      return route.fulfill({ status: 200, headers: { ...headers, 'content-type': 'text/html; charset=utf-8' }, body: page });
    }
    if (url.pathname === '/mem/_CouponListPage') {
      return route.fulfill({ status: 200, headers: { ...headers, 'content-type': 'text/html; charset=utf-8' }, body: listPage(body) });
    }
    if (url.pathname === '/mem/GetCouponTicket') {
      return route.fulfill({ status: 200, headers: { ...headers, 'content-type': 'application/json' }, body: JSON.stringify(claim(body)) });
    }
    return null;
  }

  function reset(openAt) {
    st.openAt = openAt; st.claimed = {}; st.early = 0; st.listRequests = []; st.waitOnce = new Set(['A3']);
  }

  return { st, handle, reset };
}
