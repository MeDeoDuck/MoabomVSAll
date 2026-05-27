"""검색 후보 자동 제안 오케스트레이션 회귀.

규약:
- 회귀는 오프라인 — DB/네트워크/LLM/임베딩 호출은 모두 mock 으로 격리.
- 검증 대상은 scripts.api.suggest.suggest() 의 캐스케이드 흐름과 dedupe.

기존 케이스: DB 충분 / DB 0건 fallback / 캐시 hit / 짧은 쿼리 / 정규화 / dedupe
v2 추가 케이스:
- alias 치환 동작 (suggest_aliases.apply_aliases 단위)
- 시맨틱 fallback (DB 0 + semantic 적중 → Serper 미호출)
- 시맨틱 skip (DB 충분 → search_semantic 미호출)
"""
from __future__ import annotations

from unittest.mock import patch

from scripts.api import suggest as suggest_mod
from scripts.api import suggest_aliases


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
    """DB 결과 > MIN_DB_HITS 이면 Serper·LLM·semantic 모두 호출되지 않는다."""
    db_hits = _db_hit_factory(suggest_mod.MIN_DB_HITS + 2)

    with patch.object(suggest_mod, "_cache_get", return_value=None) as m_get, \
         patch.object(suggest_mod, "_cache_set", return_value=None) as m_set, \
         patch.object(suggest_mod, "db_match", return_value=db_hits) as m_db, \
         patch.object(suggest_mod, "serper_search") as m_ser, \
         patch.object(suggest_mod, "llm_extract") as m_llm, \
         patch("scripts.api.suggest_vector.search_semantic") as m_sem:
        items = suggest_mod.suggest("아이폰", limit=6)

    assert m_get.called
    assert m_db.called
    assert not m_sem.called, "DB 결과 충분 시 시맨틱 호출 금지"
    assert not m_ser.called, "DB 결과 충분 시 Serper 호출 금지"
    assert not m_llm.called, "DB 결과 충분 시 LLM 호출 금지"
    assert m_set.called
    assert len(items) == len(db_hits)
    assert all(it["source"] == "db" for it in items)


def test_db_empty_falls_back_to_serper_and_llm():
    """DB 0 + semantic 0건 → Serper+LLM 호출되고 결과가 응답에 포함."""
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
         patch.object(suggest_mod, "llm_extract", return_value=fake_llm) as m_llm, \
         patch("scripts.api.suggest_vector.search_semantic", return_value=[]) as m_sem:
        items = suggest_mod.suggest("아이폰11", limit=6)

    assert m_db.called
    assert m_sem.called, "DB 0건일 때 시맨틱 fallback 발동"
    assert m_ser.called, "DB+semantic 0건일 때 Serper 호출 필수"
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
    """공백·하이픈 제거 + alias 치환까지 normalize 한 줄로 처리.

    v2 고도화로 영-한 alias 치환이 normalize 안에서 적용됨 — 영문 입력은
    바로 한국 정규형이 되어 DB ILIKE 와 매칭 가능.
    """
    # 한글 입력은 alias 미적용 (그대로)
    assert suggest_mod.normalize_query("아이폰 11") == "아이폰11"
    # 영문 입력은 alias 치환 적용
    assert suggest_mod.normalize_query("Galaxy-S25") == "갤럭시s25"
    assert suggest_mod.normalize_query("  iPhone  16  Pro  ") == "아이폰16pro"


# ── v2 고도화: alias + 시맨틱 fallback ──────────────────────────────────

def test_alias_translates_galaxy_to_korean():
    """apply_aliases: 영문 라인업이 한글로 치환되어 normalize 결과에 반영."""
    # normalize_query 직접 호출 — apply_aliases가 안에서 실행되어야
    assert suggest_mod.normalize_query("galaxy s25") == "갤럭시s25"
    assert suggest_mod.normalize_query("iphone 17 pro") == "아이폰17pro"
    assert suggest_mod.normalize_query("Macbook Pro 14") == "맥북pro14"
    assert suggest_mod.normalize_query("AirPods Pro 2") == "에어팟pro2"
    # 키 길이 내림차순: galaxywatch 가 galaxy 보다 먼저 매칭
    assert suggest_mod.normalize_query("galaxy watch ultra") == "갤럭시워치ultra"
    # apply_aliases 단위
    assert suggest_aliases.apply_aliases("galaxyfold") == "갤럭시fold"  # fold는 alias
    assert suggest_aliases.apply_aliases("zfold6") == "z폴드6"
    # 한글 입력은 그대로 통과
    assert suggest_aliases.apply_aliases("갤럭시s25") == "갤럭시s25"


def test_semantic_fallback_after_db_empty():
    """DB 0건이지만 시맨틱이 잡으면 → Serper 미호출 (비용·지연 절감)."""
    fake_semantic = [
        {
            "name": "갤럭시 Z 폴드 7",
            "brand": "Samsung",
            "category": "스마트폰",
            "image_url": "",
            "product_id": 999,
            "has_report": False,
            "source": "vector",
            "score": 0.72,
        },
        {
            "name": "갤럭시 Z 폴드 6",
            "brand": "Samsung",
            "category": "스마트폰",
            "image_url": "",
            "product_id": 998,
            "has_report": False,
            "source": "vector",
            "score": 0.68,
        },
        {
            "name": "갤럭시 Z 폴드 5",
            "brand": "Samsung",
            "category": "스마트폰",
            "image_url": "",
            "product_id": 997,
            "has_report": False,
            "source": "vector",
            "score": 0.61,
        },
    ]

    with patch.object(suggest_mod, "_cache_get", return_value=None), \
         patch.object(suggest_mod, "_cache_set", return_value=None), \
         patch.object(suggest_mod, "db_match", return_value=[]) as m_db, \
         patch("scripts.api.suggest_vector.search_semantic", return_value=fake_semantic) as m_sem, \
         patch.object(suggest_mod, "serper_search") as m_ser, \
         patch.object(suggest_mod, "llm_extract") as m_llm:
        items = suggest_mod.suggest("foldable phone", limit=6)

    assert m_db.called
    assert m_sem.called, "DB 0건 시 시맨틱 호출 필수"
    # DB(0) + semantic(3) > MIN_DB_HITS(2) → Serper 차단되어야
    assert not m_ser.called, "시맨틱이 충분히 잡으면 Serper 차단"
    assert not m_llm.called
    assert len(items) == 3
    assert all(it["source"] == "vector" for it in items)


def test_semantic_failure_is_isolated():
    """시맨틱 검색이 예외 던져도 부팅·캐스케이드는 정상 진행."""
    with patch.object(suggest_mod, "_cache_get", return_value=None), \
         patch.object(suggest_mod, "_cache_set", return_value=None), \
         patch.object(suggest_mod, "db_match", return_value=[]) as m_db, \
         patch("scripts.api.suggest_vector.search_semantic",
               side_effect=RuntimeError("embed timeout")) as m_sem, \
         patch.object(suggest_mod, "serper_search", return_value={}) as m_ser, \
         patch.object(suggest_mod, "llm_extract", return_value=[]) as m_llm:
        items = suggest_mod.suggest("zxqw1234", limit=6)

    assert m_db.called
    assert m_sem.called
    # semantic 예외라 격리되고 Serper 진행
    assert m_ser.called, "시맨틱 실패 후 Serper 로 폴백"
    assert items == []


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
