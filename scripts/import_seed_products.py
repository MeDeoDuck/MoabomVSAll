"""seeds/manual_products.json → tech_products 적재 (1회성 CLI).

위키피디아·공식 사이트 큐레이션 기반 정확 한국 출시명 JSON 을 받아
tech_products 에 멱등 INSERT 한다. seed_products.py 가 Serper+LLM
자동 추출을 쓰는 것과 달리, 이 스크립트는 사람(=Claude 큐레이션 또는
유현님 검수) 데이터를 신뢰하고 그대로 적재한다.

JSON 스키마 (각 항목):
    {
      "name": "갤럭시 S24 Ultra",          # 필수, 한국 정식 출시명
      "brand": "Samsung",                   # 필수, 영문 표기 통일
      "category": "스마트폰",                # 필수, 한국어 카테고리
      "release_year": 2024,                 # 선택, 메타데이터
      "source": "https://ko.wikipedia.org/..."  # 선택, 출처 위키 URL
    }

가역성: `DELETE FROM tech_products WHERE seeded=true` 한 줄로 원복.
멱등성: 같은 (LOWER(name), LOWER(brand)) row 가 이미 있으면 skip
        (UNIQUE INDEX uq_tech_products_name_brand_ci 가 가드).

사용:
    python -m scripts.import_seed_products
    python -m scripts.import_seed_products --json seeds/manual_products.json
    python -m scripts.import_seed_products --dry-run    # DB 변경 없이 sanity check
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Tuple

from scripts.database.queries import execute_insert, query_one


VALID_CATEGORIES = {
    "스마트폰", "무선이어폰", "스마트워치", "노트북", "태블릿",
    "카메라", "TV", "무선청소기", "로봇청소기", "커피머신",
    "밥솥", "인덕션", "에어프라이어", "모니터",
}


def load_json(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"JSON 루트는 배열이어야 합니다: {path}")
    return data


def validate(items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """필수 키·타입·dedupe 체크. (정상 항목, 경고 메시지) 반환."""
    out: List[Dict[str, Any]] = []
    warnings: List[str] = []
    seen: set = set()
    for i, it in enumerate(items):
        name = (it.get("name") or "").strip()
        brand = (it.get("brand") or "").strip()
        category = (it.get("category") or "").strip()
        if not name or not brand or not category:
            warnings.append(
                f"[{i}] 필수 키 누락 — name={name!r} brand={brand!r} category={category!r} (skip)"
            )
            continue
        if category not in VALID_CATEGORIES:
            warnings.append(
                f"[{i}] {name!r}: category={category!r} 가 표준 어휘에 없음 (그대로 진행)"
            )
        key = (name.lower(), brand.lower())
        if key in seen:
            warnings.append(f"[{i}] {name!r} ({brand}) JSON 내부 중복 (skip)")
            continue
        seen.add(key)
        out.append(
            {
                "name": name,
                "brand": brand,
                "category": category,
                "release_year": it.get("release_year"),
                "source": it.get("source"),
            }
        )
    return out, warnings


def upsert(name: str, brand: str, category: str) -> str:
    """기존 row 있으면 skip, 없으면 INSERT(seeded=true).

    반환: "inserted" | "skip_duplicate" | "error"
    """
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
        return "skip_duplicate"
    try:
        # execute_insert 는 INSERT 후 conn.commit() 을 보장한다. query_one 은
        # SELECT 가정이라 commit 이 없어 RETURNING 만 받고 트랜잭션이 롤백된다
        # (queries.py 참조).
        product_id = execute_insert(
            """
            INSERT INTO tech_products (name, brand, category, seeded)
            VALUES (%s, %s, %s, TRUE)
            ON CONFLICT DO NOTHING
            RETURNING product_id
            """,
            (name, brand or None, category or None),
        )
    except Exception as e:
        print(f"[IMPORT][ERR] {name!r} ({brand}) — {e}")
        return "error"
    return "inserted" if product_id else "skip_duplicate"


def auto_import_if_empty(json_path: str | None = None) -> dict:
    """앱 startup 자동 시드 — seeded row 가 0건일 때만 1회 적재.

    멱등성 4중 가드:
      ① 이 함수 자체가 `WHERE seeded=true` count 0 일 때만 실행
      ② upsert 가 (LOWER name, LOWER brand) 매칭으로 기존 row 보호
      ③ INSERT ... ON CONFLICT DO NOTHING 가 race 대비
      ④ Container Apps replica 2~5 동시 부팅 시 UNIQUE INDEX 가 중복 차단

    어떤 실패도 RuntimeError 로 던지지 않는다 (호출부가 try/except 로 격리하고
    부팅을 계속하도록). seeds JSON 부재·깨짐·DB 일시 단절 모두 빈 dict 반환.
    """
    out = {"skipped": True, "reason": "", "inserted": 0, "skip_duplicate": 0, "error": 0}
    try:
        existing = query_one(
            "SELECT COUNT(*) AS c FROM tech_products WHERE seeded=true"
        )
        if existing and existing.get("c", 0) > 0:
            out["reason"] = f"already_seeded ({existing['c']}건)"
            return out
    except Exception as e:
        out["reason"] = f"count_query_failed: {e}"
        return out

    if json_path is None:
        json_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "seeds",
            "manual_products.json",
        )
    if not os.path.exists(json_path):
        out["reason"] = f"seed_json_not_found: {json_path}"
        return out

    try:
        items = load_json(json_path)
        valid, _ = validate(items)
    except Exception as e:
        out["reason"] = f"load_failed: {e}"
        return out

    out["skipped"] = False
    out["reason"] = "auto_imported"
    for it in valid:
        status = upsert(it["name"], it["brand"], it["category"])
        if status == "inserted":
            out["inserted"] += 1
        elif status == "skip_duplicate":
            out["skip_duplicate"] += 1
        else:
            out["error"] += 1
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Import curated product seed JSON")
    parser.add_argument(
        "--json",
        default=os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "seeds",
            "manual_products.json",
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not os.path.exists(args.json):
        print(f"[IMPORT][FATAL] JSON 파일이 없습니다: {args.json}")
        return 1

    items = load_json(args.json)
    print(f"[IMPORT] {len(items)}개 항목 로드: {args.json}")

    valid, warnings = validate(items)
    for w in warnings[:20]:
        print(f"[IMPORT][WARN] {w}")
    if len(warnings) > 20:
        print(f"[IMPORT][WARN] ... 외 {len(warnings) - 20}건 경고 생략")

    print(f"[IMPORT] 검증 통과 {len(valid)}개, 경고 {len(warnings)}건, dry_run={args.dry_run}")

    if args.dry_run:
        by_cat: Dict[str, int] = {}
        by_brand: Dict[str, int] = {}
        for it in valid:
            by_cat[it["category"]] = by_cat.get(it["category"], 0) + 1
            by_brand[it["brand"]] = by_brand.get(it["brand"], 0) + 1
        print(f"[IMPORT][DRY] category 분포: {sorted(by_cat.items(), key=lambda x: -x[1])}")
        print(f"[IMPORT][DRY] brand 분포 top10: {sorted(by_brand.items(), key=lambda x: -x[1])[:10]}")
        return 0

    ins = dup = err = 0
    for it in valid:
        status = upsert(it["name"], it["brand"], it["category"])
        if status == "inserted":
            ins += 1
        elif status == "skip_duplicate":
            dup += 1
        else:
            err += 1
    print(f"[IMPORT] 완료 inserted={ins} skip_duplicate={dup} error={err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
