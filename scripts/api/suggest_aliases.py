"""검색 후보 자동 제안 — 영-한 alias 사전.

normalize_query 결과(NFKC + LOWER + 공백·하이픈 제거)에 substring 치환을
1회 더 적용하여 영문 입력이 한국 정식명과 매칭되도록 한다. 예:
    "galaxy s25"       → normalize → "galaxys25"       → alias → "갤럭시s25"
    "macbook pro m4"   → normalize → "macbookprom4"   → alias → "맥북prom4"
    "airpods pro 2"    → normalize → "airpodspro2"   → alias → "에어팟pro2"

치환 후 결과는 DB ILIKE `REPLACE(REPLACE(LOWER(name),' ',''),'-','')` 컬럼과
바로 비교 가능(시드의 "갤럭시 S25" 도 같은 정규형 "갤럭시s25" 가 됨).

설계 원칙:
1. **브랜드 키워드(samsung/apple/sony/lg/canon 등)는 의도적으로 제외** —
   시드의 name 컬럼에는 브랜드명이 들어가지 않으므로(brand 컬럼에 따로 저장)
   "samsung galaxy s25" 같은 입력이 와도 brand 키워드를 그대로 두는 게 매칭에
   유리. (alias 로 "삼성" 치환 시 오히려 "삼성갤럭시s25" 가 되어 매칭 실패)
2. **길이 내림차순 순회** — `galaxywatch` 가 `galaxy` 보다 먼저 매칭되어야
   `galaxy watch` 입력이 `갤럭시` + `watch` 가 아닌 `갤럭시워치` 로 묶임.
3. **다중 치환 허용** — `galaxy z fold 6` 같은 입력에서 `galaxy` + `zfold` +
   `fold` 모두 적용되도록. 단 이미 한글로 치환된 부분은 재매칭 안 됨.
4. **확실한 라인업만** — 표기 모호한 키워드(ultra/max/pro 등)는 라인업
   변형이라 시드에도 영문/한글 혼재. alias 에 안 넣고 그대로 둠.
"""
from __future__ import annotations

# normalize 결과(공백/하이픈 제거 + lower)와 매칭되는 영-한 라인업 사전.
# 값도 normalize 형태 — 공백 없이 한글로만.
ALIASES: dict[str, str] = {
    # ── 라인업 (긴 키워드 먼저 매칭되도록 dict 순서로는 무관, 정렬 후 사용) ──
    "galaxywatch":  "갤럭시워치",
    "galaxybuds":   "갤럭시버즈",
    "galaxybook":   "갤럭시북",
    "galaxytab":    "갤럭시탭",
    "applewatch":   "애플워치",
    "airpods":      "에어팟",
    "macbook":      "맥북",
    "iphone":       "아이폰",
    "galaxy":       "갤럭시",
    "ipad":         "아이패드",
    "pixel":        "픽셀",
    "lumix":        "루믹스",
    "redmi":        "레드미",
    "bravia":       "브라비아",
    # Garmin 한국 표기
    "fenix":        "페닉스",
    "forerunner":   "포러너",
    "instinct":     "인스팅트",
    "venu":         "베누",
    # Samsung 폴더블
    "zfold":        "z폴드",
    "zflip":        "z플립",
}


def apply_aliases(s: str) -> str:
    """normalize_query 결과 문자열에 alias 치환 적용.

    길이 내림차순으로 순회 — `galaxywatch` 같은 긴 키워드가 `galaxy` 보다
    먼저 치환되어 의도된 라인업으로 묶이도록.
    """
    if not s:
        return s
    for key in sorted(ALIASES.keys(), key=len, reverse=True):
        if key in s:
            s = s.replace(key, ALIASES[key])
    return s
