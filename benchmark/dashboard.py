#!/usr/bin/env python3
"""
시중 AI(GPT·Gemini) vs 모아봄 — 정량 비교 대시보드 생성기.

운영 사이트(main.py)와 무관한 독립 발표용 스크립트다. 실행하면 의존성 없는
단일 HTML 파일을 생성하고 브라우저로 연다. 발표 화면에 그대로 캡처하거나,
대시보드의 [PDF로 저장] 버튼(브라우저 인쇄)으로 PDF/이미지를 내보낼 수 있다.

실행:
    python benchmark/dashboard.py
    python benchmark/dashboard.py --no-open   # 브라우저 자동 열기 끄기

차트는 외부 라이브러리(Recharts 등) 없이 순수 CSS/SVG 막대로 그린다.
→ 인터넷 연결 없이도 발표장에서 100% 동일하게 렌더링되도록 한 의도적 선택.
(이 프로젝트 프론트는 React 빌드 체계가 없어 Jinja2/Vanilla JS 라인과 일치.)
"""
import json
import os
import sys
import webbrowser

# 직접 실행(python benchmark/dashboard.py) / 패키지 실행 모두 지원
try:
    from . import data as data_mod
    from . import metrics as M
except ImportError:  # python benchmark/dashboard.py 로 직접 실행한 경우
    import data as data_mod
    import metrics as M


def load_runs():
    """실험 결과(experiment/run_experiment.py 산출 JSON)가 있으면 우선 로드.
    없으면 data.py 의 샘플 데이터로 안전 퇴화. 반환: (runs, source_label)."""
    results_json = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "output", "experiment_runs.json"
    )
    if os.path.exists(results_json):
        try:
            with open(results_json, "r", encoding="utf-8") as f:
                runs = json.load(f)
            if runs:
                return runs, f"실험 측정 데이터 ({len(runs)}건)"
        except (ValueError, OSError) as e:
            print(f"[WARN] 실험 결과 로드 실패 → 샘플 데이터 사용: {e}")
    return data_mod.BENCHMARK_RUNS, "샘플(예시) 데이터"


# ---------------------------------------------------------------------------
# 색상/스타일 토큰 (과하지 않게 — 모아봄만 강조색)
# ---------------------------------------------------------------------------
ACCENT = "#0f9d8a"          # 모아봄 강조 (teal)
ACCENT_SOFT = "#e7f6f3"
GPT_COLOR = "#9aa3af"       # 중립 회색
GEMINI_COLOR = "#c4b5d6"    # 중립 연보라
INK = "#1f2933"
MUTED = "#6b7280"
BORDER = "#e5e7eb"

SYSTEM_COLOR = {"GPT": GPT_COLOR, "Gemini": GEMINI_COLOR, "모아봄": ACCENT}

DECISION_BADGE = {
    "추천": ("#e7f6f3", "#0f766e"),
    "조건부 추천": ("#fff7e6", "#b45309"),
    "비추천": ("#fdeaea", "#b91c1c"),
    "데이터 부족": ("#eef1f4", "#4b5563"),
}


def _pct(v):
    return f"{v:.1f}%"


def _num(v):
    return f"{v:,}"


# ---------------------------------------------------------------------------
# 섹션 빌더
# ---------------------------------------------------------------------------
def build_summary_cards(runs):
    """상단 요약 카드 — 모아봄 기준 헤드라인 수치.

    (영상/댓글 수는 모아봄만 수집하므로 모아봄 대표 실행 기준으로 집계)
    """
    m = M.get_system_average_metrics(runs, data_mod.HIGHLIGHT_SYSTEM)
    cards = [
        ("평균 근거 추적률", _pct(m["avg_traceability"]), "핵심 주장 대비 근거 연결 비율"),
        ("평균 판정 일관성", _pct(m["avg_consistency"]), "반복 실행 시 동일 판정 유지율"),
        ("총 분석 영상 수", _num(m["total_videos"]), "수집·분석에 사용한 리뷰 영상"),
        ("총 분석 댓글 수", _num(m["total_comments"]), "수집·분석에 사용한 실사용자 댓글"),
    ]
    items = "".join(
        f"""
        <div class="card">
          <div class="card-label">{label}</div>
          <div class="card-value">{value}</div>
          <div class="card-sub">{sub}</div>
        </div>"""
        for label, value, sub in cards
    )
    return f"""
    <section>
      <div class="section-head">
        <h2>요약 <span class="tag">모아봄 기준</span></h2>
      </div>
      <div class="card-grid">{items}</div>
    </section>"""


