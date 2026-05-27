"""가격·스펙 외부 수집 + 캐시 (Phase 5 §5 + 보강 6).

전략(§5-B 옵션 (iii) 휴리스틱):
  Serper Web 검색 → 상위 결과의 title/snippet 텍스트에서 정규식으로
  가격·화면크기·출시연도를 추출. 무거운 스크래핑 의존성 없음(requests
  만, Phase 3 와 같은 의존성). 실패해도 §7 fallback 으로 팝업 계속 동작.

[보강 6] 가격을 **공식 제조사 출시가**로 좁힘:
  - 브랜드→공식 도메인 매핑(가벼움, Phase 3 출처등급 철학) → Serper 쿼리
    에 `site:<domain>` 한정자 추가. 매핑 없는 브랜드는 기존 일반 검색.
  - 캐시 정책 버전 태그(_POLICY_TAG)를 source 앞에 부착(예: 'v2|<url>').
    구 정책 캐시(태그 없음/다른 버전) 는 collect_and_cache_meta 에서
    무시·재수집(스키마 무변경 — 기존 source 컬럼만 사용).
순수 단계 분리:
  parse_price/parse_screen/parse_release_year — 순수, 텍스트 → 값
  serper_web_search — 외부 호출(예외 격리)
  fetch_meta — search_fn 주입 가능(테스트 오프라인)
  collect_and_cache_meta — 오케스트레이터(절대 raise 안 함)
"""
from __future__ import annotations

import re
from time import perf_counter
from typing import Any, Callable, Dict, List, Optional, Tuple

# ── 순수 파서 ────────────────────────────────────────────────────

# "129만 원", "1,290,000원", "1290000원" 등을 정수(원)로 환산해 최저값 선택.
# 작업 3 재시도 — 가격 정규식 강화:
# "299,000원", "1,290,000원", "129만 원", "1만원", "190 만원" 등 다양한
# 한국어 가격 표기를 폭넓게 매칭. 천 단위 콤마는 선택.
_RE_PRICE_MAN = re.compile(r"(\d{1,4}(?:[,．]\d{3})?)\s*만\s*원?")  # '만'만 있어도 (예: "129만")
_RE_PRICE_WON = re.compile(r"(\d{1,3}(?:,\d{3})+|\d{5,9})\s*원")    # 5자리(만원)부터
_RE_SCREEN = re.compile(r"(\d{1,2}\.\d{1,2})\s*인치")
# Phase 5 마지막 보강 1: 출시연도 추출 정규식 강화.
#   tech_products 테이블에 release_year 컬럼이 없어 항상 Serper 텍스트에서
#   파싱해야 한다. 실측 (iPhone17 spec_display="6.6인치 · 스마트폰" — 연도
#   누락) → 정규식이 "2024년 출시/발매" 같은 특정 표현을 못 잡고 일반 매칭에
#   의존하다 누락. 패턴 우선순위:
#     1) "20XX년 출시" / "20XX년 발매" / "20XX년 공개" / "20XX년 출시예정"
#        — 명시적 출시 표현 (가장 신뢰도 높음)
#     2) "20XX년" — 일반 연도 표기
#     3) (선택) 단독 4자리 fallback — 본문에 "년" 없이 "2024" 만 있는 경우
#   범위: 2010~2030 (사용자 결정 — 너무 옛날 모델 정보·미래 광고 차단)
_RE_YEAR_RELEASE = re.compile(
    r"(20[1-3]\d)\s*년\s*(?:출시|발매|공개|런칭|런처|등장|출시\s*예정)"
)
_RE_YEAR_GENERIC = re.compile(r"(20[1-3]\d)\s*년")
_RE_YEAR_STANDALONE = re.compile(r"\b(20[1-3]\d)\b")


