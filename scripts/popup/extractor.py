"""④ 보고서 마크다운 → 팝업 데이터 결정론적 추출 (Phase 5 §4).

★ LLM 호출 0건 — 모든 값은 prompt_manager 가 강제한 ④ 출력 양식의 패턴에서
정규식·줄 단위로 파싱한다. 각 추출 단계는 실패에 대비(매칭 실패·섹션 누락
→ '데이터 부족' 표기). 어떤 단일 실패도 팝업 전체를 죽이지 않는다(§7).

순수 함수: 입력=④ 마크다운 문자열, 출력=구조화 dict. 부수효과 0.
Phase 4 가 보고서 생성 파이프라인 내부를 바꿔도 출력 양식이 그대로면
이 모듈은 영향받지 않는다(§0 경계).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# ── 등급 도출 임계값 (사용자 결정 — Phase 5 보강 2/7 갱신) ──
# 점수만으로 결정론적 매핑. 합의도는 시그니처 호환만 유지(미사용).
# 임계는 보강 7 의 도넛 색 임계와 동일(7.5 / 4.0 / 1.0) → 도넛 색과
# 강조 카드 라벨이 같은 점수 기준으로 결정 → 시각 일관성 보장.
_HIGH_SCORE_CUT = 7.5      # 7.5+ → high "강력 추천"
_MID_SCORE_CUT = 4.0       # 4.0~7.49 → mid "조건부 추천"
_LOW_SCORE_CUT = 1.0       # 1.0~3.99 → low "검토 필요"

# 등급 → (라벨, 색). 워딩은 다른 표현으로 의역 금지(사용자 결정).
TIER_LABELS = {
    "high": ("강력 추천", "green"),
    "mid":  ("조건부 추천", "amber"),
    "low":  ("검토 필요", "red"),
}

DATA_INSUFFICIENT = "데이터 부족"


# ── 섹션 분리 헬퍼 ────────────────────────────────────────────────

_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)
_H3_RE = re.compile(r"^###\s+(.+?)\s*$", re.M)


def _section_body(md: str, h2_token: str) -> str:
    """## {h2_token...} 헤더 본문 ~ 다음 ## 직전까지. 없으면 빈 문자열."""
    if not md:
        return ""
    matches = list(_H2_RE.finditer(md))
    for i, m in enumerate(matches):
        if h2_token in m.group(1):
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
            return md[start:end]
    return ""


def _h3_body(section_md: str, h3_token: str) -> str:
    """주어진 ##섹션 본문 안에서 ### {h3_token} 의 본문(다음 ### 또는 ## 직전)."""
    if not section_md:
        return ""
    matches = list(_H3_RE.finditer(section_md))
    for i, m in enumerate(matches):
        if h3_token in m.group(1):
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(section_md)
            return section_md[start:end]
    return ""


# ── §4-A · §4-B · §4-C · §4-F: ① 섹션 추출 ──────────────────────

_RE_SCORE = re.compile(
    r"^-\s*종합\s*평가\s*[:：]\s*([0-9]+(?:\.[0-9]+)?|데이터\s*부족)\s*/\s*10",
    re.M,
)
_RE_CONSENSUS = re.compile(
    r"^-\s*리뷰어\s*합의도\s*[:：]\s*(높음|중간|낮음)", re.M
)
_RE_VIDEOS_N = re.compile(r"\(분석\s*영상\s*(\d+)\s*개\s*기반\)")
_RE_FIRST_BULLET = re.compile(r"^-\s*(.+?)\s*$", re.M)


