"""
OANDA FX自動売買ボット - バックテスト結果チャート表示

バックテスト結果をmatplotlibで可視化する:
  - 資産推移曲線
  - ドローダウン曲線
  - 月別損益ヒートマップ
"""

import logging

import matplotlib
matplotlib.use("Agg")  # GUIバックエンド不要の場合

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import seaborn as sns
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 日本語フォント設定（Windows環境）
plt.rcParams["font.family"] = "MS Gothic"
plt.rcParams["axes.unicode_minus"] = False


def plot_backtest_results(
    equity_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    summary: dict,
    save_path: str | None = None,
    show: bool = True,
) -> None:
    """
    バックテスト結果を3パネルのチャートで表示する。

    Args:
        equity_df: 資産推移DataFrame（equityカラム必須）。
        trades_df: トレード一覧DataFrame。
        summary: パフォーマンスサマリー辞書。
        save_path: 画像保存先パス。Noneの場合は保存しない。
        show: Trueの場合plt.show()を呼ぶ。
    """
    fig, axes = plt.subplots(3, 1, figsize=(14, 12), height_ratios=[3, 1, 2])
    fig.suptitle(
        f"バックテスト結果: {summary.get('strategy', 'N/A')}",
        fontsize=16, fontweight="bold", y=0.98,
    )

    _plot_equity_curve(axes[0], equity_df, summary)
    _plot_drawdown(axes[1], equity_df)
    _plot_monthly_pnl(axes[2], trades_df)

    plt.tight_layout(rect=[0, 0, 1, 0.95])

    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches="tight")
        logger.info("チャートを保存しました: %s", save_path)

    if show:
        plt.show()
    else:
        plt.close(fig)


def _plot_equity_curve(
    ax: plt.Axes, equity_df: pd.DataFrame, summary: dict,
) -> None:
    """資産推移曲線を描画する。"""
    ax.plot(
        equity_df.index, equity_df["equity"],
        color="#2196F3", linewidth=1.2, label="資産残高",
    )
    ax.axhline(
        y=summary.get("initial_balance", 1_000_000),
        color="#9E9E9E", linestyle="--", linewidth=0.8, label="初期資金",
    )

    ax.fill_between(
        equity_df.index, equity_df["equity"],
        summary.get("initial_balance", 1_000_000),
        where=equity_df["equity"] >= summary.get("initial_balance", 1_000_000),
        alpha=0.15, color="#4CAF50",
    )
    ax.fill_between(
        equity_df.index, equity_df["equity"],
        summary.get("initial_balance", 1_000_000),
        where=equity_df["equity"] < summary.get("initial_balance", 1_000_000),
        alpha=0.15, color="#F44336",
    )

    # サマリーテキスト
    info = (
        f"純損益: ¥{summary.get('net_profit', 0):,.0f}  "
        f"勝率: {summary.get('win_rate', 0):.1f}%  "
        f"PF: {summary.get('profit_factor', 0):.2f}  "
        f"Sharpe: {summary.get('sharpe_ratio', 0):.2f}  "
        f"最大DD: {summary.get('max_drawdown_pct', 0):.1f}%"
    )
    ax.set_title(info, fontsize=10, pad=10)
    ax.set_ylabel("資産残高（円）")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))


def _plot_drawdown(ax: plt.Axes, equity_df: pd.DataFrame) -> None:
    """ドローダウン曲線を描画する。"""
    equity = equity_df["equity"]
    peak = equity.cummax()
    drawdown_pct = (equity - peak) / peak * 100

    ax.fill_between(
        equity_df.index, drawdown_pct, 0,
        color="#F44336", alpha=0.4,
    )
    ax.plot(
        equity_df.index, drawdown_pct,
        color="#D32F2F", linewidth=0.8,
    )

    ax.set_ylabel("ドローダウン（%）")
    ax.set_ylim(drawdown_pct.min() * 1.2 if drawdown_pct.min() < 0 else -1, 0.5)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))


def _plot_monthly_pnl(ax: plt.Axes, trades_df: pd.DataFrame) -> None:
    """月別損益ヒートマップを描画する。"""
    if trades_df.empty or "exit_time" not in trades_df.columns:
        ax.text(0.5, 0.5, "トレードデータなし", ha="center", va="center")
        return

    df = trades_df.copy()
    df["exit_time"] = pd.to_datetime(df["exit_time"])
    df["year"] = df["exit_time"].dt.year
    df["month"] = df["exit_time"].dt.month

    monthly = df.groupby(["year", "month"])["profit_loss"].sum().reset_index()
    pivot = monthly.pivot(index="year", columns="month", values="profit_loss")

    # 全月のカラムを確保（1-12月）
    for m in range(1, 13):
        if m not in pivot.columns:
            pivot[m] = np.nan
    pivot = pivot.reindex(columns=range(1, 13))
    pivot.columns = [f"{m}月" for m in range(1, 13)]

    sns.heatmap(
        pivot, ax=ax, annot=True, fmt=",.0f", cmap="RdYlGn",
        center=0, linewidths=0.5, cbar_kws={"label": "損益（円）"},
    )
    ax.set_title("月別損益", fontsize=12)
    ax.set_ylabel("年")
