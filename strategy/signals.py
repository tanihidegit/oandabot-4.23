"""
OANDA FX自動売買ボット - シグナル統合モジュール

複数の戦略を並列実行し、シグナルの合意（コンセンサス）に基づいて
最終的な売買判断を行う。単一戦略の誤判断リスクを低減する。

統合ルール:
  - 全戦略（またはN戦略以上）が同じ方向のシグナルを出した場合のみ発行
  - 1つでも反対方向のシグナルがあれば HOLD
  - 決済シグナルは1戦略でも出せば発行（安全サイド）
"""

import logging
from collections import Counter

import pandas as pd

from strategy.base import BaseStrategy, Signal

logger = logging.getLogger(__name__)


class SignalAggregator(BaseStrategy):
    """
    複数戦略のシグナルを統合してコンセンサス判断を行うクラス。

    登録された全戦略の generate_signal を呼び出し、
    最低合意数（min_agreement）以上の戦略が同じ方向のシグナルを
    出した場合にのみ最終シグナルを発行する。

    決済シグナル（CLOSE_LONG, CLOSE_SHORT, CLOSE）は安全のため、
    1戦略でも出せば発行する。

    Attributes:
        strategies: 登録された戦略のリスト。
        min_agreement: シグナル発行に必要な最低合意数。
    """

    def __init__(
        self,
        strategies: list[BaseStrategy],
        min_agreement: int | None = None,
    ) -> None:
        """
        シグナル統合器を初期化する。

        Args:
            strategies: 統合対象の戦略リスト（2つ以上推奨）。
            min_agreement: シグナル発行に必要な最低合意戦略数。
                           Noneの場合は全戦略の合意を要求。
        """
        strategy_names = [s.name for s in strategies]
        super().__init__(
            name="SignalAggregator",
            strategies=strategy_names,
            min_agreement=min_agreement,
        )
        self.strategies = strategies
        self.min_agreement = min_agreement or len(strategies)

        if len(strategies) < 2:
            logger.warning("統合対象の戦略が2つ未満です。効果が限定的です。")

        if self.min_agreement > len(strategies):
            raise ValueError(
                f"最低合意数({self.min_agreement})が"
                f"戦略数({len(strategies)})を超えています"
            )

        logger.info(
            "シグナル統合器を初期化: 戦略数=%d, 最低合意数=%d, 戦略=%s",
            len(strategies), self.min_agreement, strategy_names,
        )

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        全戦略のテクニカル指標を計算する。

        各戦略の calculate_indicators を順番に呼び出し、
        全ての指標を1つのDataFrameに集約する。

        Args:
            df: 生のローソク足データ。

        Returns:
            全戦略の指標が追加されたDataFrame。
        """
        for strategy in self.strategies:
            df = strategy.calculate_indicators(df)
            logger.debug("指標計算完了: %s", strategy.name)

        logger.info(
            "全戦略の指標計算が完了しました（%d戦略, %d列）",
            len(self.strategies), len(df.columns),
        )
        return df

    def generate_signal(self, df: pd.DataFrame, index: int) -> Signal:
        """
        全戦略のシグナルを収集し、コンセンサスに基づいて最終シグナルを返す。

        Args:
            df: テクニカル指標を含むデータ。
            index: 現在の行インデックス。

        Returns:
            コンセンサスに基づく最終Signal。
        """
        # 各戦略のシグナルを収集
        signals: list[Signal] = []
        for strategy in self.strategies:
            sig = strategy.generate_signal(df, index)
            signals.append(sig)

        # 決済シグナルは安全のため1つでもあれば発行
        close_signals = {Signal.CLOSE, Signal.CLOSE_LONG, Signal.CLOSE_SHORT}
        for sig in signals:
            if sig in close_signals:
                logger.debug(
                    "[統合] 決済シグナル検出（index=%d）: %s", index, sig.name,
                )
                return sig

        # エントリーシグナルの集計
        signal_counts = Counter(signals)
        buy_count = signal_counts.get(Signal.BUY, 0)
        sell_count = signal_counts.get(Signal.SELL, 0)

        # BUYの合意
        if buy_count >= self.min_agreement:
            logger.debug(
                "[統合] BUY合意成立（index=%d）: %d/%d戦略",
                index, buy_count, len(self.strategies),
            )
            return Signal.BUY

        # SELLの合意
        if sell_count >= self.min_agreement:
            logger.debug(
                "[統合] SELL合意成立（index=%d）: %d/%d戦略",
                index, sell_count, len(self.strategies),
            )
            return Signal.SELL

        return Signal.HOLD

    def get_individual_signals(
        self, df: pd.DataFrame, index: int,
    ) -> dict[str, Signal]:
        """
        各戦略の個別シグナルを辞書で取得する（デバッグ・分析用）。

        Args:
            df: テクニカル指標を含むデータ。
            index: 現在の行インデックス。

        Returns:
            戦略名をキー、Signalを値とする辞書。
        """
        result: dict[str, Signal] = {}
        for strategy in self.strategies:
            sig = strategy.generate_signal(df, index)
            result[strategy.name] = sig
        return result
