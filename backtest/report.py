"""
OANDA FX自動売買ボット - バックテストHTMLレポート生成

Plotlyを使ったインタラクティブなHTMLレポートを生成する。
生成されるHTMLは単一ファイルで完結し、外部依存がない。

含まれるチャート:
  - 資産推移曲線（含み損益帯域付き）
  - ドローダウン曲線
  - 月別損益ヒートマップ
  - 勝敗分布（円グラフ + ヒストグラム）
  - トレード一覧テーブル（ソート可能）
"""

import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go

logger = logging.getLogger(__name__)
JST = timezone(timedelta(hours=9))


def generate_html_report(
    equity_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    summary: dict[str, Any],
    save_path: str,
) -> str:
    """
    バックテスト結果をHTMLレポートとして生成・保存する。

    Args:
        equity_df: 資産推移DataFrame（equity, balance, unrealized_plカラム必須）。
        trades_df: トレード一覧DataFrame。
        summary: パフォーマンスサマリー辞書。
        save_path: HTML保存先パス。

    Returns:
        保存先の絶対パス。
    """
    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST")
    strategy_name = summary.get("strategy", "N/A")

    # 各セクションのHTMLを生成
    summary_html = _build_summary_cards(summary)
    equity_chart = _build_equity_chart(equity_df, summary)
    drawdown_chart = _build_drawdown_chart(equity_df)
    monthly_chart = _build_monthly_heatmap(trades_df)
    distribution_chart = _build_distribution_charts(trades_df)
    trades_table = _build_trades_table(trades_df)

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>バックテストレポート - {strategy_name}</title>
<style>
  :root {{
    --bg-primary: #0f1117;
    --bg-card: #1a1d29;
    --bg-hover: #252836;
    --text-primary: #e4e6eb;
    --text-secondary: #8b8fa3;
    --accent-blue: #4fc3f7;
    --accent-green: #66bb6a;
    --accent-red: #ef5350;
    --accent-amber: #ffca28;
    --border: #2d3040;
    --radius: 12px;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'Segoe UI', 'Hiragino Sans', 'Meiryo', sans-serif;
    background: var(--bg-primary);
    color: var(--text-primary);
    line-height: 1.6;
    padding: 24px;
  }}
  .header {{
    text-align: center;
    padding: 32px 0 24px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 32px;
  }}
  .header h1 {{
    font-size: 28px;
    font-weight: 700;
    background: linear-gradient(135deg, var(--accent-blue), #7c4dff);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 8px;
  }}
  .header .meta {{
    color: var(--text-secondary);
    font-size: 14px;
  }}
  .summary-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 16px;
    margin-bottom: 32px;
  }}
  .card {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 20px;
    transition: transform 0.2s, box-shadow 0.2s;
  }}
  .card:hover {{
    transform: translateY(-2px);
    box-shadow: 0 8px 25px rgba(0,0,0,0.3);
  }}
  .card .label {{
    font-size: 12px;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 6px;
  }}
  .card .value {{
    font-size: 22px;
    font-weight: 700;
  }}
  .card .value.positive {{ color: var(--accent-green); }}
  .card .value.negative {{ color: var(--accent-red); }}
  .card .value.neutral {{ color: var(--accent-blue); }}
  .section {{
    margin-bottom: 32px;
  }}
  .section-title {{
    font-size: 18px;
    font-weight: 600;
    margin-bottom: 16px;
    padding-left: 12px;
    border-left: 3px solid var(--accent-blue);
  }}
  .chart-container {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 16px;
    margin-bottom: 24px;
    overflow-x: auto;
  }}
  .charts-row {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 24px;
  }}
  @media (max-width: 900px) {{
    .charts-row {{ grid-template-columns: 1fr; }}
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }}
  th {{
    background: var(--bg-hover);
    color: var(--text-secondary);
    font-weight: 600;
    text-align: left;
    padding: 10px 12px;
    border-bottom: 2px solid var(--border);
    cursor: pointer;
    user-select: none;
    position: sticky;
    top: 0;
  }}
  th:hover {{ color: var(--accent-blue); }}
  th::after {{ content: ' ⇅'; opacity: 0.3; }}
  td {{
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
  }}
  tr:hover td {{ background: var(--bg-hover); }}
  .filter-bar {{
    display: flex;
    gap: 12px;
    margin-bottom: 12px;
    flex-wrap: wrap;
  }}
  .filter-bar input, .filter-bar select {{
    background: var(--bg-hover);
    border: 1px solid var(--border);
    color: var(--text-primary);
    padding: 8px 12px;
    border-radius: 8px;
    font-size: 13px;
    outline: none;
  }}
  .filter-bar input:focus, .filter-bar select:focus {{
    border-color: var(--accent-blue);
  }}
  .pl-positive {{ color: var(--accent-green); }}
  .pl-negative {{ color: var(--accent-red); }}
  .footer {{
    text-align: center;
    padding: 24px 0;
    color: var(--text-secondary);
    font-size: 12px;
    border-top: 1px solid var(--border);
    margin-top: 32px;
  }}
