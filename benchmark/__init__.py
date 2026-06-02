"""모아봄 vs 시중 AI 정량 비교 대시보드 패키지.

- data.py      : 발표/명분용 샘플 데이터(BENCHMARK_RUNS)
- metrics.py   : 근거 추적률·판정 일관성·근거량 등 순수 지표 함수
- dashboard.py : 의존성 없는 단일 HTML 대시보드 생성기

experiment/run_experiment.py 가 실측 결과(output/experiment_runs.json)를 남기면
dashboard 가 그것을 우선 로드하고, 없으면 data.py 샘플로 안전 퇴화한다.
"""
