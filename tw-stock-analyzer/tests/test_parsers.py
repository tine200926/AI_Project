"""資料源解析測試：樣本依各 API 公開文件的欄位格式撰寫（線上服務改版時以實際回應為準）。"""
from app.data import news_rss, twse, yahoo
from app.data.base import pick, to_float


def test_to_float_handles_twse_strings():
    assert to_float("1,234.5") == 1234.5
    assert to_float("--") is None
    assert to_float("X0.00") == 0.0
    assert to_float("") is None


def test_pick_substring_match():
    rec = {"營業收入-去年同月增減(%)": "12.5", "公司代號": "2330"}
    assert pick(rec, "去年同月增減") == "12.5"
    assert pick(rec, "不存在") is None


def test_twse_daily_quotes():
    rows = [{"Date": "1151104", "Code": "2330", "Name": "台積電", "TradeVolume": "30,000,000",
             "TradeValue": "45,000,000,000", "OpeningPrice": "1,480.00", "HighestPrice": "1,500.00",
             "LowestPrice": "1,470.00", "ClosingPrice": "1,495.00", "Change": "15.00", "Transaction": "50000"}]
    df = twse.parse_daily_quotes(rows)
    assert df.loc[0, "close"] == 1495.0 and df.loc[0, "date"] == "2026-11-04"


def test_twse_revenue_and_valuation():
    rev = twse.parse_monthly_revenue([{"資料年月": "11410", "公司代號": "2330", "公司名稱": "台積電", "產業別": "半導體業",
                                       "營業收入-當月營收": "330000000", "營業收入-上月比較增減(%)": "-2.1",
                                       "營業收入-去年同月增減(%)": "25.3", "累計營業收入-前期比較增減(%)": "30.1"}])
    assert rev.loc[0, "revenue_yoy"] == 25.3 and rev.loc[0, "revenue_mom"] == -2.1
    val = twse.parse_valuation([{"Code": "2330", "Name": "台積電", "PEratio": "22.5", "DividendYield": "1.5", "PBratio": "6.8"}])
    assert val.loc[0, "pe"] == 22.5 and val.loc[0, "pb"] == 6.8


def test_yahoo_chart_adjusts_prices():
    payload = {"chart": {"result": [{"timestamp": [1762214400, 1762300800],
        "indicators": {"quote": [{"open": [10, 11], "high": [11, 12], "low": [9, 10], "close": [10, 11], "volume": [100, 200]}],
                       "adjclose": [{"adjclose": [9, 11]}]}}], "error": None}}
    df = yahoo.parse_chart(payload)
    assert list(df["close"]) == [9, 11]
    assert df["open"].iloc[0] == 9  # 依還原比例調整


def test_rss_strips_source_suffix():
    xml = """<rss><channel><item><title>台積電營收創新高 - 經濟日報</title><link>https://x</link>
      <pubDate>Tue, 04 Nov 2026 02:00:00 GMT</pubDate><source url="https://money.udn.com">經濟日報</source></item></channel></rss>"""
    items = news_rss.parse_rss(xml)
    assert items[0]["title"] == "台積電營收創新高" and items[0]["source"] == "經濟日報"