</style>
</head>
<body>

<div class="header">
  <h1>📊 バックテストレポート</h1>
  <div class="meta">戦略: {strategy_name} | 生成: {now_str}</div>
</div>

{summary_html}

<div class="section">
  <h2 class="section-title">資産推移</h2>
  <div class="chart-container">{equity_chart}</div>
</div>

<div class="section">
  <h2 class="section-title">ドローダウン</h2>
  <div class="chart-container">{drawdown_chart}</div>
</div>

<div class="section charts-row">
  <div>
    <h2 class="section-title">月別損益</h2>
    <div class="chart-container">{monthly_chart}</div>
  </div>
  <div>
    <h2 class="section-title">勝敗分布</h2>
    <div class="chart-container">{distribution_chart}</div>
  </div>
</div>

<div class="section">
  <h2 class="section-title">トレード一覧</h2>
  <div class="chart-container">{trades_table}</div>
</div>

<div class="footer">
  OANDA FX 自動売買ボット — バックテストレポート
</div>

</body>
</html>"""

    # 保存
    path = Path(save_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")

    logger.info("HTMLレポートを保存しました: %s", path.absolute())
    return str(path.absolute())


# ═══════════════════════════════════════════════════════════
#  サマリーカード
# ═══════════════════════════════════════════════════════════

def _build_summary_cards(summary: dict[str, Any]) -> str:
    """パフォーマンスサマリーのカードグリッドを生成する。"""
    net = summary.get("net_profit", 0)
    net_class = "positive" if net >= 0 else "negative"
    roi = summary.get("roi_pct", 0)
    roi_class = "positive" if roi >= 0 else "negative"
    pf = summary.get("profit_factor", 0)
    pf_class = "positive" if pf >= 1 else "negative"

    cards = [
        ("純損益", f"¥{net:,.0f}", net_class),
        ("ROI", f"{roi:+.2f}%", roi_class),
        ("トレード数", str(summary.get("total_trades", 0)), "neutral"),
        ("勝率", f"{summary.get('win_rate', 0):.1f}%", "neutral"),
        ("PF", f"{pf:.2f}", pf_class),
        ("最大DD", f"{summary.get('max_drawdown_pct', 0):.1f}%", "negative"),
        ("Sharpe", f"{summary.get('sharpe_ratio', 0):.2f}", "neutral"),
        ("合計Pips", f"{summary.get('total_pips', 0):+.1f}", net_class),
        ("平均利益", f"¥{summary.get('avg_win', 0):,.0f}", "positive"),
        ("平均損失", f"¥{summary.get('avg_loss', 0):,.0f}", "negative"),
    ]

    items = ""
    for label, value, cls in cards:
        items += f"""<div class="card">
  <div class="label">{label}</div>
  <div class="value {cls}">{value}</div>
