# Moabom Prototype

유튜브 테크 제품 리뷰 영상을 수집해 자막과 댓글을 분석하고, 리뷰어와 소비자 의견을 비교한 통합 보고서를 생성하는 FastAPI 서비스.

## 동작 흐름

```
제품 등록 → YouTube 영상/댓글 수집 → 자막 추출 → LLM 보고서 3종 생성
                                                ├─ 자막 기반 리뷰어 분석
                                                ├─ 댓글 기반 소비자 반응
                                                └─ 통합 분석 (의견 일치도 %)
```

## 빠른 시작

### 1. 사전 준비

- Python 3.12+
- Docker Desktop
- API 키 2개:
  - [YouTube Data API v3](https://console.cloud.google.com/apis/credentials)
  - [Groq Console](https://console.groq.com/keys)

### 2. 환경 설정

```powershell
cd Moabom_Prototype

python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

`.env` 파일에 API 키를 입력합니다 (DATABASE_URL은 docker 사용 시 기본값 그대로 두면 됩니다):

```
YOUTUBE_API_KEY=...
GROQ_API_KEY=...
```

### 3. 실행

```powershell
docker compose up -d postgres   # PostgreSQL 컨테이너 기동
python main.py                  # FastAPI 서버 (기본 :8000)
```

브라우저에서 http://localhost:8000/products 접속.

### 4. 종료

```powershell
# 서버: Ctrl+C
docker compose stop postgres   # DB 컨테이너 정지 (데이터 유지)
docker compose down -v         # 데이터까지 완전 삭제
```

## 프로젝트 구조

```
Moabom_Prototype/
├── main.py                       # FastAPI 진입점
├── scripts/                      # 운영 본체
│   ├── config.py                 #   환경변수 로딩
│   ├── api/                      #   FastAPI 라우터 (products / videos / sync)
│   ├── database/                 #   PostgreSQL 연결 / 스키마 / 쿼리 헬퍼
│   ├── youtube/                  #   YouTube API + yt-dlp 자막 추출
│   ├── analysis/                 #   감정분석 / 제품 관련도 필터
│   ├── reports/                  #   Groq Llama 보고서 생성 + PDF 출력
│   └── utils/                    #   LLM 프롬프트 템플릿
├── templates/                    # Jinja2 HTML
├── comment_filtering_agent/      # 차세대 댓글 필터 Agent (개발 중, 운영 미연결)
├── app/  services/  dags/  llm/  # 병렬 리팩터링/실험 모듈 (운영 미연결)
├── docs/                         # 설계 문서, 분석 보고서, 과제 기획서
├── docker-compose.yml            # PostgreSQL 컨테이너 정의
├── Dockerfile                    # FastAPI 앱 이미지
└── requirements.txt              # Python 의존성
```

## 주요 환경변수 (.env)

| 키 | 설명 | 기본값 |
|---|---|---|
| `DATABASE_URL` | PostgreSQL 연결 문자열 | `postgresql://postgres:postgres@localhost:5432/techdb` |
| `YOUTUBE_API_KEY` | YouTube Data API v3 키 | (필수) |
| `GROQ_API_KEY` | Groq API 키 | (필수) |
| `GROQ_MODEL` | Llama 모델명 | `llama-3.3-70b-versatile` |
| `PORT` | FastAPI 포트 | `8000` |

## 기술 스택

- **Backend**: FastAPI, uvicorn
- **DB**: PostgreSQL 15 (psycopg2)
- **외부 API**: YouTube Data API v3, Groq (Llama 3.3 70B)
- **자막**: yt-dlp + requests (json3 / vtt 직접 파싱)
- **PDF**: ReportLab + 맑은 고딕

## 데이터베이스 스키마

서버 시작 시 [scripts/database/schema.py](scripts/database/schema.py)가 자동 생성합니다.

- `tech_products` — 등록한 제품
- `videos` — 제품별 수집된 영상
- `comments` / `comment_sentiments` — 댓글 + 감정 라벨
- `video_transcripts` — 자막 캐시
- `video_reports` — LLM 생성 보고서 3종 캐시

## 트러블슈팅

| 증상 | 해결 |
|---|---|
| `connection refused` | `docker compose up -d postgres` 실행 |
| `ModuleNotFoundError` | venv 활성화 / `pip install -r requirements.txt` |
| YouTube 403 | API 키 또는 일일 할당량(10,000 units) 확인 |
| Groq `model_not_found` | `.env`의 `GROQ_MODEL`을 [최신 지원 모델](https://console.groq.com/docs/models)로 변경 |
| 포트 8000 충돌 | `python main.py 8001` |

## 정량 비교 벤치마크 (모아봄 vs 시중 AI)

운영 사이트와 **독립적인** 발표/검증용 도구. 동일한 제품 + 동일한 구매판단 질문을
세 시스템(GPT · Gemini · 모아봄)에 주고, 같은 LLM "심판"이 동일 기준으로 채점한다.

- **비교 지표**: 근거 추적률(주장 대비 출처 연결 비율) · 판정 일관성(반복 실행 시 동일 판정 유지율) · 분석 근거량(영상·자막·댓글 수)
- **공정성**: GPT/Gemini는 RunYourAI 게이트웨이로 동일 프롬프트(모델만 교체), 모아봄은 운영 파이프라인(7섹션 보고서) 직접 호출. 심판은 시스템을 모른 채 "주장마다 검증 가능한 출처가 달렸는가"만 본다.
- **비교 모델**: GPT `openai/gpt-4.1-2025-04-14` · **Gemini `gemini/gemini-3.1-pro-preview`** · 모아봄(파이프라인) · 심판 `openai/gpt-4.1-2025-04-14`

```
experiment/                  # 실측 실험 (실제 API 호출 — 토큰·쿼터 비용 발생)
├── config.py                #   유일한 설정 손댈 곳 (제품·반복횟수·모델)
├── providers.py             #   GPT/Gemini(게이트웨이) + 모아봄(파이프라인) 호출기
├── judge.py                 #   동일 기준 근거추적률 LLM 심판
└── run_experiment.py        #   오케스트레이터 (제품×시스템×REPEAT → JSON → 대시보드)
benchmark/                   # 지표 계산 + 대시보드 (오프라인)
├── data.py                  #   발표용 샘플 데이터(BENCHMARK_RUNS)
├── metrics.py               #   추적률·일관성·근거량 순수 함수
├── dashboard.py             #   의존성 0 단일 HTML 생성기
└── output/                  #   생성물 (experiment_runs.json · ai_comparison_dashboard.html)
```

### 실행

```powershell
# ① 대시보드만 — API/DB 불필요 (실험 결과 JSON 없으면 data.py 샘플로 폴백)
python benchmark/dashboard.py            # 생성 후 브라우저 자동 오픈
python benchmark/dashboard.py --no-open  # 파일만 생성

# ② 실측 실험 — RunYourAI + YouTube API + DB 필요 (토큰 비용 발생)
python experiment/run_experiment.py      # 결과 JSON 저장 후 대시보드 자동 재생성
```

모아봄 단계는 해당 제품이 DB(`tech_products`/`videos`)에 영상 ≥2개로 미리 분석돼 있어야
한다. 없으면 그 단계만 graceful 하게 건너뛴다.

### 벤치마크 전용 환경변수 (전부 기본값 있음 — 새 API 키 불필요)

GPT·Gemini·심판·모아봄 모두 기존 `RUNYOURAI_API_KEY` / `YOUTUBE_API_KEY` /
`DATABASE_URL` 을 그대로 재사용한다. 동작만 조정하려면:

| 키 | 설명 | 기본값 |
|---|---|---|
| `EXP_REPEAT` | 제품당 반복 실행 횟수(일관성 측정) | `3` |
| `EXP_GPT_MODEL` | GPT 모델 문자열 | `openai/gpt-4.1-2025-04-14` |
| `EXP_GEMINI_MODEL` | Gemini 모델 문자열 | `gemini/gemini-3.1-pro-preview` |
| `EXP_JUDGE_MODEL` | 심판 모델 문자열 | `openai/gpt-4.1-2025-04-14` |
| `EXP_MAX_CLAIMS` | 심판이 뽑는 핵심 주장 최대 수 | `10` |
| `EXP_MOABOM_MAX_VIDEOS` | 모아봄 제품당 영상 상한 | `5` |

> Gemini 3.x Pro는 추론(thinking) 모델이라 출력 전에 추론 토큰을 먼저 쓴다. 작은
> `max_tokens` 를 걸면 출력이 비므로(`choices=null`) 주의. 실험 코드는 max_tokens 를
> 걸지 않으므로 영향 없다.

## 참고 문서

- [docs/COMMENT_FILTERING_AGENT_DESIGN.md](docs/COMMENT_FILTERING_AGENT_DESIGN.md) — 차세대 댓글 필터 Agent 설계
