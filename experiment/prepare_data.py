#!/usr/bin/env python3
"""
실험 데이터 준비 (1회 실행) — YouTube 검색·수집을 미리 돌려 DB에 저장한다.

    python experiment/prepare_data.py

설계 의도:
  비교 실험(run_experiment.py)의 본질은 "같은 데이터 위에서 모델 비교"다. 그래서
  무거운 수집(YouTube 검색·영상 선정·댓글·자막)은 여기서 **한 번만** 돌려 DB에
  저장(write)하고, run_experiment.py 는 그것을 **불러와(read)** ④ 보고서 생성·비교만
  한다. 이렇게 분리하면:
    - 영상 선정/검색 변동이 REPEAT 일관성 측정에 섞이지 않는다(고정 영상셋).
    - 한 번 수집하면 캐시(FR-020)되어 run_experiment 반복 실행이 빠르다.

흐름 (config.PRODUCTS 각각):
  1) tech_products find-or-create → product_id
  2) 이미 영상 ≥ MIN_VIDEOS 면 선정 skip (멱등 — 재실행해도 재검색 안 함)
  3) VideoSelectionAgent.select() → save_selection()   (YouTube 검색 → videos 적재)
  4) ensure_comment_analysis_for_videos()               (댓글 수집·분류·ABSA)
  5) ensure_all_reports_for_product()                   (자막 + 영상별 ①②③ 확보)

주의: 실제 YouTube/LLM API 호출 — 토큰·쿼터 비용 발생. 영상 선정은 YouTube 검색 +
LLM rerank 라 실행마다 결과가 달라질 수 있으므로 "선정은 1회"가 핵심이다(2번 가드).
"""
import asyncio
import os
import sys
import time

# 프로젝트 루트를 import 경로에 추가
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Windows 콘솔 한글 출력
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from experiment import config  # noqa: E402

# 선정 파라미터 (route 기본값과 동일 — k 는 모아봄 영상 상한과 일치시킴)
K = config.MOABOM_MAX_VIDEOS
CANDIDATE_POOL_SIZE = int(os.getenv("EXP_PREP_POOL_SIZE", "30"))
# 이미 영상이 이만큼 있으면 재검색하지 않는다 (멱등 가드).
MIN_VIDEOS = 2

# ── 자막 선수집 (youtube.com/api/timedtext IP rate-limit 대응) ──
# 자막은 비공식 timedtext 를 IP 로 긁어서 burst 면 429 가 난다. 그래서
# 캐시 없는 자막을 "순차(동시성 1)" 로 받되, 자막 없는 영상은 즉시 skip,
# 한 번 시도해서 안 되면 "대기 없이 바로 다음"으로 넘어간다(있는 것만 빠르게).
# 막힌 IP 에서 2분씩 기다려봐야 또 실패라 의미 없음 → 대기·재시도 제거.
# 한 번 캐시되면 이후 단계(ensure_all_reports)는 fetch 없이 재사용한다.
# (한글만 받으려면 TRANSCRIPT_LANGS=ko — transcript_service.py 에서 처리)
TX_DELAY_SEC = float(os.getenv("EXP_TX_DELAY", "1"))        # 영상 간 최소 간격(gentle)


