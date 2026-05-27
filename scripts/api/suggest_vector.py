"""검색 후보 자동 제안 — 임베딩 기반 시맨틱 fallback (v2 고도화).

DB ILIKE 매칭 + alias 치환으로도 못 잡는 표현(예: 'galaxy foldable',
'iphone fifteen', 오타)을 multilingual 임베딩(`text-embedding-3-small`)의
한-영 cross-lingual cosine 유사도로 잡는다. 보고서 RAG (`scripts/rag/`)와
완전히 분리된 sqlite3 파일(`seeds/.vector_cache.sqlite3`)에 저장 — Phase 2-b
RAG 와 스키마/제품키 충돌 없음.

캐스케이드 내 위치:
    db_match → (≤MIN_DB_HITS 면) → search_semantic → (그래도 부족) → serper

설계:
- 시드 JSON 변경 감지: name+brand 의 sha256 hash 비교. 신규/변경 항목만 재임베딩.
- 메모리 cosine: sqlite3 전체 row 1회 로드 후 순회. 532행 × 1536차원 ≈ 6.5MB.
- MIN_SCORE 컷: 0.45 (RunYourAI text-embedding-3-small 13쌍 실측 — MATCH 최저
  0.48 vs NO-MATCH 최고 0.40 사이 중앙컷). 0.55 는 0.70+만 통과시켜 dead code.

모든 실패는 호출부(suggest.py)가 try/except 로 격리 — semantic 단계가 죽어도
DB+alias+Serper 경로는 계속 동작.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime
from time import perf_counter
from typing import Any, Dict, List, Optional


# ── sqlite3 helper ──────────────────────────────────────────────────────
def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(db_path: str) -> None:
    """벡터 테이블 생성 (idempotent). 보고서 RAG 와 별도 파일."""
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS product_vectors (
                product_key  TEXT PRIMARY KEY,
                name         TEXT NOT NULL,
                brand        TEXT,
                category     TEXT,
                embedding    TEXT NOT NULL,
                dim          INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                updated_at   TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_pv_hash "
            "ON product_vectors(content_hash)"
        )
        conn.commit()
    finally:
        conn.close()


def _content_hash(name: str, brand: str) -> str:
    payload = f"{(name or '').strip()}|{(brand or '').strip()}".lower()
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _product_key(name: str, brand: str) -> str:
    return f"{(name or '').strip().lower()}|{(brand or '').strip().lower()}"


# ── 인덱스 빌드 ─────────────────────────────────────────────────────────
def build_index_if_needed(json_path: str, db_path: str) -> Dict[str, Any]:
    """JSON 변경분만 임베딩하여 sqlite3 에 upsert.

    멱등성:
      ① 기존 sqlite3 의 (product_key, content_hash) 와 JSON 의 content_hash 비교
      ② hash 일치 row 는 skip
      ③ 신규/변경 row 만 RunYourAI 임베딩 1회 배치 호출 후 INSERT OR REPLACE

    반환: {built, cached, embed_ms, embed_calls, errors}. 호출부가 startup 로그용.
    """
    out = {
        "built": 0, "cached": 0, "embed_ms": 0.0, "embed_calls": 0,
        "errors": 0, "reason": "",
    }
    if not os.path.exists(json_path):
        out["reason"] = f"seed_json_not_found: {json_path}"
        return out

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            items = json.load(f)
        if not isinstance(items, list):
            out["reason"] = "seed_json_not_list"
            return out
    except Exception as e:
        out["reason"] = f"load_failed: {e}"
        out["errors"] += 1
        return out

    ensure_schema(db_path)

    # 기존 hash 로드
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT product_key, content_hash FROM product_vectors"
        ).fetchall()
        existing = {r["product_key"]: r["content_hash"] for r in rows}
    finally:
        conn.close()

    # 변경분 추출
    new_records: List[Dict[str, Any]] = []
    for it in items:
        name = (it.get("name") or "").strip()
        brand = (it.get("brand") or "").strip()
        category = (it.get("category") or "").strip()
        if not name:
            continue
        pk = _product_key(name, brand)
        ch = _content_hash(name, brand)
        if existing.get(pk) == ch:
            out["cached"] += 1
            continue
        new_records.append({
            "product_key": pk,
            "name": name,
            "brand": brand,
            "category": category,
            "content_hash": ch,
            "text": f"{name} {brand} {category}".strip(),
        })

    if not new_records:
        out["reason"] = f"all_cached ({out['cached']}건)"
        return out

    # 임베딩 (RunYourAI 게이트웨이, 보고서 RAG 와 동일 함수 재사용)
    try:
        from scripts.rag.embedder import default_embed_fn
    except ImportError as e:
        out["reason"] = f"embedder_import_failed: {e}"
        out["errors"] += 1
        return out

    try:
        texts = [r["text"] for r in new_records]
        vecs, perf = default_embed_fn(texts)
        if len(vecs) != len(new_records):
            out["reason"] = f"embed_count_mismatch: {len(vecs)} vs {len(new_records)}"
            out["errors"] += 1
            return out
        out["embed_ms"] = perf.get("embed_ms", 0.0)
        out["embed_calls"] = perf.get("embed_calls", 0)
    except Exception as e:
        out["reason"] = f"embed_failed: {e}"
        out["errors"] += 1
        return out

    # upsert
    now = datetime.utcnow().isoformat()
    conn = _connect(db_path)
    try:
        for r, v in zip(new_records, vecs):
            conn.execute(
                """
                INSERT INTO product_vectors
                  (product_key, name, brand, category, embedding, dim,
                   content_hash, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(product_key) DO UPDATE SET
                  name=excluded.name,
                  brand=excluded.brand,
                  category=excluded.category,
                  embedding=excluded.embedding,
                  dim=excluded.dim,
                  content_hash=excluded.content_hash,
                  updated_at=excluded.updated_at
                """,
                (
                    r["product_key"], r["name"], r["brand"], r["category"],
                    json.dumps(v, ensure_ascii=False), len(v),
                    r["content_hash"], now,
                ),
            )
            out["built"] += 1
        conn.commit()
    finally:
        conn.close()

    out["reason"] = f"built={out['built']} cached={out['cached']}"
    return out


# ── 시맨틱 검색 ─────────────────────────────────────────────────────────
def embed_query(q: str) -> Optional[List[float]]:
    """쿼리 텍스트 → 임베딩 벡터. 실패 시 None (호출부가 graceful degrade)."""
    if not q:
        return None
    try:
        from scripts.rag.embedder import default_embed_fn
        vecs, _ = default_embed_fn([q])
        return vecs[0] if vecs else None
    except Exception as e:
        print(f"[SUGGEST_PERF] embed_query failed q={q!r} err={e}")
        return None


def search_semantic(
    q: str,
    db_path: str,
    top_k: int = 6,
    min_score: float = 0.45,
) -> List[Dict[str, Any]]:
    """sqlite3 벡터 인덱스에서 cosine top_k 검색.

    1) 쿼리 임베딩 1회 (RunYourAI 호출)
    2) 전체 row 메모리 로드 + cosine 계산 (532행 ≈ 15ms)
    3) min_score 이상 + top_k 추출
    4) tech_products LEFT JOIN 으로 product_id·has_report·image_url 합성
    """
    if not q:
        return []
    if not os.path.exists(db_path):
        return []

    qvec = embed_query(q)
    if qvec is None:
        return []

    # cosine 계산은 보고서 RAG 의 _cosine 재사용
    from scripts.rag.store import _cosine

    t0 = perf_counter()
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT product_key, name, brand, category, embedding FROM product_vectors"
        ).fetchall()
    finally:
        conn.close()

    scored: List[tuple[float, sqlite3.Row]] = []
    for r in rows:
        try:
            emb = json.loads(r["embedding"])
        except (ValueError, TypeError):
            continue
        sc = _cosine(qvec, emb)
        if sc >= min_score:
            scored.append((sc, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    scored = scored[:top_k]

    if not scored:
        ms = (perf_counter() - t0) * 1000
        print(
            f"[SUGGEST_PERF] semantic q={q!r} no_match "
            f"min_score={min_score} candidates={len(rows)} ms={ms:.1f}"
        )
        return []

    # tech_products JOIN 한 번에 (DB 캐스케이드 호환)
    from scripts.database.queries import query_all
    names = [r["name"] for _, r in scored]
    brands = [r["brand"] for _, r in scored]
    pg_rows = query_all(
        """
        SELECT p.product_id, p.name, p.brand, p.image_url, p.category,
               CASE WHEN r.product_id IS NOT NULL THEN TRUE ELSE FALSE END AS has_report
        FROM tech_products p
        LEFT JOIN (SELECT DISTINCT product_id FROM product_integrated_reports) r
          ON r.product_id = p.product_id
        WHERE (LOWER(p.name), COALESCE(LOWER(p.brand), ''))
          IN (SELECT LOWER(unnest(%s::text[])), COALESCE(LOWER(unnest(%s::text[])), ''))
        """,
        (names, brands),
    )
    by_key: Dict[tuple, Dict[str, Any]] = {
        (r["name"].lower(), (r["brand"] or "").lower()): r for r in pg_rows
    }

    out: List[Dict[str, Any]] = []
    for sc, r in scored:
        key = (r["name"].lower(), (r["brand"] or "").lower())
        pg = by_key.get(key)
        out.append({
            "name": r["name"],
            "brand": r["brand"] or "",
            "category": r["category"] or "",
            "image_url": (pg or {}).get("image_url") or "",
            "product_id": (pg or {}).get("product_id"),
            "has_report": bool((pg or {}).get("has_report")),
            "source": "vector",
            "score": round(float(sc), 4),
        })

    ms = (perf_counter() - t0) * 1000
    print(
        f"[SUGGEST_PERF] semantic q={q!r} hits={len(out)} "
        f"top_score={out[0]['score']} candidates={len(rows)} ms={ms:.1f}"
    )
    return out
