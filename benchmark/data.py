"""
시중 AI(GPT·Gemini) vs 모아봄 정량 비교 — 샘플 벤치마크 데이터 (v3).

운영 DB와 무관한 발표/명분용 정적 데이터다. 실험 결과 JSON(experiment_runs.json)
이 없을 때 대시보드가 이 샘플로 안전 퇴화한다. 판정만 _SPEC 에서 수정하면
대시보드가 자동으로 다시 계산해 그려준다.

[run 레코드 스키마 v3]
- productId / productName : 제품 식별자 / 표시명
- system                 : 비교 대상 ("GPT" / "Gemini" / "모아봄")
- runId                  : 반복 실행 식별자
- decision               : 최종 판정 ("추천" / "조건부 추천" / "비추천" / "데이터 부족")
- executedAt             : 실행 날짜
- note                   : 비고

[설계 의도 — 판정 일관성]
- GPT/Gemini 는 같은 제품을 반복하면 판정이 흔들린다(일관성↓).
- 모아봄은 같은 데이터(한 번 수집한 댓글·자막) 위에서 반복하므로 판정이
  일관된다(일관성↑). 틈새 제품(Pixel 9a)은 "데이터 부족"을 일관되게 반환한다.
- 각 그룹 10회 = REPEAT 기본값과 동일.
"""

# (productId, productName) → {system: [decision, ... REPEAT개]}
_SPEC = {
    ("iphone-16", "iPhone 16"): {
        "GPT":    ["추천", "조건부 추천", "추천", "조건부 추천", "추천",
                   "추천", "조건부 추천", "추천", "조건부 추천", "추천"],          # 6/10
        "Gemini": ["조건부 추천", "조건부 추천", "추천", "조건부 추천", "추천",
                   "조건부 추천", "추천", "조건부 추천", "조건부 추천", "추천"],    # 6/10
        "모아봄":  ["추천"] * 10,                                                  # 10/10
    },
    ("galaxy-s25", "Galaxy S25"): {
        "GPT":    ["추천", "추천", "조건부 추천", "비추천", "추천",
                   "조건부 추천", "추천", "비추천", "추천", "조건부 추천"],        # 5/10
        "Gemini": ["조건부 추천", "조건부 추천", "비추천", "조건부 추천", "추천",
                   "조건부 추천", "비추천", "조건부 추천", "추천", "조건부 추천"],  # 6/10
        "모아봄":  ["조건부 추천"] * 9 + ["추천"],                                  # 9/10
    },
    ("pixel-9a", "Pixel 9a"): {
        "GPT":    ["추천", "조건부 추천", "추천", "추천", "조건부 추천",
                   "추천", "조건부 추천", "추천", "추천", "조건부 추천"],          # 6/10
        "Gemini": ["조건부 추천", "추천", "조건부 추천", "추천", "조건부 추천",
                   "추천", "조건부 추천", "추천", "조건부 추천", "추천"],          # 5/10
        "모아봄":  ["데이터 부족"] * 10,                                            # 10/10
    },
}

# 비교 대상 시스템 (차트/테이블 노출 순서)
SYSTEMS = ["GPT", "Gemini", "모아봄"]

# 우리 제품 — UI 강조 기준
HIGHLIGHT_SYSTEM = "모아봄"

# 실행 날짜 (샘플 표기용 — 정적)
_SAMPLE_DATE = "2026-05-31"


def _build_runs():
    out = []
    for (pid, pname), by_system in _SPEC.items():
        for system in SYSTEMS:
            decisions = by_system.get(system, [])
            prefix = {"GPT": "gpt", "Gemini": "gemini", "모아봄": "moabom"}[system]
            for i, decision in enumerate(decisions, start=1):
                out.append({
                    "productId": pid,
                    "productName": pname,
                    "system": system,
                    "runId": f"{prefix}-{pid}-{i}",
                    "decision": decision,
                    "executedAt": _SAMPLE_DATE,
                    "note": ("범용 답변 기반" if system in ("GPT", "Gemini")
                             else "파이프라인 직접 호출"),
                })
    return out


BENCHMARK_RUNS = _build_runs()