def _prefetch_transcripts_throttled(video_ids):
    """캐시 없는 자막을 '있는 것만, 한 번씩' 받아 video_transcripts 에 저장.

    - 동시성 1(순차) — burst 회피
    - fetch_video_transcript 가 ko-only(_preferred_langs)라, 한글 자막이 없으면
      timedtext 를 치지 않고 바로 None → 자막 없는 영상은 빠르게 넘어간다
    - 한 번 시도해서 실패(429 등)하면 대기·재시도 없이 바로 다음으로
    어떤 실패도 prepare 전체를 막지 않는다(자막 없는 영상은 그대로 진행).
    """
    from scripts.reports.product_integrated_insight import (
        _videos_missing_transcript,
        _save_transcript_row,
    )
    from scripts.youtube.transcript_service import fetch_video_transcript

    missing = _videos_missing_transcript(video_ids)
    if not missing:
        print(f"  [TX] 자막 모두 캐시됨({len(video_ids)}개) — 선수집 skip")
        return
    print(f"  [TX] 자막 선수집 {len(missing)}개 — 순차(ko only), 실패 시 대기 없이 skip")
    ok = 0
    fail = 0
    for i, vid in enumerate(missing, 1):
        # fetch_video_transcript 는 ko-only(_preferred_langs). ko 자막이 없으면
        # timedtext 를 치지 않고 바로 None → en 등 추가수집 없음. 메타 추출 1회뿐.
        try:
            fetched = fetch_video_transcript(vid)
        except Exception as e:  # noqa: BLE001
            fetched = None
            print(f"    [TX] {vid} 예외: {type(e).__name__}: {e}")
        if fetched and fetched.get("transcript_text"):
            _save_transcript_row(vid, fetched)
            ok += 1
            print(f"    [TX] ({i}/{len(missing)}) OK {vid} — {len(fetched['transcript_text'])}자")
        else:
            fail += 1
            print(f"    [TX] ({i}/{len(missing)}) 한글자막 없음/실패 {vid} — 대기 없이 다음")
        time.sleep(TX_DELAY_SEC)  # 다음 영상 전 최소 간격
    print(f"  [TX] 선수집 완료 — 성공 {ok} / 없음·실패 {fail} (총 {len(missing)})")


def _find_or_create_product(name, brand=None, category=None):
    """tech_products 에서 (대소문자 무시) 이름으로 찾고 없으면 INSERT → product_id."""
    from scripts.database.queries import query_one, execute_insert

    row = query_one(
        "SELECT product_id FROM tech_products WHERE LOWER(name) = LOWER(%s) "
        "ORDER BY product_id LIMIT 1",
        (name,),
    )
    if row:
        return row["product_id"], False
    pid = execute_insert(
        "INSERT INTO tech_products (name, brand, category) VALUES (%s, %s, %s) "
        "RETURNING product_id",
        (name, brand, category),
    )
    return pid, True


def _video_ids_for(product_id):
    from scripts.database.queries import query_all

    rows = query_all(
        "SELECT video_id FROM videos WHERE product_id = %s LIMIT %s",
        (product_id, K),
    )
    return [r["video_id"] for r in rows]


def _select_and_save(product_id, name, brand, category):
    """영상 선정 Agent 실행 → videos 테이블 적재. 반환: 선정된 video_id 리스트.

    select_videos 라우트(video_selection_agent/api/routes.py)의 핵심 로직을 그대로
    재현 — agent.select() 결과를 all_scores/candidate_lookup 으로 가공해 save_selection.
    """
    from video_selection_agent.core.agent import VideoSelectionAgent
    from video_selection_agent.core.models import ProductContext
    from video_selection_agent.core.policy import SelectionPolicyConfig
    from video_selection_agent.persistence.repository import save_selection

    context = ProductContext(
        product_id=product_id, name=name, brand=brand, category=category, keywords=[]
    )
    policy = SelectionPolicyConfig(candidate_pool_size=CANDIDATE_POOL_SIZE)
    agent = VideoSelectionAgent(policy=policy)
    decision = agent.select(product=context, mode="auto", k=K)

    selected_ids = {v.video_id for v in decision.selected}
    all_scores = {
        vid: {
            "final_score": float(sb.final_score),
            "dimensions": {**sb.dimensions, **sb.extras},
            "tier": sb.tier,
            "rank": sb.rank,
            "rationale_short": sb.llm_rationale_short,
            "rationale_full": sb.llm_rationale_full,
            "selected": vid in selected_ids,
        }
        for vid, sb in decision.all_scores.items()
    }
    candidate_lookup = {
        c.video_id: {
            "title": c.title,
            "description": c.description,
            "published_at": c.published_at,
            "thumbnail_url": c.thumbnail_url,
            "view_count": c.view_count,
            "like_count": c.like_count,
            "comment_count": c.comment_count,
            "channel_id": c.channel_id,
            "channel_name": c.channel_name,
            "channel_subscriber_count": c.channel_subscriber_count,
            "duration_seconds": c.duration_seconds,
        }
        for c in decision.candidates_preview
    }
    save_selection(
        decision,
        all_scores=all_scores,
        candidate_lookup=candidate_lookup,
        reset_existing_videos=True,
    )
    return [v.video_id for v in decision.selected]