def _extract_section1(md: str) -> Dict[str, Any]:
    sec = _section_body(md, "① ")
    if not sec:
        # 동그라미 ① 변형 대비
        sec = _section_body(md, "①")
    out: Dict[str, Any] = {
        "score": None,        # str "X.X" or "데이터 부족" or None
        "consensus": None,    # "높음"/"중간"/"낮음" or None
        "one_liner": "",
        "videos_n": None,     # 분석 영상 수
    }
    if not sec:
        return out

    m = _RE_SCORE.search(sec)
    if m:
        raw = m.group(1).replace(" ", "")
        out["score"] = DATA_INSUFFICIENT if raw.startswith("데이터") else raw

    m = _RE_CONSENSUS.search(sec)
    if m:
        out["consensus"] = m.group(1)

    m = _RE_VIDEOS_N.search(sec)
    if m:
        try:
            out["videos_n"] = int(m.group(1))
        except ValueError:
            pass

    # 첫 번째 불릿 = 한 줄 결론(점수/합의도 라인 제외).
    # 보강 3: LLM 응답에 따라 '한 문장 결론:' 등 접두어가 붙기도 한다 — 결정
    # 론적 정규식으로 제거(빈 텍스트가 되면 다음 후보 불릿로 넘어가지 않고,
    # 그 자리에서 빈 채로 둔다 — 추후 §7 fallback 으로 처리).
    for b in _RE_FIRST_BULLET.finditer(sec):
        txt = b.group(1).strip()
        if not txt:
            continue
        if txt.startswith("종합 평가") or txt.startswith("리뷰어 합의도"):
            continue
        out["one_liner"] = _strip_oneliner_prefix(txt)
        break
    return out


# ── 보강 3: '한 문장 결론:' 등 접두어 결정론적 제거 ────────────────

_RE_ONELINER_PREFIX = re.compile(
    r"^\s*(?:한\s*문장\s*결론|한\s*줄\s*결론|한줄\s*결론|결론|판정)"
    r"\s*[:：]\s*"
)


def _strip_oneliner_prefix(text: str) -> str:
    """텍스트 시작의 결론 접두어를 제거(매칭 안 되면 원문 그대로)."""
    return _RE_ONELINER_PREFIX.sub("", text or "", count=1).strip()


# ── §4-D: 추천도 등급 도출 (옵션 1, 규칙 기반) ───────────────────

def derive_tier(score: Optional[str], consensus: Optional[str] = None) -> Optional[str]:
    """반환 키: 'high'/'mid'/'low' or None(결정 불가).

    사용자 결정(Phase 5 보강 2): **점수만으로** 결정. 합의도는 시그니처
    호환을 위해 받기만 하고 사용하지 않음(미래 활용 여지).
    임계는 보강 7 의 도넛 색 임계와 동일 — 시각 일관성 보장.
      - 7.5 ≤ s ≤ 10  → high "강력 추천"
      - 4.0 ≤ s < 7.5 → mid  "조건부 추천"
      - 1.0 ≤ s < 4.0 → low  "검토 필요"
      - 점수 추출 실패('데이터 부족' 또는 None)  → None (§7 fallback, 카드 숨김)
    """
    if not score or score == DATA_INSUFFICIENT:
        return None
    try:
        s = float(score)
    except (TypeError, ValueError):
        return None
    if s >= _HIGH_SCORE_CUT:
        return "high"
    if s >= _MID_SCORE_CUT:
        return "mid"
    if s >= _LOW_SCORE_CUT:
        return "low"
    return None


# ── §4-E: ④ 섹션 합의 장점/단점 추출 ────────────────────────────

_RE_AGREE_ITEM = re.compile(r"^-\s*(.+?)\s*\((\d+)\s*/\s*(\d+)\)\s*$", re.M)


def _extract_section4(md: str, top_k: int = 3) -> Dict[str, Any]:
    sec = _section_body(md, "④ ")
    if not sec:
        sec = _section_body(md, "④")
    out: Dict[str, Any] = {"pros": [], "cons": [],
                           "pros_missing": False, "cons_missing": False}
    if not sec:
        out["pros_missing"] = True
        out["cons_missing"] = True
        return out

    def _items(h3_token: str) -> Tuple[List[Dict[str, Any]], bool]:
        body = _h3_body(sec, h3_token)
        if not body:
            return [], True   # 헤딩 없음 → 데이터 부족 처리
        # "- 데이터 부족" 한 줄만 있는 경우
        cleaned = body.strip()
        if cleaned == "- 데이터 부족" or cleaned.startswith("- 데이터 부족\n"):
            # 더 이상 합의 항목 없음
            return [], True
        items: List[Dict[str, Any]] = []
        for m in _RE_AGREE_ITEM.finditer(body):
            try:
                items.append({
                    "label": m.group(1).strip(),
                    "n": int(m.group(2)),
                    "total": int(m.group(3)),
                })
            except ValueError:
                continue
        if not items:
            # 헤딩은 있는데 (N/n) 형식 항목이 0건 (예: 개별 의견만 있는 비정상)
            return [], True
        # N 내림차순, 동률은 등장 순서(stable sort)
        items.sort(key=lambda x: -x["n"])
        return items[:top_k], False

    pros, pros_missing = _items("장점")
    cons, cons_missing = _items("단점")
    out["pros"] = pros
    out["cons"] = cons
    out["pros_missing"] = pros_missing
    out["cons_missing"] = cons_missing
    return out


