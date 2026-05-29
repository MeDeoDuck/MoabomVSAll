"""
Product-related API routes
"""
import asyncio
import os
from datetime import datetime
from time import perf_counter

from fastapi import BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from scripts.database.queries import query_one, query_all, execute_insert, execute_update
from scripts.reports.pdf_generator import render_report_pdf
from scripts.config import REPORT4_INPUT_EXPANSION
from scripts.reports.product_integrated_insight import (
    collect_transcript_reports_for_product,
    ensure_comment_analysis_for_videos,
    ensure_all_reports_for_product,
    build_product_integrated_insight_report,
    save_product_integrated_report,
    get_latest_product_integrated_report,
    get_product_integrated_report,
    get_last_collect_perf,
    get_last_llm_perf,
    get_last_comment_heal_perf,
    get_last_input_expansion_perf,
)
from scripts.api.suggest import suggest as suggest_products
from scripts.utils.markdown_renderer import markdown_to_html

templates = Jinja2Templates(directory="templates")


# 작업 4 — DB 의 tech_products.category 가 NULL 인 케이스(실측: 운영 DB 의
# 모든 행 NULL) 대비 가벼운 키워드 추론. brand→domain 매핑(Phase 3 출처
# 등급)·페르소나 끝맺음(작업 1)과 같은 철학: 무거운 규칙 없이 흔한 제품군
# 몇 가지만. DB 추론 결과 — 매핑 못 찾으면 빈 문자열(스펙 줄에서 자연 생략).
_CATEGORY_KEYWORDS = [
    ("이어폰", ("에어팟", "에어팟프로", "버즈", "이어폰", "이어버드",
                 "프리바이트", "freebuds", "airpods", "buds")),
    ("스마트폰", ("아이폰", "갤럭시", "픽셀", "샤오미", "iphone", "galaxy",
                 "pixel", "노바폰")),
    ("태블릿", ("아이패드", "갤럭시탭", "탭", "ipad", "tab")),
    ("노트북", ("그램", "맥북", "갤럭시북", "ZenBook", "ThinkPad",
                "MacBook", "Yoga")),
    ("스마트워치", ("애플워치", "갤럭시워치", "watch")),
    ("헤드폰", ("헤드폰", "헤드셋", "headphone", "headset", "에어팟맥스")),
]


def _infer_category_from_name(name: str) -> str:
    """제품명 키워드 → 카테고리. 매칭 없으면 ''(자연 생략)."""
    if not name:
        return ""
    low = name.lower()
    for category, keywords in _CATEGORY_KEYWORDS:
        for kw in keywords:
            if kw.lower() in low:
                return category
    return ""


def _format_updated_label(last_report_at) -> str:
    """last_report_at(최신 보고서 생성 시각) → "업데이트 N일 전" 표시 문자열.

    UI 일관성 위해 표시 문자열 생성은 서버 한 곳에서만 수행한다(템플릿/JS
    중복 계산 금지). TIMESTAMP 컬럼은 naive datetime 으로 들어오므로
    datetime.now() (naive) 와 비교.
    """
    if not last_report_at:
        return ""
    try:
        delta = datetime.now() - last_report_at
        days = delta.days
    except TypeError:
        return ""
    if days <= 0:
        return "오늘 업데이트"
    return f"업데이트 {days}일 전"


