# 검색 후보 자동 제안 설계 문서

## 1. 시스템 개요

### 1.1 목적
사용자가 "아이폰11" 또는 "galaxy s25" 처럼 모호하게 입력하더라도 우측 패널에 **정확한 한국 정식 출시명 후보**를 자동으로 띄워, 카드 클릭만으로 정확 제품명이 확정되어 영상 선정·보고서 생성으로 흘러가도록 한다. 모호한 입력으로 인한 ① 영상 선정 품질 저하 ② 캐시 키 일관성 파괴 ③ 중복 row 누적 세 가지를 입력 단계에서 한 번에 차단한다.

### 1.2 핵심 원칙
- **5xx 절대 금지**: suggest 는 보조 기능이므로 모든 외부 호출(Serper·LLM·임베딩) 실패는 try/except 격리 → 빈 배열 또는 DB 결과만 반환. 본 검색·보고서 흐름은 계속 진행 가능.
- **점진적 확장 캐스케이드**: 빠르고 무료인 단계부터 시작해 부족할 때만 비싼 단계로. 캐시 → DB → 시맨틱 → 외부 검색 → LLM 추출 순.
- **멱등 자동 적재**: 시드 데이터와 임베딩 인덱스 모두 startup hook 에서 차이 비교 후 변경분만 적재. 운영 수동 작업 0.
- **결정론 우선, 임베딩은 보강**: 영-한 매칭은 결정론적 alias 사전이 1차 책임. 임베딩은 alias 가 못 잡는 표현·오타·축약을 위한 마지막 그물.
- **표준 캐시 키**: alias 치환 결과를 캐시 키로 사용 → "galaxy s25" / "갤럭시 s25" / "Galaxy-S25" 모두 동일 캐시 슬롯 공유로 비용 절감.

### 1.3 처리 흐름

```
사용자 입력 (e.g. "galaxy s25 ultra")
    ↓
[normalize_query]  NFKC + LOWER + 공백·하이픈 제거 + 영-한 alias 치환
    │              → "갤럭시s25ultra"
    ↓
[suggest_cache 조회]  PostgreSQL TTL 캐시 (7일)
    ├─ HIT  → 즉시 반환 (<10ms)
    └─ MISS ↓
    ↓
[db_match]  tech_products ILIKE 정규형 매칭 (LEFT JOIN has_report)
    │       정렬: has_report DESC, created_at DESC
    ↓
[충분?]  len(db_hits) > MIN_DB_HITS(=2)
    ├─ YES → dedupe → 캐시 write → 반환
    └─ NO  ↓
    ↓
[search_semantic]  sqlite3 벡터 인덱스에서 cosine top-K (multilingual)
    │              최소 점수 컷 SUGGEST_VECTOR_MIN_SCORE=0.55
    ↓
[충분?]  len(db_hits) + len(semantic) > MIN_DB_HITS
    ├─ YES → dedupe → 캐시 write → 반환
    └─ NO  ↓
    ↓
[serper_search]  Google Web Search (knowledgeGraph + organic top 5)
    ↓
[llm_extract]  GPT-4.1 — 검색 결과 텍스트에서 정확 제품명만 추출
    │            (외부 지식 사용 금지로 cutoff 이후 신제품 환각 차단)
    ↓
[dedupe (LOWER name, LOWER brand)]  → 캐시 write → 응답
```

---

## 2. 모듈별 책임

### 2.1 normalize_query (alias 치환 포함)
**파일**: `scripts/api/suggest.py:39` + `scripts/api/suggest_aliases.py`

- NFKC 정규화 → LOWER → 공백·하이픈 제거 → 영-한 alias 치환을 한 함수에서 처리.
- 결과는 DB 의 정규형 (`REPLACE(REPLACE(LOWER(name),' ',''),'-','')`) 과 직접 비교 가능.

**alias 사전 (20개 라인업, 브랜드 키워드 의도적 제외)**
```
galaxywatch → 갤럭시워치   |   iphone     → 아이폰
galaxybuds  → 갤럭시버즈   |   ipad       → 아이패드
galaxybook  → 갤럭시북     |   macbook    → 맥북
galaxytab   → 갤럭시탭     |   airpods    → 에어팟
applewatch  → 애플워치     |   pixel      → 픽셀
galaxy      → 갤럭시        |   lumix      → 루믹스
redmi       → 레드미        |   bravia     → 브라비아
zfold       → z폴드        |   zflip      → z플립
fenix       → 페닉스        |   forerunner → 포러너
instinct    → 인스팅트      |   venu       → 베누
```

