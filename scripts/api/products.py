"""
Product-related API routes
"""
import asyncio
import os
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
        return templates.TemplateResponse("products.html", {
            "request": request,
            "products": products,
            "show_product_list": show_product_list,
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
        product = dict(product) if product else {}
        product["existing_report"] = latest_report["id"] if latest_report else None
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