def _bars(title, unit, values, max_value):
    """단일 막대그래프 (시스템별 1막대). values: [(system, value)]"""
    bars = ""
    for system, val in values:
        h = 0 if max_value == 0 else max(2, val / max_value * 100)
        color = SYSTEM_COLOR.get(system, MUTED)
        emphasis = " bar-emph" if system == data_mod.HIGHLIGHT_SYSTEM else ""
        label = _pct(val) if unit == "%" else _num(round(val))
        bars += f"""
          <div class="bar-col">
            <div class="bar-value">{label}</div>
            <div class="bar-track">
              <div class="bar-fill{emphasis}" style="height:{h:.1f}%;background:{color}"></div>
            </div>
            <div class="bar-name">{system}</div>
          </div>"""
    return f"""
      <div class="chart">
        <div class="chart-title">{title}</div>
        <div class="bar-area">{bars}</div>
      </div>"""


def build_charts(runs):
    avgs = {s: M.get_system_average_metrics(runs, s) for s in data_mod.SYSTEMS}
    trace = [(s, avgs[s]["avg_traceability"]) for s in data_mod.SYSTEMS]
    cons = [(s, avgs[s]["avg_consistency"]) for s in data_mod.SYSTEMS]
    comments = [(s, avgs[s]["avg_comments_per_product"]) for s in data_mod.SYSTEMS]
    max_comments = max((v for _, v in comments), default=0)

    charts = (
        _bars("AI별 평균 근거 추적률", "%", trace, 100)
        + _bars("AI별 평균 판정 일관성", "%", cons, 100)
        + _bars("AI별 평균 분석 댓글 수", "n", comments, max_comments)
    )
    return f"""
    <section>
      <div class="section-head"><h2>AI별 평균 비교</h2></div>
      <div class="chart-grid">{charts}</div>
    </section>"""


def _decision_badge(decision):
    if not decision:
        return '<span class="badge">-</span>'
    bg, fg = DECISION_BADGE.get(decision, ("#eef1f4", "#4b5563"))
    return f'<span class="badge" style="background:{bg};color:{fg}">{decision}</span>'


def build_table(runs):
    rows = M.build_group_summary(runs)
    # 제품명 → 시스템 순서로 정렬
    sys_order = {s: i for i, s in enumerate(data_mod.SYSTEMS)}
    rows.sort(key=lambda r: (r["product_name"], sys_order.get(r["system"], 99)))

    body = ""
    for r in rows:
        emph = " row-emph" if r["system"] == data_mod.HIGHLIGHT_SYSTEM else ""
        insufficient = ' <span class="mini-tag">반복 실행 부족</span>' if r["insufficient_runs"] else ""
        data_short = "예" if r["has_data_insufficient"] else "아니오"
        body += f"""
        <tr class="{emph.strip()}">
          <td class="t-prod">{r['product_name']}</td>
          <td><span class="sys-chip" style="background:{SYSTEM_COLOR.get(r['system'], MUTED)}1a;color:{SYSTEM_COLOR.get(r['system'], INK)}">{r['system']}</span></td>
          <td class="t-num">{r['run_count']}</td>
          <td>{_decision_badge(r['final_decision'])}</td>
          <td class="t-num">{_pct(r['avg_traceability'])}</td>
          <td class="t-num">{_pct(r['consistency'])}{insufficient}</td>
          <td class="t-num">{_num(r['video_count'])}</td>
          <td class="t-num">{_num(r['comment_count'])}</td>
          <td class="t-center">{data_short}</td>
          <td class="t-note">{r['note']}</td>
        </tr>"""

    return f"""
    <section>
      <div class="section-head"><h2>비교 테이블</h2>
        <span class="hint">행은 (제품 × AI) 단위 요약 · 음영 행이 모아봄</span></div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>제품명</th><th>비교 대상 AI</th><th>실행 횟수</th><th>최종 판정</th>
              <th>근거 추적률</th><th>판정 일관성</th><th>분석 영상 수</th>
              <th>분석 댓글 수</th><th>데이터 부족</th><th>비고</th>
            </tr>
          </thead>
          <tbody>{body}</tbody>
        </table>
      </div>
    </section>"""


