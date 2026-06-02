#!/usr/bin/env python3
"""
정량 비교 실험 — 한 방 실행 오케스트레이터 (v3: 판정 일관성 단일 지표).

    python experiment/run_experiment.py

흐름 (제품 × 시스템 × REPEAT 회):
  1) GPT / Gemini  : RunYourAI 게이트웨이로 동일 구매판단 프롬프트 호출 → 판정
  2) 모아봄        : 운영 파이프라인 직접 호출 → 7섹션 보고서 생성 → 판정
  3) 집계          : benchmark/metrics.py 의 판정 일관성 계산
  4) 산출          : 결과 JSON 저장 + benchmark 대시보드 HTML 재생성·오픈

지표: 판정 일관성(같은 제품 REPEAT회 다수결 비율) 하나.
실행시간·근거 추적률·분석 근거량·심판 LLM 은 사용하지 않는다(v3).

모아봄 단계는 prepare_data.py 로 데이터를 미리 수집해 두면 빠르고 안정적이다.
"""
import json
import os
import sys
from datetime import date

# 프로젝트 루트를 import 경로에 추가 (scripts.*, video_selection_agent.*, benchmark.*)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Windows 콘솔 한글 출력
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from experiment import config, providers  # noqa: E402


def _init_db_if_possible() -> bool:
    """모아봄 단계용 DB 스키마 보장. 실패하면 False (모아봄 자동 skip)."""
    try:
        from scripts.database.schema import init_db

        init_db()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] init_db 실패 — 모아봄 단계는 건너뜁니다: {type(e).__name__}: {e}")
        return False


def _record(product, system, idx, decision, note):
    """benchmark/data.py 와 동일한 스키마의 run 레코드 생성 (v3)."""
    return {
        "productId": product["productId"],
        "productName": product["productName"],
        "system": system,
        "runId": f"{system.lower()}-{product['productId']}-{idx + 1}",
        "decision": decision,
        "executedAt": date.today().isoformat(),
        "note": note,
    }


def run_generic_system(product, system, model, runs):
    """GPT 또는 Gemini 를 REPEAT 회 실행해 레코드 누적."""
    for i in range(config.REPEAT):
        tag = f"{system} | {product['productName']} | {i + 1}/{config.REPEAT}"
        try:
            decision = providers.call_generic_llm(model, product["productName"])
        except Exception as e:  # noqa: BLE001 — 한 회 실패가 전체를 막지 않음
            print(f"[WARN][{tag}] 호출 실패: {type(e).__name__}: {e} — skip")
            continue
        runs.append(_record(product, system, i, decision,
                            note=f"RunYourAI 게이트웨이 / {model}"))
        print(f"[OK][{tag}] 판정={decision}")


def run_moabom_system(product, runs, db_ready):
    """모아봄을 REPEAT 회 실행해 레코드 누적 (DB 준비 안 됐으면 skip)."""
    if not db_ready:
        return
    for i in range(config.REPEAT):
        tag = f"모아봄 | {product['productName']} | {i + 1}/{config.REPEAT}"
        try:
            res = providers.run_moabom(
                product["productName"], product.get("db_product_id"), product.get("video_ids") or []
            )
        except Exception as e:  # noqa: BLE001
            print(f"[WARN][{tag}] 파이프라인 실패: {type(e).__name__}: {e} — skip")
            continue
        if res is None:
            print(f"[INFO][{tag}] 모아봄 단계 skip (위 경고 참고)")
            return  # 이 제품은 모아봄 불가 → 반복 중단
        runs.append(_record(product, "모아봄", i, res["decision"], res["note"]))
        print(f"[OK][{tag}] 판정={res['decision']}")


def main():
    print("=" * 70)
    print("  모아봄 vs 시중 AI — 정량 비교 실험 시작 (v3: 판정 일관성)")
    print(f"  제품 {len(config.PRODUCTS)}개 × 반복 {config.REPEAT}회")
    print("=" * 70)

    db_ready = _init_db_if_possible() if config.RUN_MOABOM else False
    runs = []

    for product in config.PRODUCTS:
        print(f"\n── 제품: {product['productName']} ──")
        if config.RUN_GPT:
            run_generic_system(product, "GPT", config.GPT_MODEL, runs)
        if config.RUN_GEMINI:
            run_generic_system(product, "Gemini", config.GEMINI_MODEL, runs)
        if config.RUN_MOABOM:
            run_moabom_system(product, runs, db_ready)

    if not runs:
        print("\n[ERROR] 수집된 실행 결과가 없습니다. API 키/DB 설정을 확인하세요.")
        return

    # 결과 저장
    os.makedirs(os.path.dirname(config.RESULTS_JSON), exist_ok=True)
    with open(config.RESULTS_JSON, "w", encoding="utf-8") as f:
        json.dump(runs, f, ensure_ascii=False, indent=2)
    print(f"\n[OK] 실험 결과 {len(runs)}건 저장 → {config.RESULTS_JSON}")

    # 대시보드 재생성 (저장된 실험 결과를 자동 로드)
    try:
        from benchmark import dashboard

        dashboard.main()
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 대시보드 생성 실패(결과 JSON 은 저장됨): {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