# 보강 6 — 가벼운 브랜드→공식 도메인 매핑(소문자 키, 부분일치). 정밀
# 매핑이 아니라 "흔한 제조사 몇 개"만. 매핑 없으면 site: 한정 없이 일반 검색.
_BRAND_OFFICIAL_DOMAINS: Dict[str, str] = {
    "apple": "apple.com",
    "애플": "apple.com",
    "samsung": "samsung.com",
    "삼성": "samsung.com",
    "lg": "lge.co.kr",
    "엘지": "lge.co.kr",
    "sony": "sony.co.kr",
    "google": "store.google.com",
    "구글": "store.google.com",
    "microsoft": "microsoft.com",
    "xiaomi": "mi.com",
    "샤오미": "mi.com",
    "asus": "asus.com",
    "lenovo": "lenovo.com",
    "dell": "dell.com",
    "hp": "hp.com",
}

# 캐시 정책 버전 태그. 정책이 바뀌면 v3 등으로 올려 자연 invalidation.
# v2 → v3 (보강 9 — 정책 완화: 도메인 후필터 폐기, 일반 검색 최저가).
# 구 v2 캐시(도메인 매칭 0 으로 가격 None 이던 행)는 자연 invalidation.
_POLICY_TAG = "v3-loose"


def official_domain_for(brand: str) -> Optional[str]:
    """브랜드 문자열에서 공식 도메인 추출(소문자 부분일치). 없으면 None."""
    b = (brand or "").strip().lower()
    if not b:
        return None
    for key, dom in _BRAND_OFFICIAL_DOMAINS.items():
        if key in b:
            return dom
    return None


def parse_prices(text: str) -> List[int]:
    """텍스트에서 가능한 모든 가격(원)을 정수로 추출. 합리적 범위 필터링."""
    if not text:
        return []
    out: List[int] = []
    for m in _RE_PRICE_MAN.finditer(text):
        digits = m.group(1).replace(",", "").replace(".", "")
        try:
            out.append(int(digits) * 10000)
        except ValueError:
            continue
    for m in _RE_PRICE_WON.finditer(text):
        digits = m.group(1).replace(",", "")
        try:
            v = int(digits)
            if v >= 10000:  # 1만 원 이상만(이어폰·액세서리도 포함)
                out.append(v)
        except ValueError:
            continue
    # 작업 3 재시도 — 합리 범위 확장: 1만 ~ 1500만 (이어폰/액세서리부터
    # 고급 노트북까지 모두 포함). 광고/잡음(0원·1억원) 차단.
    return [v for v in out if 10_000 <= v <= 15_000_000]


def parse_screen(text: str) -> Optional[str]:
    """첫 번째 매칭 화면 크기를 "X.X인치" 표기로."""
    if not text:
        return None
    m = _RE_SCREEN.search(text)
    if not m:
        return None
    return f"{m.group(1)}인치"


def parse_release_year(text: str) -> Optional[int]:
    """텍스트 → 출시 연도. Phase 5 마지막 보강 1: 패턴 우선순위 적용.

    순서:
      1) "20XX년 출시/발매/공개/런칭/등장/출시예정" — 명시적 출시 표현
      2) "20XX년" — 일반 연도 표기
      3) 단독 "20XX" — fallback (본문에 '년' 없이 4자리만 있는 경우)
    범위: 2010~2030. 그 밖은 무시(옛날 모델·미래 광고 차단).
    여러 후보가 있으면 가장 먼저 매칭된 항목(가장 신뢰도 높은 표현부터).
    """
    if not text:
        return None

    def _in_range(y: int) -> bool:
        return 2010 <= y <= 2030

    # 1) 명시적 출시 표현 우선
    for m in _RE_YEAR_RELEASE.finditer(text):
        try:
            y = int(m.group(1))
        except ValueError:
            continue
        if _in_range(y):
            return y
    # 2) 일반 "20XX년" 표기
    for m in _RE_YEAR_GENERIC.finditer(text):
        try:
            y = int(m.group(1))
        except ValueError:
            continue
        if _in_range(y):
            return y
    # 3) 단독 4자리 — fallback (제품명·모델명에 연도 박힌 경우 등)
    for m in _RE_YEAR_STANDALONE.finditer(text):
        try:
            y = int(m.group(1))
        except ValueError:
            continue
        if _in_range(y):
            return y
    return None