def _build_recommended(limit: int = 12):
    """추천 리포트 캐러셀 데이터.

    product_integrated_reports 가 존재하는 제품을 최신 보고서순으로 노출.
    집계(댓글 수·작성자 고유 수·최신 보고서 시각)는 단일 JOIN + GROUP BY 한
    쿼리로 처리(N+1 금지). pir 누적/videos/comments 카테시안은 COUNT(DISTINCT)
    로 중복 상쇄. 표시용 "업데이트 N일 전" 문자열도 서버에서 미리 생성.

    중복 제거: 같은 제품이 과거 dedupe 이전 INSERT 로 여러 product_id 행으로
    존재할 수 있어, (name, brand) 기준 DISTINCT ON 으로 제품당 최신 보고서를
    가진 1행만 남긴다(여전히 단일 쿼리 — N+1 없음). 내부 per_product 는
    product_id 단위 집계, 바깥에서 최신순 정렬 + 12개 상한.
    """
    rows = query_all(
        """
        SELECT
            product_id, name, brand, category, image_url,
            comment_count, author_count, last_report_at
        FROM (
            SELECT DISTINCT ON (
                LOWER(TRIM(per_product.name)),
                COALESCE(LOWER(TRIM(per_product.brand)), '')
            )
                per_product.product_id,
                per_product.name,
                per_product.brand,
                per_product.category,
                per_product.image_url,
                per_product.comment_count,
                per_product.author_count,
                per_product.last_report_at
            FROM (
                SELECT
                    p.product_id,
                    p.name,
                    p.brand,
                    p.category,
                    p.image_url,
                    COUNT(DISTINCT c.comment_id)        AS comment_count,
                    COUNT(DISTINCT c.author_channel_id) AS author_count,
                    MAX(pir.created_at)                 AS last_report_at
                FROM tech_products p
                JOIN product_integrated_reports pir ON pir.product_id = p.product_id
                LEFT JOIN videos v   ON v.product_id = p.product_id
                LEFT JOIN comments c ON c.video_id   = v.video_id
                GROUP BY p.product_id, p.name, p.brand, p.category, p.image_url
            ) per_product
            ORDER BY
                LOWER(TRIM(per_product.name)),
                COALESCE(LOWER(TRIM(per_product.brand)), ''),
                per_product.last_report_at DESC
        ) deduped
        ORDER BY last_report_at DESC
        LIMIT %s
        """,
        (limit,),
    )

    recommended = []
    for r in rows:
        # 칩: DB category(보통 NULL) + 제품명 키워드 추론. 1~2개, 빈 칩 없음.
        chips = []
        cat = (r.get("category") or "").strip()
        if cat:
            chips.append(cat)
        inferred = _infer_category_from_name(r.get("name") or "")
        if inferred and inferred not in chips:
            chips.append(inferred)

        recommended.append({
            "product_id": r["product_id"],
            "name": r.get("name") or "",
            "image_url": (r.get("image_url") or "").strip(),
            "chips": chips,
            "comment_count": int(r.get("comment_count") or 0),
            "author_count": int(r.get("author_count") or 0),
            "updated_label": _format_updated_label(r.get("last_report_at")),
        })
    return recommended


