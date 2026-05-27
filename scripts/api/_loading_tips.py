"""로딩 화면 *동적 인기 제품* 한 줄 조회 (5분 캐시).

★ 정적 풀(23개) 은 프론트(JS)가 진실의 원천 — 후속 보강에서 빈 박스 버그
   (fetch 동안 0줄) 차단을 위해 옮김. 백엔드는 동적 한 줄만 책임.
★ TIP 은 보조 기능 — 어떤 실패도 호출자(/loading-tips 라우트)를 죽이지 않는다.
★ 의존성 얇게 유지 — scripts.database.queries.query_all 만 사용.
"""
from __future__ import annotations

import re
from time import time
from typing import Dict, Optional, Tuple

# ── 동적 인기 제품 한 줄 (5분 메모리 캐시) ──────────────────────
_POPULAR_CACHE: dict = {"value": None, "ts": 0.0}
_POPULAR_TTL_SEC = 300  # 5분


def get_popular_products_tip() -> Optional[str]:
    """최근 7일 usage_events 합산 → 상위 3개 제품명을 한 줄로 묶음.

    조회 실패·결과 없음 → None (호출부가 정적 문장만 반환).
    절대 raise 안 함 — 로딩 TIP 은 보조 기능, 호출자를 죽이지 않음.
    """
    now = time()
    cached = _POPULAR_CACHE["value"]
    if cached is not None and (now - _POPULAR_CACHE["ts"]) < _POPULAR_TTL_SEC:
        return cached

    tip: Optional[str] = None
    try:
        from scripts.database.queries import query_all

        rows = query_all(
            """
            SELECT tp.name, COUNT(*) AS hits
              FROM usage_events ue
              JOIN tech_products tp ON tp.product_id = ue.product_id
             WHERE ue.event_type IN ('page_view', 'product_create')
               AND ue.product_id IS NOT NULL
               AND ue.ts >= NOW() - INTERVAL '7 days'
             GROUP BY tp.name
             ORDER BY hits DESC
             LIMIT 10
            """
        )
        # ★ 정규화 중복 제거 — 운영 DB 의 "아이폰17" 과 "아이폰 17" 처럼
        #   공백/대소문자만 다른 행이 갈라져 표시되는 문제 해소.
        #   첫 등장 이름이 대표(= ORDER BY hits DESC 라 hits 가 더 많음).
        #   누적 hits 로 재정렬해 상위 3개 채택. LIMIT 10 까지 후보 확장.
        seen: Dict[str, Tuple[str, int]] = {}
        for r in rows or []:
            name = r.get("name")
            if not name:
                continue
            try:
                hits = int(r.get("hits") or 0)   # psycopg2 가 Decimal 반환 대비
            except (TypeError, ValueError):
                hits = 0
            key = re.sub(r"\s+", "", str(name)).lower()
            if key in seen:
                rep_name, prev_hits = seen[key]
                seen[key] = (rep_name, prev_hits + hits)
            else:
                seen[key] = (name, hits)
        top = sorted(seen.values(), key=lambda x: -x[1])[:3]
        names = [n for n, _ in top]
        if names:
            joined = ", ".join(names)
            tip = f"최근 MOABOM에서 많이 찾아본 제품: {joined}"
    except Exception as e:  # noqa: BLE001 — TIP 은 보조 기능, raise 금지
        print(f"[WARN] loading-tips popular query failed: "
              f"{type(e).__name__}: {e}")
        tip = None

    _POPULAR_CACHE["value"] = tip
    _POPULAR_CACHE["ts"] = now
    return tip