def format_price_display(price_raw: Optional[int]) -> Optional[str]:
    """원 단위 정수 → "최저 N만 원~" 표기(§5-A 사용자 결정 10).

    소수점 반올림(만원 단위). 가격이 None 이면 None.
    """
    if not price_raw or price_raw <= 0:
        return None
    man = max(1, round(price_raw / 10000))
    return f"최저 {man}만 원~"


# ── 외부 호출 (Serper Web) ───────────────────────────────────────


def serper_web_search(query: str, *, num: int = 5) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Serper Web 검색 → 상위 organic 결과 [{title, snippet, link}].

    예외(타임아웃/HTTP 오류)는 그대로 raise — 호출부(fetch_meta)가 격리.
    키 부재는 빈 결과로 안전 처리.
    """
    import requests

    from scripts.config import SERPER_API_KEY

    perf: Dict[str, Any] = {"query": query, "num": num,
                            "received": 0, "ms": 0.0}
    if not SERPER_API_KEY:
        perf["error"] = "no_serper_key"
        return [], perf
    t0 = perf_counter()
    resp = requests.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": SERPER_API_KEY,
                 "Content-Type": "application/json"},
        json={"q": query, "num": num, "gl": "kr", "hl": "ko"},
        timeout=15,
    )
    perf["ms"] = round((perf_counter() - t0) * 1000, 1)
    perf["status_code"] = resp.status_code
    resp.raise_for_status()
    data = resp.json() or {}
    out = []
    for it in (data.get("organic") or []):
        out.append({
            "title": it.get("title") or "",
            "snippet": it.get("snippet") or "",
            "link": it.get("link") or "",
        })
    perf["received"] = len(out)
    return out, perf


# ── 조합기 ──────────────────────────────────────────────────────


def fetch_meta(
    name: str,
    brand: str = "",
    *,
    search_fn: Optional[Callable[[str], Tuple[List[Dict[str, Any]], Dict[str, Any]]]] = None,
    query_suffix: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """검색 결과 텍스트 → 가격·스펙 파싱. 반환: (fields, perf).

    fields 키: price_raw, price_display, screen_size, release_year, source.
    어느 항목이든 추출 실패 시 그 키만 None — 가능한 만큼만 채워 반환.
    search_fn / query_suffix 주입 시(테스트) Serper·config 없이 동작.
    """
    if query_suffix is None:
        from scripts.config import PRODUCT_META_QUERY_SUFFIX as _QS
        query_suffix = _QS

    base = (name or "").strip()
    b = (brand or "").strip()
    if b and b.lower() not in base.lower():
        base = f"{b} {base}"
    # 보강 6 — 옵션 (b) 후필터 채택(실측 사유: site:<dom> 쿼리 한정은
    # apple.com 안에 동일 시리즈 다른 모델 가격/사양이 섞여 노이즈가
    # 오히려 늘었음 — 5제품 실측 → 가격 None / 4.7인치 오추출 다발).
    # 새 정책: 일반 검색을 그대로 하되, **가격**은 공식 도메인으로 매칭된
    # 항목에서만 파싱(중고가·할인가 차단). 스펙(인치·연도)은 전체에서
    # 파싱(공식 페이지가 spec 노출에 인색해도 외부 매체에서 안정적).
    domain = official_domain_for(brand)
    query = f"{base} {query_suffix}".strip()

    fn = search_fn or serper_web_search
    try:
        items, perf = fn(query)
    except Exception as e:  # noqa: BLE001 — 외부 실패는 안전 퇴화
        return {"price_raw": None, "price_display": None,
                "screen_size": None, "release_year": None,
                "source": None}, {"query": query, "site": domain,
                                  "error": f"{type(e).__name__}"}

    text_blob = " ".join(
        f"{i.get('title','')} {i.get('snippet','')}" for i in items
    )
    # 스펙(인치·연도) — 전체 결과에서
    screen = parse_screen(text_blob)
    year = parse_release_year(text_blob)
    # 보강 9 — 정책 완화: 공식 도메인 후필터 폐기. 일반 검색 전체 결과의
    # **최저가** 채택 (사용자 표기 "최저 N만 원~" 의도와 일치).
    # 부작용으로 중고/할인가가 잡힐 수 있지만 — 가격 None 보다 신뢰성 있는
    # 한 가지 가격 표시가 사용자 의도("가격이 너무 자주 '없음'으로 나오지
    # 않게"). parse_prices() 가 상식 범위(5만~1500만)로 노이즈 1차 필터.
    # 공식 도메인 매칭이 있으면 source 의 raw_link 만 거기로(추적/디버그용).
    prices = parse_prices(text_blob)
    price_raw = min(prices) if prices else None
    official_matched = [it for it in items
                        if domain and domain in (it.get("link") or "").lower()]
    if official_matched:
        raw_link = official_matched[0]["link"]
    else:
        raw_link = items[0]["link"] if items else None
    # 정책 태그 부착 — 캐시 invalidation 용(스키마 무변경, source 컬럼만 사용)
    source = f"{_POLICY_TAG}|{raw_link}" if raw_link else _POLICY_TAG

    perf["site"] = domain
    perf["official_items"] = len(official_matched)
    fields = {
        "price_raw": price_raw,
        "price_display": format_price_display(price_raw),
        "screen_size": screen,
        "release_year": year,
        "source": source,
    }
    return fields, perf


def collect_and_cache_meta(
    product_id: int,
    name: str,
    brand: str = "",
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """오케스트레이터: 캐시 확인 → 검색·파싱 → 저장. 절대 raise 안 함.

    반환: {price_display, screen_size, release_year, source, status}.
    status ∈ {cached, fetched, disabled, error}.
    """
    result: Dict[str, Any] = {"status": "error", "price_display": None,
                              "screen_size": None, "release_year": None,
                              "source": None}
    try:
        from scripts.config import PRODUCT_META_FETCH_ENABLED
        from scripts.popup import store

        if not force:
            cached = store.get_meta(product_id)
            # 캐시 유효 조건: (a) 정책 태그 일치 AND (b) 의미있는 값 1개 이상.
            # 작업 3 재시도 — 빈 결과 캐시(전 필드 None) 가 재시도를 막던
            # 문제 해결. 의미있는 값이 하나도 없으면 캐시 무효로 보고 재수집.
            src = (cached or {}).get("source") or ""
            tag_ok = (src.startswith(_POLICY_TAG + "|") or src == _POLICY_TAG)
            has_value = cached and any((
                cached.get("price_display"),
                cached.get("screen_size"),
                cached.get("release_year"),
            ))
            if cached and tag_ok and has_value:
                result.update({
                    "status": "cached",
                    "price_display": cached.get("price_display"),
                    "screen_size": cached.get("screen_size"),
                    "release_year": cached.get("release_year"),
                    "source": cached.get("source"),
                })
                return result

        if not PRODUCT_META_FETCH_ENABLED:
            result["status"] = "disabled"
            return result

        fields, perf = fetch_meta(name, brand)
        print(f"[PERF][popup_meta] product={product_id} query="
              f"{perf.get('query')!r} ms={perf.get('ms')} "
              f"err={perf.get('error')} "
              f"price={fields.get('price_display')} "
              f"screen={fields.get('screen_size')} "
              f"year={fields.get('release_year')}")
        try:
            store.upsert_meta(product_id, **fields)
        except Exception as e:  # noqa: BLE001 — 저장 실패도 호출부 안 죽임
            print(f"[WARN][popup_meta] cache write failed "
                  f"{type(e).__name__}: {e}")
        result.update({
            "status": "fetched",
            "price_display": fields.get("price_display"),
            "screen_size": fields.get("screen_size"),
            "release_year": fields.get("release_year"),
            "source": fields.get("source"),
        })
        return result
    except Exception as e:  # noqa: BLE001 — 최후 안전망
        print(f"[WARN][popup_meta] unexpected {type(e).__name__}: {e}")
        result["status"] = "error"
        result["reason"] = f"unexpected:{type(e).__name__}"
        return result
