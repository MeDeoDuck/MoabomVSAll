"""유사 제품 비교 모듈 (별도 PR — feature/similar-products).

기능 요약
  - GET /products/{id}/similar 응답 빌더 (내부/외부 모드 자동 분기)
  - 내부 모드: tech_products 카테고리 + product_meta_cache.release_year ±2년
    매칭으로 후보 → 연도 가까운 순 + 브랜드 다양성 가중치로 상위 3개.
  - 외부 모드: 내부 0개 시 Serper 웹 검색으로 동일 카테고리 동시기 후보 추출.
  - 외부 카드 이미지: Serper Images 병렬 fetch (10초 timeout, 실패는 placeholder).
  - 사유 텍스트: LLM 1~2줄 명사구 생성. 메모리 LRU 캐시(500쌍).

★ DB 스키마 변경 0. tech_products·product_meta_cache 의 기존 컬럼만 사용.
★ 두 Agent(video_selection_agent / comment_filtering_agent) 무변경.
★ 보고서 파이프라인 무변경. 본 모듈은 *소비 측* — 보고서 텍스트만 읽음.

이 파일의 LLM 호출 범위는 "유사 제품 비교 카드의 사유 텍스트" 한 가지로
한정. 모달 안의 점수·등급·페르소나·도넛 색·이미지·가격 등은 *Phase 5 의
기존 결과* 그대로 — 손대지 않는다.
"""
from __future__ import annotations

import asyncio
import re
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

# ──────────────────────────────────────────────────────────────
# 제품명 정규화 (§Q9) — 외부 카드 dedupe·기존 DB 매칭에 공통 사용
# ──────────────────────────────────────────────────────────────

_RE_WS = re.compile(r"\s+")
_RE_STRIP_TOKENS = re.compile(r"[\s\-_·.,/()\[\]+]")


def normalize_product_name(name: str) -> str:
    """제품명 비교용 정규화: 공백·기호 제거, 소문자.

    같은 제품의 한·영 표기까지 매칭하지는 못한다(Q9 결정 — 받아들임).
    예: "갤럭시 S24" / "Galaxy S24" 는 정규화 후 서로 다름.
    """
    if not name:
        return ""
    s = str(name).strip().lower()
    s = _RE_WS.sub(" ", s)
    return _RE_STRIP_TOKENS.sub("", s)


# ──────────────────────────────────────────────────────────────
# 카테고리·연도 fallback — tech_products 에 release_year 없음
# ──────────────────────────────────────────────────────────────

# scripts/api/products.py 의 _CATEGORY_KEYWORDS 와 동일 의도(중복 회피
# 위해 함수로 import). 작업 4 의 카테고리 추론 헬퍼 재사용.
def _infer_category_from_name(name: str) -> str:
    from scripts.api.products import _infer_category_from_name as _impl
    return _impl(name)


