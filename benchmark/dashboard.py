#!/usr/bin/env python3
"""
시중 AI(GPT·Gemini) vs 모아봄 — 정량 비교 대시보드 생성기 (v3).

지표: 판정 일관성(같은 제품 REPEAT회 다수결 비율) 하나.
(실행시간·근거 추적률·분석 근거량·심판 LLM 은 v3 에서 제거)

운영 사이트(main.py)와 무관한 독립 발표용 스크립트다. 실행하면 의존성 없는
단일 HTML 파일을 생성하고 브라우저로 연다.

실행:
    python benchmark/dashboard.py
    python benchmark/dashboard.py --no-open   # 브라우저 자동 열기 끄기
"""
import json
import os
import sys
import webbrowser

# 한글 출력 깨짐 방지 (Windows cp949 콘솔에서 직접 실행 대비)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 직접 실행(python benchmark/dashboard.py) / 패키지 실행 모두 지원
try:
    from . import data as data_mod
    from . import metrics as M
except ImportError:
    import data as data_mod
    import metrics as M


def load_runs():
    """실험 결과(run_experiment.py 산출 JSON)가 있으면 우선 로드.
    없으면 data.py 샘플로 안전 퇴화. 반환: (runs, source_label)."""
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


# ── 색상/스타일 토큰 (모아봄만 강조색) ──────────────────────────────
ACCENT = "#0f9d8a"
ACCENT_SOFT = "#e7f6f3"
GPT_COLOR = "#9aa3af"
GEMINI_COLOR = "#c4b5d6"
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


def _repeat_count(runs):
    """그룹당 최대 실행 횟수(= REPEAT) 추정."""
    groups = M.group_runs(runs)
    return max((len(g) for g in groups.values()), default=0)


# ── 섹션 빌더 ───────────────────────────────────────────────────────
def build_summary_cards(runs):
    """상단 요약 카드 — 모아봄 기준 일관성 + 실험 규모."""
    m = M.get_system_average_metrics(runs, data_mod.HIGHLIGHT_SYSTEM)
    cards = [
        ("평균 판정 일관성", _pct(m["avg_consistency"]), "같은 제품 반복 시 동일 판정 유지율"),
        ("비교 제품 수", str(m["product_count"]), "동일 질문으로 비교한 제품"),
        ("제품당 반복 횟수", str(_repeat_count(runs)), "판정 일관성 측정용 반복"),
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


def build_charts(runs):
    """AI별 평균 판정 일관성 막대 (유일 지표)."""
    avgs = {s: M.get_system_average_metrics(runs, s) for s in data_mod.SYSTEMS}
    bars = ""
    for system in data_mod.SYSTEMS:
        val = avgs[system]["avg_consistency"]
        h = max(2, val)  # 0~100 → 높이 %
        color = SYSTEM_COLOR.get(system, MUTED)
        emphasis = " bar-emph" if system == data_mod.HIGHLIGHT_SYSTEM else ""
        bars += f"""
          <div class="bar-col">
            <div class="bar-value">{_pct(val)}</div>
            <div class="bar-track">
              <div class="bar-fill{emphasis}" style="height:{h:.1f}%;background:{color}"></div>
            </div>
            <div class="bar-name">{system}</div>
          </div>"""
    return f"""
    <section>
      <div class="section-head"><h2>AI별 평균 판정 일관성 <span class="hint">(높을수록 좋음)</span></h2></div>
      <div class="chart chart-wide">
        <div class="bar-area">{bars}</div>
      </div>
    </section>"""


def _decision_badge(decision):
    if not decision:
        return '<span class="badge">-</span>'
    bg, fg = DECISION_BADGE.get(decision, ("#eef1f4", "#4b5563"))
    return f'<span class="badge" style="background:{bg};color:{fg}">{decision}</span>'


def build_table(runs):
    rows = M.build_group_summary(runs)
    sys_order = {s: i for i, s in enumerate(data_mod.SYSTEMS)}
    rows.sort(key=lambda r: (r["product_name"], sys_order.get(r["system"], 99)))

    body = ""
    for r in rows:
        emph = " row-emph" if r["system"] == data_mod.HIGHLIGHT_SYSTEM else ""
        insufficient = ' <span class="mini-tag">반복 부족</span>' if r["insufficient_runs"] else ""
        body += f"""
        <tr class="{emph.strip()}">
          <td class="t-prod">{r['product_name']}</td>
          <td><span class="sys-chip" style="background:{SYSTEM_COLOR.get(r['system'], MUTED)}1a;color:{SYSTEM_COLOR.get(r['system'], INK)}">{r['system']}</span></td>
          <td class="t-num">{r['run_count']}</td>
          <td>{_decision_badge(r['final_decision'])}</td>
          <td class="t-num">{_pct(r['consistency'])}{insufficient}</td>
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
              <th>판정 일관성</th><th>비고</th>
            </tr>
          </thead>
          <tbody>{body}</tbody>
        </table>
      </div>
    </section>"""


def build_product_detail(runs):
    """제품별 상세 — 드롭다운으로 선택하면 해당 제품의 반복 실행 결과 표시."""
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
              <td class="t-note">{r.get('note', '')}</td>
            </tr>"""
        blocks += f"""
        <div class="detail-block{hidden}" data-product="{pid}">
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>run_id</th><th>실행 날짜</th><th>AI</th><th>최종 판정</th><th>비고</th>
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
        f"본 데이터 기준, 모아봄의 평균 판정 일관성은 {_pct(moabom['avg_consistency'])}입니다 "
        f"— 같은 제품을 반복해서 물어도 같은 구매 판정을 유지합니다."
    )
    quotes = [
        "시중 AI는 같은 제품을 반복해서 물으면 판정이 흔들립니다(일관성↓).",
        "모아봄은 한 번 수집한 같은 댓글·자막 위에서 판정하므로, 반복해도 "
        "동일한 결론을 유지합니다(일관성↑).",
        "따라서 본 비교는 '어떤 AI가 더 똑똑한가'가 아니라, '같은 질문에 몇 번을 "
        "물어도 같은 판정을 내놓는가(일관성)'를 확인하는 방식입니다.",
    ]
    lis = "".join(f"<li>{q}</li>" for q in quotes)
    return f"""
    <section class="quote-section">
      <div class="section-head"><h2>발표용 요약</h2></div>
      <p class="quote-dynamic">{dynamic}</p>
      <ul class="quote-list">{lis}</ul>
    </section>"""


