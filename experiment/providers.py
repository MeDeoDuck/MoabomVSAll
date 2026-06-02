"""
시스템별 호출기 (provider).

- GPT / Gemini: RunYourAI 통합 게이트웨이로 동일 프롬프트를 호출(모델만 교체).
- 모아봄: 운영 파이프라인 함수를 그대로 직접 호출해 7섹션 보고서를 생성하고
  최종 판정을 추출한다.

v3: 심판 LLM·실행시간·근거 추적률 모두 제거 → 각 호출기는 "판정"만 돌려준다.
"""
import asyncio
import re
from typing import Dict, List, Optional, Tuple

from scripts.llm import get_chat_llm
from experiment import config


# ── 공통 구매 판단 프롬프트 (GPT·Gemini 동일) ─────────────────────
_BUYER_PROMPT = """{product} 제품을 살지 말지 판단해줘.

- 핵심 평가 주장을 5~10개 나열하고, 각 주장마다 가능한 한 근거(출처)를 함께 제시해.
- 과장하지 말고, 네가 실제로 아는 근거만 사용해.
- 마지막 줄에 반드시 아래 형식으로 결론을 적어줘 (보기 중 하나만):
[최종판정: 추천 / 조건부 추천 / 비추천 / 데이터 부족]"""

DECISIONS = ["추천", "조건부 추천", "비추천", "데이터 부족"]


def _parse_decision_from_verdict_line(text: str) -> Optional[str]:
    """GPT/Gemini 출력의 [최종판정: ...] 줄에서 판정 추출."""
    if not text:
        return None
    m = re.search(r"\[?\s*최종판정\s*[:：]\s*(추천|조건부\s*추천|비추천|데이터\s*부족)", text)
    if m:
        v = re.sub(r"\s+", " ", m.group(1)).strip()
        return "조건부 추천" if "조건부" in v else ("데이터 부족" if "데이터" in v else v)
    # 폴백: 본문 끝부분에서 키워드 스캔
    tail = text[-200:]
    for d in ["조건부 추천", "비추천", "데이터 부족", "추천"]:
        if d in tail:
            return d
    return None


def call_generic_llm(model: str, product_name: str) -> str:
    """범용 LLM(GPT/Gemini)을 1회 호출. 반환: 판정 문자열.

    판정 파싱 실패 시 "데이터 부족" 으로 둔다(모델이 명시 안 한 판정을 임의
    주입하지 않기 위함 — 일관성 지표 왜곡 방지).
    """
    prompt = _BUYER_PROMPT.format(product=product_name)
    llm = get_chat_llm(temperature=0.7, model=model)  # 사용자 기본 사용처럼 약간의 다양성
    resp = llm.invoke(prompt)
    text = getattr(resp, "content", "") or ""
    return _parse_decision_from_verdict_line(text) or "데이터 부족"


# ── 모아봄 판정 추출 (종합 평가 점수 → 4값 어휘 매핑) ──────────────
# scripts/popup/extractor.py 의 임계값과 동일하게 맞춤:
#   7.5+ → 추천, 4.0~7.49 → 조건부 추천, 1.0~3.99 → 비추천,
#   점수 없음/데이터 부족 → 데이터 부족
_RE_MOABOM_SCORE = re.compile(
    r"종합\s*(?:평가|점수)\s*[:：]\s*([0-9]+(?:\.[0-9]+)?|데이터\s*부족)\s*/\s*10"
)


def _moabom_decision_from_report(report_text: str) -> str:
    if not report_text:
        return "데이터 부족"
    m = _RE_MOABOM_SCORE.search(report_text)
    if not m:
        return "데이터 부족"
    raw = m.group(1)
    if "데이터" in raw:
        return "데이터 부족"
    try:
        score = float(raw)
    except ValueError:
        return "데이터 부족"
    if score >= 7.5:
        return "추천"
    if score >= 4.0:
        return "조건부 추천"
    return "비추천"


def _resolve_product_and_videos(
    product_name: str,
    db_product_id: Optional[int],
    video_ids: List[str],
) -> Tuple[Optional[int], List[str]]:
    """config 에 비어 있으면 DB(tech_products/videos)에서 자동 조회."""
    from scripts.database.queries import query_one, query_all

    pid = db_product_id
    if pid is None:
        row = query_one(
            "SELECT product_id FROM tech_products WHERE LOWER(name) = LOWER(%s) "
            "ORDER BY product_id LIMIT 1",
            (product_name,),
        )
        pid = row["product_id"] if row else None
    if pid is None:
        return None, []

    vids = list(video_ids or [])
    if not vids:
        rows = query_all(
            "SELECT video_id FROM videos WHERE product_id = %s LIMIT %s",
            (pid, config.MOABOM_MAX_VIDEOS),
        )
        vids = [r["video_id"] for r in rows]
    return pid, vids[: config.MOABOM_MAX_VIDEOS]


async def _moabom_pipeline(
    product_id: int, product_name: str, video_ids: List[str]
) -> Tuple[str, str]:
    """운영 파이프라인 그대로: 댓글 self-heal → ①②③ 확보 → ④ 생성.

    데이터는 prepare_data 단계에서 캐시돼 있으면 fetch 없이 즉시 진행한다.
    반환: (④ 보고서 텍스트, 사용 모델).
    """
    from scripts.reports.product_integrated_insight import (
        ensure_comment_analysis_for_videos,
        ensure_all_reports_for_product,
        build_product_integrated_insight_report,
    )

    await ensure_comment_analysis_for_videos(product_name, video_ids)
    per_video = await ensure_all_reports_for_product(product_id, product_name, video_ids)
    report_text, model_used = build_product_integrated_insight_report(
        product_name, per_video, video_ids=video_ids, selected_video_count=len(video_ids)
    )
    return report_text, model_used


def run_moabom(
    product_name: str,
    db_product_id: Optional[int],
    video_ids: List[str],
) -> Optional[Dict]:
    """모아봄 1회 실행. 반환: {"decision", "note"} 또는 None(불가 시)."""
    pid, vids = _resolve_product_and_videos(product_name, db_product_id, video_ids)
    if pid is None or len(vids) < 2:
        print(
            f"[WARN][moabom] '{product_name}' — DB 에서 product_id/영상(≥2)을 찾지 못해 "
            f"모아봄 단계를 건너뜁니다 (pid={pid}, videos={len(vids)}). "
            f"experiment/prepare_data.py 를 먼저 실행하세요."
        )
        return None

    report_text, model_used = asyncio.run(_moabom_pipeline(pid, product_name, vids))
    decision = _moabom_decision_from_report(report_text)
    return {
        "decision": decision,
        "note": f"파이프라인 직접 호출 ({model_used})",
    }
