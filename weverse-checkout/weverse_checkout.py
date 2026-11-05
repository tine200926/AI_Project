"""
Weverse Shop 快速結帳工具

把多個商品(含選項、數量)一次組成結帳網址，直接在瀏覽器開啟 Weverse Shop 結帳頁。
原理與官網購物車「結帳」按鈕相同：/order/checkout?saleItems=[{saleStockId, quantity}, ...]

用法:
  python weverse_checkout.py                         # 互動模式：逐一貼上商品網址並選擇選項
  python weverse_checkout.py items.txt               # 讀取清單檔，立即開啟結帳頁
  python weverse_checkout.py items.txt --at 20:00    # 等到 20:00:00 立即開啟結帳頁
  python weverse_checkout.py items.txt --refresh 3   # 每 3 秒檢查一次，有貨/開賣就開啟結帳頁
  python weverse_checkout.py items.txt --at 20:00 --refresh 1   # 20:00 開始每秒檢查，直到可購買
  python weverse_checkout.py items.txt --no-open     # 只印出網址，不開瀏覽器
  python weverse_checkout.py items.txt --list        # 列出每個商品所有選項的編號/名稱/saleStockId

清單檔格式（每行一個商品，# 開頭為註解）:
  網址                                   -> 無選項商品，數量 1
  網址 | 2                               -> 無選項商品，數量 2
  網址 | FOXDUNGEE:1 | KIMJA:2           -> 指定選項名稱與數量
  網址 | #6:1   或   網址 | 6:1          -> 用選項編號（從 1 開始，依 --list 的順序）
  網址 | BLACK+M:1 | WHITE+L:2           -> 多組按鈕/下拉（顏色+尺寸等），關鍵字用 + 連接
  網址 | @123456:2                       -> 直接指定 saleStockId（最準確，--list 可查）
  網址 | *                               -> 執行時再互動選擇

不論商品頁上的選項是「單選」、「下拉選單」還是「按鈕」，每個組合最後都對應一個 saleStockId，
結帳網址只需要它，所以三種寫法通用；按鈕有兩層以上時用「關鍵字+關鍵字」或 @saleStockId。
"""

import argparse
import datetime as dt
import json
import random
import re
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor

BASE = "https://shop.weverse.io"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
URL_RE = re.compile(r"shop\.weverse\.io/(?P<lang>[^/]+)/shop/(?P<sac>[^/]+)/artists/\d+/sales/(?P<sale>\d+)")
NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
MIN_REFRESH = 1.0
STOCK_SPEC_RE = re.compile(r"@\d+(:[xX]?\d+)?")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


