"""product_meta_cache 캐시 read/write (Phase 5 §5-C).

scripts.database 경로 재사용(지연 import — 오프라인/순수 경로 보호).
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def get_meta(product_id: int) -> Optional[Dict[str, Any]]:
    from scripts.database.queries import query_one

    return query_one(
        "SELECT product_id, price_raw, price_display, screen_size, "
        "release_year, source, fetched_at "
        "FROM product_meta_cache WHERE product_id = %s",
        (product_id,),
    )


def upsert_meta(
    product_id: int,
    *,
    price_raw: Optional[int] = None,
    price_display: Optional[str] = None,
    screen_size: Optional[str] = None,
    release_year: Optional[int] = None,
    source: Optional[str] = None,
) -> None:
    from scripts.database.queries import execute_update

    execute_update(
        """INSERT INTO product_meta_cache
              (product_id, price_raw, price_display, screen_size,
               release_year, source, fetched_at)
           VALUES (%s, %s, %s, %s, %s, %s, NOW())
           ON CONFLICT (product_id) DO UPDATE SET
              price_raw     = EXCLUDED.price_raw,
              price_display = EXCLUDED.price_display,
              screen_size   = EXCLUDED.screen_size,
              release_year  = EXCLUDED.release_year,
              source        = EXCLUDED.source,
              fetched_at    = NOW()
        """,
        (product_id, price_raw, price_display, screen_size,
         release_year, source),
    )