# ── §4-F: ⑤ 분석 댓글 수 ─────────────────────────────────────────

_RE_COMMENTS_N = re.compile(r"^-\s*분석\s*댓글\s*수\s*[:：]\s*(\d+)\s*건", re.M)


def _extract_comments_n(md: str) -> Optional[int]:
    """④ ⑤ 섹션 '분석 댓글 수: N건' 에서 N. 없으면 None."""
    sec = _section_body(md, "⑤ ")
    if not sec:
        sec = _section_body(md, "⑤")
    if not sec:
        return None
    m = _RE_COMMENTS_N.search(sec)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


# ── §4-G: 주의 노트 — ⑥/④ 개별 의견에서 도출 ────────────────────

_RE_ROW = re.compile(r"^\|\s*(.+?)\s*\|", re.M)
_AMBIGUOUS_HINTS = ("갈림", "엇갈", "혼재", "의견 분분", "데이터 부족",
                    "평가 분분")


def _extract_caveat(md: str) -> str:
    """ⓘ 주의 노트 — 평가가 갈린/데이터 부족인 항목 + 개별 의견 그룹 요약."""
    notes: List[str] = []

    # 1) ⑥ 표에서 "변화 평가" 가 갈리거나 "데이터 부족"
    sec6 = _section_body(md, "⑥ ")
    if not sec6:
        sec6 = _section_body(md, "⑥")
    if sec6:
        ambiguous_items: List[str] = []
        for line in sec6.splitlines():
            line = line.strip()
            if not line.startswith("|") or "---" in line:
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 4:
                continue
            # 헤더 행 스킵
            if cells[0] == "항목":
                continue
            evaluation = cells[3] if len(cells) > 3 else ""
            if any(h in evaluation for h in _AMBIGUOUS_HINTS):
                ambiguous_items.append(cells[0])
        if ambiguous_items:
            joined = ", ".join(ambiguous_items[:3])
            notes.append(
                f"{joined}에 대한 전작 대비 평가가 갈려 요약에서 제외했어요. "
                "상세 보고서에서 직접 확인하세요."
            )

    # 2) ④ "### 개별 리뷰어 의견" 그룹이 있으면 한 줄 언급
    sec4 = _section_body(md, "④ ")
    if not sec4:
        sec4 = _section_body(md, "④")
    if sec4:
        opinion_body = _h3_body(sec4, "개별 리뷰어 의견")
        if opinion_body.strip():
            count = len([
                l for l in opinion_body.splitlines()
                if l.strip().startswith("- ") and "데이터 부족" not in l
            ])
            if count > 0 and not notes:
                notes.append(
                    f"리뷰어 1명만 언급한 의견 {count}건은 합의가 아니라 "
                    "요약에서 제외했어요. 상세 보고서에서 확인하세요."
                )

    return notes[0] if notes else ""


# ── 보강 4: ④ ⑦ 추천/비추 페르소나 추출 + 두 줄 템플릿 조립 ───────
#  결정론적(LLM 0건). prompt_manager 의 ⑦ 절대 규칙(페르소나 10자 내외
#  명사구) 패턴을 그대로 활용.
#
# Phase 5 마지막 보강 2: **합의 영상 수 최대** 페르소나 선택.
#   기존: `_first()` 가 첫 번째 매칭만 선택 → ⑦ 의 항목 순서(LLM 출력 순)에
#   민감해 합의가 적은 항목이 표면화 가능. ⑦ 의 "(근거: 영상 1,3,5)"의
#   콤마 개수 = 합의 영상 수. 그것이 가장 많은 항목을 선택 → "여러 리뷰어가
#   동의한 페르소나" 가 카드에 노출됨(사용자 의도).
#   동수면 먼저 등장한 항목(stable sort) — 결정론적.
#   '데이터 부족' 항목은 제외.

