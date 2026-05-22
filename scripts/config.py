"""
Config module - Environment variables and settings
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Database
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/techdb")

# API Keys
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
HF_TOKEN = os.getenv("HF_TOKEN", "")
# Serper (serper.dev) — Google Images 검색. 키 값은 .env 에만 존재하며
# (gitignore), 여기서는 환경변수로 읽기만 한다 — 코드/커밋 하드코딩 금지.
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")

# RunYourAI — 통합 LLM provider (OpenAI / Claude / Gemini 단일 키).
# 모델 형식: "openai/gpt-4.1", "claude/claude-haiku-4-5", "gemini/gemini-2.5-flash" 등.
RUNYOURAI_API_KEY = os.getenv("RUNYOURAI_API_KEY", "")
RUNYOURAI_BASE_URL = os.getenv("RUNYOURAI_BASE_URL", "https://api.runyour.ai/v1")
RUNYOURAI_MODEL = os.getenv("RUNYOURAI_MODEL", "openai/gpt-4.1-2025-04-14")

# Server
PORT = int(os.getenv("PORT", 8000))
HOST = "0.0.0.0"

# ── Phase 2-a: 보고서 ④ 입력 확장 (영상별 ①②③ 종합) on/off ──
# on  : ④ 가 영상 N개의 ①②③ 을 종합 (Phase 2-a 기본)
# off : Phase 2-a 이전과 정확히 동일 (영상별 ① + ⑤용 댓글 집계만)
# 회귀 비교·긴급 대응용. REPORT4_INPUT_EXPANSION=0/false/no/off → 비활성.
REPORT4_INPUT_EXPANSION = os.getenv(
    "REPORT4_INPUT_EXPANSION", "1"
).strip().lower() not in ("0", "false", "no", "off")

# ── Phase 2-b: 보고서 ④ 생성 RAG (의미 검색·재정렬) on/off ──
# on  : truncate_bundles(절삭) 자리에 RAG 검색·재정렬 (Phase 2-b 기본)
# off : Phase 2-a 동작(=truncate_bundles 절삭)과 정확히 동일
# REPORT4_INPUT_EXPANSION 이 off 면 RAG 도 무의미하므로 자동 off.
# RAG 실패(임베딩 API·벡터DB)는 절삭으로 안전 퇴화 — ④ 생성은 계속.
REPORT4_RAG = os.getenv(
    "REPORT4_RAG", "1"
).strip().lower() not in ("0", "false", "no", "off")
# 임베딩 모델 (기존 OpenAI 호환 RunYourAI 게이트웨이 재활용). RunYourAI
# 게이트웨이는 RUNYOURAI_MODEL(openai/gpt-4.1-...) 처럼 provider/model
# 형식을 요구한다(실측: 'text-embedding-3-small' → 400 'model should be in
# provider/model format'). 변경 시 재인덱싱 필요.
REPORT4_RAG_EMBED_MODEL = os.getenv(
    "REPORT4_RAG_EMBED_MODEL", "openai/text-embedding-3-small"
)
# RAG 전용 SQLite 벡터 저장소 경로 (기존 14테이블·PostgreSQL 과 완전 분리).
REPORT4_RAG_DB_PATH = os.getenv(
    "REPORT4_RAG_DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), ".rag", "rag_vectors.sqlite3"),
)
# 검색 쿼리당 상위 청크 수.
REPORT4_RAG_TOP_K = int(os.getenv("REPORT4_RAG_TOP_K", "8"))

# ── Phase 3: 제품 이미지 검색·검증·저장 ──
# off 면 이미지 수집을 통째로 건너뜀(긴급 대응·비용 통제). 키 부재여도
# 안전 퇴화(이미지 없이 진행) — 호출부가 죽지 않는다.
PRODUCT_IMAGE_ENABLED = os.getenv(
    "PRODUCT_IMAGE_ENABLED", "1"
).strip().lower() not in ("0", "false", "no", "off")
# Serper Google Images 엔드포인트 (공식 문서 기준).
SERPER_IMAGES_ENDPOINT = os.getenv(
    "SERPER_IMAGES_ENDPOINT", "https://google.serper.dev/images"
)
# Serper Google Web Search 엔드포인트 (검색 후보 자동 제안에서 사용).
# 키 없으면 빈 결과로 안전 퇴화 — suggest 는 보조 기능이므로 5xx 금지.
SERPER_SEARCH_ENDPOINT = os.getenv(
    "SERPER_SEARCH_ENDPOINT", "https://google.serper.dev/search"
)
# 검색 후보 수(1등이 늘 정확하진 않음 → 여러 개 받아 검증).
PRODUCT_IMAGE_SEARCH_NUM = int(os.getenv("PRODUCT_IMAGE_SEARCH_NUM", "10"))
# (보강 B) 검색 순위 기반 컷 제거 — 명백한 노이즈가 아닌 후보는 검색
# 순위와 무관하게 전부 비전으로 넘긴다. 비전 비용은 검색 단계에서 받는
# 수(PRODUCT_IMAGE_SEARCH_NUM)로 통제. (구 PRODUCT_IMAGE_VISION_MAX 폐지)
# (보강 A) 후보 이미지를 서버가 직접 다운로드해 base64 로 비전에 전달 —
# 제공자측 다운로드 실패를 원천 차단, 후보 1개 실패가 전체를 막지 않음.
PRODUCT_IMAGE_DL_TIMEOUT = float(os.getenv("PRODUCT_IMAGE_DL_TIMEOUT", "12"))
# 다운로드 이미지 1장 용량 상한(base64 페이로드 폭주 방지). 초과 시 그
# 후보만 탈락. 기본 8MB — 일반 제품컷 충분.
PRODUCT_IMAGE_MAX_BYTES = int(os.getenv("PRODUCT_IMAGE_MAX_BYTES", str(8 * 1024 * 1024)))
# 명백히 작은 이미지(썸네일/아이콘) 배제 최소 변(px). 너무 높게 잡지 않는다.
PRODUCT_IMAGE_MIN_PX = int(os.getenv("PRODUCT_IMAGE_MIN_PX", "300"))
# 비전 검증 모델 — RunYourAI 게이트웨이는 provider/model 형식 요구
# (Phase 2-b 에서 겪은 이슈). 기본은 비전 가능한 RUNYOURAI_MODEL.
PRODUCT_IMAGE_VISION_MODEL = os.getenv(
    "PRODUCT_IMAGE_VISION_MODEL", RUNYOURAI_MODEL
)
# 검색 쿼리에 덧붙여 단독 제품 사진을 유도하는 표현(상수 — 근거: 리뷰
# 썸네일·밈·비교짤보다 공식/스토어 제품컷이 잘 잡히도록).
PRODUCT_IMAGE_QUERY_SUFFIX = os.getenv(
    "PRODUCT_IMAGE_QUERY_SUFFIX", "공식 제품 사진"
)
