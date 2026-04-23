"""
OANDA FX自動売買ボット - パラメータ最適化モジュール

グリッドサーチによるパラメータ探索とウォークフォワード分析を提供する。

機能:
  - グリッドサーチ: 全パラメータ組み合わせのバックテスト実行
  - 最適化対象: Sharpe Ratio / プロフィットファクター
  - ヒートマップ可視化
  - ウォークフォワード分析: 学習→検証を移動窓で反復
  - 過剰最適化の警告検出
"""

import logging
import itertools
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from backtest.engine import Backtester, BacktestConfig

logger = logging.getLogger(__name__)
JST = timezone(timedelta(hours=9))

# 最適化対象メトリクス
METRIC_SHARPE = "sharpe_ratio"
METRIC_PF = "profit_factor"
METRIC_NET = "net_profit"
METRIC_WINRATE = "win_rate"


@dataclass
class OptimizationResult:
    """最適化結果。"""
    best_params: dict[str, Any]
    best_score: float
    metric: str
    all_results: pd.DataFrame
    overfitting_warning: bool = False
    overfitting_details: str = ""


@dataclass
class WalkForwardResult:
    """ウォークフォワード分析結果。"""
    windows: list[dict[str, Any]]
    aggregate_score: float
    metric: str
    consistency_ratio: float  # 検証期間で利益を出したウィンドウの割合
    overfitting_warning: bool = False


# ═══════════════════════════════════════════════════════════
#  戦略ファクトリ（パラメータ付き）
# ═══════════════════════════════════════════════════════════

def _create_strategy_with_params(strategy_name: str, params: dict[str, Any]):
    """パラメータ指定で戦略インスタンスを生成する。"""
    if strategy_name == "sma_cross":
        from strategy.sma_cross import SmaCrossStrategy
        return SmaCrossStrategy(**params)
    elif strategy_name == "momentum":
        from strategy.momentum import MomentumStrategy
        return MomentumStrategy(**params)
    elif strategy_name == "breakout":
        from strategy.breakout import BreakoutStrategy
        return BreakoutStrategy(**params)
    else:
        raise ValueError(f"不明な戦略: {strategy_name}")


# 各戦略のデフォルト探索グリッド
DEFAULT_GRIDS: dict[str, dict[str, list]] = {
    "sma_cross": {
        "short_period": [5, 10, 15, 20, 25],
        "long_period": [30, 40, 50, 60, 80],
    },
    "momentum": {
        "rsi_period": [7, 10, 14, 21],
        "ema_short": [10, 15, 20, 25],
        "ema_long": [40, 50, 60, 80],
    },
    "breakout": {
        "lookback_period": [10, 15, 20, 30, 40],
        "atr_multiplier": [1.5, 2.0, 2.5, 3.0],
    },
}


# ═══════════════════════════════════════════════════════════
#  グリッドサーチ
# ═══════════════════════════════════════════════════════════