# group(1) = 페르소나 명사구, group(2) = "영상 X, Y, Z" 의 ID 부분
_RE_PERSONA_ITEM = re.compile(
    r"^-\s*(.+?)\s*\(\s*근거\s*[:：]\s*영상\s*([\d,\s]+)\)\s*$", re.M
)


def pick_most_supported_persona(section_body: str) -> Optional[str]:
    """⑦ 의 ### 추천/### 비추 본문 → '합의 영상 수' 최대 페르소나 1개.

    동수면 먼저 등장한 항목(items 리스트의 안정 정렬). 매칭 0 또는
    '데이터 부족' 만 있으면 None.
    """
    if not section_body:
        return None
    items: List[Tuple[int, str]] = []
    for m in _RE_PERSONA_ITEM.finditer(section_body):
        persona = m.group(1).strip()
        if not persona or persona == "데이터 부족":
            continue
        # group(2) 예: "1, 3, 5" → split → ['1','3','5']
        ids = [s.strip() for s in m.group(2).split(",") if s.strip()]
        items.append((len(ids), persona))
    if not items:
        return None
    # 합의 영상 수 내림차순 — 동수면 등장 순서 유지(stable sort)
    items.sort(key=lambda x: -x[0])
    return items[0][1]


def _extract_personas(md: str) -> Tuple[Optional[str], Optional[str]]:
    """⑦ 섹션의 '### 추천' / '### 비추' 합의 영상 수 최대 페르소나 한 개씩.

    반환: (recommend_persona, not_recommend_persona). 각 None 가능.
    근거 괄호는 버리고 페르소나 명사구만.
    Phase 5 마지막 보강 2: 첫 매칭(_first) 대신 '근거 영상' 수 최대 항목 선택.
    """
    sec = _section_body(md, "⑦ ")
    if not sec:
        sec = _section_body(md, "⑦")
    if not sec:
        return None, None

    rec = pick_most_supported_persona(_h3_body(sec, "추천"))
    notrec = pick_most_supported_persona(_h3_body(sec, "비추"))
    return rec, notrec


def _has_final_jongseong(text: str) -> bool:
    """문자열의 마지막 의미 글자(한글)의 종성(받침) 유무.

    한글 음절(0xAC00~0xD7A3): (ord-0xAC00) % 28 != 0 → 받침 있음.
    한글이 아닌 경우(영문/숫자 등) — 보수적으로 받침 있다(True) 반환 →
    '을'/'이' 채택.
    """
    s = (text or "").rstrip()
    if not s:
        return True
    ch = s[-1]
    code = ord(ch)
    if 0xAC00 <= code <= 0xD7A3:
        return (code - 0xAC00) % 28 != 0
    return True


# 작업 1 (사용자 결정): 템플릿이 "중시한다면"/"최우선이라면" 으로 끝맺음을
# 또 붙이므로, 페르소나가 같은 토큰("중시", "최우선" 등)으로 끝나면 중복
# 발생("중시 중시한다면"). 결정론적 정규식으로 끝맺음 토큰 제거 후 조합.
_RE_RECO_TAIL = re.compile(r"\s*(중시|중요|선호|우선시)\s*$")
_RE_NOT_RECO_TAIL = re.compile(r"\s*(최우선|우선|중시|중요)\s*$")


def _strip_persona_tail(persona: Optional[str], pattern: re.Pattern) -> Optional[str]:
    """페르소나 끝맺음 토큰 제거. 매칭 없으면 원문 그대로(strip 처리)."""
    if not persona:
        return persona
    return pattern.sub("", persona).strip() or persona  # 모두 잘리면 원문