</div>\n"""

    return f'<div class="summary-grid">{items}</div>'


# ═══════════════════════════════════════════════════════════
#  Plotlyチャート（HTMLインライン）
# ═══════════════════════════════════════════════════════════

_PLOTLY_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(26,29,41,1)",
    font=dict(color="#e4e6eb", family="Segoe UI, sans-serif"),
    margin=dict(l=50, r=20, t=40, b=40),
    xaxis=dict(gridcolor="#2d3040", showgrid=True),
    yaxis=dict(gridcolor="#2d3040", showgrid=True),
    hovermode="x unified",
)


def _fig_to_html(fig: go.Figure, height: int = 400) -> str:
    """Plotly FigureをインラインHTML文字列に変換する。"""
    fig.update_layout(**_PLOTLY_LAYOUT, height=height)
    return fig.to_html(
        full_html=False, include_plotlyjs="cdn",
        config={"displayModeBar": True, "locale": "ja"},
    )


def _build_equity_chart(
    equity_df: pd.DataFrame, summary: dict[str, Any],
) -> str:
    """資産推移チャートを生成する。"""
    if equity_df.empty:
        return "<p>データなし</p>"

    fig = go.Figure()
    initial = summary.get("initial_balance", 1_000_000)

    fig.add_trace(go.Scatter(
        x=equity_df.index, y=equity_df["equity"],
        name="資産残高", mode="lines",
        line=dict(color="#4fc3f7", width=2),
        fill="tonexty" if "balance" in equity_df.columns else None,
    ))

    if "balance" in equity_df.columns:
        fig.add_trace(go.Scatter(
            x=equity_df.index, y=equity_df["balance"],
            name="確定残高", mode="lines",
            line=dict(color="#7c4dff", width=1, dash="dot"),
        ))

    fig.add_hline(
        y=initial, line_dash="dash",
        line_color="#8b8fa3", annotation_text="初期資金",
    )
    fig.update_layout(title="資産推移", yaxis_title="資産（円）")
    return _fig_to_html(fig, 420)


def _build_drawdown_chart(equity_df: pd.DataFrame) -> str:
    """ドローダウンチャートを生成する。"""
    if equity_df.empty:
        return "<p>データなし</p>"

    equity = equity_df["equity"]
    peak = equity.cummax()
    dd_pct = (equity - peak) / peak * 100

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=equity_df.index, y=dd_pct,
        name="ドローダウン", mode="lines",
        line=dict(color="#ef5350", width=1.5),
        fill="tozeroy",
        fillcolor="rgba(239,83,80,0.2)",
    ))
    fig.update_layout(
        title="ドローダウン",
        yaxis_title="DD（%）",
        yaxis_range=[dd_pct.min() * 1.2 if dd_pct.min() < 0 else -1, 0.5],
    )
    return _fig_to_html(fig, 280)


def _build_monthly_heatmap(trades_df: pd.DataFrame) -> str:
    """月別損益ヒートマップを生成する。"""
    if trades_df.empty or "exit_time" not in trades_df.columns:
        return "<p>データなし</p>"

    df = trades_df.copy()
    df["exit_time"] = pd.to_datetime(df["exit_time"])
    df["year"] = df["exit_time"].dt.year
    df["month"] = df["exit_time"].dt.month

    monthly = df.groupby(["year", "month"])["profit_loss"].sum().reset_index()
    pivot = monthly.pivot(index="year", columns="month", values="profit_loss")

    for m in range(1, 13):
        if m not in pivot.columns:
            pivot[m] = np.nan
    pivot = pivot.reindex(columns=range(1, 13))

    month_labels = [f"{m}月" for m in range(1, 13)]

    fig = go.Figure(data=go.Heatmap(
        z=pivot.values,
        x=month_labels,
        y=[str(y) for y in pivot.index],
        colorscale=[
            [0, "#ef5350"], [0.5, "#1a1d29"], [1, "#66bb6a"],
        ],
        zmid=0,
        text=[[
            f"¥{v:,.0f}" if not np.isnan(v) else "" for v in row
        ] for row in pivot.values],
        texttemplate="%{text}",
        textfont=dict(size=11),
        hovertemplate="%{y} %{x}: %{z:,.0f}円<extra></extra>",
        colorbar=dict(title="損益（円）"),
    ))
    fig.update_layout(title="月別損益", yaxis_autorange="reversed")
    return _fig_to_html(fig, 300)


def _build_distribution_charts(trades_df: pd.DataFrame) -> str:
    """勝敗分布チャートを生成する（円グラフ+ヒストグラム）。"""
    if trades_df.empty:
        return "<p>データなし</p>"

    wins = len(trades_df[trades_df["profit_loss"] > 0])
    losses = len(trades_df[trades_df["profit_loss"] <= 0])

    # 円グラフ
    fig_pie = go.Figure(data=go.Pie(
        labels=["勝ち", "負け"],
        values=[wins, losses],
        marker=dict(colors=["#66bb6a", "#ef5350"]),
    ))
    fig_pie.update_layout(title="勝敗比率", showlegend=True)
    pie_html = _fig_to_html(fig_pie, 260)

    # ヒストグラム
    fig_hist = go.Figure(data=go.Histogram(
        x=trades_df["profit_loss"],
        nbinsx=30,
        marker_color="#4fc3f7",
        opacity=0.8,
        name="損益分布",
    ))
    fig_hist.add_vline(x=0, line_dash="dash", line_color="#8b8fa3")
    fig_hist.update_layout(title="損益分布", xaxis_title="損益（円）", yaxis_title="回数")
    hist_html = _fig_to_html(fig_hist, 260)

    return pie_html + hist_html


# ═══════════════════════════════════════════════════════════
#  トレードテーブル（JS付きソート・フィルター）
# ═══════════════════════════════════════════════════════════

def _build_trades_table(trades_df: pd.DataFrame) -> str:
    """ソート・フィルター可能なトレード一覧テーブルを生成する。"""
    if trades_df.empty:
        return "<p>トレードデータなし</p>"

    table_id = "tradesTable"

    # フィルターバー
    html = f"""
