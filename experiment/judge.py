"""
근거 추적률 채점 심판 (LLM judge).

세 시스템(GPT·Gemini·모아봄)의 출력을 **동일한 기준·동일한 모델**로 채점한다.
이게 공정성의 핵심: 모아봄이 유리하도록 기준을 비틀지 않고, 누구든 "주장마다
검증 가능한 출처를 달았는가"만 본다.

반환: {"total_claims": int, "evidence_linked_claims": int, "claims": [...]}
"""
import json
import re

from scripts.llm import get_chat_llm
from experiment import config


_JUDGE_PROMPT = """아래는 '{product}' 구매 판단에 대한 한 AI 시스템의 답변이다.
다음 기준으로 **객관적으로** 채점하라. 시스템이 누구인지는 고려하지 말 것.

[핵심 주장] = 제품에 대한 개별 평가성 진술. 예: "배터리가 오래간다", "가격이 비싸다",
  "카메라가 전작보다 개선됐다". 최대 {max_claims}개까지 추출한다.

[근거 연결됨(evidence_linked=true)] = 그 주장에 **검증 가능한 구체적 출처**가 함께
  제시된 경우만 해당. 구체적 출처란:
   - 특정 영상/리뷰어/타임스탬프 지칭
   - 자막·리뷰 본문 인용
   - 사용자 댓글 원문 인용
   - 실제 URL 또는 출처 매체명 + 구체적 수치
  반대로 "일반적으로", "많은 리뷰가", "알려져 있다" 같은 **출처 없는 일반 진술**은
  evidence_linked=false 로 본다.

아래 JSON 형식으로만 답하라. 설명·코드펜스 금지:
{{"total_claims": <정수>, "evidence_linked_claims": <정수>, "claims": [{{"claim": "...", "evidence_linked": true, "evidence": "근거 요지 또는 빈 문자열"}}]}}

답변 본문:
\"\"\"
{output}
\"\"\""""


def _extract_json(text: str) -> dict:
    """LLM 응답에서 첫 JSON 객체를 robust 하게 파싱."""
    if not text:
        return {}
    # 코드펜스 제거
    text = re.sub(r"```(?:json)?", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        return json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return {}


def score_evidence_traceability(product_name: str, output_text: str) -> dict:
    """한 출력의 근거 추적률 채점. 실패 시 0개로 안전 퇴화."""
    if not output_text or not output_text.strip():
        return {"total_claims": 0, "evidence_linked_claims": 0, "claims": []}

    prompt = _JUDGE_PROMPT.format(
        product=product_name,
        max_claims=config.MAX_CLAIMS,
        output=output_text.strip()[:8000],  # 심판 입력 토큰 보호
    )
    try:
        llm = get_chat_llm(temperature=0.0, model=config.JUDGE_MODEL)
        resp = llm.invoke(prompt)
        parsed = _extract_json(getattr(resp, "content", "") or "")
    except Exception as e:  # noqa: BLE001 — 채점 실패가 실험 전체를 막지 않음
        print(f"[WARN][judge] 채점 실패 ({type(e).__name__}: {e}) → 0개 처리")
        return {"total_claims": 0, "evidence_linked_claims": 0, "claims": []}

    claims = parsed.get("claims") or []
    # claims 기반으로 합계를 재계산해 LLM 의 자기 합산 오류를 보정
    total = len(claims) if claims else int(parsed.get("total_claims", 0) or 0)
    linked = (
        sum(1 for c in claims if c.get("evidence_linked"))
        if claims
        else int(parsed.get("evidence_linked_claims", 0) or 0)
    )
    # 상한 적용 + 모순 방지
    total = min(total, config.MAX_CLAIMS) if total else 0
    linked = min(linked, total)
    return {"total_claims": total, "evidence_linked_claims": linked, "claims": claims[: config.MAX_CLAIMS]}