def build_product_detail(runs):
    """제품별 상세 — 드롭다운으로 선택하면 해당 제품의 반복 실행 결과 표시."""
    groups = M.group_runs(runs)
    products = []
    for r in runs:
        if r["productId"] not in [p[0] for p in products]:
            products.append((r["productId"], r["productName"]))

    options = "".join(
        f'<option value="{pid}">{pname}</option>' for pid, pname in products
    )

    blocks = ""
    for idx, (pid, pname) in enumerate(products):
        hidden = "" if idx == 0 else " hidden"
        # 시스템별 → 실행별 정렬
        sys_order = {s: i for i, s in enumerate(data_mod.SYSTEMS)}
        prod_runs = sorted(
            [r for r in runs if r["productId"] == pid],
            key=lambda r: (sys_order.get(r["system"], 99), r["runId"]),
        )
        run_rows = ""
        for r in prod_runs:
            run_rows += f"""
            <tr>
              <td class="t-mono">{r['runId']}</td>
              <td>{r['executedAt']}</td>
              <td><span class="sys-chip" style="background:{SYSTEM_COLOR.get(r['system'], MUTED)}1a;color:{SYSTEM_COLOR.get(r['system'], INK)}">{r['system']}</span></td>
              <td>{_decision_badge(r['decision'])}</td>
              <td class="t-num">{r['totalClaims']}</td>
              <td class="t-num">{r['evidenceLinkedClaims']}</td>
              <td class="t-num">{_pct(M.calculate_evidence_traceability(r))}</td>
              <td class="t-num">{_num(r['videoCount'])}</td>
              <td class="t-num">{_num(r['commentCount'])}</td>
              <td class="t-note">{M.evidence_summary_text(r)}</td>
            </tr>"""
        blocks += f"""
        <div class="detail-block{hidden}" data-product="{pid}">
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>run_id</th><th>실행 날짜</th><th>AI</th><th>최종 판정</th>
                  <th>전체 핵심 주장</th><th>근거 연결 주장</th><th>근거 추적률</th>
                  <th>분석 영상</th><th>분석 댓글</th><th>대표 근거 요약</th>
                </tr>
              </thead>
              <tbody>{run_rows}</tbody>
            </table>
          </div>
        </div>"""

    return f"""
    <section>
      <div class="section-head"><h2>제품별 상세</h2>
        <select id="product-select" class="select no-print" onchange="selectProduct(this.value)">
          {options}
        </select>
      </div>
      <div id="detail-area">{blocks}</div>
    </section>"""