**설계 결정**:
- **브랜드 키워드 제외** (samsung/apple/sony/lg/canon 등): 시드의 `name` 컬럼에는 브랜드명이 들어가지 않으므로 (`brand` 컬럼에 별도 저장) "samsung galaxy s25" 같은 입력이 와도 brand 키워드는 그대로 둠. alias 로 "삼성" 치환 시 오히려 "삼성갤럭시s25" 가 되어 ILIKE 매칭 실패.
- **길이 내림차순 순회**: `galaxywatch`(11자) 가 `galaxy`(6자) 보다 먼저 매칭되어 `galaxy watch` 입력이 `갤럭시워치` 로 묶임.
- **다중 치환 허용**: "galaxy z fold 6" → "galaxy" + "zfold" + "fold" 모두 적용. 한글로 치환된 부분은 재매칭 안 됨.

### 2.2 db_match (PostgreSQL ILIKE)
**파일**: `scripts/api/suggest.py:51`

- `tech_products` 테이블의 `name`·`brand` 컬럼을 같은 정규형(공백·하이픈 제거 + LOWER) 으로 LIKE.
- `product_integrated_reports` LEFT JOIN 으로 `has_report` 플래그 한 번에 산출.
- 정렬: 보고서 있는 제품 우선 (`has_report DESC`) → 최근 등록순.
- 반환: `[{name, brand, category, image_url, product_id, has_report, source:"db"}]`

### 2.3 search_semantic (임베딩 시맨틱 fallback)
**파일**: `scripts/api/suggest_vector.py`

- DB+alias 가 못 잡는 표현(`iphone fifteen`, `galaxy foldable`, 오타) 대비 multilingual 임베딩 cosine 검색.
- 저장소: `seeds/.vector_cache.sqlite3` (보고서 RAG 와 분리된 별도 파일).
- 스키마: `product_vectors(product_key PK, name, brand, category, embedding TEXT, dim, content_hash, updated_at)`.
- 검색 흐름:
  1. 쿼리 텍스트 1건 임베딩 호출 (~150~250ms).
  2. sqlite3 전체 row 메모리 로드 (532행 × 1536차원 ≈ 6.5MB).
  3. `scripts.rag.store._cosine` 으로 점수 산출 (~5ms).
  4. `SUGGEST_VECTOR_MIN_SCORE`(0.55) 컷 + top_k 추출.
  5. `tech_products` LEFT JOIN 1회로 `product_id`·`image_url`·`has_report` 합성.
- 반환 항목에 `source:"vector"`, `score` 동봉.

### 2.4 serper_search + llm_extract (외부 검색)
**파일**: `scripts/api/suggest.py:96`, `scripts/api/suggest.py:163`

- `serper_search`: `google.serper.dev/search` HTTP POST. `gl=kr&hl=ko` 강제. 응답에서 `knowledgeGraph` + `organic[:5].title/snippet` 만 추출 (페이로드 압축).
- `llm_extract`: RunYourAI GPT-4.1 호출. **외부 지식 사용 금지** 프롬프트로 강제 → 검색 결과 텍스트에 명시적으로 등장하는 제품명만 추출. confidence < 0.7 제외.
- **GPT-4.1 cutoff 문제 해결 원리**: LLM 은 "지식"이 아닌 "텍스트 추출" 역할만. Serper 가 실시간으로 가져온 결과에 신제품(예: 갤럭시 S26)이 있으면 그대로 추출되므로 cutoff 이후 출시 제품도 커버.

### 2.5 _dedupe + 캐시 I/O
**파일**: `scripts/api/suggest.py:295` (_dedupe), `scripts/api/suggest.py:250-280` (cache)

- `(LOWER(name).strip(), LOWER(brand).strip())` 튜플 키로 중복 제거. DB → semantic → serper 순서 유지(앞 우선).
- 캐시: `suggest_cache(query_norm PK, response_json JSONB, expires_at)`. TTL 7일. Container Apps replica 2~5 간 공유.
- 캐시 키는 normalize_query 결과 — 영-한 동일 쿼리가 1 슬롯 공유.

---

## 3. 시드 데이터 시스템

### 3.1 manual_products.json (532건 큐레이션)
**파일**: `seeds/manual_products.json`

