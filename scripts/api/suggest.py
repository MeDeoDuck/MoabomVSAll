"""검색 후보 자동 제안 — DB → Serper Web Search → LLM 추출 캐스케이드.

사용자가 "아이폰11" 처럼 모호하게 입력하면 우측 패널에 정확 제품명 후보를
띄우기 위한 모듈. 보고서 파이프라인과 완전히 독립적이며 suggest 는 보조
기능이므로 어떤 외부 호출이 실패해도 5xx 를 내지 않는다 (graceful degrade).

캐스케이드:
  1) tech_products ILIKE 매칭 (LEFT JOIN 으로 has_report 산출)
  2) DB 결과 ≤ MIN_DB_HITS 이면 Serper Web Search 호출
  3) Serper 응답의 knowledgeGraph + organic 상위를 GPT-4.1 에 넘겨 정확
     제품명만 "추출". LLM 은 외부 지식 사용 금지(프롬프트로 강제) → GPT-4.1
     cutoff 이후 출시 제품도 검색 결과에 들어있으면 그대로 잡힘.

캐시: suggest_cache(query_norm PK, response_json, expires_at). TTL 7일.
"""
from __future__ import annotations

import json
import os
import unicodedata
from time import perf_counter
from typing import Any, Dict, List

import psycopg2.extras

from scripts.database.queries import query_all, query_one, execute_update


# ── 튜닝 상수 ────────────────────────────────────────────────────────────
MIN_DB_HITS = 2           # DB 결과 ≤ 이 값이면 Serper/LLM 보완
DEFAULT_LIMIT = 6         # 응답 카드 최대 수
CACHE_TTL_DAYS = 7
SERPER_TIMEOUT = 10
LLM_MAX_TOKENS = 500
LLM_CONFIDENCE_THRESHOLD = 0.70


# ── normalize ───────────────────────────────────────────────────────────
def normalize_query(q: str) -> str:
    """캐시 키·매칭 일관성 위한 정규화.

    NFKC → LOWER → 공백/하이픈 제거. 한글 자모 분해는 v1 SKIP(후속).
    """
    if not q:
        return ""
    s = unicodedata.normalize("NFKC", q).lower().strip()
    return s.replace(" ", "").replace("-", "")


# ── DB 매칭 ─────────────────────────────────────────────────────────────
def db_match(q_norm: str, limit: int = DEFAULT_LIMIT) -> List[Dict[str, Any]]:
    """tech_products ILIKE 매칭 + has_report 플래그 LEFT JOIN 한 번에.

    q_norm 은 normalize_query 결과(공백/하이픈 제거됨)이지만 DB 는 원본
    표기를 가지고 있으므로 LOWER 후 공백·하이픈 제거한 표현식과 LIKE.
    """
    if not q_norm:
        return []
    pattern = f"%{q_norm}%"
    rows = query_all(
        """
        SELECT
            p.product_id,
            p.name,
            p.brand,
            p.category,
            p.image_url,
            CASE WHEN r.product_id IS NOT NULL THEN TRUE ELSE FALSE END AS has_report
        FROM tech_products p
        LEFT JOIN (
            SELECT DISTINCT product_id FROM product_integrated_reports
        ) r ON r.product_id = p.product_id
        WHERE REPLACE(REPLACE(LOWER(p.name),  ' ', ''), '-', '') ILIKE %s
           OR REPLACE(REPLACE(LOWER(COALESCE(p.brand, '')), ' ', ''), '-', '') ILIKE %s
        ORDER BY has_report DESC, p.created_at DESC
        LIMIT %s
        """,
        (pattern, pattern, limit),
    )
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "name": r["name"],
                "brand": r.get("brand") or "",
                "category": r.get("category") or "",
                "image_url": r.get("image_url") or "",
                "product_id": r["product_id"],
                "has_report": bool(r["has_report"]),
                "source": "db",
            }
        )
    return out