def build_summary_quotes(runs):
    moabom = M.get_system_average_metrics(runs, data_mod.HIGHLIGHT_SYSTEM)
    dynamic = (
        f"본 데이터 기준, 모아봄의 평균 근거 추적률은 {_pct(moabom['avg_traceability'])}, "
        f"평균 판정 일관성은 {_pct(moabom['avg_consistency'])}이며, "
        f"제품당 평균 {_num(round(moabom['avg_comments_per_product']))}건의 댓글을 분석 근거로 사용했습니다."
    )
    quotes = [
        "시중 AI가 범용 검색·답변 도구라면, 모아봄은 제품 리뷰 분석에 특화된 "
        "데이터 수집·검증·판정 시스템입니다.",
        "모아봄은 근거 추적률, 판정 일관성, 분석 근거량을 수치화하여 결과의 "
        "신뢰성을 설명할 수 있습니다.",
        "따라서 본 비교는 '어떤 AI가 더 똑똑한가'가 아니라, '제품 리뷰 분석이라는 "
        "특정 과업에서 누가 더 추적 가능하고 일관된 결과를 내는가'를 확인하는 방식입니다.",
    ]
    lis = "".join(f"<li>{q}</li>" for q in quotes)
    return f"""
    <section class="quote-section">
      <div class="section-head"><h2>발표용 요약</h2></div>
      <p class="quote-dynamic">{dynamic}</p>
      <ul class="quote-list">{lis}</ul>
    </section>"""


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------
def build_css():
    return f"""
    :root {{ color-scheme: light; }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; background: #f4f6f8; color: {INK};
      font-family: -apple-system, "Segoe UI", "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
      -webkit-font-smoothing: antialiased;
    }}
    .wrap {{ max-width: 1180px; margin: 0 auto; padding: 32px 28px 64px; }}
    header.page {{
      display: flex; justify-content: space-between; align-items: flex-end;
      margin-bottom: 28px; padding-bottom: 18px; border-bottom: 2px solid {INK};
    }}
    header.page h1 {{ font-size: 24px; margin: 0 0 6px; letter-spacing: -.5px; }}
    header.page p {{ margin: 0; color: {MUTED}; font-size: 13px; }}
    .btn {{
      border: 1px solid {ACCENT}; background: {ACCENT}; color: #fff;
      padding: 9px 16px; border-radius: 8px; font-size: 13px; cursor: pointer;
      font-weight: 600;
    }}
    .btn:hover {{ filter: brightness(1.05); }}
    section {{ margin-bottom: 34px; }}
    .section-head {{ display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }}
    .section-head h2 {{ font-size: 16px; margin: 0; }}
    .hint, .tag {{ font-size: 11px; color: {MUTED}; }}
    .tag {{ background: {ACCENT_SOFT}; color: {ACCENT}; padding: 2px 8px; border-radius: 20px; font-weight: 600; }}

    .card-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }}
    .card {{
      background: #fff; border: 1px solid {BORDER}; border-radius: 12px;
      padding: 18px 18px 16px; border-top: 3px solid {ACCENT};
    }}
    .card-label {{ font-size: 12px; color: {MUTED}; }}
    .card-value {{ font-size: 30px; font-weight: 700; margin: 6px 0 4px; letter-spacing: -1px; }}
    .card-sub {{ font-size: 11px; color: {MUTED}; }}

    .chart-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }}
    .chart {{ background: #fff; border: 1px solid {BORDER}; border-radius: 12px; padding: 18px; }}
    .chart-title {{ font-size: 13px; font-weight: 600; margin-bottom: 14px; }}
    .bar-area {{ display: flex; align-items: flex-end; justify-content: space-around; height: 180px; gap: 10px; }}
    .bar-col {{ display: flex; flex-direction: column; align-items: center; flex: 1; height: 100%; justify-content: flex-end; }}
    .bar-value {{ font-size: 12px; font-weight: 600; margin-bottom: 6px; }}
    .bar-track {{ width: 46px; height: 130px; display: flex; align-items: flex-end; }}
    .bar-fill {{ width: 100%; border-radius: 6px 6px 0 0; transition: height .3s; }}
    .bar-fill.bar-emph {{ box-shadow: 0 0 0 2px {ACCENT_SOFT}, 0 6px 14px rgba(15,157,138,.25); }}
    .bar-name {{ font-size: 12px; margin-top: 8px; color: {MUTED}; }}

    .table-wrap {{ overflow-x: auto; background: #fff; border: 1px solid {BORDER}; border-radius: 12px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    thead th {{
      background: #f8fafc; color: {MUTED}; font-weight: 600; text-align: left;
      padding: 11px 12px; border-bottom: 1px solid {BORDER}; white-space: nowrap; font-size: 12px;
    }}
    tbody td {{ padding: 11px 12px; border-bottom: 1px solid #f1f3f5; white-space: nowrap; }}
    tbody tr:last-child td {{ border-bottom: none; }}
    .row-emph {{ background: {ACCENT_SOFT}; }}
    .row-emph .t-prod {{ font-weight: 700; }}
    .t-num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .t-center {{ text-align: center; }}
    .t-note {{ color: {MUTED}; white-space: normal; min-width: 180px; }}
    .t-mono {{ font-family: ui-monospace, "SFMono-Regular", Menlo, monospace; font-size: 12px; }}
    .badge {{ padding: 3px 9px; border-radius: 20px; font-size: 12px; font-weight: 600; white-space: nowrap; }}
    .sys-chip {{ padding: 3px 10px; border-radius: 6px; font-size: 12px; font-weight: 600; }}
    .mini-tag {{ font-size: 10px; color: #b45309; background: #fff7e6; padding: 1px 6px; border-radius: 10px; margin-left: 4px; }}
    .select {{ padding: 7px 12px; border: 1px solid {BORDER}; border-radius: 8px; font-size: 13px; background: #fff; }}
    .detail-block[hidden] {{ display: none; }}

    .quote-section {{ background: {INK}; color: #fff; border-radius: 14px; padding: 26px 28px; }}
    .quote-section h2 {{ color: #fff; }}
    .quote-dynamic {{ font-size: 14px; color: #cfe8e3; margin: 0 0 14px; }}
    .quote-list {{ margin: 0; padding-left: 20px; }}
    .quote-list li {{ margin-bottom: 10px; line-height: 1.55; font-size: 15px; }}
    footer {{ margin-top: 30px; color: {MUTED}; font-size: 11px; text-align: center; }}

    @media print {{
      body {{ background: #fff; }}
      .no-print {{ display: none !important; }}
      .detail-block[hidden] {{ display: block !important; }}
      section {{ break-inside: avoid; }}
      .quote-section {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
      .card, .chart, .bar-fill, .row-emph, .badge, .sys-chip {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
    }}
    """