def _assemble_two_line_desc(
    one_liner: str,
    recommend_persona: Optional[str],
    not_recommend_persona: Optional[str],
) -> Tuple[str, str]:
    """결정론적 두 줄 조립.

    윗줄(main): {추천 페르소나}{을/를} 중시한다면 긍정적으로 고려하세요.
    아랫줄(sub): {비추 페르소나}{이/가} 최우선이라면 유사 제품과 비교를 권합니다.

    작업 1: 페르소나에서 끝맺음 토큰(중시/우선/선호/중요/최우선) 제거 후
    조합 → "X 중시 중시한다면" 같은 중복 제거. 조사도 후처리 결과 마지막
    글자 기준으로 재계산.

    Fallback:
      - 추천 없음 → 윗줄 = one_liner (한 줄 결론 fallback).
      - 비추 없음 → 아랫줄 = '' (억지 생성 금지, UI 에서 숨김).
      - 둘 다 없음 → 윗줄 = one_liner, 아랫줄 = ''.
    """
    rec_clean = _strip_persona_tail(recommend_persona, _RE_RECO_TAIL)
    notrec_clean = _strip_persona_tail(not_recommend_persona, _RE_NOT_RECO_TAIL)

    if rec_clean:
        # 조사는 후처리 후 마지막 글자 기준으로 재계산(중요!)
        josa1 = "을" if _has_final_jongseong(rec_clean) else "를"
        main = f"{rec_clean}{josa1} 중시한다면 긍정적으로 고려하세요."
    else:
        main = (one_liner or "").strip()

    if notrec_clean:
        josa2 = "이" if _has_final_jongseong(notrec_clean) else "가"
        sub = (
            f"{notrec_clean}{josa2} 최우선이라면 "
            "유사 제품과 비교를 권합니다."
        )
    else:
        sub = ""
    return main, sub


# ── 메인 진입점 ──────────────────────────────────────────────────


def extract_popup_data(report_md: str) -> Dict[str, Any]:
    """④ 마크다운 전체 → 팝업이 쓰는 구조화 dict.

    출력 키:
      verdict: {score, consensus, one_liner, tier, label, color}
      pros: [{label, n, total}, ...]   (top 3)
      cons: [...]
      caveat: str (or "")
      videos_n: int or None
      missing: [str, ...]   (각 fallback 사유)
    """
    md = report_md or ""
    s1 = _extract_section1(md)
    s4 = _extract_section4(md)

    missing: List[str] = []
    if s1["score"] is None:
        missing.append("verdict.score")
    if s1["consensus"] is None:
        missing.append("verdict.consensus")
    if not s1["one_liner"]:
        missing.append("verdict.one_liner")
    if s4["pros_missing"]:
        missing.append("pros")
    if s4["cons_missing"]:
        missing.append("cons")
    if s1["videos_n"] is None:
        missing.append("videos_n")
    comments_n = _extract_comments_n(md)
    if comments_n is None:
        missing.append("comments_n")

    tier = derive_tier(s1["score"], s1["consensus"])
    if tier:
        label, color = TIER_LABELS[tier]
    else:
        label, color = None, None
        missing.append("verdict.tier")

    caveat = _extract_caveat(md)
    # 보강 4: ④ ⑦ 추천/비추 페르소나 → 결정론적 두 줄 문구 조립
    rec_p, notrec_p = _extract_personas(md)
    one_liner_main, one_liner_sub = _assemble_two_line_desc(
        s1["one_liner"], rec_p, notrec_p
    )
    return {
        "verdict": {
            "score": s1["score"],
            "consensus": s1["consensus"],
            "one_liner": s1["one_liner"],
            # 보강 4: UI 가 카드 본문을 두 줄로 표시 (윗줄 굵음/큼, 아랫줄
            # 옅음/작음). 둘 중 하나가 빈 문자열이면 UI 는 그 줄을 숨김.
            "one_liner_main": one_liner_main,
            "one_liner_sub": one_liner_sub,
            "recommend_persona": rec_p,
            "not_recommend_persona": notrec_p,
            "tier": tier,
            "label": label,
            "color": color,
        },
        "pros": s4["pros"],
        "cons": s4["cons"],
        "caveat": caveat,
        "videos_n": s1["videos_n"],
        "comments_n": comments_n,
        "missing": missing,
    }