# ── CSS ─────────────────────────────────────────────────────────────
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

    .card-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }}
    .card {{
      background: #fff; border: 1px solid {BORDER}; border-radius: 12px;
      padding: 18px 18px 16px; border-top: 3px solid {ACCENT};
    }}
    .card-label {{ font-size: 12px; color: {MUTED}; }}
    .card-value {{ font-size: 30px; font-weight: 700; margin: 6px 0 4px; letter-spacing: -1px; }}
    .card-sub {{ font-size: 11px; color: {MUTED}; }}

    .chart {{ background: #fff; border: 1px solid {BORDER}; border-radius: 12px; padding: 18px; }}
    .chart-wide {{ width: 100%; }}
    .bar-area {{ display: flex; align-items: flex-end; justify-content: space-around; height: 200px; gap: 10px; max-width: 560px; margin: 0 auto; }}
    .bar-col {{ display: flex; flex-direction: column; align-items: center; flex: 1; height: 100%; justify-content: flex-end; }}
    .bar-value {{ font-size: 13px; font-weight: 700; margin-bottom: 6px; }}
    .bar-track {{ width: 64px; height: 150px; display: flex; align-items: flex-end; }}
    .bar-fill {{ width: 100%; border-radius: 6px 6px 0 0; transition: height .3s; }}
    .bar-fill.bar-emph {{ box-shadow: 0 0 0 2px {ACCENT_SOFT}, 0 6px 14px rgba(15,157,138,.25); }}
    .bar-name {{ font-size: 13px; margin-top: 8px; color: {MUTED}; }}

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
    .t-note {{ color: {MUTED}; white-space: normal; min-width: 160px; }}
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
<title>모아봄 vs 시중 AI — 판정 일관성 비교</title>
<style>{build_css()}</style>
</head>
<body>
  <div class="wrap">
    <header class="page">
      <div>
        <h1>모아봄 vs 시중 AI — 판정 일관성 비교</h1>
        <p>같은 제품을 반복 질의했을 때의 구매 판정 일관성 &nbsp;·&nbsp; <strong>데이터: {source_label}</strong></p>
      </div>
      <button class="btn no-print" onclick="window.print()">PDF로 저장 / 인쇄</button>
    </header>
    {build_summary_cards(runs)}
    {build_charts(runs)}
    {build_table(runs)}
    {build_product_detail(runs)}
    {build_summary_quotes(runs)}
    <footer>
      ※ 본 비교는 '같은 질문을 반복했을 때 동일한 판정을 유지하는가(판정 일관성)'를 수치화한 것입니다.
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
