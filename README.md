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

운영 사이트와 **독립적인** 발표/검증용 도구. 동일한 제품 + 동일한 "이 제품 살지 말지"
질문을 세 시스템(GPT · Gemini · 모아봄)에 주고, **같은 제품을 여러 번 반복**해
**판정 일관성**을 측정한다.

- **지표 (v3)**: **판정 일관성(%)** = 같은 제품을 N회 반복했을 때 **다수결 판정의 비율**
  (높을수록 결과가 안정적). 판정은 `추천 / 조건부 추천 / 비추천 / 데이터 부족` 4값.
  - 모아봄: ④ 보고서의 종합 점수(0~10)를 임계값(7.5 / 4.0 / 1.0)으로 4값에 매핑.
  - GPT/Gemini: 출력의 `[최종판정: …]` 줄을 파싱.
  - (이전 v2의 근거 추적률·분석 근거량·실행시간·심판 LLM 지표는 제거 — 일관성 단일 지표로 단순화.)
- **공정성**: GPT/Gemini는 RunYourAI 게이트웨이로 동일 프롬프트(모델만 교체), 모아봄은 운영 파이프라인(7섹션 보고서)을 직접 호출. 셋 다 같은 제품·질문을 동일 횟수 반복.
- **비교 모델**: GPT `openai/gpt-4.1-2025-04-14` · **Gemini `gemini/gemini-3.1-pro-preview`** · 모아봄(파이프라인 = RunYourAI GPT-4.1)

```
experiment/                  # 실측 실험 (실제 API 호출 — 토큰·쿼터 비용 발생)
├── config.py                #   유일한 설정 손댈 곳 (제품·반복횟수·모델)
├── prepare_data.py          #   ① 수집: YouTube 영상 선정·자막·댓글 → DB 저장 (1회, 멱등)
├── providers.py             #   GPT/Gemini(게이트웨이) + 모아봄(파이프라인) 호출기
├── run_experiment.py        #   ② 비교: 저장된 데이터로 제품×시스템×REPEAT → JSON → 대시보드
└── RESULTS.md               #   실측 결과 정리 (300런 기준)
benchmark/                   # 지표 계산 + 대시보드 (오프라인)
├── data.py                  #   발표용 샘플 데이터(BENCHMARK_RUNS)
├── metrics.py               #   판정 일관성 순수 함수
├── dashboard.py             #   의존성 0 단일 HTML 생성기
└── output/                  #   생성물 (experiment_runs.json · ai_comparison_dashboard.html)
```

### 데이터 흐름 (수집 ↔ 비교 분리)

무거운 수집(영상 선정·자막·댓글)은 `prepare_data.py`로 **한 번만** 돌려 DB에 저장하고,
`run_experiment.py`는 그 **고정된 데이터**로 N회 반복 비교만 한다. → 영상 선정/수집 변동이
일관성 측정에 섞이지 않고, 캐시 덕에 반복 실행이 빠르다.

### 실행

```powershell
$env:PYTHONIOENCODING = "utf-8"
$env:EXP_REPEAT = "10"

python experiment/prepare_data.py     # ① 수집 → DB 저장 (1회)
python experiment/run_experiment.py   # ② 비교 → benchmark/output/experiment_runs.json + 대시보드

# 대시보드만 다시 보기 (실험 결과 JSON 없으면 data.py 샘플로 폴백)
python benchmark/dashboard.py            # 생성 후 브라우저 자동 오픈
python benchmark/dashboard.py --no-open  # 파일만 생성
```

모아봄 단계는 해당 제품이 DB(`tech_products`/`videos`)에 영상 ≥2개로 있어야 한다(없으면
`prepare_data.py`가 영상 선정 Agent로 수집). 자막/댓글이 없으면 self-heal로 채운다.

### 결과 (300런 실측 — 상세는 [experiment/RESULTS.md](experiment/RESULTS.md))

| 시스템 | 평균 판정 일관성 |
|---|---:|
| **모아봄** | **98.0 %** |
| GPT | 90.0 % |
| Gemini | 86.0 % |

→ 모아봄은 같은 제품을 반복해도 가장 일관된 판정을 유지한다. 시중 AI는 반복 시 더
흔들리고, 모르는(학습 cutoff 이후) 제품엔 "데이터 부족"을 자주 반환한다.

### 자막 수집 (429 대응)

자막은 비공식 `api/timedtext` 엔드포인트라 IP 단위 레이트리밋(429)에 걸린다. 그래서:
- **워커(주거용 IP) 우선 → 로컬 폴백**, 양쪽 다 **쿠키(로그인 세션)** 사용
  (`YT_COOKIES_PATH` 또는 `.secrets/yt_cookies.txt`).
- **원본 언어 우선**(`ko→en→ja`), 자동번역(`tlang=`) URL은 제외 — 모아봄 보고서는 자막
  언어를 가리지 않고(GPT-4.1) 한국어로 생성하므로 원본을 받아 처리한다.

### 벤치마크 전용 환경변수 (전부 기본값 있음 — 새 API 키 불필요)

GPT·Gemini·모아봄 모두 기존 `RUNYOURAI_API_KEY` / `YOUTUBE_API_KEY` / `DATABASE_URL` 을
재사용한다. 동작만 조정하려면:

| 키 | 설명 | 기본값 |
|---|---|---|
| `EXP_REPEAT` | 제품당 반복 횟수(일관성 측정) | `10` |
| `EXP_GPT_MODEL` | GPT 모델 문자열 | `openai/gpt-4.1-2025-04-14` |
| `EXP_GEMINI_MODEL` | Gemini 모델 문자열 | `gemini/gemini-3.1-pro-preview` |
| `EXP_MOABOM_MAX_VIDEOS` | 모아봄 제품당 영상 상한 | `5` |
| `TRANSCRIPT_LANGS` | 자막 우선 언어(쉼표) | `ko,en` (experiment 는 `ko,en,ja`) |
| `YT_COOKIES_PATH` | 쿠키 파일 경로 | `.secrets/yt_cookies.txt` |

> Gemini 3.x Pro는 추론(thinking) 모델이라 출력 전에 추론 토큰을 먼저 쓴다. 작은
> `max_tokens` 를 걸면 출력이 비므로(`choices=null`) 주의. 실험 코드는 max_tokens 를
> 걸지 않으므로 영향 없다.

## 참고 문서

- [docs/COMMENT_FILTERING_AGENT_DESIGN.md](docs/COMMENT_FILTERING_AGENT_DESIGN.md) — 차세대 댓글 필터 Agent 설계