def _resolve_release_year(product_id: int, fallback_meta: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """target 의 출시 연도. tech_products 컬럼 없음 → product_meta_cache 사용.

    fallback_meta 가 이미 캐시된 dict 면 한 번 더 조회하지 않음(호출부 효율).
    """
    if fallback_meta and fallback_meta.get("release_year"):
        try:
            return int(fallback_meta["release_year"])
        except (TypeError, ValueError):
            pass
    from scripts.popup.store import get_meta

    meta = get_meta(product_id)
    if not meta:
        return None
    try:
        y = meta.get("release_year")
        return int(y) if y is not None else None
    except (TypeError, ValueError):
        return None


def _resolve_category(product: Dict[str, Any]) -> str:
    """카테고리 우선순위: tech_products.category → 제품명 키워드 추론."""
    cat = (product.get("category") or "").strip()
    if cat:
        return cat
    return _infer_category_from_name(product.get("name") or "")


# ──────────────────────────────────────────────────────────────
# 내부 모드 — DB 카테고리·연도 매칭
# ──────────────────────────────────────────────────────────────


def find_similar_internal(target_id: int) -> List[Dict[str, Any]]:
    """카테고리 일치 + 연도 ±2년 + 자기 자신 제외.

    반환: tech_products 행 dict 리스트 (release_year 키 동봉, 빈 슬롯 보강 전).
    매칭 0건이면 빈 리스트 — 호출부가 외부 모드로 분기.
    """
    from scripts.database.queries import query_one, query_all

    target = query_one(
        "SELECT product_id, name, brand, category, image_url "
        "FROM tech_products WHERE product_id = %s",
        (target_id,),
    )
    if not target:
        return []

    target_cat = _resolve_category(target)
    target_year = _resolve_release_year(target_id)
    if not target_cat or not target_year:
        # 카테고리·연도 둘 다 있어야 의미 있는 매칭 가능 → 외부 모드로
        return []

    # tech_products.category 는 NULL 케이스 다수 — 정규화 비교(LOWER+TRIM).
    # product_meta_cache 와 LEFT JOIN 으로 release_year 가져옴.
    # 1차: 카테고리 직접 일치 (대소문자·공백 정규화)
    # ★ 축소판 명세 §③: 매칭 조건은 카테고리 + 연도 ±2 *둘 뿐*. 직전 EXISTS
    #   조건(보고서 보유 필터)은 *완전 제거* — 보고서 보유 여부는 카드 클릭
    #   시점에 has-integrated-report 로 분기 처리 (분기 ①/②).
    rows = query_all(
        """
        SELECT tp.product_id, tp.name, tp.brand, tp.category, tp.image_url,
               pmc.release_year
          FROM tech_products tp
          JOIN product_meta_cache pmc ON pmc.product_id = tp.product_id
         WHERE tp.product_id <> %s
           AND pmc.release_year IS NOT NULL
           AND pmc.release_year BETWEEN %s AND %s
           AND LOWER(TRIM(COALESCE(tp.category,''))) = LOWER(TRIM(%s))
        """,
        (target_id, target_year - 2, target_year + 2, target_cat),
    ) or []

    # 2차 (1차에 후보 부족): 제품명 기반 카테고리 추론까지 포함해 보강
    # EXISTS 조건 미적용 — 1단계와 정책 일치.
    if len(rows) < 3:
        extra = query_all(
            """
            SELECT tp.product_id, tp.name, tp.brand, tp.category, tp.image_url,
                   pmc.release_year
              FROM tech_products tp
              JOIN product_meta_cache pmc ON pmc.product_id = tp.product_id
             WHERE tp.product_id <> %s
               AND pmc.release_year IS NOT NULL
               AND pmc.release_year BETWEEN %s AND %s
            """,
            (target_id, target_year - 2, target_year + 2),
        ) or []
        seen_ids = {r["product_id"] for r in rows}
        for r in extra:
            if r["product_id"] in seen_ids:
                continue
            inferred = _resolve_category(r)
            if inferred and inferred == target_cat:
                rows.append(r)
                seen_ids.add(r["product_id"])

    # 같은 정규화 이름은 한 번만 (운영 DB 의 중복 등록 케이스 정리). target
    # 자신의 정규화 이름도 제외 — 다른 product_id 로 박힌 동일 이름까지 컷.
    target_name_norm = normalize_product_name(target.get("name") or "")
    seen_names: set = {target_name_norm} if target_name_norm else set()
    deduped = []
    for r in rows:
        n = normalize_product_name(r.get("name") or "")
        if n and n in seen_names:
            continue
        seen_names.add(n)
        deduped.append(r)
    rows = deduped

    # 연도 가까운 순 정렬
    rows.sort(key=lambda r: abs((r.get("release_year") or 0) - target_year))

    # 브랜드 다양성 정렬 (1차 패스: 새 브랜드 우선, 2차 패스: 잔여 채움)
    selected: List[Dict[str, Any]] = []
    seen_brands: set = set()
    for r in rows:
        if len(selected) >= 3:
            break
        b = (r.get("brand") or "").strip().lower()
        if b and b not in seen_brands:
            selected.append(r)
            seen_brands.add(b)
    for r in rows:
        if len(selected) >= 3:
            break
        if r not in selected:
            selected.append(r)

    return selected[:3]


# ──────────────────────────────────────────────────────────────
# 외부 모드 — Serper 웹 검색 + 제품명 휴리스틱 추출
# ──────────────────────────────────────────────────────────────

# 브랜드 토큰 사전 — Serper organic 의 title/snippet 에서 제품명 추출 시
# "브랜드 + 모델" 패턴 매칭에 사용. 너무 일반적인 단어("폰")는 제외.
_BRAND_TOKENS: Tuple[str, ...] = (
    "iphone", "아이폰",
    "galaxy", "갤럭시",
    "pixel", "픽셀",
    "xiaomi", "샤오미", "redmi", "mi",
    "airpods", "에어팟", "buds", "버즈",
    "macbook", "맥북", "ipad", "아이패드",
    "그램", "갤럭시북",
    "watch", "워치", "garmin",
    "헤드폰", "맥스",
)

# 후보 명 말미에 자주 붙는 노이즈 (제거 대상)
_RE_STRIP_TAIL = re.compile(
    r"\s*(?:리뷰|사용기|비교|언박싱|개봉|첫인상|출시|발매|체험|스펙|특징|"
    r"가격|후기|논란|뉴스|소식|영상|광고|구매|판매)\s*$"
)
# 모델명 안에서 끊어야 할 결합어(stopwords). "갤럭시 S24 vs 아이폰" 같은
# 결과의 "vs 아이폰" 절단용. 앞 부분만 유효 모델명으로 채택.
_RE_STOPWORD_CUT = re.compile(
    r"\s*\b(?:vs|VS|와|과|그리고|또는|혹은|및)\b.*$"
)

# 브랜드 + 1~3 토큰 (영문/숫자/한글) 패턴
_PRODUCT_TOKEN = r"[A-Za-z0-9가-힣]{1,5}"


def _extract_external_candidates(
    items: List[Dict[str, Any]],
    exclude_normalized: set,
    *,
    take: int = 3,
) -> List[Dict[str, str]]:
    """Serper organic 결과 → 제품명 후보 리스트 [{name, brand}].

    휴리스틱: 각 결과의 title + snippet 에서 브랜드 토큰을 찾고, 그 뒤
    1~3개의 영숫자/한글 토큰을 잡아 "브랜드 모델" 로 결합. 노이즈 단어 제거
    후 정규화 dedupe.
    """
    out: List[Dict[str, str]] = []
    seen = set(exclude_normalized)

    for it in items:
        text = f"{it.get('title','')} {it.get('snippet','')}"
        for brand_kw in _BRAND_TOKENS:
            pattern = re.compile(
                r"(" + re.escape(brand_kw) + r")\s*("
                + _PRODUCT_TOKEN + r"(?:\s*" + _PRODUCT_TOKEN + r"){0,2})",
                re.IGNORECASE,
            )
            for m in pattern.finditer(text):
                brand_text = m.group(1)
                tail = (m.group(2) or "").strip()
                if not tail:
                    continue
                cand = f"{brand_text} {tail}".strip()
                # stopword 절단 ("갤럭시 S24 vs 아이폰" → "갤럭시 S24")
                cand = _RE_STOPWORD_CUT.sub("", cand).strip()
                cand = _RE_STRIP_TAIL.sub("", cand).strip()
                if not cand or len(cand) > 22:
                    continue
                norm = normalize_product_name(cand)
                if norm in seen:
                    continue
                # 브랜드만 있고 모델이 없는 경우 ("아이폰" 만) 거름
                if normalize_product_name(brand_text) == norm:
                    continue
                seen.add(norm)
                out.append({"name": cand, "brand": brand_text})
                if len(out) >= take:
                    return out
        if len(out) >= take:
            break

    return out


def find_similar_external(
    target_category: str,
    target_year: Optional[int],
    target_name: str,
    *,
    exclude_names: Optional[set] = None,
    take: int = 3,
) -> List[Dict[str, str]]:
    """Serper 웹 검색 → 동일 카테고리(+가능하면 동시기) 후보 명사구.

    ★ 축소판 명세 §⑤ 매칭 0개 차단: target_year 가 None 이어도 카테고리만
      으로 검색 수행. 결과 품질은 약간 떨어질 수 있지만 매칭 0개보다 낫다.
    target 자신과 호출부가 지정한 exclude_names 들은 결과에서 제외.
    실패·키 부재 시 빈 리스트.
    """
    from scripts.popup.product_meta import serper_web_search

    if target_year:
        queries = [
            f"{target_category} {target_year} 추천",
            f"{target_category} {target_year} 비교",
        ]
    else:
        queries = [
            f"{target_category} 추천 인기",
            f"{target_category} 비교",
        ]

    exclude: set = {normalize_product_name(target_name)}
    if exclude_names:
        exclude |= {normalize_product_name(n) for n in exclude_names if n}

    aggregated: List[Dict[str, Any]] = []
    for q in queries:
        try:
            items, _perf = serper_web_search(q, num=10)
        except Exception:
            items = []
        aggregated.extend(items)
        if len(aggregated) >= 8:
            break

    return _extract_external_candidates(aggregated, exclude, take=take)


def _lookup_existing_product_id(name: str, category: str) -> Optional[int]:
    """외부 검색 결과 이름·카테고리가 tech_products 에 이미 있으면 그 id.

    카드 클릭 시 has-integrated-report 분기에 사용. 정규화 비교 — 한·영 변형
    까지는 못 잡음(Q9 결정 그대로). 카테고리는 비어있거나 같으면 일치 인정.
    """
    if not (name and name.strip()):
        return None
    from scripts.database.queries import query_all

    rows = query_all(
        "SELECT product_id, name, category FROM tech_products"
    ) or []
    target_norm = normalize_product_name(name)
    target_cat = (category or "").strip().lower()
    for r in rows:
        rn = normalize_product_name(r.get("name") or "")
        if rn != target_norm:
            continue
        rcat = (r.get("category") or "").strip().lower()
        if not rcat or not target_cat or rcat == target_cat:
            return int(r["product_id"])
    return None


# ──────────────────────────────────────────────────────────────
# 외부 카드 이미지 — Serper Images 병렬 fetch (10초 timeout)
# ──────────────────────────────────────────────────────────────

# 회색 placeholder (이미지 못 가져왔을 때) — data URI SVG. base64 인코딩
# 불필요 — utf8 SVG 그대로.
_PLACEHOLDER_IMAGE = (
    "data:image/svg+xml;utf8,"
    "<svg xmlns='http://www.w3.org/2000/svg' width='120' height='120' "
    "viewBox='0 0 120 120'>"
    "<rect width='120' height='120' fill='%23e5e7eb' rx='12'/>"
    "<text x='50%' y='50%' text-anchor='middle' dominant-baseline='central' "
    "fill='%239ca3af' font-family='sans-serif' font-size='12'>이미지 없음</text>"
    "</svg>"
)


def fetch_external_image_sync(name: str, brand: str = "", timeout_sec: float = 10.0) -> str:
    """단일 제품명의 첫 Serper Images 결과 URL. 실패 시 placeholder."""
    from scripts.product_image.search import build_query, serper_image_search

    try:
        q = build_query(name, brand)
        candidates, _ = serper_image_search(q, num=3)
        for c in candidates:
            url = (c.get("image_url") or "").strip()
            if url:
                return url
    except Exception:
        pass
    return _PLACEHOLDER_IMAGE


async def fetch_external_images(
    candidates: List[Dict[str, str]],
    *,
    timeout_sec: float = 10.0,
) -> List[str]:
    """후보 N개의 이미지 URL을 *병렬* 로 fetch (asyncio.gather + to_thread).

    각 호출에 개별 timeout 적용. 실패는 placeholder.
    """
    async def _one(c):
        coro = asyncio.to_thread(
            fetch_external_image_sync, c["name"], c.get("brand", "")
        )
        try:
            return await asyncio.wait_for(coro, timeout=timeout_sec)
        except (asyncio.TimeoutError, Exception):
            return _PLACEHOLDER_IMAGE

    return await asyncio.gather(*[_one(c) for c in candidates])


# ──────────────────────────────────────────────────────────────
# LLM 사유 — 메모리 LRU 캐시 (500쌍)
# ──────────────────────────────────────────────────────────────

_REASON_CACHE: "OrderedDict[Tuple[int, str], str]" = OrderedDict()
_REASON_CACHE_MAX = 500


def reason_cache_get(key: Tuple[int, str]) -> Optional[str]:
    if key in _REASON_CACHE:
        _REASON_CACHE.move_to_end(key)
        return _REASON_CACHE[key]
    return None


def reason_cache_put(key: Tuple[int, str], value: str) -> None:
    if not value:
        return
    _REASON_CACHE[key] = value
    _REASON_CACHE.move_to_end(key)
    if len(_REASON_CACHE) > _REASON_CACHE_MAX:
        _REASON_CACHE.popitem(last=False)


def _reason_cache_clear_for_test() -> None:
    """테스트 헬퍼. 운영 코드에서는 호출 안 함."""
    _REASON_CACHE.clear()


# ──────────────────────────────────────────────────────────────
# 보고서 텍스트 → 핵심 요약 추출 (LLM 입력용, 결정론적)
# ──────────────────────────────────────────────────────────────


def _extract_report_essentials(report_text: Optional[str]) -> Dict[str, Any]:
    """④ 보고서에서 LLM 사유 입력에 쓸 핵심 정보 추출.

    score, 강점 top3, 약점 top3 만. extract_popup_data 재사용 — 보고서
    파이프라인은 *건드리지 않음*.
    """
    if not report_text:
        return {"score": None, "pros": [], "cons": []}
    try:
        from scripts.popup.extractor import extract_popup_data

        d = extract_popup_data(report_text)
        return {
            "score": d.get("verdict", {}).get("score"),
            "pros": [p.get("label") for p in (d.get("pros") or []) if p.get("label")],
            "cons": [c.get("label") for c in (d.get("cons") or []) if c.get("label")],
        }
    except Exception:
        return {"score": None, "pros": [], "cons": []}


# ──────────────────────────────────────────────────────────────
# LLM 사유 텍스트 빌더
# ──────────────────────────────────────────────────────────────

_REASON_MAX_TOKENS = 80
_REASON_TEMPERATURE = 0.4


def _call_llm_reason(prompt: str) -> Optional[str]:
    """RunYourAI 호출. 실패는 None (호출부에서 fallback)."""
    try:
        from scripts.reports.transcript_report import (
            REPORT_LLM_DEPLOYMENT,
            get_report_llm_client,
        )

        client = get_report_llm_client()
        resp = client.chat.completions.create(
            model=REPORT_LLM_DEPLOYMENT,
            messages=[{"role": "user", "content": prompt}],
            temperature=_REASON_TEMPERATURE,
            max_tokens=_REASON_MAX_TOKENS,
        )
        if not resp.choices:
            return None
        text = (resp.choices[0].message.content or "").strip()
        # 첫 줄·따옴표 제거 정도만 — 본문 정규화는 §4 모호 가이드(LLM 출력
        # 자연어 깨뜨리지 않음). prefix·따옴표 등 *명백한* 군더더기만.
        text = text.strip("\"'`")
        text = text.split("\n", 1)[0].strip()
        return text or None
    except Exception:
        return None


def _fallback_reason_internal(similar: Dict[str, Any]) -> str:
    brand = (similar.get("brand") or "동급").strip() or "동급"
    year = similar.get("release_year")
    return f"{brand} {year}년 — 동일 카테고리 비교 후보" if year else f"{brand} — 동일 카테고리 비교 후보"


def _fallback_reason_external(target_category: str, target_year: int) -> str:
    return f"동일 {target_category} · {target_year}년 동시기 비교 후보"


def build_reason_internal(
    target: Dict[str, Any],
    target_year: int,
    target_report: Dict[str, Any],
    similar: Dict[str, Any],
    similar_report: Dict[str, Any],
) -> str:
    """내부 모드 사유 — 양쪽 ④ 보고서 핵심 요약 입력. 캐시 사용."""
    sid = similar.get("product_id")
    tid = target.get("product_id")
    if tid is None or sid is None:
        return _fallback_reason_internal(similar)
    key = (int(tid), f"internal:{sid}")
    cached = reason_cache_get(key)
    if cached:
        return cached

    prompt = (
        "당신은 제품 비교 추천 카피라이터다. 두 제품의 종합 분석 결과를 기반으로,"
        " '왜 비교해볼 가치가 있는지' 1~2줄 명사구로 생성하라.\n\n"
        f"[현재 제품: {target.get('name','')}]\n"
        f"카테고리: {_resolve_category(target)}\n"
        f"출시: {target_year}년\n"
        f"종합 점수: {target_report.get('score') or '데이터 부족'}/10\n"
        f"강점: {', '.join(target_report.get('pros') or []) or '데이터 부족'}\n"
        f"약점: {', '.join(target_report.get('cons') or []) or '데이터 부족'}\n\n"
        f"[비교 후보: {similar.get('name','')}]\n"
        f"카테고리: {_resolve_category(similar)}\n"
        f"출시: {similar.get('release_year') or '데이터 부족'}년\n"
        f"종합 점수: {similar_report.get('score') or '데이터 부족'}/10\n"
        f"강점: {', '.join(similar_report.get('pros') or []) or '데이터 부족'}\n"
        f"약점: {', '.join(similar_report.get('cons') or []) or '데이터 부족'}\n\n"
        "제약:\n"
        "- 1~2 줄, 명사로 끝나는 개조식 구문\n"
        "- 30자 내외, 한국어\n"
        "- 두 제품의 차이·공통점 중 비교 가치가 명확한 한 가지 포인트\n"
        "- 단순 '비슷한 제품' 같은 일반론 금지\n"
        "- 따옴표·prefix 없이 순수 텍스트만\n\n"
        "출력:"
    )
    out = _call_llm_reason(prompt)
    reason = out or _fallback_reason_internal(similar)
    reason_cache_put(key, reason)
    return reason


def build_reason_external(
    target: Dict[str, Any],
    target_year: int,
    target_report: Dict[str, Any],
    candidate: Dict[str, Any],
) -> str:
    """외부 모드 사유 — target 보고서 + 후보 메타만으로 생성. 캐시 사용."""
    tid = target.get("product_id")
    cname = candidate.get("name") or ""
    if tid is None or not cname:
        return _fallback_reason_external(_resolve_category(target), target_year)
    norm = normalize_product_name(cname)
    key = (int(tid), f"external:{norm}")
    cached = reason_cache_get(key)
    if cached:
        return cached

    target_cat = _resolve_category(target)
    prompt = (
        "당신은 제품 비교 추천 카피라이터다. 사용자가 현재 보고 있는 제품과"
        " 비교할 만한 다른 제품을 추천하는 1~2줄 사유 명사구를 생성하라."
        " 비교 후보의 상세 분석은 없으므로 메타 정보(브랜드·연도·카테고리)와"
        " 일반적 지식만으로 작성하라.\n\n"
        f"[현재 제품: {target.get('name','')}]\n"
        f"카테고리: {target_cat}\n"
        f"출시: {target_year}년\n"
        f"종합 점수: {target_report.get('score') or '데이터 부족'}/10\n"
        f"강점: {', '.join(target_report.get('pros') or []) or '데이터 부족'}\n\n"
        f"[비교 후보: {cname}]\n"
        f"브랜드: {candidate.get('brand') or '정보 없음'}\n"
        f"출시: {candidate.get('release_year') or '정보 없음'}년\n"
        f"카테고리: {target_cat}  ← 동일\n\n"
        "제약:\n"
        "- 1~2 줄, 명사로 끝나는 개조식 구문\n"
        "- 30자 내외, 한국어\n"
        "- 동일 카테고리 + 동시기 출시 기준의 비교 가치 한 가지\n"
        "- 단정·과장 표현 금지 (검증 안 된 메타이므로)\n"
        "- 따옴표·prefix 없이 순수 텍스트만\n\n"
        "출력:"
    )
    out = _call_llm_reason(prompt)
    reason = out or _fallback_reason_external(target_cat, target_year)
    reason_cache_put(key, reason)
    return reason


# ──────────────────────────────────────────────────────────────
# 최상위 진입점 — 라우트가 호출
# ──────────────────────────────────────────────────────────────


async def build_similar_payload(target_id: int) -> Dict[str, Any]:
    """GET /products/{target_id}/similar 응답 dict 생성 (축소판: 혼합 모드).

    ★ 명세 §②: DB 우선 + 부족분 외부 검색 + *반드시 3개*. 부족분은 null.
    ★ 명세 §⑤: 매칭 0개 차단 — target_year=None 이어도 외부 검색은 카테고리
      만으로 시도. 그래도 안 잡힌 자리만 null.

    응답:
      {"cards": [card|null, card|null, card|null]}
    각 card:
      {kind: "internal"|"external", product_id: int|null, name, image_url,
       reason, brand, release_year}
    (이전의 "mode" top-level 필드는 제거 — 혼합이라 의미 없음. 각 카드 kind 로 분기.)
    """
    from scripts.database.queries import query_one
    from scripts.reports.product_integrated_insight import (
        get_latest_product_integrated_report,
    )

    target = query_one(
        "SELECT product_id, name, brand, category, image_url "
        "FROM tech_products WHERE product_id = %s",
        (target_id,),
    )
    if not target:
        return {"cards": [None, None, None]}

    target_year = _resolve_release_year(target_id)
    target_cat = _resolve_category(target)

    # 1) target 의 ④ 보고서 핵심 요약 (LLM 사유 입력용) — 1회만 추출
    target_latest = get_latest_product_integrated_report(target_id)
    target_report = _extract_report_essentials(
        (target_latest or {}).get("report_text")
    )

    # 2) DB 내부 매칭 (카테고리·연도 둘 다 있어야 시도)
    internal_rows: List[Dict[str, Any]] = []
    if target_cat and target_year:
        internal_rows = find_similar_internal(target_id)[:3]

    cards: List[Optional[Dict[str, Any]]] = []
    # 2-A) 내부 카드 — LLM 사유 + 이미지는 DB image_url 우선
    for r in internal_rows:
        sid = r["product_id"]
        slatest = get_latest_product_integrated_report(sid)
        sreport = _extract_report_essentials(
            (slatest or {}).get("report_text")
        )
        reason = build_reason_internal(
            target, target_year or 0, target_report, r, sreport,
        )
        cards.append({
            "kind": "internal",
            "product_id": sid,
            "name": r.get("name"),
            "image_url": (r.get("image_url") or "").strip() or _PLACEHOLDER_IMAGE,
            "reason": reason,
            "brand": r.get("brand"),
            "release_year": r.get("release_year"),
        })

    # 3) 부족분 외부 검색 — target_year 없어도 카테고리만으로 시도(§⑤).
    needed = 3 - len(cards)
    if needed > 0 and target_cat:
        exclude = {r.get("name") for r in internal_rows if r.get("name")}
        candidates = find_similar_external(
            target_cat, target_year, target.get("name") or "",
            exclude_names=exclude,
            take=needed,
        )
        if candidates:
            images = await fetch_external_images(candidates, timeout_sec=10.0)
            for c, img in zip(candidates, images):
                # tech_products 에 이미 있는 동일 정규화 이름이면 product_id
                # 채움 → 카드 클릭 시 has-integrated-report 분기 가능 (분기 ①).
                # 없으면 None — 클릭 시 무조건 분기 ② (안내 박스).
                ext_pid = _lookup_existing_product_id(c["name"], target_cat)
                candidate_meta = {
                    "name": c["name"],
                    "brand": c.get("brand"),
                    "release_year": target_year,   # 추정값, 사유 LLM 메타용
                }
                reason = build_reason_external(
                    target, target_year or 0, target_report, candidate_meta
                )
                cards.append({
                    "kind": "external",
                    "product_id": ext_pid,        # 매칭되면 int, 없으면 None
                    "name": c["name"],
                    "image_url": img,
                    "reason": reason,
                    "brand": c.get("brand"),
                    "release_year": target_year,
                })

    # 4) 최종 — 항상 길이 3. 부족분 null 슬롯.
    while len(cards) < 3:
        cards.append(None)
    return {"cards": cards[:3]}
