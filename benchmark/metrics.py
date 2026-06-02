"""
정량 비교 지표 계산 함수 (v3 — 판정 일관성 단일 지표).

지표:
    calculate_decision_consistency(runs)       판정 일관성(%)
    get_system_average_metrics(runs, system)   시스템별 평균(일관성)

실행시간·근거 추적률·분석 근거량·심판 LLM 은 v3 에서 제거됨.
모든 계산은 data.py 의 BENCHMARK_RUNS(또는 동일 스키마 리스트)에 대해
순수 함수로 동작한다.
"""
from collections import Counter, defaultdict

# 최종 판정 허용값
DECISIONS = ["추천", "조건부 추천", "비추천", "데이터 부족"]


def group_runs(runs):
    """(productId, system) 키로 실행 결과를 묶어 dict 로 반환."""
    groups = defaultdict(list)
    for r in runs:
        groups[(r["productId"], r["system"])].append(r)
    return dict(groups)


def calculate_decision_consistency(runs):
    """판정 일관성(%) — 동일 (productId, system) 그룹의 반복 실행 리스트를 받는다.

    - decision 값 중 가장 많이 나온 값(majority)의 개수 / 전체 실행 수 * 100
    - 실행이 1개뿐이면 100.0 으로 처리하되 insufficient_runs=True 로 표시
      (UI 에서 "반복 부족" 배지를 띄우기 위함)

    반환:
        {"consistency": float, "majority_decision": str|None,
         "run_count": int, "insufficient_runs": bool}
    """
    n = len(runs)
    if n == 0:
        return {"consistency": 0.0, "majority_decision": None,
                "run_count": 0, "insufficient_runs": True}

    counts = Counter(r["decision"] for r in runs)
    majority_decision, top = counts.most_common(1)[0]

    if n == 1:
        return {"consistency": 100.0, "majority_decision": majority_decision,
                "run_count": 1, "insufficient_runs": True}

    return {"consistency": top / n * 100.0, "majority_decision": majority_decision,
            "run_count": n, "insufficient_runs": False}


def get_system_average_metrics(runs, system):
    """특정 system 의 평균 지표 묶음 (v3 — 일관성만).

    반환:
        {"system": str, "run_count": int, "product_count": int,
         "avg_consistency": float}
    """
    sys_runs = [r for r in runs if r["system"] == system]
    if not sys_runs:
        return {"system": system, "run_count": 0, "product_count": 0,
                "avg_consistency": 0.0}

    groups = group_runs(sys_runs)
    consistencies = [
        calculate_decision_consistency(g)["consistency"] for g in groups.values()
    ]
    avg_consistency = sum(consistencies) / len(consistencies) if consistencies else 0.0

    return {
        "system": system,
        "run_count": len(sys_runs),
        "product_count": len(groups),
        "avg_consistency": avg_consistency,
    }


def build_group_summary(runs):
    """비교 테이블용 — (productId, system) 그룹별 요약 행 리스트 (v3).

    각 행:
        product_id, product_name, system, run_count, final_decision,
        consistency, insufficient_runs, note
    """
    rows = []
    for (pid, system), group in group_runs(runs).items():
        cons = calculate_decision_consistency(group)
        rows.append({
            "product_id": pid,
            "product_name": group[0]["productName"],
            "system": system,
            "run_count": len(group),
            "final_decision": cons["majority_decision"],
            "consistency": cons["consistency"],
            "insufficient_runs": cons["insufficient_runs"],
            "note": group[-1].get("note", ""),
        })
    return rows