<div class="filter-bar">
  <input type="text" id="searchInput" placeholder="🔍 検索..."
    onkeyup="filterTable()">
  <select id="dirFilter" onchange="filterTable()">
    <option value="">全方向</option>
    <option value="LONG">LONG</option>
    <option value="SHORT">SHORT</option>
  </select>
  <select id="resultFilter" onchange="filterTable()">
    <option value="">全結果</option>
    <option value="win">勝ち</option>
    <option value="loss">負け</option>
  </select>
</div>
<div style="max-height:500px; overflow-y:auto;">
<table id="{table_id}">
<thead><tr>
  <th onclick="sortTable(0)">#</th>
  <th onclick="sortTable(1)">エントリー</th>
  <th onclick="sortTable(2)">エグジット</th>
  <th onclick="sortTable(3)">方向</th>
  <th onclick="sortTable(4)">数量</th>
  <th onclick="sortTable(5)">入口</th>
  <th onclick="sortTable(6)">出口</th>
  <th onclick="sortTable(7)">Pips</th>
  <th onclick="sortTable(8)">損益</th>
  <th onclick="sortTable(9)">理由</th>
</tr></thead>
<tbody>"""

    for i, row in trades_df.iterrows():
        pl = row.get("profit_loss", 0)
        pl_class = "pl-positive" if pl > 0 else "pl-negative" if pl < 0 else ""
        pips = row.get("pips", 0)
        entry_t = str(row.get("entry_time", ""))[:16]
        exit_t = str(row.get("exit_time", ""))[:16]
        direction = row.get("direction", "")
        units = row.get("units", 0)
        entry_p = row.get("entry_price", 0)
        exit_p = row.get("exit_price", 0)
        reason = row.get("exit_reason", "")

        html += f"""<tr>
  <td>{i + 1 if isinstance(i, int) else i}</td>
  <td>{entry_t}</td>
  <td>{exit_t}</td>
  <td>{direction}</td>
  <td>{units:,}</td>
  <td>{entry_p:.3f}</td>
  <td>{exit_p:.3f}</td>
  <td class="{pl_class}">{pips:+.1f}</td>
  <td class="{pl_class}">¥{pl:+,.0f}</td>
  <td>{reason}</td>
</tr>"""

    html += """</tbody></table></div>

<script>
let sortDir = {};
function sortTable(col) {
  const table = document.getElementById('tradesTable');
  const tbody = table.tBodies[0];
  const rows = Array.from(tbody.rows);
  sortDir[col] = !sortDir[col];
  rows.sort((a, b) => {
    let va = a.cells[col].textContent.replace(/[¥,+]/g, '');
    let vb = b.cells[col].textContent.replace(/[¥,+]/g, '');
    let na = parseFloat(va), nb = parseFloat(vb);
    if (!isNaN(na) && !isNaN(nb)) return sortDir[col] ? na - nb : nb - na;
    return sortDir[col] ? va.localeCompare(vb) : vb.localeCompare(va);
  });
  rows.forEach(r => tbody.appendChild(r));
}
function filterTable() {
  const search = document.getElementById('searchInput').value.toLowerCase();
  const dirF = document.getElementById('dirFilter').value;
  const resF = document.getElementById('resultFilter').value;
  const rows = document.getElementById('tradesTable').tBodies[0].rows;
  for (let r of rows) {
    const text = r.textContent.toLowerCase();
    const dir = r.cells[3].textContent;
    const pl = parseFloat(r.cells[8].textContent.replace(/[¥,+]/g, ''));
    let show = text.includes(search);
    if (dirF && dir !== dirF) show = false;
    if (resF === 'win' && pl <= 0) show = false;
    if (resF === 'loss' && pl > 0) show = false;
    r.style.display = show ? '' : 'none';
  }
}
</script>"""

    return html