class Product:
    def __init__(self, url):
        m = URL_RE.search(url)
        if not m:
            raise ValueError(f"無法辨識的商品網址: {url}")
        self.url = url
        self.lang = m["lang"]
        self.shop_and_currency = m["sac"]
        self.sale_id = int(m["sale"])
        self.name = f"sale {self.sale_id}"
        self.status = None
        self.options = []
        self.max_qty = None

    def load(self):
        """重新抓取商品頁（加時間戳避免快取）。"""
        sep = "&" if "?" in self.url else "?"
        req = urllib.request.Request(
            f"{self.url}{sep}_={int(time.time() * 1000)}",
            headers={"User-Agent": UA, "Cache-Control": "no-cache", "Pragma": "no-cache"})
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read().decode("utf-8", "replace")
        m = NEXT_DATA_RE.search(html)
        if not m:
            raise RuntimeError("讀不到商品資料（頁面結構可能已改變）")
        queries = json.loads(m.group(1))["props"]["pageProps"]["$dehydratedState"]["queries"]
        data = next((q["state"]["data"] for q in queries
                     if (q.get("queryKey") or [None])[0] == "GET:/api/v1/sales/:saleId"), None)
        if not data:
            raise RuntimeError("頁面中找不到銷售資料")
        self.name = data["name"]
        self.status = data.get("status")
        self.options = collect_options(data)
        limit = data.get("goodsOrderLimit") or {}
        self.max_qty = limit.get("availableQuantity") or limit.get("maxOrderQuantity")
        if not self.options:
            raise RuntimeError(f"目前沒有可選的選項（狀態: {self.status}）")
        return self

    @property
    def has_choices(self):
        return len(self.options) > 1

    def option_by_stock(self, sid):
        return next((o for o in self.options if o["saleStockId"] == sid), None)

    def print_options(self, show_sid=False):
        for i, o in enumerate(self.options, 1):
            mark = "  (售完)" if o.get("isSoldOut") else ""
            sid = f"  [@{o['saleStockId']}]" if show_sid else ""
            print(f"    {i:>2}. {o['saleOptionName']}  {o.get('optionSalePrice', '')}{mark}{sid}")

    def find_option(self, token):
        """
        支援的寫法（不分大小寫）:
          #3            第 3 個選項（--list 看到的編號）
          3             若沒有選項剛好叫「3」，視為第 3 個選項
          @123456       直接指定 saleStockId（最準，--list 可查）
          BLACK + M     多組按鈕/下拉（例如顏色 + 尺寸）：每個關鍵字都要出現在選項名稱中
          FOXDUNGEE     選項名稱（完全相同優先，否則唯一的部分符合）
        """
        token = token.strip()
        if token.startswith("@"):
            sid = int(token[1:])
            opt = self.option_by_stock(sid)
            if not opt:
                raise ValueError(f"此商品沒有 saleStockId {sid}")
            return opt
        if token.startswith("#") or (token.isdigit() and not self._match_exact(token)):
            idx = int(token.lstrip("#")) - 1
            if not 0 <= idx < len(self.options):
                raise ValueError(f"選項編號超出範圍: {token}（共 {len(self.options)} 個）")
            return self.options[idx]
        exact = self._match_exact(token)
        if exact:
            return exact[0]
        keys = [norm(k) for k in re.split(r"\s*[+＋&]\s*", token) if k.strip()]
        partial = [o for o in self.options if all(k in norm(o["saleOptionName"]) for k in keys)]
        if len(partial) == 1:
            return partial[0]
        if partial:
            raise ValueError(f"「{token}」符合多個選項: {[o['saleOptionName'] for o in partial]}"
                             "，請多加關鍵字（用 + 連接）或改用 #編號 / @saleStockId")
        raise ValueError(f"找不到選項「{token}」")

    def _match_exact(self, token):
        return [o for o in self.options if norm(o["saleOptionName"]) == norm(token)]


def norm(s):
    return re.sub(r"\s+", " ", str(s)).strip().lower()


NAME_KEYS = ("saleOptionName", "optionName", "optionValue", "optionValueName", "value", "name", "title")


def _label(node):
    for k in NAME_KEYS:
        v = node.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def collect_options(data):
    """
    收集商品所有可購買的 SKU（含 saleStockId）。

    Weverse 的選項不論畫面上是單選、下拉選單還是按鈕（例如「顏色」按鈕 + 「尺寸」按鈕），
    最後都是對應到一個 saleStockId，結帳網址只需要它。一般商品放在 option.options（扁平清單），
    多層/按鈕式選項可能是巢狀分組，這裡遞迴走訪，把上層分組名稱接成「BLACK / M」這種完整名稱。
    """
    found = {}

    def walk(node, path, is_root=False):
        if isinstance(node, list):
            for v in node:
                walk(v, path)
            return
        if not isinstance(node, dict):
            return
        sid = node.get("saleStockId")
        if isinstance(sid, int) and not isinstance(sid, bool):
            own = _label(node)
            # 上層分組名稱（例如顏色按鈕），若選項名稱本身已包含就不重複
            prefix = [p for p in path if not (own and norm(p) in norm(own))]
            name = " / ".join(prefix + ([own] if own else [])) or f"stock {sid}"
            opt = dict(node, saleOptionName=name)
            old = found.get(sid)
            if old is None or len(name) > len(old["saleOptionName"]):
                if old is not None:
                    opt.setdefault("isSoldOut", old.get("isSoldOut"))
                found[sid] = opt
            return
        label = None if is_root else _label(node)
        sub = path + [label] if label else path
        for v in node.values():
            if isinstance(v, (dict, list)):
                walk(v, sub)

    root = data.get("option")
    if root:
        walk(root, [], is_root=True)
    if not found:
        walk(data, [], is_root=True)
    return list(found.values())


