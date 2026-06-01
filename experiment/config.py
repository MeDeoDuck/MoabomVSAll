"""
정량 비교 실험 설정 — `python experiment/run_experiment.py` 한 방으로 도는 실험의
유일한 손댈 곳.

실험 설계(공정성 원칙):
  - 세 시스템(GPT·Gemini·모아봄)에 완전히 동일한 제품 + 동일한 질문을 준다.
  - 같은 LLM "심판"이 동일 기준으로 모든 출력의 근거 추적률을 채점한다.
  - 같은 제품을 REPEAT 회 반복 실행해 판정 일관성을 측정한다.
  - GPT/Gemini 는 RunYourAI 통합 게이트웨이로 호출(모델 문자열만 다름).
  - 모아봄은 운영 파이프라인 함수를 그대로 직접 호출(7섹션 보고서 생성).

주의:
  - 실제 LLM/YouTube API 를 호출하므로 토큰·쿼터 비용이 발생한다.
  - 모아봄 단계는 PostgreSQL(DATABASE_URL) + YOUTUBE_API_KEY 가 필요하고,
    제품과 선정 영상이 DB(tech_products / videos)에 미리 있어야 한다
    (= 앱에서 한 번 분석해 둔 제품). 없으면 모아봄 단계만 건너뛴다.
"""
import os

# ── 반복 실행 횟수 (판정 일관성 측정용) ───────────────────────────
# 권장 3~5. 클수록 일관성 추정이 안정적이지만 비용↑.
REPEAT = int(os.getenv("EXP_REPEAT", "3"))

# ── 비교 대상 시스템 on/off ───────────────────────────────────────
RUN_GPT = True
RUN_GEMINI = True
RUN_MOABOM = True   # DB·키 미구비 시 자동 skip (graceful)

# ── 모델 문자열 (RunYourAI 게이트웨이 provider/model 형식) ─────────
# GPT/Gemini 모두 동일 게이트웨이로 호출 — 모델만 교체.
GPT_MODEL = os.getenv("EXP_GPT_MODEL", "openai/gpt-4.1-2025-04-14")
GEMINI_MODEL = os.getenv("EXP_GEMINI_MODEL", "gemini/gemini-3.1-pro-preview")
# 근거 추적률을 채점하는 중립 심판 모델 (어느 시스템을 채점하든 동일).
JUDGE_MODEL = os.getenv("EXP_JUDGE_MODEL", "openai/gpt-4.1-2025-04-14")

# 심판이 한 출력에서 뽑는 핵심 주장 최대 개수 (세 시스템 동일 적용).
MAX_CLAIMS = int(os.getenv("EXP_MAX_CLAIMS", "10"))

# ── 비교 대상 제품 ────────────────────────────────────────────────
# product_id / video_ids 를 비워두면(None/[]) 모아봄 단계에서 DB 의
# tech_products(name 매칭) · videos 테이블을 자동 조회해 채운다.
# 명시적으로 적으면 그 값을 그대로 사용한다.
PRODUCTS = [
    {"productId": "iphone-16", "productName": "iPhone 16", "db_product_id": None, "video_ids": []},
    {"productId": "galaxy-s25", "productName": "Galaxy S25", "db_product_id": None, "video_ids": []},
    {"productId": "pixel-9a", "productName": "Pixel 9a", "db_product_id": None, "video_ids": []},
]

# 모아봄이 한 제품에서 사용할 최대 영상 수 (DB 자동 조회 시 상한).
MOABOM_MAX_VIDEOS = int(os.getenv("EXP_MOABOM_MAX_VIDEOS", "5"))

# 결과 저장 경로 (대시보드가 이 파일을 우선 로드).
RESULTS_JSON = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "benchmark", "output", "experiment_runs.json",
)
