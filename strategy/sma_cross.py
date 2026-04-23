"""
OANDA FX自動売買ボット - SMAクロス戦略

短期移動平均線と長期移動平均線のクロスオーバーで
売買シグナルを生成するシンプルな戦略。

- ゴールデンクロス（短期 > 長期）→ 買いシグナル
- デッドクロス（短期 < 長期）→ 売りシグナル
"""

import logging

import pandas as pd

from strategy.base import BaseStrategy, Signal

logger = logging.getLogger(__name__)


class SmaCrossStrategy(BaseStrategy):
    """
    SMA（単純移動平均線）クロスオーバー戦略。

    短期SMAが長期SMAを上抜け（ゴールデンクロス）で買い、
    短期SMAが長期SMAを下抜け（デッドクロス）で売りを行う。

    Attributes:
        short_period: 短期SMAの期間。
        long_period: 長期SMAの期間。
    """

    def __init__(
        self,
        short_period: int = 20,
        long_period: int = 50,
    ) -> None:
        """
        SMAクロス戦略を初期化する。

        Args:
            short_period: 短期SMAの期間（デフォルト20）。
            long_period: 長期SMAの期間（デフォルト50）。
        """
        super().__init__(
            name="SMA_Cross",
            short_period=short_period,
            long_period=long_period,
        )
        self.short_period = short_period
        self.long_period = long_period

        if short_period >= long_period:
            raise ValueError(
                f"短期SMA期間({short_period})は長期SMA期間({long_period})より"
                "小さい値にしてください。"
            )

        logger.info(
            "SMAクロス戦略: 短期=%d, 長期=%d",
            short_period, long_period,
        )

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        SMA指標を計算してDataFrameに追加する。

        Args:
            df: ローソク足データ（closeカラムが必要）。

        Returns:
            sma_short, sma_longカラムが追加されたDataFrame。
        """
        df["sma_short"] = df["close"].rolling(window=self.short_period).mean()
        df["sma_long"] = df["close"].rolling(window=self.long_period).mean()

        logger.info(
            "SMA指標を計算しました（短期: %d, 長期: %d）",
            self.short_period, self.long_period,
        )
        return df

    def generate_signal(self, df: pd.DataFrame, index: int) -> Signal:
        """
        SMAクロスに基づいて売買シグナルを生成する。

        Args:
            df: SMA指標を含むローソク足データ。
            index: 現在の行インデックス（0始まり）。

        Returns:
            Signal.BUY / Signal.SELL / Signal.HOLD。
        """
        if index < 1:
            return Signal.HOLD

        current_short = df.iloc[index].get("sma_short")
        current_long = df.iloc[index].get("sma_long")
        prev_short = df.iloc[index - 1].get("sma_short")
        prev_long = df.iloc[index - 1].get("sma_long")

        if pd.isna(current_short) or pd.isna(current_long):
            return Signal.HOLD
        if pd.isna(prev_short) or pd.isna(prev_long):
            return Signal.HOLD

        # ゴールデンクロス
        if prev_short <= prev_long and current_short > current_long:
            return Signal.BUY

        # デッドクロス
        if prev_short >= prev_long and current_short < current_long:
            return Signal.SELL

        return Signal.HOLD