def build_js():
    return """
    function selectProduct(pid) {
      document.querySelectorAll('.detail-block').forEach(function (b) {
        b.hidden = (b.getAttribute('data-product') !== pid);
      });
    }
    """


def build_html(runs, source_label="샘플(예시) 데이터"):
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=1200">
<title>모아봄 vs 시중 AI — 정량 비교 대시보드</title>
<style>{build_css()}</style>
</head>
<body>
  <div class="wrap">
    <header class="page">
      <div>
        <h1>모아봄 vs 시중 AI — 정량 비교 대시보드</h1>
        <p>제품 리뷰 분석 과업에서의 근거 추적률 · 판정 일관성 · 분석 근거량 비교 &nbsp;·&nbsp; <strong>데이터: {source_label}</strong></p>
      </div>
      <button class="btn no-print" onclick="window.print()">PDF로 저장 / 인쇄</button>
    </header>
    {build_summary_cards(runs)}
    {build_charts(runs)}
    {build_table(runs)}
    {build_product_detail(runs)}
    {build_summary_quotes(runs)}
    <footer>
      ※ 본 비교는 정확도 우열이 아니라 '근거 추적 가능성·판정 일관성·분석 근거량'을 수치화한 것입니다.
      샘플 데이터 기준이며 benchmark/data.py 에서 수정할 수 있습니다.
    </footer>
  </div>
  <script>{build_js()}</script>
</body>
</html>"""


def main():
    runs, source_label = load_runs()
    print(f"[INFO] 대시보드 데이터 소스: {source_label}")
    html = build_html(runs, source_label)

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "ai_comparison_dashboard.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print("[OK] 대시보드 생성 완료")
    print(f"     -> {out_path}")

    if "--no-open" not in sys.argv:
        try:
            webbrowser.open("file:///" + out_path.replace("\\", "/"))
            print("[OK] 기본 브라우저로 열었습니다.")
        except Exception as e:
            print(f"[WARN] 브라우저 자동 열기 실패(파일을 직접 열어주세요): {e}")


if __name__ == "__main__":
    main()
