"""
시중 AI(GPT·Gemini) vs 모아봄 정량 비교 — 샘플 벤치마크 데이터.

운영 DB와 무관한 발표/명분용 정적 데이터다. 발표 시나리오에 맞게 숫자만
여기서 수정하면 대시보드(dashboard.py)가 자동으로 다시 계산해 그려준다.

[필드 정의]
- productId / productName : 제품 식별자 / 표시명
- system                 : 비교 대상 AI ("GPT" / "Gemini" / "모아봄")
- runId                  : 반복 실행 식별자
- decision               : 최종 판정 ("추천" / "조건부 추천" / "비추천" / "데이터 부족")
- totalClaims            : 보고서가 내세운 전체 핵심 주장 수
- evidenceLinkedClaims   : 그중 영상/자막/댓글 근거가 연결된 주장 수
- videoCount             : 분석에 실제 사용된 영상 수
- captionCount           : 분석에 실제 사용된 자막 수
- commentCount           : 분석에 실제 사용된 댓글 수
- representativeCommentCount : 보고서에 인용된 대표 댓글 수
- hasDataInsufficient    : "데이터 부족" 처리 여부
- executedAt             : 실행 날짜 (YYYY-MM-DD)
- note                   : 비고

[데이터 설계 의도 — 비하가 아니라 과업 적합도 비교]
- GPT/Gemini 도 검색·요약은 가능하다. 다만 "영상·자막·댓글을 정해진 절차로
  수집"하지 않으므로 videoCount/commentCount 가 0이고, 주장-근거 연결률이 낮다.
- 모아봄은 근거 연결률·반복 일관성이 높지만, 근거가 부족하면(예: 틈새 제품)
  무리한 판정 대신 "데이터 부족"을 정직하게 반환한다(Pixel 9a 케이스).
"""

