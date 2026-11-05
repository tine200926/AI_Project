from datetime import datetime, timezone

from app.analysis import sentiment as s

NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)


def test_promo_is_filtered():
    items = [
        {"title": "明天必漲！老師免費報牌，加LINE領取飆股名單", "source": "某部落格"},
        {"title": "台積電10月營收創新高，年增36%", "source": "經濟日報", "published": "2026-11-04T10:00:00+08:00"},
    ]
    res = s.analyze(items, ["台積電"], now=NOW)
    assert res["counts"]["filtered"] == 1
    assert "灌水" in res["filtered"][0]["reason"]
    assert res["articles"][0]["tone"] == "pos"


def test_duplicates_merge_but_different_companies_do_not():
    items = [
        {"title": "台積電法說會釋利多，法人調升目標價", "source": "鉅亨網"},
        {"title": "台積電法說會釋利多 法人調升目標價", "source": "Yahoo股市"},
        {"title": "鴻海法說會釋利多，法人調升目標價", "source": "中央社"},
    ]
    res = s.analyze(items, now=NOW)
    assert res["counts"]["kept"] == 2
    assert max(a["coverage"] for a in res["articles"]) == 2


def test_negation_flips_polarity():
    pos, _ = s.lexicon_score("營收成長")
    neg, _ = s.lexicon_score("營收未成長")
    assert pos > 0 > neg


def test_negative_news_scores_bearish():
    items = [{"title": t, "source": "工商時報", "published": "2026-11-04T10:00:00+08:00"} for t in (
        "某公司遭客戶砍單，下修全年財測", "某公司第三季轉虧，毛利率重挫", "外資大舉賣超某公司，股價跌停")]
    res = s.analyze(items, now=NOW)
    assert res["label"] == "偏空" and res["score"] < -25


def test_source_weights():
    assert s.source_weight("中央社") > s.source_weight("ETtoday") > s.source_weight("不知名網站")


def test_digest_only_quotes_titles():
    items = [{"title": "台積電接單暢旺", "source": "MoneyDJ"}]
    res = s.analyze(items, now=NOW)
    assert any("台積電接單暢旺" in line for line in res["digest"])
