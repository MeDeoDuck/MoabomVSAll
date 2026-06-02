"""
정량 비교 실험 설정 — `python experiment/run_experiment.py` 한 방으로 도는 실험의
유일한 손댈 곳.

실험 설계(공정성 원칙):
  - 세 시스템(GPT·Gemini·모아봄)에 완전히 동일한 제품 + 동일한 질문을 준다.
  - 같은 제품을 REPEAT(기본 10)회 반복 실행해 **판정 일관성**을 측정한다.
  - GPT/Gemini 는 RunYourAI 통합 게이트웨이로 호출(모델 문자열만 다름).
  - 모아봄은 운영 파이프라인 함수를 그대로 직접 호출(7섹션 보고서 생성).

지표 (v3 — 실행시간·근거 추적률·분석 근거량 모두 제거, 심판 LLM 미사용):
  · 판정 일관성(%) : 같은 제품 REPEAT회 중 다수결 판정 비율 (유일 지표)

주의:
  - 실제 LLM/YouTube API 를 호출하므로 토큰·쿼터 비용이 발생한다.
  - 모아봄 단계는 PostgreSQL(DATABASE_URL) + 제품/영상이 DB(tech_products /
    videos)에 미리 있어야 한다. 없으면 모아봄 단계만 건너뛴다.
    → experiment/prepare_data.py 로 먼저 수집·저장해 두는 것을 권장.
"""
import os

# 벤치마크 기본 자막 우선순위: ko → en → ja (옵션 B).
# tlang(자동번역)은 transcript_service 가 URL 레벨에서 건너뛰므로, 여기서 받는 건
# 항상 "원본" 자막이다. ko 원본이 없으면 en/ja 원본을 받아 GPT-4.1 이 한국어
# 보고서로 처리한다(모아봄 보고서 agent 는 자막 언어 불문). 명시 지정 시 그 값 우선.
os.environ.setdefault("TRANSCRIPT_LANGS", "ko,en,ja")

# ── 반복 실행 횟수 (판정 일관성 측정용) ───────────────────────────
# 사용자 요구: 같은 제품으로 10번 뽑아 일관성 측정.
REPEAT = int(os.getenv("EXP_REPEAT", "10"))

# ── 비교 대상 시스템 on/off ───────────────────────────────────────
RUN_GPT = True
RUN_GEMINI = True
RUN_MOABOM = True   # DB·키 미구비 시 자동 skip (graceful)

# ── 모델 문자열 (RunYourAI 게이트웨이 provider/model 형식) ─────────
# GPT/Gemini 모두 동일 게이트웨이로 호출 — 모델만 교체.
GPT_MODEL = os.getenv("EXP_GPT_MODEL", "openai/gpt-4.1-2025-04-14")
GEMINI_MODEL = os.getenv("EXP_GEMINI_MODEL", "gemini/gemini-3.1-pro-preview")

# ── 비교 대상 제품 ────────────────────────────────────────────────
# product_id / video_ids 를 비워두면(None/[]) 모아봄 단계에서 DB 의
# tech_products(name 매칭) · videos 테이블을 자동 조회해 채운다.
# 명시적으로 적으면 그 값을 그대로 사용한다.
PRODUCTS = [
    {"productId": "galaxy-21", "productName": "갤럭시21", "db_product_id": 4, "video_ids": []},
    {"productId": "iphone-16", "productName": "iPhone 16", "db_product_id": 554, "video_ids": []},
    {"productId": "galaxy-s25", "productName": "Galaxy S25", "db_product_id": 555, "video_ids": []},
    {"productId": "pixel-9a", "productName": "Pixel 9a", "db_product_id": 556, "video_ids": []},
    {"productId": "airpods-pro3", "productName": "에어팟 프로3", "db_product_id": 6, "video_ids": []},
    {"productId": "iphone-17", "productName": "아이폰 17", "db_product_id": 13, "video_ids": []},
    {"productId": "gopro", "productName": "고프로", "db_product_id": 2, "video_ids": []},
    {"productId": "airpods-4", "productName": "에어팟 4", "db_product_id": 14, "video_ids": []},
    {"productId": "iphone-12-pro-max", "productName": "아이폰 12 Pro Max", "db_product_id": 87, "video_ids": []},
    {"productId": "airpods-2", "productName": "에어팟 2세대", "db_product_id": 146, "video_ids": []},
]

# 모아봄이 한 제품에서 사용할 최대 영상 수 (DB 자동 조회 시 상한).
MOABOM_MAX_VIDEOS = int(os.getenv("EXP_MOABOM_MAX_VIDEOS", "5"))

# 결과 저장 경로 (대시보드가 이 파일을 우선 로드).
RESULTS_JSON = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "benchmark", "output", "experiment_runs.json",
)
