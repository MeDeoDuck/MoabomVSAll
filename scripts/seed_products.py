"""tech_products 초기 시드 — 1회성 CLI.

seeds/popular_keywords.txt 의 각 카테고리 키워드를 Serper Web Search +
LLM 추출 파이프라인에 통과시켜 정확 제품명을 일괄 적재한다. suggest 모듈
재사용 — 시드와 런타임이 같은 추출 로직을 공유하므로 일관성 자동 보장.

cold-start UX 해소가 목표: 사용자가 첫 검색했을 때 우측 패널에 후보가
즉시 노출되도록(LLM 호출 없이 DB 만으로) ~200~500 정확 제품명을 미리 적재.

가역성: `DELETE FROM tech_products WHERE seeded=true` 한 줄로 원복.

사용:
    python -m scripts.seed_products
    python -m scripts.seed_products --keywords seeds/popular_keywords.txt
    python -m scripts.seed_products --dry-run    # DB 변경 없이 추출만 출력

비용 추정: Serper ~50회 ($0.05), LLM ~50회 ($0.05~0.1). 총 약 $0.10~0.15.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import List, Tuple

from scripts.api.suggest import llm_extract, serper_search
from scripts.database.queries import execute_update, query_one


# 시드 단계는 suggest 보다 엄격: knowledgeGraph 존재 + confidence ≥ 0.85 인
# 후보만 통과. 자유 입력 검색은 약한 신호도 후보 노출이 낫지만 시드는 적재
# 정확도가 우선(잘못 시드된 row 는 검색 결과 오염의 원인).
SEED_MIN_CONFIDENCE = 0.85


def load_keywords(path: str) -> List[str]:
    """주석(#)·빈 줄 무시 후 키워드 리스트 반환."""
    out: List[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out.append(line)
    return out


def upsert_seed(name: str, brand: str, category: str) -> Tuple[str, int | None]:
    """(LOWER(name), LOWER(brand)) 중복 체크 후 INSERT(seeded=true).

    반환: ("inserted" | "skip_duplicate" | "skip_invalid", product_id 또는 None)
    """
    name = (name or "").strip()
    brand = (brand or "").strip()
    category = (category or "").strip()
    if not name:
        return ("skip_invalid", None)

    existing = query_one(
        """
        SELECT product_id FROM tech_products
        WHERE LOWER(name) = LOWER(%s)
          AND COALESCE(LOWER(brand), '') = COALESCE(LOWER(%s), '')
        LIMIT 1
        """,
        (name, brand),
    )
    if existing:
        return ("skip_duplicate", existing["product_id"])

    # UNIQUE INDEX 가드 — 동시 실행 시 충돌 회피.
    try:
        row = query_one(
            """
            INSERT INTO tech_products (name, brand, category, seeded)
            VALUES (%s, %s, %s, TRUE)
            ON CONFLICT DO NOTHING
            RETURNING product_id
            """,
            (name, brand or None, category or None),
        )
    except Exception as e:
        print(f"[SEED][ERR] upsert {name=!r} brand={brand!r} err={e}")
        return ("skip_invalid", None)

    if row:
        return ("inserted", row["product_id"])
    return ("skip_duplicate", None)


def main() -> int:
    parser = argparse.ArgumentParser(description="tech_products seed")
    parser.add_argument(
        "--keywords",
        default=os.path.join(os.path.dirname(os.path.dirname(__file__)), "seeds", "popular_keywords.txt"),
        help="키워드 파일 경로 (기본: seeds/popular_keywords.txt)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="DB 변경 없이 추출 결과만 출력"
    )
    args = parser.parse_args()

    if not os.path.exists(args.keywords):
        print(f"[SEED][FATAL] 키워드 파일이 없습니다: {args.keywords}")
        return 1

    keywords = load_keywords(args.keywords)
    print(f"[SEED] 키워드 {len(keywords)}개 로드: {args.keywords}")
    print(f"[SEED] dry_run={args.dry_run} min_confidence={SEED_MIN_CONFIDENCE}")

    total_inserted = 0
    total_dup = 0
    total_invalid = 0
    total_no_kg = 0
    for i, kw in enumerate(keywords, 1):
        payload = serper_search(kw)
        if not payload or not payload.get("knowledgeGraph") and not payload.get("organic"):
            print(f"[SEED][{i}/{len(keywords)}] {kw!r} → Serper 빈 응답 (skip)")
            continue
        # 시드 단계는 knowledgeGraph 존재 행을 우선 — 라인업 키워드는 KG 가
        # 비어있을 수 있으므로 organic 만으로도 진행하되 컨피던스 컷이 강함.
        candidates = llm_extract(kw, payload, db_hits=[])
        filtered = [c for c in candidates if (c.get("confidence") or 0.0) >= SEED_MIN_CONFIDENCE]
        if not filtered:
            total_no_kg += 1
            print(f"[SEED][{i}/{len(keywords)}] {kw!r} → 통과 0건 (confidence < {SEED_MIN_CONFIDENCE})")
            continue

        line_ins = line_dup = line_inv = 0
        for c in filtered:
            if args.dry_run:
                print(
                    f"[SEED][{i}/{len(keywords)}][DRY] {kw!r} → "
                    f"name={c['name']!r} brand={c.get('brand')!r} "
                    f"cat={c.get('category')!r} conf={c.get('confidence')}"
                )
                continue
            status, _ = upsert_seed(c["name"], c.get("brand", ""), c.get("category", ""))
            if status == "inserted":
                line_ins += 1
            elif status == "skip_duplicate":
                line_dup += 1
            else:
                line_inv += 1
        total_inserted += line_ins
        total_dup += line_dup
        total_invalid += line_inv
        print(
            f"[SEED][{i}/{len(keywords)}] {kw!r} → "
            f"inserted={line_ins} dup={line_dup} invalid={line_inv} "
            f"(추출 {len(filtered)}건)"
        )

    print(
        f"[SEED] 완료 inserted={total_inserted} dup={total_dup} "
        f"invalid={total_invalid} no_pass={total_no_kg}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
