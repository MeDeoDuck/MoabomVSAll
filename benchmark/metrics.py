"""
정량 비교 지표 계산 함수.

요청 명세의 4개 함수를 그대로 구현한다(파이썬 네이밍 컨벤션으로):
    1. calculate_evidence_traceability(run)       근거 추적률(%)
    2. calculate_decision_consistency(runs)       판정 일관성(%)
    3. calculate_evidence_volume(run)             분석 근거량(영상+자막+댓글)
    4. get_system_average_metrics(runs, system)   시스템별 평균 지표

부수적으로 그룹핑/대표 실행 추출 헬퍼를 제공한다. 모든 계산은 data.py 의
BENCHMARK_RUNS(또는 동일 스키마의 리스트)에 대해 순수 함수로 동작한다.
"""
from collections import Counter, defaultdict

# 최종 판정 허용값
DECISIONS = ["추천", "조건부 추천", "비추천", "데이터 부족"]


def calculate_evidence_traceability(run):
    """근거 추적률(%) = evidenceLinkedClaims / totalClaims * 100.

    totalClaims 가 0이면 0.0 을 반환한다(0 나눗셈 방지).
    """
    total = run.get("totalClaims", 0) or 0
    if total == 0:
        return 0.0
    return run.get("evidenceLinkedClaims", 0) / total * 100.0


def calculate_evidence_volume(run):
    """분석 근거량 = videoCount + captionCount + commentCount."""
    return (
        run.get("videoCount", 0)
        + run.get("captionCount", 0)
        + run.get("commentCount", 0)
    )


def group_runs(runs):
    """(productId, system) 키로 실행 결과를 묶어 dict 로 반환."""
    groups = defaultdict(list)
    for r in runs:
        groups[(r["productId"], r["system"])].append(r)
    return dict(groups)


def representative_run(runs):
    """그룹 내 대표 실행 = executedAt 이 가장 최신인 실행.

    날짜가 같으면 입력 순서상 뒤(=더 최근 runId)를 대표로 본다.
    분석 근거량 총합을 낼 때 반복 실행이 중복 합산되지 않도록 쓰는 헬퍼.
    """
    if not runs:
        return None
    best = runs[0]
    for r in runs[1:]:
        if r["executedAt"] >= best["executedAt"]:
            best = r
    return best


def calculate_decision_consistency(runs):
    """판정 일관성(%) — 동일 (productId, system) 그룹의 반복 실행 리스트를 받는다.

    - decision 값 중 가장 많이 나온 값(majority)의 개수 / 전체 실행 수 * 100
    - 실행이 1개뿐이면 100.0 으로 처리하되 insufficient_runs=True 로 표시
      (UI 에서 "반복 실행 부족" 배지를 띄우기 위함)

    반환:
        {
          "consistency": float,        # 0~100
          "majority_decision": str|None,
          "run_count": int,
          "insufficient_runs": bool,   # 반복 실행 부족(<2) 여부
        }
    """
    n = len(runs)
    if n == 0:
        return {
            "consistency": 0.0,
            "majority_decision": None,
            "run_count": 0,
            "insufficient_runs": True,
        }

    counts = Counter(r["decision"] for r in runs)
    majority_decision, top = counts.most_common(1)[0]

    if n == 1:
        return {
            "consistency": 100.0,
            "majority_decision": majority_decision,
            "run_count": 1,
            "insufficient_runs": True,
        }

    return {
        "consistency": top / n * 100.0,
        "majority_decision": majority_decision,
        "run_count": n,
        "insufficient_runs": False,
    }


def get_system_average_metrics(runs, system):
    """특정 system 의 평균 지표 묶음.

    반환:
        {
          "system": str,
          "run_count": int,                 # 해당 시스템 전체 실행 수
          "product_count": int,             # 해당 시스템이 분석한 제품 수
          "avg_traceability": float,        # 평균 근거 추적률(%)
          "avg_consistency": float,         # 제품별 판정 일관성의 평균(%)
          "total_videos": int,              # 대표 실행 기준 영상 수 합(중복 합산 방지)
          "total_comments": int,            # 대표 실행 기준 댓글 수 합
          "total_evidence_volume": int,     # 대표 실행 기준 근거량 합
          "avg_comments_per_product": float # 제품당 평균 분석 댓글 수
        }
    """
    sys_runs = [r for r in runs if r["system"] == system]
    if not sys_runs:
        return {
            "system": system, "run_count": 0, "product_count": 0,
            "avg_traceability": 0.0, "avg_consistency": 0.0,
            "total_videos": 0, "total_comments": 0,
            "total_evidence_volume": 0, "avg_comments_per_product": 0.0,
        }

    # 평균 근거 추적률 — 전체 실행 평균
    avg_traceability = sum(
        calculate_evidence_traceability(r) for r in sys_runs
    ) / len(sys_runs)

    # 제품별 그룹 → 일관성 평균 / 대표 실행 기반 근거량 합(중복 합산 방지)
    groups = group_runs(sys_runs)
    consistencies = []
    total_videos = total_comments = total_volume = 0
    for group in groups.values():
        consistencies.append(calculate_decision_consistency(group)["consistency"])
        rep = representative_run(group)
        total_videos += rep.get("videoCount", 0)
        total_comments += rep.get("commentCount", 0)
        total_volume += calculate_evidence_volume(rep)

    product_count = len(groups)
    avg_consistency = sum(consistencies) / len(consistencies) if consistencies else 0.0
    avg_comments_per_product = total_comments / product_count if product_count else 0.0

    return {
        "system": system,
        "run_count": len(sys_runs),
        "product_count": product_count,
        "avg_traceability": avg_traceability,
        "avg_consistency": avg_consistency,
        "total_videos": total_videos,
        "total_comments": total_comments,
        "total_evidence_volume": total_volume,
        "avg_comments_per_product": avg_comments_per_product,
    }


def build_group_summary(runs):
    """비교 테이블용 — (productId, system) 그룹별 요약 행 리스트.

    각 행:
        product_id, product_name, system, run_count, final_decision,
        avg_traceability, consistency, insufficient_runs,
        video_count(대표), comment_count(대표), has_data_insufficient(그룹 내 any), note
    """
    rows = []
    for (pid, system), group in group_runs(runs).items():
        cons = calculate_decision_consistency(group)
        rep = representative_run(group)
        avg_trace = sum(calculate_evidence_traceability(r) for r in group) / len(group)
        rows.append({
            "product_id": pid,
            "product_name": group[0]["productName"],
            "system": system,
            "run_count": len(group),
            "final_decision": cons["majority_decision"],
            "avg_traceability": avg_trace,
            "consistency": cons["consistency"],
            "insufficient_runs": cons["insufficient_runs"],
            "video_count": rep.get("videoCount", 0),
            "comment_count": rep.get("commentCount", 0),
            "has_data_insufficient": any(r.get("hasDataInsufficient") for r in group),
            "note": rep.get("note", ""),
        })
    return rows


def evidence_summary_text(run):
    """제품별 상세 영역의 '대표 근거 요약' 문구 생성."""
    if calculate_evidence_volume(run) <= 0:
        return "외부 데이터 미수집 — 모델 내부 지식 기반 답변"
    return (
        f"영상 {run.get('videoCount', 0)}편 · 자막 {run.get('captionCount', 0)}건 · "
        f"댓글 {run.get('commentCount', 0):,}건 수집, "
        f"대표 댓글 {run.get('representativeCommentCount', 0)}건 인용"
    )