위키피디아 한국어 페이지 + 영문 위키 + 제조사 공식 보도자료를 5개 서브에이전트로 병렬 수집해 큐레이션. 한국 정식 출시명·브랜드·카테고리·release_year·source URL 포함.

**카테고리 분포** (2026-05 기준)
| 카테고리 | 건수 | 카테고리 | 건수 |
|---|---|---|---|
| 노트북 | 115 | 무선청소기 | 28 |
| 스마트폰 | 78 | 로봇청소기 | 27 |
| TV | 67 | 커피머신 | 8 |
| 카메라 | 62 | 밥솥 | 4 |
| 무선이어폰 | 51 | 에어프라이어 | 4 |
| 태블릿 | 42 | 헤어드라이어 | 3 |
| 스마트워치 | 41 | 정수기 | 2 |
| **합계** | **532** | | |

### 3.2 import_seed_products.py + auto_import_if_empty
**파일**: `scripts/import_seed_products.py`

- CLI 모드: `python -m scripts.import_seed_products [--dry-run]`. 카테고리·브랜드 분포 출력.
- **자동 적재 모드**: `auto_import_if_empty()` — startup hook 에서 호출. DB `seeded=true` count 와 JSON 항목 수 비교 후 차이만 INSERT (멱등).
- 멱등 4중 가드:
  1. count 비교로 이미 동등하면 즉시 skip
  2. `(LOWER(name), LOWER(brand))` 매칭으로 기존 row 보호
  3. `ON CONFLICT DO NOTHING` 으로 race 대비
  4. UNIQUE INDEX `uq_tech_products_name_brand_ci` 가 Container Apps replica 동시 부팅 시 중복 차단
- 가역: `DELETE FROM tech_products WHERE seeded=true` 한 줄.

### 3.3 build_index_if_needed (임베딩 인덱스 자동 빌드)
**파일**: `scripts/api/suggest_vector.py:67`

- startup hook 에서 호출되어 시드 JSON 변경분만 임베딩.
- `content_hash = sha256(name|brand)` 비교 → 같으면 cached, 다르면 신규 임베딩.
- 첫 부팅: 532건 임베딩 (~1초). 이후 부팅: hash 비교 1회만 (<100ms).
- 어떤 실패도 부팅 차단 안 함 (graceful degrade).

---

## 4. 데이터베이스 스키마

### 4.1 PostgreSQL (운영 DB)

```sql
-- 정규화 중복 방지 (alias·dedupe 와 한 쌍)
CREATE UNIQUE INDEX uq_tech_products_name_brand_ci
  ON tech_products (LOWER(name), COALESCE(LOWER(brand), ''));

-- 시드 추적 (가역성용)
ALTER TABLE tech_products ADD COLUMN seeded BOOLEAN DEFAULT FALSE;

-- 응답 캐시 (Container Apps replica 공유)
CREATE TABLE suggest_cache (
    query_norm    VARCHAR(255) PRIMARY KEY,
    response_json JSONB NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    expires_at    TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_suggest_cache_expires ON suggest_cache(expires_at);
```

### 4.2 SQLite (임베딩 인덱스 — 컨테이너 로컬)

```sql
-- seeds/.vector_cache.sqlite3
CREATE TABLE product_vectors (
    product_key  TEXT PRIMARY KEY,        -- f"{name.lower()}|{brand.lower()}"
    name         TEXT NOT NULL,
    brand        TEXT,
    category     TEXT,
    embedding    TEXT NOT NULL,           -- JSON list[float] (1536 dim)
    dim          INTEGER NOT NULL,
    content_hash TEXT NOT NULL,           -- sha256(name|brand)
    updated_at   TEXT
);
CREATE INDEX ix_pv_hash ON product_vectors(content_hash);
```

보고서 RAG 의 `rag_report_chunks` 와는 **별도 파일** 로 분리. 스키마·제품키 충돌 없음.

---

## 5. API 엔드포인트

### 5.1 GET /products/suggest
**파일**: `scripts/api/products.py`

- 입력: `q` (string), 선택 `limit` (기본 6, 최대 10)
- 응답:
  ```json
  {
    "items": [
      {
        "name": "갤럭시 S25 Ultra",
        "brand": "Samsung",
        "category": "스마트폰",
        "image_url": "",
        "product_id": 481,
        "has_report": false,
        "source": "db",  // 또는 "vector" / "serper"
        "score": 0.72    // source=vector 일 때만
      }
    ],
    "q": "galaxy s25 ultra"
  }
  ```
