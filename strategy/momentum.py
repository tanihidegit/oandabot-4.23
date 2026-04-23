"""
OANDA FX自動売買ボット - モメンタム戦略

RSI（相対力指数）とEMA（指数移動平均線）クロスを組み合わせた
モメンタムベースの売買戦略。

エントリー条件:
  - 買い: RSI < 30（売られすぎ） かつ EMA短期 > EMA長期（上昇トレンド）
  - 売り: RSI > 70（買われすぎ） かつ EMA短期 < EMA長期（下降トレンド）

決済条件:
  - ロング決済: RSI > 70（利確ゾーン到達）
  - ショート決済: RSI < 30（利確ゾーン到達）
"""

import logging

import pandas as pd

from strategy.base import BaseStrategy, Signal

logger = logging.getLogger(__name__)


class MomentumStrategy(BaseStrategy):
    """
    RSI + EMAクロスのモメンタム戦略。

    RSIで売られすぎ/買われすぎを判定し、EMAのトレンド方向と
    合致する場合にエントリーする。RSIが反対の極値に達したら決済。

    Attributes:
        rsi_period: RSIの計算期間。
        rsi_overbought: 買われすぎ閾値（この値以上でロング決済/ショートエントリー）。
        rsi_oversold: 売られすぎ閾値（この値以下でショート決済/ロングエントリー）。
        ema_short: 短期EMAの期間。
        ema_long: 長期EMAの期間。
    """

    def __init__(
        self,
        rsi_period: int = 14,
        rsi_overbought: float = 70.0,
        rsi_oversold: float = 30.0,
        ema_short: int = 20,
        ema_long: int = 50,
    ) -> None:
        """
        モメンタム戦略を初期化する。

        Args:
            rsi_period: RSIの計算期間（デフォルト14）。
            rsi_overbought: 買われすぎ閾値（デフォルト70）。
            rsi_oversold: 売られすぎ閾値（デフォルト30）。
            ema_short: 短期EMA期間（デフォルト20）。
            ema_long: 長期EMA期間（デフォルト50）。
        """
        super().__init__(
            name="Momentum_RSI_EMA",
            rsi_period=rsi_period,
            rsi_overbought=rsi_overbought,
            rsi_oversold=rsi_oversold,
            ema_short=ema_short,
            ema_long=ema_long,
        )
        self.rsi_period = rsi_period
        self.rsi_overbought = rsi_overbought
        self.rsi_oversold = rsi_oversold
        self.ema_short = ema_short
        self.ema_long = ema_long

        if ema_short >= ema_long:
            raise ValueError(
                f"短期EMA({ema_short})は長期EMA({ema_long})より小さい値にしてください"
            )

        logger.info(
            "モメンタム戦略: RSI(%d), EMA(%d/%d), OB=%.0f, OS=%.0f",
            rsi_period, ema_short, ema_long, rsi_overbought, rsi_oversold,
        )

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        RSIとEMAを計算してDataFrameに追加する。

        Args:
            df: ローソク足データ（closeカラムが必要）。

        Returns:
            rsi, ema_short, ema_longカラムが追加されたDataFrame。
        """
        # RSIの計算
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.rolling(window=self.rsi_period, min_periods=1).mean()
        avg_loss = loss.rolling(window=self.rsi_period, min_periods=1).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-10)
        df["rsi"] = 100 - (100 / (1 + rs))

        # EMAの計算
        df["ema_short"] = df["close"].ewm(span=self.ema_short, adjust=False).mean()
        df["ema_long"] = df["close"].ewm(span=self.ema_long, adjust=False).mean()

        logger.info(
            "モメンタム指標を計算しました: RSI(%d), EMA(%d/%d)",
            self.rsi_period, self.ema_short, self.ema_long,
        )
        return df

    def generate_signal(self, df: pd.DataFrame, index: int) -> Signal:
        """
        RSIとEMAクロスに基づいて売買シグナルを生成する。

        Args:
            df: テクニカル指標を含むデータ。
            index: 現在の行インデックス。

        Returns:
            Signal値。BUY/SELL/CLOSE_LONG/CLOSE_SHORT/HOLD のいずれか。
        """
        if index < 1:
            return Signal.HOLD

        row = df.iloc[index]
        rsi = row.get("rsi")
        ema_s = row.get("ema_short")
        ema_l = row.get("ema_long")

        if pd.isna(rsi) or pd.isna(ema_s) or pd.isna(ema_l):
            return Signal.HOLD

        ema_bullish = ema_s > ema_l  # 上昇トレンド
        ema_bearish = ema_s < ema_l  # 下降トレンド

        # ─── 決済シグナル（エントリーより優先）───
        if rsi >= self.rsi_overbought:
            return Signal.CLOSE_LONG

        if rsi <= self.rsi_oversold:
            return Signal.CLOSE_SHORT

        # ─── エントリーシグナル ───
        # 前回のRSIを確認してクロスオーバーを検出
        prev_rsi = df.iloc[index - 1].get("rsi")
        if pd.isna(prev_rsi):
            return Signal.HOLD

        # 買い: RSIが売られすぎゾーンを上抜け + 上昇トレンド
        if prev_rsi <= self.rsi_oversold and rsi > self.rsi_oversold and ema_bullish:
            return Signal.BUY

        # 売り: RSIが買われすぎゾーンを下抜け + 下降トレンド
        if prev_rsi >= self.rsi_overbought and rsi < self.rsi_overbought and ema_bearish:
            return Signal.SELL

        return Signal.HOLD