BENCHMARK_RUNS = [
    # ===================== iPhone 16 =====================
    # --- GPT (외부 데이터 미수집, 근거 연결률 중하 / 판정 다소 흔들림) ---
    {
        "productId": "iphone-16", "productName": "iPhone 16", "system": "GPT",
        "runId": "gpt-001", "decision": "추천",
        "totalClaims": 10, "evidenceLinkedClaims": 5,
        "videoCount": 0, "captionCount": 0, "commentCount": 0,
        "representativeCommentCount": 0, "hasDataInsufficient": False,
        "executedAt": "2026-05-30", "note": "범용 답변 기반",
    },
    {
        "productId": "iphone-16", "productName": "iPhone 16", "system": "GPT",
        "runId": "gpt-002", "decision": "조건부 추천",
        "totalClaims": 10, "evidenceLinkedClaims": 4,
        "videoCount": 0, "captionCount": 0, "commentCount": 0,
        "representativeCommentCount": 0, "hasDataInsufficient": False,
        "executedAt": "2026-05-30", "note": "범용 답변 기반(재실행)",
    },
    {
        "productId": "iphone-16", "productName": "iPhone 16", "system": "GPT",
        "runId": "gpt-003", "decision": "추천",
        "totalClaims": 10, "evidenceLinkedClaims": 6,
        "videoCount": 0, "captionCount": 0, "commentCount": 0,
        "representativeCommentCount": 0, "hasDataInsufficient": False,
        "executedAt": "2026-05-31", "note": "범용 답변 기반(재실행)",
    },
    # --- Gemini (검색 요약 기반) ---
    {
        "productId": "iphone-16", "productName": "iPhone 16", "system": "Gemini",
        "runId": "gemini-001", "decision": "조건부 추천",
        "totalClaims": 10, "evidenceLinkedClaims": 6,
        "videoCount": 0, "captionCount": 0, "commentCount": 0,
        "representativeCommentCount": 0, "hasDataInsufficient": False,
        "executedAt": "2026-05-30", "note": "검색 요약 기반",
    },
    {
        "productId": "iphone-16", "productName": "iPhone 16", "system": "Gemini",
        "runId": "gemini-002", "decision": "조건부 추천",
        "totalClaims": 10, "evidenceLinkedClaims": 5,
        "videoCount": 0, "captionCount": 0, "commentCount": 0,
        "representativeCommentCount": 0, "hasDataInsufficient": False,
        "executedAt": "2026-05-30", "note": "검색 요약 기반(재실행)",
    },
    {
        "productId": "iphone-16", "productName": "iPhone 16", "system": "Gemini",
        "runId": "gemini-003", "decision": "추천",
        "totalClaims": 10, "evidenceLinkedClaims": 6,
        "videoCount": 0, "captionCount": 0, "commentCount": 0,
        "representativeCommentCount": 0, "hasDataInsufficient": False,
        "executedAt": "2026-05-31", "note": "검색 요약 기반(재실행)",
    },
    # --- 모아봄 (영상·자막·댓글 수집 기반) ---
    {
        "productId": "iphone-16", "productName": "iPhone 16", "system": "모아봄",
        "runId": "moabom-001", "decision": "추천",
        "totalClaims": 10, "evidenceLinkedClaims": 9,
        "videoCount": 5, "captionCount": 5, "commentCount": 3200,
        "representativeCommentCount": 30, "hasDataInsufficient": False,
        "executedAt": "2026-05-30", "note": "영상·자막·댓글 수집 기반",
    },
    {
        "productId": "iphone-16", "productName": "iPhone 16", "system": "모아봄",
        "runId": "moabom-002", "decision": "추천",
        "totalClaims": 10, "evidenceLinkedClaims": 9,
        "videoCount": 5, "captionCount": 5, "commentCount": 3120,
        "representativeCommentCount": 30, "hasDataInsufficient": False,
        "executedAt": "2026-05-30", "note": "영상·자막·댓글 수집 기반(재실행)",
    },
    {
        "productId": "iphone-16", "productName": "iPhone 16", "system": "모아봄",
        "runId": "moabom-003", "decision": "추천",
        "totalClaims": 10, "evidenceLinkedClaims": 8,
        "videoCount": 5, "captionCount": 5, "commentCount": 3340,
        "representativeCommentCount": 28, "hasDataInsufficient": False,
        "executedAt": "2026-05-31", "note": "영상·자막·댓글 수집 기반(재실행)",
    },

    # ===================== Galaxy S25 =====================
    # --- GPT ---
    {
        "productId": "galaxy-s25", "productName": "Galaxy S25", "system": "GPT",
        "runId": "gpt-101", "decision": "추천",
        "totalClaims": 10, "evidenceLinkedClaims": 5,
        "videoCount": 0, "captionCount": 0, "commentCount": 0,
        "representativeCommentCount": 0, "hasDataInsufficient": False,
        "executedAt": "2026-05-30", "note": "범용 답변 기반",
    },
    {
        "productId": "galaxy-s25", "productName": "Galaxy S25", "system": "GPT",
        "runId": "gpt-102", "decision": "추천",
        "totalClaims": 10, "evidenceLinkedClaims": 4,
        "videoCount": 0, "captionCount": 0, "commentCount": 0,
        "representativeCommentCount": 0, "hasDataInsufficient": False,
        "executedAt": "2026-05-30", "note": "범용 답변 기반(재실행)",
    },
    {
        "productId": "galaxy-s25", "productName": "Galaxy S25", "system": "GPT",
        "runId": "gpt-103", "decision": "조건부 추천",
        "totalClaims": 10, "evidenceLinkedClaims": 5,
        "videoCount": 0, "captionCount": 0, "commentCount": 0,
        "representativeCommentCount": 0, "hasDataInsufficient": False,
        "executedAt": "2026-05-31", "note": "범용 답변 기반(재실행)",
    },
    # --- Gemini ---
    {
        "productId": "galaxy-s25", "productName": "Galaxy S25", "system": "Gemini",
        "runId": "gemini-101", "decision": "조건부 추천",
        "totalClaims": 10, "evidenceLinkedClaims": 6,
        "videoCount": 0, "captionCount": 0, "commentCount": 0,
        "representativeCommentCount": 0, "hasDataInsufficient": False,
        "executedAt": "2026-05-30", "note": "검색 요약 기반",
    },
    {
        "productId": "galaxy-s25", "productName": "Galaxy S25", "system": "Gemini",
        "runId": "gemini-102", "decision": "조건부 추천",
        "totalClaims": 10, "evidenceLinkedClaims": 6,
        "videoCount": 0, "captionCount": 0, "commentCount": 0,
        "representativeCommentCount": 0, "hasDataInsufficient": False,
        "executedAt": "2026-05-30", "note": "검색 요약 기반(재실행)",
    },
    {
        "productId": "galaxy-s25", "productName": "Galaxy S25", "system": "Gemini",
        "runId": "gemini-103", "decision": "비추천",
        "totalClaims": 10, "evidenceLinkedClaims": 5,
        "videoCount": 0, "captionCount": 0, "commentCount": 0,
        "representativeCommentCount": 0, "hasDataInsufficient": False,
        "executedAt": "2026-05-31", "note": "검색 요약 기반(재실행)",
    },
    # --- 모아봄 ---
    {
        "productId": "galaxy-s25", "productName": "Galaxy S25", "system": "모아봄",
        "runId": "moabom-101", "decision": "조건부 추천",
        "totalClaims": 10, "evidenceLinkedClaims": 8,
        "videoCount": 6, "captionCount": 6, "commentCount": 4500,
        "representativeCommentCount": 32, "hasDataInsufficient": False,
        "executedAt": "2026-05-30", "note": "영상·자막·댓글 수집 기반",
    },
    {
        "productId": "galaxy-s25", "productName": "Galaxy S25", "system": "모아봄",
        "runId": "moabom-102", "decision": "조건부 추천",
        "totalClaims": 10, "evidenceLinkedClaims": 8,
        "videoCount": 6, "captionCount": 6, "commentCount": 4380,
        "representativeCommentCount": 32, "hasDataInsufficient": False,
        "executedAt": "2026-05-30", "note": "영상·자막·댓글 수집 기반(재실행)",
    },
    {
        "productId": "galaxy-s25", "productName": "Galaxy S25", "system": "모아봄",
        "runId": "moabom-103", "decision": "조건부 추천",
        "totalClaims": 10, "evidenceLinkedClaims": 9,
        "videoCount": 6, "captionCount": 6, "commentCount": 4480,
        "representativeCommentCount": 34, "hasDataInsufficient": False,
        "executedAt": "2026-05-31", "note": "영상·자막·댓글 수집 기반(재실행)",
    },

    # ===================== Pixel 9a (틈새 제품 — 근거량이 적은 케이스) ====
    # --- GPT (데이터가 적어도 자신 있게 추천 — 다만 근거 연결률은 낮음) ---
    {
        "productId": "pixel-9a", "productName": "Pixel 9a", "system": "GPT",
        "runId": "gpt-201", "decision": "추천",
        "totalClaims": 10, "evidenceLinkedClaims": 4,
        "videoCount": 0, "captionCount": 0, "commentCount": 0,
        "representativeCommentCount": 0, "hasDataInsufficient": False,
        "executedAt": "2026-05-30", "note": "범용 답변 기반",
    },
    {
        "productId": "pixel-9a", "productName": "Pixel 9a", "system": "GPT",
        "runId": "gpt-202", "decision": "추천",
        "totalClaims": 10, "evidenceLinkedClaims": 5,
        "videoCount": 0, "captionCount": 0, "commentCount": 0,
        "representativeCommentCount": 0, "hasDataInsufficient": False,
        "executedAt": "2026-05-30", "note": "범용 답변 기반(재실행)",
    },
    {
        "productId": "pixel-9a", "productName": "Pixel 9a", "system": "GPT",
        "runId": "gpt-203", "decision": "추천",
        "totalClaims": 10, "evidenceLinkedClaims": 4,
        "videoCount": 0, "captionCount": 0, "commentCount": 0,
        "representativeCommentCount": 0, "hasDataInsufficient": False,
        "executedAt": "2026-05-31", "note": "범용 답변 기반(재실행)",
    },
    # --- Gemini ---
    {
        "productId": "pixel-9a", "productName": "Pixel 9a", "system": "Gemini",
        "runId": "gemini-201", "decision": "조건부 추천",
        "totalClaims": 10, "evidenceLinkedClaims": 5,
        "videoCount": 0, "captionCount": 0, "commentCount": 0,
        "representativeCommentCount": 0, "hasDataInsufficient": False,
        "executedAt": "2026-05-30", "note": "검색 요약 기반",
    },
    {
        "productId": "pixel-9a", "productName": "Pixel 9a", "system": "Gemini",
        "runId": "gemini-202", "decision": "추천",
        "totalClaims": 10, "evidenceLinkedClaims": 5,
        "videoCount": 0, "captionCount": 0, "commentCount": 0,
        "representativeCommentCount": 0, "hasDataInsufficient": False,
        "executedAt": "2026-05-30", "note": "검색 요약 기반(재실행)",
    },
    {
        "productId": "pixel-9a", "productName": "Pixel 9a", "system": "Gemini",
        "runId": "gemini-203", "decision": "조건부 추천",
        "totalClaims": 10, "evidenceLinkedClaims": 4,
        "videoCount": 0, "captionCount": 0, "commentCount": 0,
        "representativeCommentCount": 0, "hasDataInsufficient": False,
        "executedAt": "2026-05-31", "note": "검색 요약 기반(재실행)",
    },
    # --- 모아봄 (근거 부족 시 무리한 판정 대신 "데이터 부족" 반환) ---
    {
        "productId": "pixel-9a", "productName": "Pixel 9a", "system": "모아봄",
        "runId": "moabom-201", "decision": "데이터 부족",
        "totalClaims": 6, "evidenceLinkedClaims": 5,
        "videoCount": 2, "captionCount": 2, "commentCount": 180,
        "representativeCommentCount": 8, "hasDataInsufficient": True,
        "executedAt": "2026-05-30", "note": "수집 영상·댓글 부족 → 데이터 부족 명시",
    },
    {
        "productId": "pixel-9a", "productName": "Pixel 9a", "system": "모아봄",
        "runId": "moabom-202", "decision": "데이터 부족",
        "totalClaims": 6, "evidenceLinkedClaims": 5,
        "videoCount": 2, "captionCount": 2, "commentCount": 210,
        "representativeCommentCount": 8, "hasDataInsufficient": True,
        "executedAt": "2026-05-30", "note": "수집 영상·댓글 부족 → 데이터 부족 명시",
    },
    {
        "productId": "pixel-9a", "productName": "Pixel 9a", "system": "모아봄",
        "runId": "moabom-203", "decision": "조건부 추천",
        "totalClaims": 8, "evidenceLinkedClaims": 6,
        "videoCount": 3, "captionCount": 3, "commentCount": 260,
        "representativeCommentCount": 10, "hasDataInsufficient": False,
        "executedAt": "2026-05-31", "note": "추가 수집 후 조건부 판정 가능",
    },
]

# 비교 대상 시스템 (차트/테이블 노출 순서)
SYSTEMS = ["GPT", "Gemini", "모아봄"]

# 우리 제품 — UI 강조 기준
HIGHLIGHT_SYSTEM = "모아봄"