def register_product_routes(app):
    """Register all product-related routes"""
    
    @app.get("/", response_class=HTMLResponse)
    async def root():
        """Redirect to products page."""
        return "<script>window.location.href='/products'</script>"
    
    @app.get("/products", response_class=HTMLResponse)
    async def list_products(request: Request):
        """List all products."""
        show_product_list = os.getenv("SHOW_PRODUCT_LIST", "0") == "1"
        products = query_all("SELECT * FROM tech_products ORDER BY created_at DESC") if show_product_list else []
        recommended = _build_recommended()
        return templates.TemplateResponse("products.html", {
            "request": request,
            "products": products,
            "show_product_list": show_product_list,
            "recommended": recommended,
        })
    
    # ────────────────────────────────────────────────────────────────
    # 검색 후보 자동 제안 (search-suggest)
    # ────────────────────────────────────────────────────────────────
    @app.get("/products/suggest")
    async def suggest_endpoint(q: str = "", limit: int = 6):
        """모호 검색어 → 정확 제품명 후보 카드 리스트.

        DB → Serper Web Search → LLM 추출 캐스케이드. 어떤 외부 호출 실패도
        5xx 로 나가지 않는다 (suggest 는 보조 기능). 동기 함수라 asyncio
        이벤트 루프 차단 회피 위해 to_thread.
        """
        import asyncio
        items = await asyncio.to_thread(suggest_products, q, max(1, min(limit, 10)))
        return JSONResponse(
            content={"items": items, "q": q},
            headers={"Cache-Control": "public, max-age=300"},
        )

    @app.post("/products")
    async def create_product(data: dict):
        """Create a new product.

        dedupe: 같은 (LOWER(name), LOWER(brand)) 조합 row 가 이미 있으면
        새로 INSERT 하지 않고 기존 product_id 재사용. 응답에 existing_report
        (해당 제품의 최신 product_integrated_reports.id 또는 null) 동봉 →
        프론트가 캐시 적중 시 보고서로 즉시 이동.
        """
        name = data.get("name", "").strip()
        brand = (data.get("brand") or "").strip() or None
        category = (data.get("category") or "").strip() or None

        if not name:
            raise HTTPException(status_code=400, detail="Product name is required")

        existing = query_one(
            """
            SELECT product_id FROM tech_products
            WHERE LOWER(name) = LOWER(%s)
              AND COALESCE(LOWER(brand), '') = COALESCE(LOWER(%s), '')
            ORDER BY product_id ASC
            LIMIT 1
            """,
            (name, brand or ""),
        )
        if existing:
            product_id = existing["product_id"]
        else:
            product_id = execute_insert(
                "INSERT INTO tech_products (name, brand, category) VALUES (%s, %s, %s) RETURNING product_id",
                (name, brand, category),
            )

        product = query_one(
            "SELECT * FROM tech_products WHERE product_id = %s", (product_id,)
        )
        latest_report = query_one(
            """
            SELECT id FROM product_integrated_reports
            WHERE product_id = %s
            ORDER BY id DESC LIMIT 1
            """,
            (product_id,),
        )
        # 빈 페이지 방지: '기존 보고서 보기' 게이트는 목적지(/products/{id})가 실제
        # 렌더하는 videos 테이블의 카운트와 종합 보고서 존재를 *둘 다* 만족할 때만
        # truthy. 종합 보고서 row 만 남고 videos 가 0개인 제품이 빈 페이지로 빠지던
        # 문제 차단. videos 카운트는 단일 쿼리(N+1 없음).
        video_count_row = query_one(
            "SELECT COUNT(*) AS cnt FROM videos WHERE product_id = %s",
            (product_id,),
        )
        video_count = int(video_count_row["cnt"]) if video_count_row else 0
        product = dict(product) if product else {}
        product["video_count"] = video_count
        product["existing_report"] = (
            latest_report["id"] if (latest_report and video_count > 0) else None
        )
        return product
    
    @app.get("/products/{product_id}", response_class=HTMLResponse)
    async def product_detail(request: Request, product_id: int):
        """Show product detail page with videos."""
        product = query_one("SELECT * FROM tech_products WHERE product_id = %s", (product_id,))
        
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
        
        videos = query_all(
            "SELECT * FROM videos WHERE product_id = %s ORDER BY view_count DESC",
            (product_id,)
        )
        
        return templates.TemplateResponse("product_detail.html", {
            "request": request,
            "product": product,
            "videos": videos,
        })

    @app.post("/products/{product_id}/image")
    async def collect_product_image(product_id: int, force: bool = False):
        """Phase 3: 제품 이미지 수집을 명시적으로 트리거(수동/검증용).

        collect_and_store_product_image 는 절대 예외를 던지지 않으므로
        결과 dict 를 그대로 반환. 동기 함수라 to_thread 로 분리.
        """
        from scripts.product_image.collector import (
            collect_and_store_product_image,
        )
        result = await asyncio.to_thread(
            collect_and_store_product_image, product_id, force=force
        )
        return result

    @app.delete("/products/{product_id}")
    async def delete_product(product_id: int):
        """Delete a product. CASCADE removes related videos, comments, transcripts, reports."""
        affected = execute_update(
            "DELETE FROM tech_products WHERE product_id = %s",
            (product_id,)
        )
        if affected == 0:
            raise HTTPException(status_code=404, detail="Product not found")
        return {"deleted": True, "product_id": product_id}

    # ────────────────────────────────────────────────────────────────
    # 제품 단위 통합 인사이트 보고서 (영상별 자막 보고서 N건 → 1건 합성)
    # ────────────────────────────────────────────────────────────────

    @app.post("/products/{product_id}/integrated-insight")
    async def create_product_integrated_insight(
        product_id: int, data: dict, background_tasks: BackgroundTasks
    ):
        """선택된 영상들의 자막 기반 보고서를 합성하여 제품 단위 통합 인사이트 보고서를 생성한다."""
        product = query_one("SELECT * FROM tech_products WHERE product_id = %s", (product_id,))
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")

        # Phase 3: 제품 이미지 수집을 백그라운드로 예약 — 응답 송신 후 실행
        # 되므로 ④ 생성 지연·실패에 영향 0(독립). 없으면 수집·있으면 캐시
        # 재사용. collect_and_store_product_image 는 절대 예외를 던지지 않음.
        def _bg_collect_image(pid: int):
            from scripts.product_image.collector import (
                collect_and_store_product_image,
            )
            collect_and_store_product_image(pid)

        background_tasks.add_task(_bg_collect_image, product_id)

        raw_ids = data.get("video_ids") or []
        if not isinstance(raw_ids, list):
            raise HTTPException(status_code=400, detail="video_ids must be a list")
        # 중복 제거 및 정규화 (입력 순서 유지)
        seen = set()
        video_ids = []
        for v in raw_ids:
            if not v:
                continue
            sv = str(v).strip()
            if sv and sv not in seen:
                seen.add(sv)
                video_ids.append(sv)

        if len(video_ids) < 2:
            raise HTTPException(status_code=400, detail="영상 2개 이상을 선택해 주세요")

        route_t0 = perf_counter()

        # ── self-healing ──────────────────────────────────────────────
        # Phase 2-a ON: 댓글 agent self-heal 을 먼저(②③ 이 comment_sentiments
        #   에 의존) → ensure_all_reports_for_product 가 ①②③ 보장+수집.
        #   collect_transcript_reports_for_product 는 부분 upsert 로 ②③ 을
        #   NULL 로 덮으므로 ON 경로에서 호출하지 않는다(중복 ① build 도 방지).
        # OFF: Phase 2-a 이전과 정확히 동일 — 자막 ① + 댓글 agent 를 병렬.
        collect_t0 = perf_counter()
        if REPORT4_INPUT_EXPANSION:
            comment_heal_stats = await ensure_comment_analysis_for_videos(
                product["name"], video_ids
            )
            per_video_reports = await ensure_all_reports_for_product(
                product_id, product["name"], video_ids
            )
        else:
            per_video_reports, comment_heal_stats = await asyncio.gather(
                collect_transcript_reports_for_product(product_id, video_ids),
                ensure_comment_analysis_for_videos(product["name"], video_ids),
            )
        collect_ms = (perf_counter() - collect_t0) * 1000

        if len(per_video_reports) < 2:
            raise HTTPException(
                status_code=400,
                detail="자막 기반 보고서를 생성할 수 있는 영상이 2개 이상 필요합니다 (자막 부재 영상 제외 후 부족)",
            )

        # 선정 영상 수 vs 실제 분석 영상 수 — 자막 부재로 일부가 제외됐을 수
        # 있다. 보고서·UI 모두 정직 표기하도록 분리해 추적.
        selected_video_count = len(video_ids)
        analyzed_video_ids = [r["video_id"] for r in per_video_reports]
        excluded_video_ids = [v for v in video_ids if v not in set(analyzed_video_ids)]

        # build_product_integrated_insight_report 가 내부에서 댓글 self-healing 으로
        # 적재된 DB 를 READ ONLY 로 집계해 ⑤ 소비자 여론 섹션을 채운다.
        build_t0 = perf_counter()
        report_text, model_used = build_product_integrated_insight_report(
            product_name=product["name"],
            per_video_reports=per_video_reports,
            video_ids=analyzed_video_ids,
            selected_video_count=selected_video_count,
        )
        build_ms = (perf_counter() - build_t0) * 1000

        save_t0 = perf_counter()
        report_id = save_product_integrated_report(
            product_id=product_id,
            video_ids=[r["video_id"] for r in per_video_reports],
            report_text=report_text,
            model_used=model_used,
        )
        save_ms = (perf_counter() - save_t0) * 1000

        total_ms = (perf_counter() - route_t0) * 1000
        collect_detail = get_last_collect_perf()
        llm_detail = get_last_llm_perf()
        comment_heal_detail = get_last_comment_heal_perf()
        input_expansion_detail = get_last_input_expansion_perf()
        print(
            f"[PERF][insight_route] product_id={product_id} total_ms={total_ms:.1f} "
            f"collect_ms={collect_ms:.1f} build_ms={build_ms:.1f} llm_ms={llm_detail.get('llm_ms')} "
            f"save_ms={save_ms:.1f} self_heal={collect_detail.get('self_heal_count')}/{collect_detail.get('self_heal_count', 0) + collect_detail.get('cache_hits', 0)} "
            f"comment_heal={comment_heal_detail.get('healed', 0)}/{comment_heal_detail.get('total_videos', 0)} "
            f"comment_already={comment_heal_detail.get('already_analyzed', 0)} "
            f"comment_failed={comment_heal_detail.get('failed', 0)} "
            f"input_expansion={REPORT4_INPUT_EXPANSION} "
            f"bundles={input_expansion_detail.get('bundles', 0)} "
            f"fallback={llm_detail.get('fallback')}"
        )

        return {
            "report_id": report_id,
            "product_id": product_id,
            "report_text": report_text,
            "report_html": markdown_to_html(report_text),
            "source_video_count": len(per_video_reports),       # 실제 분석된 영상 수 (분모 기준)
            "selected_video_count": selected_video_count,        # 사용자가 선정한 총 수
            "analyzed_video_count": len(per_video_reports),      # source_video_count 와 동일, 명확성 위해 중복
            "excluded_video_count": len(excluded_video_ids),     # 자막 부재로 제외된 수
            "excluded_video_ids": excluded_video_ids,            # 제외된 video_id 리스트 (디버그/UI 보조)
            "video_ids": analyzed_video_ids,
            "model_used": model_used,
            "perf_breakdown": {
                "total_ms": round(total_ms, 1),
                "collect_ms": round(collect_ms, 1),
                "build_ms": round(build_ms, 1),
                "llm_ms": llm_detail.get("llm_ms"),
                "save_ms": round(save_ms, 1),
                "llm_fallback": llm_detail.get("fallback"),
                "collect_detail": collect_detail,
                "comment_heal_detail": comment_heal_detail,
                "input_expansion_detail": input_expansion_detail,
            },
        }

    @app.get("/products/{product_id}/integrated-insight/latest")
    async def get_latest_integrated_insight(product_id: int):
        """제품의 최신 통합 인사이트 보고서를 조회한다."""
        product = query_one("SELECT * FROM tech_products WHERE product_id = %s", (product_id,))
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")

        latest = get_latest_product_integrated_report(product_id)
        if not latest:
            raise HTTPException(status_code=404, detail="통합 인사이트 보고서가 아직 없습니다")

        return {
            "report_id": latest["id"],
            "product_id": product_id,
            "report_text": latest["report_text"],
            "report_html": markdown_to_html(latest["report_text"]),
            "source_video_count": latest["source_video_count"],
            "video_ids": latest["video_ids"],
            "model_used": latest.get("model_used"),
            "created_at": latest.get("created_at").isoformat() if latest.get("created_at") else None,
        }

    @app.get("/products/{product_id}/popup-summary")
    async def get_product_popup_summary(product_id: int):
        """Phase 5: 최종 판정 팝업이 쓰는 모든 값을 한 JSON 으로 반환.

        ★ LLM 호출 0건 — ④ 마크다운을 결정론적으로 추출(§4) + 가격·스펙 캐시
        (§5)만 사용. ④ 가 아직 없으면 404 (프론트는 안전 퇴화로 곧장 ④ 화면).
        외부 호출(가격·스펙) 실패는 §7 fallback 으로 항목만 빈 값.
        """
        from scripts.popup.extractor import extract_popup_data
        from scripts.popup.product_meta import collect_and_cache_meta

        product = query_one(
            "SELECT product_id, name, brand, category, image_url "
            "FROM tech_products WHERE product_id = %s",
            (product_id,),
        )
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")

        latest = get_latest_product_integrated_report(product_id)
        if not latest or not latest.get("report_text"):
            # ④ 부재 — 프론트는 모달 띄우지 않고 곧장 ④ 생성/조회로(§3-C).
            raise HTTPException(status_code=404,
                                detail="통합 인사이트 보고서가 아직 없습니다")

        # 1) ④ 결정론적 추출 (순수)
        popup = extract_popup_data(latest["report_text"])

        # 2) 가격·스펙 — 외부 수집(캐시 hit 우선). sync → to_thread 로 비동기화.
        meta_result = await asyncio.to_thread(
            collect_and_cache_meta, product_id,
            product.get("name") or "", product.get("brand") or "",
        )

        # 3) 스펙 한 줄 조립 (가져온 항목만 — §7).
        # 작업 4 — category 가 DB 에서 NULL 인 경우(실측: 운영 DB 의 모든
        # tech_products 행이 NULL) 제품명 키워드로 가벼운 추론. DB 데이터
        # 결손이 코드 측 fallback 으로 자연 보완 — 스키마/생성 로직 무변경.
        category = (product.get("category") or "").strip()
        if not category:
            category = _infer_category_from_name(product.get("name") or "")
        spec_parts = []
        if meta_result.get("screen_size"):
            spec_parts.append(meta_result["screen_size"])
        if meta_result.get("release_year"):
            spec_parts.append(f"{meta_result['release_year']}년 출시")
        if category:
            spec_parts.append(category)
        spec_display = " · ".join(spec_parts) if spec_parts else None

        # 4) missing 합산
        missing = list(popup.get("missing", []))
        if not meta_result.get("price_display"):
            missing.append("price")
        if not meta_result.get("screen_size"):
            missing.append("spec.screen_size")
        if not meta_result.get("release_year"):
            missing.append("spec.release_year")
        if not (product.get("image_url") or "").strip():
            missing.append("image_url")
        if not spec_display:
            missing.append("spec_display")

        return {
            "product": {
                "product_id": product_id,
                "name": product.get("name"),
                "brand": product.get("brand"),
                "category": product.get("category"),
                "image_url": product.get("image_url"),
            },
            "meta": {
                "videos": popup.get("videos_n"),
                "comments": popup.get("comments_n"),
            },
            "verdict": popup["verdict"],
            "pros": popup["pros"],
            "cons": popup["cons"],
            "caveat": popup["caveat"],
            "price_display": meta_result.get("price_display"),
            "spec_display": spec_display,
            "missing": missing,
            "report_id": latest["id"],   # 프론트가 후속 PDF 등 연결 가능
        }

    @app.get("/products/{product_id}/integrated-insight/{report_id}.pdf")
    async def download_integrated_insight_pdf(product_id: int, report_id: int):
        """통합 인사이트 보고서를 PDF로 다운로드한다."""
        product = query_one("SELECT * FROM tech_products WHERE product_id = %s", (product_id,))
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")

        report = get_product_integrated_report(product_id, report_id)
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")

        title = f"[{product['name']}] 종합 인사이트 보고서"
        pdf_bytes = render_report_pdf(title, report["report_text"])
        filename = f"product_{product_id}_integrated_insight_{report_id}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # ────────────────────────────────────────────────────────────
    # feature/similar-products — 유사 제품 비교하기 (축소판)
    #   - GET /products/{id}/similar               혼합 모드(내부+외부) 카드 3개
    #   - GET /products/{id}/has-integrated-report 카드 클릭 분기용 보고서 보유 여부
    # 변경 범위: 라우트 2개만. 기존 라우트·DB 스키마·Agent 무변경.
    # 직전 보강(external-analyze + background-task + 자동 모달 트리거) 는
    # 사용자 명세 변경에 따라 *물리적 삭제* — 카드 클릭은 같은 페이지 안에서
    # 세 번째 박스(최종 팝업/안내) 토글로 처리.
    # ────────────────────────────────────────────────────────────

    @app.get("/products/{product_id}/similar")
    async def get_similar_products(product_id: int):
        """유사 제품 카드 3개. DB 매칭 우선, 부족분 Serper 검색으로 보충."""
        from scripts.popup.similar import build_similar_payload

        product = query_one(
            "SELECT product_id FROM tech_products WHERE product_id = %s",
            (product_id,),
        )
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
        return await build_similar_payload(product_id)

    @app.get("/products/{product_id}/has-integrated-report")
    async def has_integrated_report(product_id: int):
        """카드 클릭 분기용 — 종합 인사이트 보고서 보유 여부.
        있으면 다이얼로그 → 세 번째 박스에 유사 제품 최종 팝업.
        없으면 안내 박스로 'MOABOM 을 통해 확인해보세요' 노출."""
        row = query_one(
            "SELECT EXISTS (SELECT 1 FROM product_integrated_reports "
            "WHERE product_id = %s) AS has_report",
            (product_id,),
        )
        return {"has_report": bool(row and row.get("has_report"))}

    # ────────────────────────────────────────────────────────────
    # feature/loading-tips — 로딩 화면 TIP 슬라이드
    #   - GET /loading-tips  정적 문장 23개 + 동적 인기 제품 한 줄 (5분 캐시)
    # 영상 선정 / 종합 인사이트 두 로딩 카드 하단 TIP 박스에서 5초마다 회전.
    # 보조 기능 — 어떤 실패도 사용자 경험을 죽이지 않음 (DB 실패 → 정적만 반환).
    # ────────────────────────────────────────────────────────────

    @app.get("/loading-tips")
    async def get_loading_tips():
        """로딩 화면 TIP 슬라이드의 *동적 인기 제품 한 줄*만 반환.

        정적 풀(23개)은 프론트(JS)가 보유 — fetch 가 실패해도 100% 동작.
        fetch 성공 시 JS 가 동적 한 줄을 풀에 push.
        """
        from scripts.api._loading_tips import get_popular_products_tip
        return {"dynamic_tip": get_popular_products_tip()}