class GridSearchOptimizer:
    """
    グリッドサーチによるパラメータ最適化。

    全パラメータ組み合わせでバックテストを実行し、
    指定メトリクスが最大となるパラメータを特定する。
    """

    def __init__(
        self,
        strategy_name: str,
        param_grid: dict[str, list] | None = None,
        metric: str = METRIC_SHARPE,
        backtest_config: BacktestConfig | None = None,
    ) -> None:
        """
        最適化エンジンを初期化する。

        Args:
            strategy_name: 戦略名。
            param_grid: パラメータグリッド。Noneならデフォルト使用。
            metric: 最適化対象メトリクス。
            backtest_config: バックテスト設定。
        """
        self.strategy_name = strategy_name
        self.param_grid = param_grid or DEFAULT_GRIDS.get(strategy_name, {})
        self.metric = metric
        self.bt_config = backtest_config or BacktestConfig()

        # 組み合わせ数を計算
        keys = list(self.param_grid.keys())
        values = list(self.param_grid.values())
        self.combinations = list(itertools.product(*values))
        self.param_keys = keys

        logger.info(
            "GridSearch初期化: 戦略=%s, メトリクス=%s, 組み合わせ=%d",
            strategy_name, metric, len(self.combinations),
        )

    def run(self, df: pd.DataFrame) -> OptimizationResult:
        """
        グリッドサーチを実行する。

        Args:
            df: ローソク足データ。

        Returns:
            最適化結果。
        """
        total = len(self.combinations)
        results = []

        print(f"\n  ⚙️  グリッドサーチ開始: {total}パターン")

        for i, combo in enumerate(self.combinations):
            params = dict(zip(self.param_keys, combo))

            # 無効な組み合わせをスキップ
            if not self._validate_params(params):
                continue

            try:
                strategy = _create_strategy_with_params(
                    self.strategy_name, params,
                )
                bt = Backtester(strategy=strategy, config=self.bt_config)
                summary = bt.run(df)

                score = summary.get(self.metric, 0)
                if isinstance(score, str):
                    score = 0

                result_row = {**params, **summary, "_score": score}
                results.append(result_row)

                # 進捗表示（10%刻み）
                progress = (i + 1) / total * 100
                if (i + 1) % max(1, total // 10) == 0:
                    print(f"    [{progress:5.1f}%] {i+1}/{total}")

            except Exception as e:
                logger.debug("パラメータ %s でエラー: %s", params, e)

        if not results:
            print("  ❌ 有効な結果がありませんでした")
            return OptimizationResult(
                best_params={}, best_score=0, metric=self.metric,
                all_results=pd.DataFrame(),
            )

        results_df = pd.DataFrame(results)
        best_idx = results_df["_score"].idxmax()
        best_row = results_df.loc[best_idx]
        best_params = {k: best_row[k] for k in self.param_keys}
        best_score = best_row["_score"]

        # 過剰最適化チェック
        warning, details = self._check_overfitting(results_df)

        print(f"\n  ✅ 最適パラメータ: {best_params}")
        print(f"     {self.metric} = {best_score:.4f}")
        if warning:
            print(f"  ⚠️  {details}")

        return OptimizationResult(
            best_params=best_params,
            best_score=best_score,
            metric=self.metric,
            all_results=results_df,
            overfitting_warning=warning,
            overfitting_details=details,
        )

    def _validate_params(self, params: dict) -> bool:
        """パラメータの組み合わせが有効か検証する。"""
        # SMA: short < long
        if "short_period" in params and "long_period" in params:
            if params["short_period"] >= params["long_period"]:
                return False
        # Momentum: ema_short < ema_long
        if "ema_short" in params and "ema_long" in params:
            if params["ema_short"] >= params["ema_long"]:
                return False
        return True

    def _check_overfitting(
        self, results_df: pd.DataFrame,
    ) -> tuple[bool, str]:
        """過剰最適化の兆候をチェックする。"""
        scores = results_df["_score"].dropna()
        if len(scores) < 5:
            return False, ""

        warnings = []

        # 1. 最良と平均の乖離が大きすぎる
        best = scores.max()
        mean = scores.mean()
        std = scores.std()
        if std > 0 and (best - mean) / std > 3.0:
            warnings.append(
                f"最良値が平均から3σ以上乖離 (best={best:.3f}, "
                f"mean={mean:.3f}, std={std:.3f})"
            )

        # 2. 上位パラメータのばらつきが大きい
        top_n = max(3, len(scores) // 10)
        top = scores.nlargest(top_n)
        bottom = scores.nsmallest(top_n)
        if len(top) > 1 and top.std() > mean * 0.5:
            warnings.append("上位パラメータ間のスコアばらつきが大きい")

        # 3. 正のスコアが少数
        positive = (scores > 0).sum()
        if positive < len(scores) * 0.3:
            warnings.append(
                f"正のスコアが全体の{positive/len(scores)*100:.0f}%のみ"
            )

        if warnings:
            return True, "過剰最適化の可能性: " + "; ".join(warnings)
        return False, ""


# ═══════════════════════════════════════════════════════════
#  ウォークフォワード分析
# ═══════════════════════════════════════════════════════════

class WalkForwardAnalyzer:
    """
    ウォークフォワード分析。

    データを学習期間と検証期間に分割し、学習期間で最適化した
    パラメータを検証期間で評価する。これを移動窓で反復する。
    """

    def __init__(
        self,
        strategy_name: str,
        param_grid: dict[str, list] | None = None,
        metric: str = METRIC_SHARPE,
        train_ratio: float = 0.7,
        n_windows: int = 5,
        backtest_config: BacktestConfig | None = None,
    ) -> None:
        """
        ウォークフォワード分析を初期化する。

        Args:
            strategy_name: 戦略名。
            param_grid: パラメータグリッド。
            metric: 最適化対象メトリクス。
            train_ratio: 各ウィンドウの学習期間比率。
            n_windows: 分析ウィンドウ数。
            backtest_config: バックテスト設定。
        """
        self.strategy_name = strategy_name
        self.param_grid = param_grid or DEFAULT_GRIDS.get(strategy_name, {})
        self.metric = metric
        self.train_ratio = train_ratio
        self.n_windows = n_windows
        self.bt_config = backtest_config or BacktestConfig()

    def run(self, df: pd.DataFrame) -> WalkForwardResult:
        """
        ウォークフォワード分析を実行する。

        Args:
            df: 全期間のローソク足データ。

        Returns:
            ウォークフォワード分析結果。
        """
        total_len = len(df)
        window_size = total_len // self.n_windows
        train_size = int(window_size * self.train_ratio)
        test_size = window_size - train_size

        if train_size < 50 or test_size < 20:
            logger.warning("データ不足: window=%d, train=%d, test=%d",
                          window_size, train_size, test_size)

        print(f"\n  📐 ウォークフォワード分析")
        print(f"     ウィンドウ数={self.n_windows}, "
              f"学習比率={self.train_ratio:.0%}")
        print(f"     学習={train_size}本, 検証={test_size}本\n")

        windows = []

        for w in range(self.n_windows):
            start = w * window_size
            train_end = start + train_size
            test_end = min(start + window_size, total_len)

            train_df = df.iloc[start:train_end].copy()
            test_df = df.iloc[train_end:test_end].copy()

            if len(train_df) < 30 or len(test_df) < 10:
                continue

            train_start_dt = str(train_df.index[0])[:10]
            test_end_dt = str(test_df.index[-1])[:10]

            # 学習期間で最適化
            optimizer = GridSearchOptimizer(
                strategy_name=self.strategy_name,
                param_grid=self.param_grid,
                metric=self.metric,
                backtest_config=self.bt_config,
            )

            # 進捗表示を抑制
            import io, sys
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            opt_result = optimizer.run(train_df)
            sys.stdout = old_stdout

            if not opt_result.best_params:
                continue

            # 検証期間でテスト
            strategy = _create_strategy_with_params(
                self.strategy_name, opt_result.best_params,
            )
            bt = Backtester(strategy=strategy, config=self.bt_config)
            test_summary = bt.run(test_df)
            test_score = test_summary.get(self.metric, 0)
            if isinstance(test_score, str):
                test_score = 0

            window_info = {
                "window": w + 1,
                "train_period": f"{train_start_dt} ~ {str(train_df.index[-1])[:10]}",
                "test_period": f"{str(test_df.index[0])[:10]} ~ {test_end_dt}",
                "best_params": opt_result.best_params,
                "train_score": opt_result.best_score,
                "test_score": test_score,
                "train_trades": opt_result.all_results.iloc[
                    opt_result.all_results["_score"].idxmax()
                ].get("total_trades", 0) if not opt_result.all_results.empty else 0,
                "test_trades": test_summary.get("total_trades", 0),
                "test_net_profit": test_summary.get("net_profit", 0),
            }
            windows.append(window_info)

            # ウィンドウ結果表示
            train_s = window_info["train_score"]
            test_s = window_info["test_score"]
            marker = "✅" if test_score > 0 else "❌"
            print(f"  {marker} W{w+1}: 学習{self.metric}={train_s:.3f} "
                  f"→ 検証={test_s:.3f} | {opt_result.best_params}")

        if not windows:
            return WalkForwardResult(
                windows=[], aggregate_score=0, metric=self.metric,
                consistency_ratio=0, overfitting_warning=True,
            )

        # 集計
        test_scores = [w["test_score"] for w in windows]
        profitable = sum(1 for s in test_scores if s > 0)
        consistency = profitable / len(windows)
        avg_score = np.mean(test_scores)

        # 過剰最適化チェック
        train_scores = [w["train_score"] for w in windows]
        avg_train = np.mean(train_scores)
        avg_test = np.mean(test_scores)
        overfit = avg_test < avg_train * 0.3 if avg_train > 0 else False

        print(f"\n  ── ウォークフォワード集計 ──")
        print(f"     平均学習{self.metric}: {avg_train:.4f}")
        print(f"     平均検証{self.metric}: {avg_test:.4f}")
        print(f"     一貫性: {profitable}/{len(windows)} ({consistency:.0%})")
        if overfit:
            print(f"  ⚠️  過剰最適化の疑い: 検証スコアが学習の30%未満")

        return WalkForwardResult(
            windows=windows,
            aggregate_score=avg_score,
            metric=self.metric,
            consistency_ratio=consistency,
            overfitting_warning=overfit,
        )


# ═══════════════════════════════════════════════════════════
#  可視化
# ═══════════════════════════════════════════════════════════

def plot_optimization_heatmap(
    result: OptimizationResult,
    param_x: str,
    param_y: str,
    save_path: str | None = None,
) -> str:
    """
    グリッドサーチ結果をヒートマップHTMLとして出力する。

    Args:
        result: 最適化結果。
        param_x: X軸パラメータ名。
        param_y: Y軸パラメータ名。
        save_path: 保存先パス。Noneの場合はデフォルト。

    Returns:
        保存先パス。
    """
    df = result.all_results
    if df.empty or param_x not in df.columns or param_y not in df.columns:
        logger.warning("ヒートマップ生成不可: データまたはパラメータが不足")
        return ""

    pivot = df.pivot_table(
        index=param_y, columns=param_x,
        values="_score", aggfunc="mean",
    )

    text_vals = [[f"{v:.3f}" if not np.isnan(v) else ""
                  for v in row] for row in pivot.values]

    fig = go.Figure(data=go.Heatmap(
        z=pivot.values,
        x=[str(x) for x in pivot.columns],
        y=[str(y) for y in pivot.index],
        colorscale=[[0, "#ef5350"], [0.5, "#1a1d29"], [1, "#66bb6a"]],
        zmid=0,
        text=text_vals,
        texttemplate="%{text}",
        hovertemplate=f"{param_x}=%{{x}}, {param_y}=%{{y}}<br>"
                      f"{result.metric}=%{{z:.4f}}<extra></extra>",
        colorbar=dict(title=result.metric),
    ))

    # 最良パラメータをマーク
    bx = str(result.best_params.get(param_x, ""))
    by = str(result.best_params.get(param_y, ""))
    fig.add_trace(go.Scatter(
        x=[bx], y=[by], mode="markers",
        marker=dict(size=18, color="gold", symbol="star",
                    line=dict(width=2, color="white")),
        name="最適値", showlegend=True,
    ))

    fig.update_layout(
        title=f"パラメータ最適化 — {result.metric}",
        xaxis_title=param_x, yaxis_title=param_y,
        paper_bgcolor="#0f1117", plot_bgcolor="#1a1d29",
        font=dict(color="#e4e6eb"),
        height=500,
    )

    if save_path is None:
        save_path = f"data/results/opt_{param_x}_{param_y}.html"

    path = Path(save_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(path))
    logger.info("ヒートマップを保存: %s", path)
    return str(path)


def plot_walk_forward(
    wf_result: WalkForwardResult,
    save_path: str | None = None,
) -> str:
    """
    ウォークフォワード結果をHTMLチャートで出力する。

    Args:
        wf_result: WalkForwardResult。
        save_path: 保存先パス。

    Returns:
        保存先パス。
    """
    if not wf_result.windows:
        return ""

    windows = wf_result.windows
    labels = [f"W{w['window']}" for w in windows]
    train_scores = [w["train_score"] for w in windows]
    test_scores = [w["test_score"] for w in windows]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=labels, y=train_scores, name="学習",
        marker_color="#4fc3f7", opacity=0.7,
    ))
    fig.add_trace(go.Bar(
        x=labels, y=test_scores, name="検証",
        marker_color=[
            "#66bb6a" if s > 0 else "#ef5350" for s in test_scores
        ],
    ))

    fig.update_layout(
        title=f"ウォークフォワード分析 — {wf_result.metric}",
        xaxis_title="ウィンドウ", yaxis_title=wf_result.metric,
        barmode="group",
        paper_bgcolor="#0f1117", plot_bgcolor="#1a1d29",
        font=dict(color="#e4e6eb"),
        height=400,
        annotations=[dict(
            text=f"一貫性: {wf_result.consistency_ratio:.0%} | "
                 f"平均検証: {wf_result.aggregate_score:.4f}",
            xref="paper", yref="paper", x=0.5, y=1.08,
            showarrow=False, font=dict(size=13, color="#8b8fa3"),
        )],
    )

    if save_path is None:
        save_path = "data/results/walk_forward.html"

    path = Path(save_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(path))
    logger.info("ウォークフォワードチャートを保存: %s", path)
    return str(path)