class Pick:
    def __init__(self, product, option, qty):
        self.product = product
        self.sale_stock_id = option["saleStockId"]
        self.option_name = option["saleOptionName"]
        self.qty = qty

    @property
    def label(self):
        return f"{self.product.name} / {self.option_name} x{self.qty}"

    def unavailable_reason(self):
        """None 表示目前可購買。"""
        p = self.product
        if p.status != "SALE":
            return f"狀態 {p.status}"
        opt = p.option_by_stock(self.sale_stock_id)
        if opt is None:
            return "選項已不存在"
        if opt.get("isSoldOut"):
            return "售完"
        return None


def parse_qty(text, default=1):
    text = text.strip().lstrip("xX")
    return int(text) if text else default


def pick_interactively(p):
    """回傳 [(option, qty), ...]"""
    if not p.has_choices:
        q = input("    數量 [1]: ").strip()
        return [(p.options[0], parse_qty(q))]
    p.print_options(show_sid=True)
    print("    輸入選項編號與數量，例如: 6:1, 9:2   （只寫編號則數量為 1，空白跳過此商品；也可用 @saleStockId:數量）")
    while True:
        raw = input("    > ").strip()
        if not raw:
            return []
        try:
            picks = []
            for part in re.split(r"[,\s]+", raw):
                if not part:
                    continue
                idx, _, qty = part.partition(":")
                picks.append((p.find_option(idx if idx.startswith("@") else "#" + idx), parse_qty(qty)))
            return picks
        except ValueError as e:
            print(f"    ! {e}，請重新輸入")


def parse_line_specs(p, specs, interactive=True):
    if not specs or specs == ["*"]:
        if not p.has_choices and not specs:
            return [(p.options[0], 1)]
        if not interactive:
            raise ValueError("需要互動選擇選項，請在清單檔中直接寫出選項")
        if not specs:
            print(f"  「{p.name}」有多個選項但清單沒指定，請選擇：")
        return pick_interactively(p)
    picks = []
    for spec in specs:
        if not p.has_choices and re.fullmatch(r"[xX]?\d+", spec):
            picks.append((p.options[0], parse_qty(spec)))
            continue
        name, sep, qty = spec.rpartition(":")
        if not sep:
            name, qty = spec, "1"
        picks.append((p.find_option(name), parse_qty(qty)))
    return picks


def load_from_file(path):
    entries = []
    with open(path, encoding="utf-8-sig") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [s.strip() for s in line.split("|")]
            entries.append((n, parts[0], [s for s in parts[1:] if s]))
    return entries


def interactive_entries():
    print("貼上商品網址（一行一個），輸入空白行結束：")
    entries, n = [], 0
    while True:
        url = input("網址> ").strip()
        if not url:
            break
        n += 1
        entries.append((n, url, ["*"]))
    return entries


