"""scripts/popup/product_meta.py 단위 테스트 (순수 파서 + 주입 search_fn).

오프라인 — 외부 Serper/DB 호출 없음.
"""
from scripts.popup.product_meta import (
    fetch_meta,
    format_price_display,
    official_domain_for,
    parse_prices,
    parse_release_year,
    parse_screen,
)


# ── 순수 파서 ────────────────────────────────────────────────


def test_parse_prices_man_won_formats():
    text = "최저 129만 원~ / 정가 1,290,000원 / 호환 5만원"
    prices = parse_prices(text)
    # 5만원(50000)은 합리범위 통과(>=50000) — 129만(1,290,000)·1,290,000 둘 다 통과
    assert 1_290_000 in prices
    # 가장 낮은 값이 출시가 추정으로 쓰임
    assert min(prices) <= 1_290_000


def test_parse_prices_ignores_noise():
    # 합리 범위(5만~1500만) 밖은 제외
    assert parse_prices("3원") == []
    assert parse_prices("99999999원") == []


def test_parse_screen_first_match():
    assert parse_screen("6.7인치 OLED 화면") == "6.7인치"
    assert parse_screen("화면 없음") is None


def test_parse_release_year_recent_first():
    assert parse_release_year("2026년 출시") == 2026
    # 너무 옛날 연도는 무시
    assert parse_release_year("1999년 모델") is None


# ── Phase 5 마지막 보강 1: 출시연도 정규식 강화 ───────────────────


def test_parse_release_year_explicit_release_phrases():
    """명시적 출시 표현 — '출시/발매/공개/런칭/등장/출시예정'."""
    assert parse_release_year("2024년 출시 아이폰16") == 2024
    assert parse_release_year("삼성 갤럭시 S25는 2025년 발매") == 2025
    assert parse_release_year("2023년 공개된 제품") == 2023
    assert parse_release_year("2026년 출시 예정") == 2026
    assert parse_release_year("2024년 런칭") == 2024


def test_parse_release_year_generic_year():
    """일반 '20XX년' 표기 — 출시 표현이 없을 때 fallback."""
    assert parse_release_year("2024년 모델 비교") == 2024
    assert parse_release_year("2023년 신제품") == 2023


def test_parse_release_year_standalone_4digit_fallback():
    """본문에 '년' 없이 4자리만 있는 경우 — 마지막 fallback."""
    assert parse_release_year("iPhone 2024 Pro Max") == 2024
    assert parse_release_year("Galaxy S25 / 2025 / Best") == 2025


def test_parse_release_year_priority_release_first():
    """우선순위 — 명시적 출시 표현이 일반 연도보다 먼저 매칭."""
    # 텍스트에 2023(일반)·2024(출시) 둘 다 있으면 출시 표현이 우선
    text = "2023년 리뷰. 2024년 출시 예정."
    assert parse_release_year(text) == 2024


def test_parse_release_year_range_filter():
    """2010~2030 밖은 모두 무시."""
    assert parse_release_year("2009년 출시") is None
    assert parse_release_year("2099년 출시") is None
    assert parse_release_year("1999") is None
    # 경계
    assert parse_release_year("2010년 출시") == 2010
    assert parse_release_year("2030년 출시") == 2030


def test_parse_release_year_empty_or_no_match():
    assert parse_release_year("") is None
    assert parse_release_year(None) is None
    assert parse_release_year("이 텍스트에는 연도가 없다") is None


def test_format_price_display_man_unit():
    assert format_price_display(1_290_000) == "최저 129만 원~"
    assert format_price_display(500_000) == "최저 50만 원~"
    assert format_price_display(None) is None
    assert format_price_display(0) is None


# ── 조합기 — search_fn 주입 ────────────────────────────────


