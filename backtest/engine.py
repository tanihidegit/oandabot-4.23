"""
OANDA FX自動売買ボット - バックテストエンジン

ローソク足データと売買戦略を受け取り、売買シミュレーションを実行する。
ポジション管理、利確・損切判定、スプレッド考慮、パフォーマンス集計を行う。
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from strategy.base import BaseStrategy, Signal

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    """個別トレードの記録。"""
    entry_time: datetime
    exit_time: datetime | None = None
    direction: str = "LONG"       # "LONG" or "SHORT"
    entry_price: float = 0.0
    exit_price: float = 0.0
    units: int = 1000
    pips_result: float = 0.0
    profit_loss: float = 0.0      # 金額（円）
    tp_price: float | None = None # 利確価格
    sl_price: float | None = None # 損切価格
    exit_reason: str = ""         # "signal", "take_profit", "stop_loss"


@dataclass
class BacktestConfig:
    """バックテスト設定パラメータ。"""
    initial_balance: float = 1_000_000.0   # 初期資金（円）
    units: int = 1000                       # 1トレードあたりの取引数量
    spread_pips: float = 0.3                # スプレッド（pips）
    take_profit_pips: float | None = None   # 利確幅（pips）。Noneで無効。
    stop_loss_pips: float | None = None     # 損切幅（pips）。Noneで無効。
    max_positions: int = 2                  # 最大同時保有ポジション数
    pip_value: float = 0.01                 # 1pipの値（USD_JPYなら0.01）
    risk_pct: float = 0.02                  # 1トレードあたりのリスク上限（口座残高の2%）


class Backtester:
    """
    バックテストエンジン。

    DataFrameとBaseStrategy実装を受け取り、ローソク足を1本ずつ処理して
    売買シミュレーションを行う。

    Attributes:
        strategy: 売買戦略。
        config: バックテスト設定。
        trades: 完了したトレードのリスト。
        open_positions: オープン中のポジション。
        equity_curve: 資産推移リスト。
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        config: BacktestConfig | None = None,
    ) -> None:
        """
        バックテストエンジンを初期化する。

        Args:
            strategy: 使用する売買戦略。
            config: バックテスト設定。Noneの場合はデフォルト値を使用。
        """
        self.strategy = strategy
        self.config = config or BacktestConfig()
        self.trades: list[Trade] = []
        self.open_positions: list[Trade] = []
        self.equity_curve: list[dict[str, Any]] = []

        logger.info(
            "バックテストエンジンを初期化: 戦略=%s, 初期資金=%.0f, "
            "TP=%.1f pips, SL=%.1f pips, スプレッド=%.1f pips",
            self.strategy.name, self.config.initial_balance,
            self.config.take_profit_pips, self.config.stop_loss_pips,
            self.config.spread_pips,
        )

    def run(self, df: pd.DataFrame) -> dict[str, Any]:
        """
        バックテストを実行する。

        Args:
            df: ローソク足データ（open, high, low, close, volumeカラム必須）。

        Returns:
            パフォーマンスサマリーの辞書。
        """
        # 戦略による前処理（テクニカル指標の計算等）
        df = self.strategy.prepare(df)

        self.trades = []
        self.open_positions = []
        self.equity_curve = []
        balance = self.config.initial_balance

        logger.info(
            "バックテスト開始: %s, データ %d本",
            self.strategy.name, len(df),
        )

        for i in range(len(df)):
            row = df.iloc[i]
            current_time = df.index[i]
            current_price = row["close"]
            high = row["high"]
            low = row["low"]

            # 1. オープンポジションの利確・損切チェック
            balance = self._check_tp_sl(
                current_time, high, low, current_price, balance,
            )

            # 2. 動的TP/SLの取得とシグナル生成
            tp_pips, sl_pips = self.strategy.get_dynamic_exits(df, i)
            signal = self.strategy.generate_signal(df, i)

            # 3. シグナルに基づく売買処理
            balance = self._process_signal(
                signal, current_time, current_price, balance, tp_pips, sl_pips
            )

            # 4. 資産推移を記録（含み損益込み）
            unrealized_pl = self._calc_unrealized_pl(current_price)
            self.equity_curve.append({
                "time": current_time,
                "balance": balance,
                "equity": balance + unrealized_pl,
                "unrealized_pl": unrealized_pl,
                "open_positions": len(self.open_positions),
            })

        # 残ったポジションを最終価格で強制決済
        if self.open_positions:
            last_price = df.iloc[-1]["close"]
            last_time = df.index[-1]
            balance = self._close_all(last_time, last_price, balance, "end_of_data")

        logger.info(
            "バックテスト完了: トレード数=%d, 最終残高=%.0f",
            len(self.trades), balance,
        )

        return self.get_summary()

    def _process_signal(
        self,
        signal: Signal,
        current_time: datetime,
        current_price: float,
        balance: float,
        tp_pips: float | None = None,
        sl_pips: float | None = None,
    ) -> float:
        """シグナルに基づいてエントリー・決済処理を行う。"""
        spread_cost = self.config.spread_pips * self.config.pip_value
        
        # SL/TPの決定（動的 > Config）
        final_sl_pips = sl_pips if sl_pips is not None else self.config.stop_loss_pips
        final_tp_pips = tp_pips if tp_pips is not None else self.config.take_profit_pips

        # ポジションサイジング
        units = self.config.units
        if final_sl_pips is not None and final_sl_pips > 0:
            max_loss_amount = balance * self.config.risk_pct
            loss_per_unit = final_sl_pips * self.config.pip_value
            calculated_units = int(max_loss_amount / loss_per_unit)
            # 1000通貨単位で丸める等の調整も可能だが、今回はそのまま
            units = max(1000, calculated_units)

        if signal == Signal.BUY:
            # ショートポジションがあれば決済
            balance = self._close_by_direction(
                current_time, current_price, balance, "SHORT", "signal",
            )
            # ロングエントリー（最大ポジション数チェック）
            if len(self.open_positions) < self.config.max_positions:
                entry_price = current_price + spread_cost / 2  # スプレッド考慮
                tp_price = entry_price + final_tp_pips * self.config.pip_value if final_tp_pips else None
                sl_price = entry_price - final_sl_pips * self.config.pip_value if final_sl_pips else None
                
                trade = Trade(
                    entry_time=current_time,
                    direction="LONG",
                    entry_price=entry_price,
                    units=units,
                    tp_price=tp_price,
                    sl_price=sl_price,
                )
                self.open_positions.append(trade)

        elif signal == Signal.SELL:
            # ロングポジションがあれば決済
            balance = self._close_by_direction(
                current_time, current_price, balance, "LONG", "signal",
            )
            # ショートエントリー
            if len(self.open_positions) < self.config.max_positions:
                entry_price = current_price - spread_cost / 2
                tp_price = entry_price - final_tp_pips * self.config.pip_value if final_tp_pips else None
                sl_price = entry_price + final_sl_pips * self.config.pip_value if final_sl_pips else None

                trade = Trade(
                    entry_time=current_time,
                    direction="SHORT",
                    entry_price=entry_price,
                    units=units,
                    tp_price=tp_price,
                    sl_price=sl_price,
                )
                self.open_positions.append(trade)

        elif signal == Signal.CLOSE:
            balance = self._close_all(
                current_time, current_price, balance, "signal",
            )

        elif signal == Signal.CLOSE_LONG:
            balance = self._close_by_direction(
                current_time, current_price, balance, "LONG", "signal",
            )

        elif signal == Signal.CLOSE_SHORT:
            balance = self._close_by_direction(
                current_time, current_price, balance, "SHORT", "signal",
            )

        return balance

    def _check_tp_sl(
        self,
        current_time: datetime,
        high: float,
        low: float,
        close: float,
        balance: float,
    ) -> float:
        """オープンポジションの利確・損切を判定する。"""
        closed = []
        for pos in self.open_positions:
            if pos.direction == "LONG":
                # 損切チェック（損切が先に発動する想定）
                if pos.sl_price is not None and low <= pos.sl_price:
                    balance = self._close_trade(
                        pos, current_time, pos.sl_price, balance, "stop_loss",
                    )
                    closed.append(pos)
                # 利確チェック
                elif pos.tp_price is not None and high >= pos.tp_price:
                    balance = self._close_trade(
                        pos, current_time, pos.tp_price, balance, "take_profit",
                    )
                    closed.append(pos)
            else:  # SHORT
                if pos.sl_price is not None and high >= pos.sl_price:
                    balance = self._close_trade(
                        pos, current_time, pos.sl_price, balance, "stop_loss",
                    )
                    closed.append(pos)
                elif pos.tp_price is not None and low <= pos.tp_price:
                    balance = self._close_trade(
                        pos, current_time, pos.tp_price, balance, "take_profit",
                    )
                    closed.append(pos)

        for pos in closed:
            self.open_positions.remove(pos)

        return balance

    def _close_trade(
        self,
        trade: Trade,
        exit_time: datetime,
        exit_price: float,
        balance: float,
        reason: str,
    ) -> float:
        """個別トレードを決済し残高を更新する。"""
        pip_val = self.config.pip_value
        spread_cost = self.config.spread_pips * pip_val

        if trade.direction == "LONG":
            raw_pips = (exit_price - trade.entry_price) / pip_val
        else:
            raw_pips = (trade.entry_price - exit_price) / pip_val

        # 決済時スプレッド控除
        net_pips = raw_pips - self.config.spread_pips / 2
        profit_loss = net_pips * pip_val * trade.units

        trade.exit_time = exit_time
        trade.exit_price = exit_price
        trade.pips_result = round(net_pips, 2)
        trade.profit_loss = round(profit_loss, 2)
        trade.exit_reason = reason

        self.trades.append(trade)
        return balance + profit_loss

    def _close_by_direction(
        self, time: datetime, price: float,
        balance: float, direction: str, reason: str,
    ) -> float:
        """指定方向のポジションを全て決済する。"""
        to_close = [p for p in self.open_positions if p.direction == direction]
        for pos in to_close:
            balance = self._close_trade(pos, time, price, balance, reason)
            self.open_positions.remove(pos)
        return balance

    def _close_all(
        self, time: datetime, price: float,
        balance: float, reason: str,
    ) -> float:
        """全ポジションを決済する。"""
        for pos in list(self.open_positions):
            balance = self._close_trade(pos, time, price, balance, reason)
        self.open_positions.clear()
        return balance

    def _calc_unrealized_pl(self, current_price: float) -> float:
        """オープンポジションの含み損益を計算する。"""
        pip_val = self.config.pip_value
        total = 0.0
        for pos in self.open_positions:
            if pos.direction == "LONG":
                pips = (current_price - pos.entry_price) / pip_val
            else:
                pips = (pos.entry_price - current_price) / pip_val
            total += pips * pip_val * pos.units
        return total

    # ─── 結果集計 ───────────────────────────────────────────

    def get_trades_df(self) -> pd.DataFrame:
        """
        トレード一覧をDataFrameで返す。

        Returns:
            全トレードの詳細DataFrame。
        """
        if not self.trades:
            return pd.DataFrame()

        records = []
        for t in self.trades:
            records.append({
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "direction": t.direction,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "units": t.units,
                "pips": t.pips_result,
                "profit_loss": t.profit_loss,
                "exit_reason": t.exit_reason,
            })
        return pd.DataFrame(records)

    def get_equity_df(self) -> pd.DataFrame:
        """資産推移をDataFrameで返す。"""
        if not self.equity_curve:
            return pd.DataFrame()
        df = pd.DataFrame(self.equity_curve)
        df.set_index("time", inplace=True)
        return df

    def get_summary(self) -> dict[str, Any]:
        """
        バックテスト結果のパフォーマンスサマリーを返す。

        Returns:
            総損益、勝率、PF、最大DD、シャープレシオ等を含む辞書。
        """
        trades_df = self.get_trades_df()
        equity_df = self.get_equity_df()

        if trades_df.empty:
            return {"error": "トレードが発生しませんでした"}

        total_trades = len(trades_df)
        wins = trades_df[trades_df["profit_loss"] > 0]
        losses = trades_df[trades_df["profit_loss"] <= 0]
        win_count = len(wins)
        loss_count = len(losses)

        total_profit = wins["profit_loss"].sum() if not wins.empty else 0
        total_loss = abs(losses["profit_loss"].sum()) if not losses.empty else 0
        net_profit = trades_df["profit_loss"].sum()
        total_pips = trades_df["pips"].sum()

        win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0
        profit_factor = (total_profit / total_loss) if total_loss > 0 else float("inf")
        avg_win = wins["profit_loss"].mean() if not wins.empty else 0
        avg_loss = losses["profit_loss"].mean() if not losses.empty else 0

        # 最大ドローダウン
        max_dd, max_dd_pct = self._calc_max_drawdown(equity_df)

        # シャープレシオ（日次リターンベース）
        sharpe = self._calc_sharpe_ratio(equity_df)

        # 初期・最終残高
        initial_balance = self.config.initial_balance
        final_balance = initial_balance + net_profit
        roi = (net_profit / initial_balance * 100)

        summary = {
            "strategy": self.strategy.name,
            "total_trades": total_trades,
            "win_count": win_count,
            "loss_count": loss_count,
            "win_rate": round(win_rate, 2),
            "total_pips": round(total_pips, 2),
            "net_profit": round(net_profit, 2),
            "total_profit": round(total_profit, 2),
            "total_loss": round(total_loss, 2),
            "profit_factor": round(profit_factor, 4),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "max_drawdown": round(max_dd, 2),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "sharpe_ratio": round(sharpe, 4),
            "initial_balance": initial_balance,
            "final_balance": round(final_balance, 2),
            "roi_pct": round(roi, 2),
        }
        return summary

    def _calc_max_drawdown(
        self, equity_df: pd.DataFrame,
    ) -> tuple[float, float]:
        """最大ドローダウン（金額と%）を計算する。"""
        if equity_df.empty:
            return 0.0, 0.0

        equity = equity_df["equity"]
        peak = equity.cummax()
        drawdown = equity - peak
        max_dd = drawdown.min()
        max_dd_pct = (max_dd / peak[drawdown.idxmin()] * 100) if max_dd < 0 else 0
        return abs(max_dd), abs(max_dd_pct)

    def _calc_sharpe_ratio(
        self, equity_df: pd.DataFrame, risk_free_rate: float = 0.0,
    ) -> float:
        """シャープレシオを計算する（年率換算）。"""
        if equity_df.empty or len(equity_df) < 2:
            return 0.0

        returns = equity_df["equity"].pct_change().dropna()
        if returns.std() == 0:
            return 0.0

        excess_returns = returns.mean() - risk_free_rate / 252
        sharpe = excess_returns / returns.std() * np.sqrt(252)
        return sharpe