class Order:
    def __init__(self):
        self.picks = []
        self.pending = []      # (n, product, specs)：商品資料還讀不到，之後再試
        self.sac = None
        self.lang = "zh-tw"

    def _accept_product(self, p):
        if self.sac and p.shop_and_currency != self.sac:
            raise ValueError(f"幣別/商店 {p.shop_and_currency} 與先前 {self.sac} 不同（同一張結帳單需相同幣別）")
        if not self.sac:
            self.sac, self.lang = p.shop_and_currency, p.lang

    def add(self, n, p, specs, interactive):
        self._accept_product(p)
        picks = [Pick(p, o, q) for o, q in parse_line_specs(p, specs, interactive) if q > 0]
        total = sum(x.qty for x in picks)
        if p.max_qty and total > p.max_qty:
            print(f"  ! 此商品限購 {p.max_qty} 個，你選了 {total} 個，結帳頁可能會擋下")
        self.picks.extend(picks)
        return picks

    def resolve(self, entries, allow_pending):
        for n, url, specs in entries:
            print(f"\n讀取商品 {url}")
            try:
                p = Product(url)
            except ValueError as e:
                print(f"  ! 第 {n} 項略過: {e}")
                continue
            try:
                p.load()
            except (RuntimeError, OSError) as e:
                if allow_pending and specs and specs != ["*"]:
                    print(f"  ! 暫時讀不到（{e}），開賣後會自動重試")
                    self.pending.append((n, p, specs))
                elif specs and all(STOCK_SPEC_RE.fullmatch(s) for s in specs):
                    print(f"  ! 讀不到商品頁（{e}），直接用指定的 saleStockId 結帳")
                    self._accept_product(p)
                    for s in specs:
                        sid, _, qty = s[1:].partition(":")
                        x = Pick(p, {"saleStockId": int(sid), "saleOptionName": f"@{sid}"}, parse_qty(qty))
                        self.picks.append(x)
                        print(f"  + @{sid} x{x.qty}")
                else:
                    print(f"  ! 第 {n} 項略過: {e}")
                continue
            print(f"  {p.name}" + ("" if p.status == "SALE" else f"  [狀態: {p.status}]"))
            try:
                for x in self.add(n, p, specs, interactive=True):
                    reason = x.unavailable_reason()
                    print(f"  + {x.option_name} x{x.qty}" + (f"  （目前{reason}）" if reason else ""))
            except ValueError as e:
                print(f"  ! 第 {n} 項略過: {e}")

    def retry_pending(self):
        still = []
        for n, p, specs in self.pending:
            try:
                p.load()
                for x in self.add(n, p, specs, interactive=False):
                    print(f"\n  + 已讀到 {x.label}")
            except (RuntimeError, OSError):
                still.append((n, p, specs))
            except ValueError as e:
                print(f"\n  ! 第 {n} 項放棄: {e}")
        self.pending = still

    def refresh_all(self):
        products = {id(x.product): x.product for x in self.picks}.values()
        errors = []

        def load(p):
            try:
                p.load()
            except (RuntimeError, OSError) as e:
                errors.append(f"{p.name}: {e}")

        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(load, products))
        return errors

    def checkout_url(self, picks):
        items = {}
        for x in picks:
            items[x.sale_stock_id] = items.get(x.sale_stock_id, 0) + x.qty
        sale_items = [{"quantity": q, "saleStockId": sid} for sid, q in items.items()]
        query = urllib.parse.urlencode({
            "shopAndCurrency": self.sac,
            "saleItems": json.dumps(sale_items, separators=(",", ":")),
        })
        return f"{BASE}/{self.lang}/order/checkout?{query}"


def parse_time(text):
    now = dt.datetime.now()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            pass
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            t = dt.datetime.strptime(text, fmt).time()
            return dt.datetime.combine(now.date(), t)
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(f"時間格式錯誤: {text}（例：20:00、20:00:00、2026-10-15 20:00）")


def wait_until(target):
    if target <= dt.datetime.now():
        print(f"\n指定時間 {target:%Y-%m-%d %H:%M:%S} 已過，立即開始。")
        return
    print(f"\n等待到 {target:%Y-%m-%d %H:%M:%S}（Ctrl+C 取消）")
    while True:
        left = (target - dt.datetime.now()).total_seconds()
        if left <= 0:
            break
        h, rem = divmod(int(left), 3600)
        m, s = divmod(rem, 60)
        print(f"\r  倒數 {h:02d}:{m:02d}:{s:02d}   ", end="", flush=True)
        time.sleep(min(left, 1 if left > 1 else left))
    print(f"\r  時間到 {dt.datetime.now():%H:%M:%S.%f}"[:-3] + "      ")


def notify():
    try:
        import winsound
        for _ in range(3):
            winsound.Beep(1200, 200)
    except Exception:
        print("\a", end="")


def open_checkout(order, picks, no_open):
    url = order.checkout_url(picks)
    print("\n=== 結帳內容 ===")
    for x in picks:
        print("  " + x.label)
    print(f"\n結帳網址:\n{url}\n")
    if not no_open:
        webbrowser.open(url)
        print("已在預設瀏覽器開啟（請確認該瀏覽器已登入 Weverse 帳號）。")
    notify()


