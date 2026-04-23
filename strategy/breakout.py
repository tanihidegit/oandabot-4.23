"""
OANDA FX自動売買ボット - レンジブレイクアウト戦略

過去N本のローソク足のHigh/Lowを基準にブレイクアウトを検出し、
エントリーシグナルを生成する。ATRベースの動的ストップロスを使用。

エントリー条件:
  - 買い: 終値が過去N本の最高値を上抜け
  - 売り: 終値が過去N本の最安値を下抜け

決済条件:
  - ATR × 倍率 分だけ逆行したら損切
  - 反対方向のブレイクアウトで決済
"""

import logging

import pandas as pd

from strategy.base import BaseStrategy, Signal

logger = logging.getLogger(__name__)


class BreakoutStrategy(BaseStrategy):
    """
    レンジブレイクアウト戦略。

    過去lookback_period本のHigh/Lowチャネルをブレイクした時に
    エントリーする。ATRベースの動的ストップロスで損失を制限する。

    Attributes:
        lookback_period: ブレイクアウト判定のルックバック期間。
        atr_period: ATR計算期間。
        atr_multiplier: ストップロス距離のATR倍率。
    """

    def __init__(
        self,
        lookback_period: int = 20,
        atr_period: int = 14,
        atr_multiplier: float = 2.0,
    ) -> None:
        """
        ブレイクアウト戦略を初期化する。

        Args:
            lookback_period: チャネルのルックバック期間（デフォルト20本）。
            atr_period: ATRの計算期間（デフォルト14本）。
            atr_multiplier: SLのATR倍率（デフォルト2.0倍）。
        """
        super().__init__(
            name="Range_Breakout",
            lookback_period=lookback_period,
            atr_period=atr_period,
            atr_multiplier=atr_multiplier,
        )
        self.lookback_period = lookback_period
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier

        logger.info(
            "ブレイクアウト戦略: lookback=%d, ATR(%d)×%.1f",
            lookback_period, atr_period, atr_multiplier,
        )

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        ブレイクアウト用指標（チャネル上下限、ATR）を計算する。

        Args:
            df: ローソク足データ（high, low, closeカラムが必要）。

        Returns:
            channel_high, channel_low, atr, atr_stopカラムが追加されたDataFrame。
        """
        # チャネル上限・下限（現在の足を含まない直前N本）
        df["channel_high"] = df["high"].shift(1).rolling(
            window=self.lookback_period
        ).max()
        df["channel_low"] = df["low"].shift(1).rolling(
            window=self.lookback_period
        ).min()

        # ATR（Average True Range）の計算
        high_low = df["high"] - df["low"]
        high_prev_close = (df["high"] - df["close"].shift(1)).abs()
        low_prev_close = (df["low"] - df["close"].shift(1)).abs()
        true_range = pd.concat(
            [high_low, high_prev_close, low_prev_close], axis=1,
        ).max(axis=1)
        df["atr"] = true_range.rolling(window=self.atr_period).mean()

        # ATRベースのストップロス距離
        df["atr_stop"] = df["atr"] * self.atr_multiplier

        logger.info(
            "ブレイクアウト指標を計算しました: channel(%d), ATR(%d)×%.1f",
            self.lookback_period, self.atr_period, self.atr_multiplier,
        )
        return df

    def generate_signal(self, df: pd.DataFrame, index: int) -> Signal:
        """
        ブレイクアウトに基づいて売買シグナルを生成する。

        Args:
            df: 指標を含むローソク足データ。
            index: 現在の行インデックス。

        Returns:
            Signal値。BUY/SELL/HOLD のいずれか。
        """
        if index < 1:
            return Signal.HOLD

        row = df.iloc[index]
        close = row.get("close")
        channel_high = row.get("channel_high")
        channel_low = row.get("channel_low")
        atr = row.get("atr")

        if pd.isna(channel_high) or pd.isna(channel_low) or pd.isna(atr):
            return Signal.HOLD

        prev_close = df.iloc[index - 1].get("close")
        prev_channel_high = df.iloc[index - 1].get("channel_high")
        prev_channel_low = df.iloc[index - 1].get("channel_low")

        if pd.isna(prev_close) or pd.isna(prev_channel_high) or pd.isna(prev_channel_low):
            return Signal.HOLD

        # 上方ブレイクアウト: 前回はチャネル内、今回は上抜け
        if prev_close <= prev_channel_high and close > channel_high:
            return Signal.BUY

        # 下方ブレイクアウト: 前回はチャネル内、今回は下抜け
        if prev_close >= prev_channel_low and close < channel_low:
            return Signal.SELL

        return Signal.HOLD

    def get_stop_loss_distance(self, df: pd.DataFrame, index: int) -> float:
        """
        現在のATRベースのストップロス距離を取得する。

        Args:
            df: atr_stopカラムを含むDataFrame。
            index: 現在の行インデックス。

        Returns:
            ストップロス距離（価格幅）。計算不可の場合は0.0。
        """
        atr_stop = df.iloc[index].get("atr_stop")
        if pd.isna(atr_stop):
            return 0.0
        return float(atr_stop)
