"""검색 후보 자동 제안 오케스트레이션 회귀.

규약:
- 회귀는 오프라인 — DB/네트워크/LLM 호출은 모두 mock 으로 격리.
- 검증 대상은 scripts.api.suggest.suggest() 의 캐스케이드 흐름과 dedupe.

3 케이스:
(a) DB hit 충분 → Serper·LLM 미호출
(b) DB 부족(0건) → Serper+LLM 호출, 결과 반환
(c) 캐시 hit → db_match·Serper 둘 다 미호출
"""
from __future__ import annotations

from unittest.mock import patch

from scripts.api import suggest as suggest_mod


def _db_hit_factory(count: int):
    return [
        {
            "name": f"제품 {i}",
            "brand": "Apple",
            "category": "스마트폰",
            "image_url": "",
            "product_id": 100 + i,
            "has_report": (i == 0),
            "source": "db",
        }
        for i in range(count)
    ]


def test_db_hits_sufficient_skips_external_calls():
    """DB 결과 > MIN_DB_HITS 이면 Serper·LLM 둘 다 호출되지 않는다."""
    db_hits = _db_hit_factory(suggest_mod.MIN_DB_HITS + 2)

    with patch.object(suggest_mod, "_cache_get", return_value=None) as m_get, \
         patch.object(suggest_mod, "_cache_set", return_value=None) as m_set, \
         patch.object(suggest_mod, "db_match", return_value=db_hits) as m_db, \
         patch.object(suggest_mod, "serper_search") as m_ser, \
         patch.object(suggest_mod, "llm_extract") as m_llm:
        items = suggest_mod.suggest("아이폰", limit=6)

    assert m_get.called
    assert m_db.called
    assert not m_ser.called, "DB 결과 충분 시 Serper 호출 금지"
    assert not m_llm.called, "DB 결과 충분 시 LLM 호출 금지"
    assert m_set.called
    assert len(items) == len(db_hits)
    assert all(it["source"] == "db" for it in items)


def test_db_empty_falls_back_to_serper_and_llm():
    """DB 0건 → Serper+LLM 호출되고 결과가 응답에 포함."""
    fake_serper = {
        "knowledgeGraph": {"title": "iPhone 11", "description": "..."},
        "organic": [{"title": "아이폰 11 Pro 리뷰", "snippet": "..."}],
    }
    fake_llm = [
        {
            "name": "아이폰 11 Pro",
            "brand": "Apple",
            "category": "스마트폰",
            "image_url": "",
            "product_id": None,
            "has_report": False,
            "source": "serper",
            "confidence": 0.92,
        }
    ]

    with patch.object(suggest_mod, "_cache_get", return_value=None), \
         patch.object(suggest_mod, "_cache_set", return_value=None), \
         patch.object(suggest_mod, "db_match", return_value=[]) as m_db, \
         patch.object(suggest_mod, "serper_search", return_value=fake_serper) as m_ser, \
         patch.object(suggest_mod, "llm_extract", return_value=fake_llm) as m_llm:
        items = suggest_mod.suggest("아이폰11", limit=6)

    assert m_db.called
    assert m_ser.called, "DB 0건일 때 Serper 호출 필수"
    assert m_llm.called, "Serper 응답 시 LLM 추출 필수"
    assert len(items) == 1
    assert items[0]["source"] == "serper"
    assert items[0]["name"] == "아이폰 11 Pro"


def test_cache_hit_skips_db_and_external():
    """캐시 hit → db_match·Serper·LLM 모두 호출되지 않는다."""
    cached = _db_hit_factory(2)

    with patch.object(suggest_mod, "_cache_get", return_value=cached) as m_get, \
         patch.object(suggest_mod, "_cache_set") as m_set, \
         patch.object(suggest_mod, "db_match") as m_db, \
         patch.object(suggest_mod, "serper_search") as m_ser, \
         patch.object(suggest_mod, "llm_extract") as m_llm:
        items = suggest_mod.suggest("아이폰", limit=6)

    assert m_get.called
    assert not m_db.called, "캐시 hit 시 DB 조회 금지"
    assert not m_ser.called
    assert not m_llm.called
    assert not m_set.called, "캐시 hit 시 cache_set 금지"
    assert items == cached[:6]


def test_short_query_returns_empty_without_calls():
    """2자 미만 입력은 즉시 빈 결과 — 어떤 호출도 일어나지 않아야."""
    with patch.object(suggest_mod, "_cache_get") as m_get, \
         patch.object(suggest_mod, "db_match") as m_db, \
         patch.object(suggest_mod, "serper_search") as m_ser, \
         patch.object(suggest_mod, "llm_extract") as m_llm:
        assert suggest_mod.suggest("a", limit=6) == []
        assert suggest_mod.suggest("", limit=6) == []

    assert not m_get.called
    assert not m_db.called
    assert not m_ser.called
    assert not m_llm.called


def test_normalize_query_strips_spaces_and_hyphens():
    assert suggest_mod.normalize_query("아이폰 11") == "아이폰11"
    assert suggest_mod.normalize_query("Galaxy-S25") == "galaxys25"
    assert suggest_mod.normalize_query("  iPhone  16  Pro  ") == "iphone16pro"


def test_dedupe_keeps_db_then_serper():
    """같은 (name, brand) 중복은 앞에 있는 DB 결과 우선."""
    db_item = {
        "name": "아이폰 11 Pro",
        "brand": "Apple",
        "category": "스마트폰",
        "image_url": "https://example.com/a.jpg",
        "product_id": 7,
        "has_report": True,
        "source": "db",
    }
    serper_item = {
        "name": "아이폰 11 Pro",
        "brand": "Apple",
        "category": "스마트폰",
        "image_url": "",
        "product_id": None,
        "has_report": False,
        "source": "serper",
        "confidence": 0.9,
    }
    deduped = suggest_mod._dedupe([db_item, serper_item])
    assert len(deduped) == 1
    assert deduped[0]["source"] == "db"
    assert deduped[0]["product_id"] == 7