- 응답 헤더: `Cache-Control: public, max-age=300` (브라우저 측 추가 절약)
- 동기 함수 `suggest()` 를 `asyncio.to_thread` 로 래핑 (이벤트 루프 차단 회피).

### 5.2 POST /products (보강)
**파일**: `scripts/api/products.py`

- 카드 클릭 자동 submit 흐름의 종착점.
- `(LOWER(name), LOWER(brand))` 매칭으로 기존 `product_id` 재사용 (중복 INSERT 방지).
- 응답에 `existing_report` (latest `product_integrated_reports.id` 또는 null) 동봉.
- 프론트는 `existing_report` truthy 면 `/products/{id}` redirect → 캐시된 보고서 즉시 노출 (FR-020 캐시 정책과 결 일치).

---

## 6. 환경 변수

| 변수 | 기본값 | 용도 |
|---|---|---|
| `SERPER_API_KEY` | (필수) | Serper Web Search 호출. 비우면 외부 검색 단계 skip. |
| `SERPER_SEARCH_ENDPOINT` | `https://google.serper.dev/search` | Serper endpoint URL. |
| `RUNYOURAI_API_KEY` | (필수) | GPT-4.1 + text-embedding-3-small 호출 (게이트웨이 경유). |
| `RUNYOURAI_BASE_URL` | `https://api.runyour.ai/v1` | OpenAI SDK base_url 오버라이드. |
| `REPORT4_RAG_EMBED_MODEL` | `openai/text-embedding-3-small` | 임베딩 모델 (보고서 RAG 와 공유). |
| `SUGGEST_VECTOR_DB_PATH` | `seeds/.vector_cache.sqlite3` | 임베딩 인덱스 sqlite 파일. |
| `SUGGEST_VECTOR_TOP_K` | `6` | 시맨틱 검색 top-K. |
| `SUGGEST_VECTOR_MIN_SCORE` | `0.55` | cosine 점수 컷오프 (튜닝 가능). |
| `SUGGEST_SEMANTIC_ENABLED` | `1` | 시맨틱 단계 on/off (긴급 비활성용). |

---

## 7. 운영 특성

### 7.1 비용·지연
| 항목 | 값 |
|---|---|
| 시드 임베딩 빌드 (1회, 532건) | ~$0.00013 |
| 매 부팅 hash 비교 | API 호출 0, <100ms |
| 쿼리당 임베딩 (cold-path 만) | ~$0.0000001 |
| 캐시 HIT 지연 | <10ms |
| DB 적중 지연 | 35~60ms (위 검증치) |
| 시맨틱 적중 지연 | +~200ms |
| Serper+LLM 적중 지연 | +~2초 |
| 월 10K 쿼리 비용 | ~$1~5 (캐시 적중률 의존) |

### 7.2 Graceful Degrade 동작
- `SERPER_API_KEY` 부재 → Serper·LLM 단계 skip → DB+시맨틱 결과만 반환.
- `RUNYOURAI_API_KEY` 401 → 시맨틱 인덱스 빌드 실패, LLM 추출 실패 → DB+alias 결과만 반환. **부팅 차단 X**.
- sqlite 파일 손상 → 시맨틱 단계만 무력, 다른 경로 계속 동작.
- 시맨틱 검색 예외 → try/except 격리, Serper 로 폴백.
- Container Apps replica 동시 부팅 → 각자 자체 인덱스 빌드(결정론적), UNIQUE INDEX 가 중복 INSERT 차단.

### 7.3 머지·배포 시 자동 작동
1. Azure Container App 자동 재배포
2. startup hook → `init_db()` → `auto_import_if_empty()` → `build_index_if_needed()`
3. 첫 배포: 시드 INSERT + 임베딩 인덱스 빌드 (~30초 추가 startup)
4. 이후 배포: count·hash 비교만 (<200ms 추가)
5. 시드 JSON 늘려도 다음 배포에서 자동 차이분 적재

---

## 8. 회귀·검증

