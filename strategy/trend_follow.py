"""
OANDA FX自動売買ボット - トレンドフォロー戦略

ボリンジャーバンドとRSIを使用し、強いトレンド（バンドウォーク）
の発生を捉えて順張りエントリーを行う戦略。

エントリー条件:
  - 買い: 終値がボリンジャーバンドの+2σを超え、かつRSI(14)が55以上
  - 売り: 終値がボリンジャーバンドの-2σを下回り、かつRSI(14)が45以下
  - 時間フィルター: 08:00-15:00 JST または 16:00-25:00 JST

決済条件:
  - TP: ATR(14) * 2.0 （リターン重視）
  - SL: ATR(14) * 1.0
  - シグナル決済: 反対側のバンド（1σ等）に触れる、またはRSIの逆転
"""

import logging
import pandas as pd

from strategy.base import BaseStrategy, Signal

logger = logging.getLogger(__name__)


class TrendFollowStrategy(BaseStrategy):
    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 2.0,
        rsi_period: int = 14,
        atr_period: int = 14,
        atr_tp_multi: float = 2.0,
        atr_sl_multi: float = 1.0,
    ) -> None:
        super().__init__(
            name="Trend_Follow_BB",
            bb_period=bb_period,
            bb_std=bb_std,
            rsi_period=rsi_period,
            atr_period=atr_period,
            atr_tp_multi=atr_tp_multi,
            atr_sl_multi=atr_sl_multi,
        )
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.rsi_period = rsi_period
        self.atr_period = atr_period
        self.atr_tp_multi = atr_tp_multi
        self.atr_sl_multi = atr_sl_multi

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """指標計算（ボリンジャーバンド、RSI、ATR）"""
        # Bollinger Bands
        df["sma"] = df["close"].rolling(window=self.bb_period).mean()
        df["std"] = df["close"].rolling(window=self.bb_period).std()
        df["bb_upper"] = df["sma"] + (df["std"] * self.bb_std)
        df["bb_lower"] = df["sma"] - (df["std"] * self.bb_std)
        # 決済用（1σ）
        df["bb_upper_1"] = df["sma"] + df["std"]
        df["bb_lower_1"] = df["sma"] - df["std"]

        # RSI
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.rolling(window=self.rsi_period, min_periods=1).mean()
        avg_loss = loss.rolling(window=self.rsi_period, min_periods=1).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-10)
        df["rsi"] = 100 - (100 / (1 + rs))

        # ATR
        high_low = df["high"] - df["low"]
        high_prev_close = (df["high"] - df["close"].shift(1)).abs()
        low_prev_close = (df["low"] - df["close"].shift(1)).abs()
        true_range = pd.concat([high_low, high_prev_close, low_prev_close], axis=1).max(axis=1)
        df["atr"] = true_range.rolling(window=self.atr_period).mean()

        return df

    def get_dynamic_exits(self, df: pd.DataFrame, index: int) -> tuple[float | None, float | None]:
        row = df.iloc[index]
        atr = row.get("atr")
        if pd.isna(atr) or atr == 0:
            return None, None
            
        is_jpy = row["close"] > 50
        pip_value = 0.01 if is_jpy else 0.0001
        
        atr_pips = atr / pip_value
        tp_pips = atr_pips * self.atr_tp_multi
        sl_pips = atr_pips * self.atr_sl_multi
        return tp_pips, sl_pips

    def generate_signal(self, df: pd.DataFrame, index: int) -> Signal:
        if index < 1:
            return Signal.HOLD

        row = df.iloc[index]
        prev_row = df.iloc[index - 1]

        close = row.get("close")
        prev_close = prev_row.get("close")
        
        bb_upper = row.get("bb_upper")
        bb_lower = row.get("bb_lower")
        bb_upper_1 = row.get("bb_upper_1")
        bb_lower_1 = row.get("bb_lower_1")
        rsi = row.get("rsi")
        sma = row.get("sma")

        if pd.isna(bb_upper) or pd.isna(rsi):
            return Signal.HOLD

        # ─── 時間フィルター ───
        current_dt = df.index[index]
        hour_jst = (current_dt.hour + 9) % 24
        is_trading_hours = (8 <= hour_jst < 15) or (16 <= hour_jst <= 24) or (hour_jst == 0)

        # ─── エントリーシグナル（時間フィルター内のみ） ───
        if is_trading_hours:
            if close > bb_upper and rsi >= 55:
                return Signal.BUY
    
            if close < bb_lower and rsi <= 45:
                return Signal.SELL

        # ─── 決済シグナル（全時間帯）───
        # ロング決済: 終値が+1σを下回った、またはRSIが40を下回った
        if close < bb_upper_1 or rsi < 40:
            return Signal.CLOSE_LONG

        # ショート決済: 終値が-1σを上回った、またはRSIが60を上回った
        if close > bb_lower_1 or rsi > 60:
            return Signal.CLOSE_SHORT

        return Signal.HOLD