def refresh_loop(order, interval, partial, no_open):
    print(f"\n開始每 {interval:g} 秒檢查一次，{'任一' if partial else '全部'}商品可購買時開啟結帳頁（Ctrl+C 停止）")
    attempt = 0
    while True:
        attempt += 1
        started = time.monotonic()
        if order.pending:
            order.retry_pending()
        errors = order.refresh_all() if order.picks else []
        ready = [x for x in order.picks if x.unavailable_reason() is None]
        waiting = [x for x in order.picks if x.unavailable_reason() is not None]
        stamp = f"{dt.datetime.now():%H:%M:%S}"
        if order.picks and not order.pending and not waiting:
            print(f"\r[{stamp}] 第 {attempt} 次：全部可購買！" + " " * 30)
            open_checkout(order, ready, no_open)
            return 0
        if partial and ready:
            print(f"\r[{stamp}] 第 {attempt} 次：{len(ready)} 項可購買，先結帳這些" + " " * 20)
            for x in waiting:
                print(f"  - 未加入: {x.label}（{x.unavailable_reason()}）")
            open_checkout(order, ready, no_open)
            return 0
        detail = "; ".join(f"{x.option_name}:{x.unavailable_reason()}" for x in waiting[:3])
        if len(waiting) > 3:
            detail += f" …等 {len(waiting)} 項"
        if order.pending:
            detail += f"; {len(order.pending)} 項商品未開放"
        if errors:
            detail += f"; 讀取失敗 {len(errors)}"
        print(f"\r[{stamp}] 第 {attempt} 次：尚未可購買（{detail}）"[:70].ljust(70), end="", flush=True)
        elapsed = time.monotonic() - started
        time.sleep(max(0.0, interval + random.uniform(0, interval * 0.2) - elapsed))


def list_options(entries):
    seen = set()
    for n, url, _ in entries:
        if url in seen:
            continue
        seen.add(url)
        print(f"\n{url}")
        try:
            p = Product(url).load()
        except (ValueError, RuntimeError, OSError) as e:
            print(f"  ! 讀取失敗: {e}")
            continue
        print(f"  {p.name}  [狀態: {p.status}]  共 {len(p.options)} 個選項")
        p.print_options(show_sid=True)
    print("\n清單檔可寫: 網址 | #編號:數量   或   網址 | @saleStockId:數量   或   網址 | 名稱關鍵字+關鍵字:數量")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Weverse Shop 多商品快速結帳")
    ap.add_argument("file", nargs="?", help="商品清單檔（省略則進入互動模式）")
    ap.add_argument("--at", type=parse_time, metavar="時間",
                    help="指定開搶時間，例如 20:00、20:00:00、2026-10-15 20:00（依本機時鐘）")
    ap.add_argument("--refresh", type=float, metavar="秒",
                    help=f"每隔幾秒檢查一次，直到可購買才開啟結帳頁（最小 {MIN_REFRESH:g} 秒）")
    ap.add_argument("--partial", action="store_true",
                    help="搭配 --refresh：只要有任一項可購買就先用可購買的商品結帳")
    ap.add_argument("--no-open", action="store_true", help="只印出結帳網址，不自動開啟瀏覽器")
    ap.add_argument("--list", action="store_true",
                    help="只列出清單中每個商品的所有選項（編號、名稱、saleStockId），方便填寫清單檔")
    args = ap.parse_args()
    if args.refresh is not None and args.refresh < MIN_REFRESH:
        print(f"刷新間隔過短，改為 {MIN_REFRESH:g} 秒")
        args.refresh = MIN_REFRESH

    entries = load_from_file(args.file) if args.file else interactive_entries()
    if not entries:
        print("沒有商品。")
        return 1

    if args.list:
        return list_options(entries)

    order = Order()
    order.resolve(entries, allow_pending=bool(args.at or args.refresh))
    if not order.picks and not order.pending:
        print("\n沒有可結帳的商品。")
        return 1

    if args.at:
        wait_until(args.at)

    if args.refresh:
        return refresh_loop(order, args.refresh, args.partial, args.no_open)

    if order.pending:
        order.retry_pending()
        for n, p, _ in order.pending:
            print(f"  ! 第 {n} 項仍讀不到，略過: {p.url}")
    if not order.picks:
        print("\n沒有可結帳的商品。")
        return 1
    open_checkout(order, order.picks, args.no_open)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已停止")
        sys.exit(130)