### 8.1 자동 회귀 (`regression/tests/test_suggest_endpoint.py`)
오프라인 mock 패턴으로 9 케이스:
1. DB 결과 충분 → Serper·시맨틱·LLM 미호출
2. DB 0 + 시맨틱 0 → Serper+LLM 호출
3. 캐시 HIT → 모든 외부 호출 미호출
4. 짧은 쿼리(<2자) → 빈 배열 즉시 반환
5. normalize_query 공백·하이픈·alias 치환 검증
6. alias 단위(`apply_aliases`) 동작 검증
7. **시맨틱 fallback** — DB 0 + 시맨틱 적중 → Serper 차단
8. **시맨틱 실패 격리** — 예외 던져도 Serper 로 폴백
9. dedupe 우선순위 (DB → 시맨틱 → Serper)

### 8.2 수동 검증 시나리오
```bash
# 영문 입력 alias 검증
curl 'http://localhost:8000/products/suggest?q=galaxy%20s25&limit=6'
curl 'http://localhost:8000/products/suggest?q=iphone%2017&limit=6'
curl 'http://localhost:8000/products/suggest?q=macbook%20pro&limit=6'

# 시맨틱 fallback (alias 사전에 없는 표현)
curl 'http://localhost:8000/products/suggest?q=foldable%20phone&limit=6'

# graceful degrade
SUGGEST_SEMANTIC_ENABLED=0 docker compose up app  # 시맨틱 비활성
```

### 8.3 운영 관찰 로그
```
[STARTUP] Seed auto-import done — inserted=106 skip_duplicate=426
[STARTUP] Vector index ready — built=106 cached=426 embed_ms=850
[SUGGEST_PERF] q='galaxy s25' cache=MISS db_hits=6 semantic_called=False ... total_ms=61
[SUGGEST_PERF] semantic q='foldable phone' hits=3 top_score=0.71 candidates=532 ms=210
```

---

## 9. 관련 파일 빠른 인덱스

```
scripts/api/
  ├─ suggest.py           # 오케스트레이션 (normalize → DB → semantic → serper → llm)
  ├─ suggest_aliases.py   # 영-한 라인업 치환 사전 + apply_aliases
  ├─ suggest_vector.py    # sqlite3 임베딩 인덱스 빌드·검색
  └─ products.py          # GET /products/suggest, POST /products dedupe

scripts/
  ├─ import_seed_products.py  # CLI + auto_import_if_empty (startup hook)
  ├─ database/schema.py       # tech_products·suggest_cache·UNIQUE INDEX 마이그레이션
  ├─ rag/embedder.py          # default_embed_fn (RunYourAI 경유 OpenAI embedding)
  ├─ rag/store.py             # _cosine 순수 파이썬 cosine 유사도
  └─ config.py                # SUGGEST_* / SERPER_* / RUNYOURAI_* 환경변수

seeds/
  ├─ manual_products.json     # 532건 큐레이션 데이터셋
  └─ popular_keywords.txt     # (v1) seed_products.py 입력 키워드

templates/
  └─ products.html            # 우측 패널 + 디바운스 fetch + 카드 클릭 자동 submit

regression/tests/
  └─ test_suggest_endpoint.py # 9 케이스 회귀 (오프라인 mock)

main.py                       # startup hook (init_db → seed import → vector build)
```

---

## 10. 향후 과제

| 우선순위 | 항목 | 비고 |
|---|---|---|
| P1 | `SUGGEST_VECTOR_MIN_SCORE` 튜닝 | 운영 첫 배포 후 `[SUGGEST_PERF] top_score` 분포 측정 → false positive·negative 균형점 찾기 |
| P1 | 운영 PG raw input row 정리 | v1 사용자가 입력한 `아이폰 17`(brand=NULL) 등 비정규 row 가 시드와 충돌하지 않지만 검색에 중복 노출. 정규화 마이그레이션 |
| P2 | 시맨틱 검색 결과 카드에 score 노출 | 현재 응답에는 포함되지만 UI 에는 미표시. 디버깅 시 score 보이면 튜닝 편함 |
| P2 | alias 사전 확장 | 신규 브랜드·라인업 출시 시 PR 로 추가. 한 분기 1회 정도 |
| P3 | BM25 / pg_trgm 추가 (full hybrid) | 한국어 형태소 분석기 도입 부담 + 현재 alias+임베딩 으로 충분히 잡힌다고 판단 시 후속 검토 |
| P3 | OpenAI 직접 API 키 옵션 | RunYourAI 게이트웨이 의존성 줄이고 싶을 때 `default_embed_fn` 만 교체 (인터페이스 그대로) |