# ── Serper Web Search ───────────────────────────────────────────────────
def serper_search(q: str) -> Dict[str, Any]:
    """google.serper.dev/search 호출 → knowledgeGraph + organic 상위 5개.

    예외(timeout·5xx·키 부재)는 모두 빈 dict 로 안전 퇴화. 호출부가 빈
    페이로드를 받으면 LLM 추출도 skip.
    """
    import requests  # 지연 import (오프라인 테스트 보호)

    from scripts.config import SERPER_API_KEY, SERPER_SEARCH_ENDPOINT

    if not SERPER_API_KEY:
        return {}

    try:
        resp = requests.post(
            SERPER_SEARCH_ENDPOINT,
            headers={
                "X-API-KEY": SERPER_API_KEY,
                "Content-Type": "application/json",
            },
            json={"q": q, "gl": "kr", "hl": "ko", "num": 10},
            timeout=SERPER_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json() or {}
    except Exception as e:
        print(f"[SUGGEST_PERF] serper_search failed q={q!r} err={e}")
        return {}

    payload: Dict[str, Any] = {}
    kg = data.get("knowledgeGraph") or {}
    if kg:
        payload["knowledgeGraph"] = {
            "title": kg.get("title") or "",
            "type": kg.get("type") or "",
            "description": kg.get("description") or "",
            "attributes": kg.get("attributes") or {},
        }
    organic = (data.get("organic") or [])[:5]
    payload["organic"] = [
        {"title": (o.get("title") or "")[:200], "snippet": (o.get("snippet") or "")[:300]}
        for o in organic
    ]
    return payload


# ── LLM 추출 (외부 지식 사용 금지 — 검색 결과 텍스트에서만) ───────────
_LLM_SYSTEM = (
    "당신은 검색 결과 텍스트에서 IT/가전 제품명만 추출하는 도우미입니다. "
    "본인의 지식이나 기억은 절대 사용하지 마세요 — 오직 제공된 검색 결과 "
    "텍스트에 명시적으로 등장하는 제품명만 추출합니다. 검색 결과에 없는 "
    "제품은 절대 만들어내지 마세요. cutoff 이후 출시 제품도 검색 결과에 "
    "있으면 그대로 추출하세요."
)


def _compact_db_hits(db_hits: List[Dict[str, Any]]) -> str:
    if not db_hits:
        return "(없음)"
    return "\n".join(
        f"- {h['name']} (brand={h.get('brand') or '-'})" for h in db_hits[:5]
    )


def _format_serper(payload: Dict[str, Any]) -> str:
    if not payload:
        return "(없음)"
    lines: List[str] = []
    kg = payload.get("knowledgeGraph") or {}
    if kg:
        lines.append(
            f"knowledgeGraph: title={kg.get('title')!r} "
            f"type={kg.get('type')!r} description={kg.get('description')!r}"
        )
    organic = payload.get("organic") or []
    if organic:
        lines.append("organic 상위:")
        for i, o in enumerate(organic, 1):
            lines.append(f"  {i}. 제목: {o.get('title')!r}")
            lines.append(f"     스니펫: {o.get('snippet')!r}")
    return "\n".join(lines) if lines else "(없음)"


def llm_extract(
    q: str, serper_payload: Dict[str, Any], db_hits: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Serper 페이로드에서 정확 제품명 추출. 외부 지식 사용 금지.

    빈 페이로드면 호출 skip → 빈 배열. LLM 응답이 JSON 깨지면 빈 배열.
    """
    if not serper_payload:
        return []

    from scripts.llm import get_chat_llm

    user_prompt = (
        f"사용자 검색어: {q}\n"
        f"이미 DB 매칭된 후보(중복 방지 힌트, 비어있을 수 있음):\n"
        f"{_compact_db_hits(db_hits)}\n\n"
        f"[검색 결과]\n"
        f"{_format_serper(serper_payload)}\n\n"
        f'JSON 스키마: {{"candidates":[{{"name":"...","brand":"...",'
        f'"category":"...","confidence":0.0~1.0}}]}}\n'
        f"규칙:\n"
        f"- candidates 최대 5개\n"
        f"- confidence < {LLM_CONFIDENCE_THRESHOLD} 항목 제외\n"
        f"- 검색 결과에 명시적으로 등장하는 제품명만 추출\n"
        f"- 검색 결과에 제품명 추출할 텍스트가 없으면 빈 배열\n"
        f"- 검색 결과에 없는 제품을 추측·생성하면 안 됨\n"
        f'- 한국 정식 출시명 우선 (예: "갤럭시 Z 폴드6", "아이폰 15 Pro Max")'
    )

    try:
        llm = get_chat_llm(temperature=0.0, max_tokens=LLM_MAX_TOKENS)
        # langchain ChatOpenAI: bind 로 OpenAI 호환 response_format 강제.
        llm_json = llm.bind(response_format={"type": "json_object"})
        resp = llm_json.invoke([
            {"role": "system", "content": _LLM_SYSTEM},
            {"role": "user", "content": user_prompt},
        ])
        content = resp.content if hasattr(resp, "content") else str(resp)
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        print(f"[SUGGEST_PERF] llm_extract json decode failed err={e}")
        return []
    except Exception as e:
        print(f"[SUGGEST_PERF] llm_extract failed err={e}")
        return []

    candidates = parsed.get("candidates") or []
    out: List[Dict[str, Any]] = []
    for c in candidates[:5]:
        try:
            name = (c.get("name") or "").strip()
            if not name:
                continue
            confidence = float(c.get("confidence") or 0.0)
            if confidence < LLM_CONFIDENCE_THRESHOLD:
                continue
            out.append(
                {
                    "name": name,
                    "brand": (c.get("brand") or "").strip(),
                    "category": (c.get("category") or "").strip(),
                    "image_url": "",
                    "product_id": None,
                    "has_report": False,
                    "source": "serper",
                    "confidence": confidence,
                }
            )
        except (TypeError, ValueError):
            continue
    return out


# ── 캐시 I/O ────────────────────────────────────────────────────────────
def _cache_get(q_norm: str) -> List[Dict[str, Any]] | None:
    row = query_one(
        """
        SELECT response_json FROM suggest_cache
        WHERE query_norm = %s AND expires_at > NOW()
        """,
        (q_norm,),
    )
    if not row:
        return None
    payload = row["response_json"]
    if isinstance(payload, (str, bytes)):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, list) else None


def _cache_set(q_norm: str, items: List[Dict[str, Any]]) -> None:
    try:
        execute_update(
            """
            INSERT INTO suggest_cache (query_norm, response_json, created_at, expires_at)
            VALUES (%s, %s::jsonb, NOW(), NOW() + (%s || ' days')::interval)
            ON CONFLICT (query_norm) DO UPDATE
              SET response_json = EXCLUDED.response_json,
                  created_at = EXCLUDED.created_at,
                  expires_at = EXCLUDED.expires_at
            """,
            (q_norm, json.dumps(items, ensure_ascii=False), str(CACHE_TTL_DAYS)),
        )
    except Exception as e:
        print(f"[SUGGEST_PERF] cache_set failed q_norm={q_norm!r} err={e}")


# ── dedupe ──────────────────────────────────────────────────────────────
def _dedupe(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """(LOWER(name), LOWER(brand)) 키로 중복 제거. DB 결과 우선(앞에서)."""
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for it in items:
        key = (
            (it.get("name") or "").strip().lower(),
            (it.get("brand") or "").strip().lower(),
        )
        if not key[0] or key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


# ── 오케스트레이션 ──────────────────────────────────────────────────────
def suggest(q: str, limit: int = DEFAULT_LIMIT) -> List[Dict[str, Any]]:
    """후보 카드 리스트 반환. 어떤 실패도 5xx 로 나가지 않는다."""
    t0 = perf_counter()
    q = (q or "").strip()
    if len(q) < 2:
        return []

    q_norm = normalize_query(q)
    cache_hit = False
    serper_called = False
    llm_called = False

    cached = _cache_get(q_norm)
    if cached is not None:
        cache_hit = True
        ms = (perf_counter() - t0) * 1000
        print(
            f"[SUGGEST_PERF] q={q!r} cache=HIT total_ms={ms:.1f} n={len(cached)}"
        )
        return cached[:limit]

    db_hits = db_match(q_norm, limit=limit)
    serper_items: List[Dict[str, Any]] = []
    if len(db_hits) <= MIN_DB_HITS:
        payload = serper_search(q)
        serper_called = True
        serper_items = llm_extract(q, payload, db_hits)
        llm_called = bool(payload)

    merged = _dedupe(db_hits + serper_items)[:limit]
    _cache_set(q_norm, merged)

    ms = (perf_counter() - t0) * 1000
    print(
        f"[SUGGEST_PERF] q={q!r} cache=MISS db_hits={len(db_hits)} "
        f"serper_called={serper_called} llm_called={llm_called} "
        f"merged={len(merged)} total_ms={ms:.1f}"
    )
    return merged


# psycopg2 JSONB 직렬화 보조 (필요 시 호출부에서 사용)
def _register_jsonb_adapter() -> None:
    psycopg2.extras.register_default_jsonb(loads=json.loads, globally=True)


_register_jsonb_adapter()