def test_fetch_meta_combines_fields_from_search():
    def fake_search(q):
        return [
            {"title": "노바폰 5 Pro 출시", "snippet": "2026년 출시 6.7인치 OLED", "link": "https://x/1"},
            {"title": "가격 동향", "snippet": "최저 129만 원~ / 정가 1,290,000원", "link": "https://x/2"},
        ], {"query": q, "ms": 1.0}

    fields, perf = fetch_meta("노바폰 5 Pro", "노바", search_fn=fake_search, query_suffix="x")
    assert fields["screen_size"] == "6.7인치"
    assert fields["release_year"] == 2026
    assert fields["price_display"] == "최저 129만 원~"
    # 보강 9: 캐시 정책 태그 v2 → v3-loose
    assert fields["source"].startswith("v3-loose|") and fields["source"].endswith("https://x/1")
    assert perf.get("ms") == 1.0


def test_fetch_meta_partial_fields_safe():
    # 검색 결과는 있는데 가격 파싱 실패
    def fake_search(q):
        return [{"title": "스펙", "snippet": "6.7인치 OLED", "link": ""}], {}

    fields, _ = fetch_meta("노바폰", "", search_fn=fake_search, query_suffix="x")
    assert fields["screen_size"] == "6.7인치"
    assert fields["price_display"] is None  # 가격 못 찾음 → None(§7 fallback)
    assert fields["release_year"] is None


def test_official_domain_for_light_mapping():
    # 보강 6: 가벼운 매핑 — 한/영 표기 모두 일치, 없는 브랜드는 None
    assert official_domain_for("Apple") == "apple.com"
    assert official_domain_for("애플") == "apple.com"
    assert official_domain_for("samsung electronics") == "samsung.com"
    assert official_domain_for("LG") == "lge.co.kr"
    assert official_domain_for("xiaomi") == "mi.com"
    assert official_domain_for("Nothing") is None  # 매핑 없음 → 일반 검색
    assert official_domain_for("") is None


def test_fetch_meta_takes_lowest_price_from_all_results():
    # 보강 9: 도메인 후필터 폐기. 전체 결과의 **최저가** 채택("최저 N만 원~"
    # 표기 의도와 일치). source 의 raw_link 는 공식 매칭이 있으면 그쪽으로.
    def fake_search(q):
        return [
            {"title": "쇼핑몰 할인", "snippet": "최저 50만 원~", "link": "https://kt-mall.co.kr/p"},
            {"title": "Apple 공식", "snippet": "최저 129만 원~", "link": "https://apple.com/iphone"},
            {"title": "스펙", "snippet": "6.1인치 2023년 출시", "link": "https://news.x.com/p"},
        ], {}

    fields, perf = fetch_meta("아이폰15", "Apple", search_fn=fake_search, query_suffix="x")
    # 보강 9: 전체 결과 최저가 = 50만 (이전엔 공식만 봐서 129만이었음)
    assert fields["price_display"] == "최저 50만 원~"
    # source 의 raw_link 는 공식 매칭(추적용) 이 있으면 거기로
    assert "apple.com/iphone" in fields["source"]
    assert perf.get("site") == "apple.com"
    assert perf.get("official_items") == 1
    # 스펙(인치·연도)은 전체 결과에서
    assert fields["screen_size"] == "6.1인치"
    assert fields["release_year"] == 2023


def test_fetch_meta_price_taken_even_without_official_match():
    # 보강 9: 매핑된 브랜드인데 공식 도메인 매칭 0건이어도 — 일반 결과에서
    # 가격 채택(이전 None → 표시로 완화). 잘못된 가격이 빈 값보다 낫다는
    # 사용자 의도(상식 범위는 parse_prices 가 5만~1500만 1차 필터링).
    def fake_search(q):
        return [{"title": "쇼핑몰", "snippet": "최저 99만 원~ 6.7인치",
                 "link": "https://kt-mall.co.kr/p"}], {}

    fields, perf = fetch_meta("아이폰15", "Apple", search_fn=fake_search, query_suffix="x")
    assert fields["price_display"] == "최저 99만 원~"
    assert fields["screen_size"] == "6.7인치"
    assert perf.get("official_items") == 0


def test_fetch_meta_search_exception_safe_degrade():
    def boom(q):
        raise RuntimeError("network")

    fields, perf = fetch_meta("x", "", search_fn=boom, query_suffix="x")
    # 어떤 필드도 채워지지 않지만 raise 안 함
    assert all(fields[k] is None for k in (
        "price_raw", "price_display", "screen_size", "release_year", "source"))
    assert perf.get("error") == "RuntimeError"