async def _collect_comments_and_reports(product_id, name, video_ids):
    """댓글(수집·분류·ABSA) + 자막/영상별 ①②③ 을 보장한다 (self-heal).

    run_experiment 모아봄 단계가 쓰는 공개 함수 그대로. 이미 있으면 캐시 재사용.
    """
    from scripts.reports.product_integrated_insight import (
        ensure_comment_analysis_for_videos,
        ensure_all_reports_for_product,
    )

    await ensure_comment_analysis_for_videos(name, video_ids)
    await ensure_all_reports_for_product(product_id, name, video_ids)


def prepare_one(product):
    name = product["productName"]
    brand = product.get("brand")
    category = product.get("category")

    pid, created = _find_or_create_product(name, brand, category)
    print(f"\n── 제품: {name} (product_id={pid}{', 신규 생성' if created else ''}) ──")

    existing = _video_ids_for(pid)
    if len(existing) >= MIN_VIDEOS:
        print(f"  [SKIP] 이미 영상 {len(existing)}개 적재됨 — 선정/검색 건너뜀(멱등)")
        video_ids = existing
    else:
        # config 에 video_ids 가 명시돼 있으면 그대로 사용, 아니면 검색·선정.
        explicit = product.get("video_ids") or []
        if explicit:
            print(f"  [SELECT] config 명시 영상 {len(explicit)}개 사용")
            video_ids = explicit[:K]
        else:
            print(f"  [SELECT] YouTube 검색·영상 선정 Agent 실행 (k={K}, pool={CANDIDATE_POOL_SIZE}) …")
            video_ids = _select_and_save(pid, name, brand, category)
            print(f"  [SELECT] 선정 완료 — videos {len(video_ids)}개 적재")

    if len(video_ids) < MIN_VIDEOS:
        print(f"  [WARN] 영상 {len(video_ids)}개(<{MIN_VIDEOS}) — 모아봄 비교 불가. "
              f"검색 결과 부족/할당량 확인 필요. 댓글·보고서 수집은 건너뜀")
        return {"product": name, "product_id": pid, "videos": len(video_ids), "ok": False}

    # 자막 먼저 throttle 로 선수집(429 회피) → 이후 단계는 캐시 재사용
    _prefetch_transcripts_throttled(video_ids)
    print(f"  [COLLECT] 댓글·①②③ 수집 (영상 {len(video_ids)}개) …")
    asyncio.run(_collect_comments_and_reports(pid, name, video_ids))
    print(f"  [OK] 준비 완료 — product_id={pid}, videos={len(video_ids)}")
    return {"product": name, "product_id": pid, "videos": len(video_ids), "ok": True}


def _init_db():
    from scripts.database.schema import init_db

    init_db()


def main():
    print("=" * 70)
    print("  실험 데이터 준비 — YouTube 검색·수집 → DB 저장 (1회용)")
    print(f"  제품 {len(config.PRODUCTS)}개")
    print("=" * 70)

    _init_db()  # 스키마 보장

    results = []
    for product in config.PRODUCTS:
        try:
            results.append(prepare_one(product))
        except Exception as e:  # noqa: BLE001 — 한 제품 실패가 전체를 막지 않음
            print(f"  [ERROR] '{product['productName']}' 준비 실패: {type(e).__name__}: {e}")
            results.append({"product": product["productName"], "ok": False, "error": str(e)})

    print("\n" + "=" * 70)
    print("  준비 결과 요약")
    print("=" * 70)
    ready = 0
    for r in results:
        status = "✅ 준비됨" if r.get("ok") else "❌ 불가"
        extra = f"videos={r.get('videos', 0)}" if "videos" in r else r.get("error", "")
        print(f"  {status}  {r['product']:14s} {extra}")
        if r.get("ok"):
            ready += 1
    print(f"\n  → {ready}/{len(results)} 제품 비교 준비 완료. 이제 다음을 실행하세요:")
    print("     python experiment/run_experiment.py")


if __name__ == "__main__":
    main()
