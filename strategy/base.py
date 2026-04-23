"""
OANDA FX自動売買ボット - 戦略基底クラス

全ての売買戦略が継承するベースクラスとシグナル列挙型を定義する。
新しい戦略を作成する場合は BaseStrategy を継承し、
generate_signal と calculate_indicators を実装すること。
"""

import logging
from abc import ABC, abstractmethod
from enum import Enum, auto

import pandas as pd

logger = logging.getLogger(__name__)


class Signal(Enum):
    """
    売買シグナルの列挙型。

    戦略のgenerate_signalメソッドが返すシグナルの種類を定義する。
    """

    BUY = auto()           # 買いシグナル（ロングエントリー）
    SELL = auto()          # 売りシグナル（ショートエントリー）
    HOLD = auto()          # 何もしない（ポジション維持）
    CLOSE = auto()         # 全ポジション決済シグナル（後方互換用）
    CLOSE_LONG = auto()    # ロングポジション決済シグナル
    CLOSE_SHORT = auto()   # ショートポジション決済シグナル


class BaseStrategy(ABC):
    """
    売買戦略の基底クラス。

    全ての戦略はこのクラスを継承し、generate_signal と
    calculate_indicators を実装する。
    バックテストエンジンはこのインターフェースを通じて戦略を実行する。

    Attributes:
        name: 戦略名。
        params: 戦略パラメータの辞書。
    """

    def __init__(self, name: str = "BaseStrategy", **params) -> None:
        """
        戦略を初期化する。

        Args:
            name: 戦略の識別名。
            **params: 戦略固有のパラメータ。
        """
        self.name = name
        self.params = params
        logger.info("戦略を初期化しました: %s (params=%s)", self.name, self.params)

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame, index: int) -> Signal:
        """
        指定された時点での売買シグナルを生成する。

        バックテストエンジンがローソク足データの各行に対して呼び出す。
        実装クラスは、渡されたDataFrameの index 番目の行までの情報を使い
        シグナルを判定すること（未来のデータは参照禁止）。

        Args:
            df: ローソク足データ（テクニカル指標を含む場合あり）。
            index: 現在の行インデックス（0始まり）。
                   df.iloc[:index+1] までのデータを参照可能。

        Returns:
            Signal列挙型の値。
        """
        pass

    @abstractmethod
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        戦略に必要なテクニカル指標を計算してDataFrameに追加する。

        このメソッドはバックテスト開始前にprepareから呼び出される。
        各戦略が必要とする指標（SMA, EMA, RSI, ATR等）を計算し、
        DataFrameの新規カラムとして追加する。

        Args:
            df: 生のローソク足データ（open, high, low, close, volume）。

        Returns:
            テクニカル指標カラムが追加されたDataFrame。
        """
        pass

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        バックテスト前のデータ前処理を行う。

        calculate_indicators を呼び出してテクニカル指標を計算する。
        サブクラスでオーバーライドする場合は super().prepare() を呼ぶこと。

        Args:
            df: 生のローソク足データ。

        Returns:
            前処理済みのDataFrame。
        """
        df = df.copy()
        df = self.calculate_indicators(df)
        logger.info("前処理完了: %s（%d行, %d列）", self.name, len(df), len(df.columns))
        return df

    def __repr__(self) -> str:
        """戦略の文字列表現を返す。"""
        return f"{self.name}(params={self.params})"

    def get_dynamic_exits(self, df: pd.DataFrame, index: int) -> tuple[float | None, float | None]:
        """
        現在の動的TPとSL（pips幅）を取得する。

        サブクラスでオーバーライドして、ATRなどに基づいたTP/SLを返す。
        
        Args:
            df: テクニカル指標を含むデータ。
            index: 現在の行インデックス。

        Returns:
            (tp_pips, sl_pips) のタプル。設定しない場合はNoneを返す。
        """
        return None, None
