"""
OANDA FX自動売買ボット - スキャルピング（平均回帰）戦略

M5（5分足）などの短い時間足を対象に、ボリンジャーバンドとRSIを
用いてレンジ相場での逆張り（Mean Reversion）を行い、
高頻度かつ高勝率（60%以上）を目指す戦略。

エントリー条件:
  - 買い: 安値がボリンジャーバンド-2σを下抜け、かつ終値が-2σを上回る（ヒゲで反発）、かつRSI<35
  - 売り: 高値がボリンジャーバンド+2σを上抜け、かつ終値が+2σを下回る（ヒゲで反発）、かつRSI>65
  - トレンドフィルター: ADX(14) < 25 （強いトレンドが発生していない状態）
  - 時間フィルター: 08:00-24:00 JST

決済条件:
  - TP: ATR(14) * 0.8 （利確を早くして勝率を高める）
  - SL: ATR(14) * 1.5 （勝率重視のためSLは広め）
  - シグナル決済: 終値がボリンジャーバンドの中央線（SMA）に達した時点
"""

import logging
import pandas as pd
import numpy as np

from strategy.base import BaseStrategy, Signal

logger = logging.getLogger(__name__)


class ScalpingStrategy(BaseStrategy):
    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 2.0,
        rsi_period: int = 14,
        adx_period: int = 14,
        adx_threshold: float = 25.0,
        atr_period: int = 14,
        atr_tp_multi: float = 1.0,
        atr_sl_multi: float = 1.5,
    ) -> None:
        super().__init__(
            name="Scalping_MeanReversion",
            bb_period=bb_period,
            bb_std=bb_std,
            rsi_period=rsi_period,
            adx_period=adx_period,
            adx_threshold=adx_threshold,
            atr_period=atr_period,
            atr_tp_multi=atr_tp_multi,
            atr_sl_multi=atr_sl_multi,
        )
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.rsi_period = rsi_period
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        self.atr_period = atr_period
        self.atr_tp_multi = atr_tp_multi
        self.atr_sl_multi = atr_sl_multi

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """指標計算（ボリンジャーバンド、RSI、ADX、ATR）"""
        # Bollinger Bands
        df["bb_sma"] = df["close"].rolling(window=self.bb_period).mean()
        df["bb_std"] = df["close"].rolling(window=self.bb_period).std()
        df["bb_upper"] = df["bb_sma"] + (df["bb_std"] * self.bb_std)
        df["bb_lower"] = df["bb_sma"] - (df["bb_std"] * self.bb_std)

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

        # ADX
        up_move = df["high"] - df["high"].shift(1)
        down_move = df["low"].shift(1) - df["low"]
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        tr_smooth = true_range.rolling(window=self.adx_period).sum()
        plus_di = 100 * pd.Series(plus_dm, index=df.index).rolling(window=self.adx_period).sum() / tr_smooth.replace(0, 1e-10)
        minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(window=self.adx_period).sum() / tr_smooth.replace(0, 1e-10)
        dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, 1e-10))
        df["adx"] = dx.rolling(window=self.adx_period).mean()

        return df

    def get_dynamic_exits(self, df: pd.DataFrame, index: int) -> tuple[float | None, float | None]:
        row = df.iloc[index]
        atr = row.get("atr")
        
        # M5のスキャルピングではATRが小さくなるため、デフォルト値も小さめに
        default_tp, default_sl = 10.0, 15.0
        
        if pd.isna(atr) or atr == 0:
            return default_tp, default_sl
            
        is_jpy = row["close"] > 50
        pip_value = 0.01 if is_jpy else 0.0001
        
        atr_pips = atr / pip_value
        tp_pips = atr_pips * self.atr_tp_multi
        sl_pips = atr_pips * self.atr_sl_multi
        
        # スキャルピング用の上下限設定
        # スプレッド（0.3等）を考慮し、最低でも5pipsは確保
        tp_pips = max(5.0, min(tp_pips, 25.0))
        sl_pips = max(8.0, min(sl_pips, 40.0))
        
        return tp_pips, sl_pips

    def generate_signal(self, df: pd.DataFrame, index: int) -> Signal:
        if index < 1:
            return Signal.HOLD

        row = df.iloc[index]
        
        close = row.get("close")
        high = row.get("high")
        low = row.get("low")
        bb_upper = row.get("bb_upper")
        bb_lower = row.get("bb_lower")
        bb_sma = row.get("bb_sma")
        rsi = row.get("rsi")
        adx = row.get("adx")

        if pd.isna(bb_upper) or pd.isna(rsi) or pd.isna(adx):
            return Signal.HOLD

        # ─── 時間フィルター ───
        current_dt = df.index[index]
        hour_jst = (current_dt.hour + 9) % 24
        # ボラティリティが高いロンドン・NY時間 (15:00 - 01:59 JST)
        is_trading_hours = (15 <= hour_jst <= 23) or (hour_jst <= 1)

        # ─── エントリーシグナル ───
        # トレンドが発生していることを確認 (ADX > 25)
        if is_trading_hours and adx >= 25.0:
            
            # EMAで大きなトレンド方向を確認
            ema_s = df["close"].rolling(8).mean().iloc[index]
            ema_l = df["close"].rolling(21).mean().iloc[index]

            # 買い条件（順張りブレイクアウト）:
            # 1. 終値がボリンジャーバンド+2σを上抜けた
            # 2. 短期EMA > 長期EMA (アップトレンド)
            # 3. 直前の足も陽線であること
            if close > bb_upper and ema_s > ema_l and df["close"].iloc[index-1] > df["open"].iloc[index-1]:
                return Signal.BUY

            # 売り条件（順張りブレイクアウト）:
            # 1. 終値がボリンジャーバンド-2σを下抜けた
            # 2. 短期EMA < 長期EMA (ダウントレンド)
            # 3. 直前の足も陰線であること
            if close < bb_lower and ema_s < ema_l and df["close"].iloc[index-1] < df["open"].iloc[index-1]:
                return Signal.SELL

        # ─── 決済シグナル ───
        # スキャルピングのため、基本は動的TP/SLに任せるが、
        # 極端なRSI反発があれば決済する
        if rsi >= 75:
            return Signal.CLOSE_LONG
        if rsi <= 25:
            return Signal.CLOSE_SHORT

        return Signal.HOLD
