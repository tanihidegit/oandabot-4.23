"""
OANDA FX自動売買ボット - 改善版モメンタム戦略

EMAクロスをベースに、RSI、MACD、ADXでトレンドと勢いを確認し、
特定時間帯（東京・ロンドン）のみエントリーを行う戦略。
決済にはATRベースの動的TP/SLを使用。

エントリー条件:
  - 買い: EMA(8) > EMA(21) かつ RSI(14)が40〜65 かつ MACDがシグナルより上 かつ ADX(14) > 20
  - 売り: EMA(8) < EMA(21) かつ RSI(14)が35〜60 かつ MACDがシグナルより下 かつ ADX(14) > 20
  - 時間フィルター: 08:00-15:00 JST または 16:00-25:00 JST

決済条件:
  - TP: ATR(14) * 1.5
  - SL: ATR(14) * 1.0
  - シグナル決済: EMA逆クロス、またはRSI極端値（買い時>75、売り時<25）
"""

import logging
import pandas as pd
import numpy as np

from strategy.base import BaseStrategy, Signal

logger = logging.getLogger(__name__)


class MomentumStrategy(BaseStrategy):
    def __init__(
        self,
        ema_short: int = 8,
        ema_long: int = 21,
        rsi_period: int = 14,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        adx_period: int = 14,
        adx_threshold: float = 25.0,
        atr_period: int = 14,
        atr_tp_multi: float = 2.0,
        atr_sl_multi: float = 1.0,
    ) -> None:
        super().__init__(
            name="Advanced_Momentum",
            ema_short=ema_short,
            ema_long=ema_long,
            rsi_period=rsi_period,
            macd_fast=macd_fast,
            macd_slow=macd_slow,
            macd_signal=macd_signal,
            adx_period=adx_period,
            adx_threshold=adx_threshold,
            atr_period=atr_period,
            atr_tp_multi=atr_tp_multi,
            atr_sl_multi=atr_sl_multi,
        )
        self.ema_short = ema_short
        self.ema_long = ema_long
        self.rsi_period = rsi_period
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        self.atr_period = atr_period
        self.atr_tp_multi = atr_tp_multi
        self.atr_sl_multi = atr_sl_multi

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """各種テクニカル指標を計算する。"""
        # EMA
        df["ema_short"] = df["close"].ewm(span=self.ema_short, adjust=False).mean()
        df["ema_long"] = df["close"].ewm(span=self.ema_long, adjust=False).mean()

        # RSI
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.rolling(window=self.rsi_period, min_periods=1).mean()
        avg_loss = loss.rolling(window=self.rsi_period, min_periods=1).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-10)
        df["rsi"] = 100 - (100 / (1 + rs))

        # MACD
        ema_fast = df["close"].ewm(span=self.macd_fast, adjust=False).mean()
        ema_slow = df["close"].ewm(span=self.macd_slow, adjust=False).mean()
        df["macd"] = ema_fast - ema_slow
        df["macd_signal"] = df["macd"].ewm(span=self.macd_signal, adjust=False).mean()

        # ATR
        high_low = df["high"] - df["low"]
        high_prev_close = (df["high"] - df["close"].shift(1)).abs()
        low_prev_close = (df["low"] - df["close"].shift(1)).abs()
        true_range = pd.concat([high_low, high_prev_close, low_prev_close], axis=1).max(axis=1)
        # Bollinger Bands (20, 2σ)
        df["bb_sma"] = df["close"].rolling(window=20).mean()
        df["bb_std"] = df["close"].rolling(window=20).std()
        df["bb_upper"] = df["bb_sma"] + (df["bb_std"] * 2.0)
        df["bb_lower"] = df["bb_sma"] - (df["bb_std"] * 2.0)

        # ADX (簡易計算)
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
        """ATRベースの動的TP/SL幅（pips）を計算して返す。フォールバックおよび上下限付き。"""
        row = df.iloc[index]
        atr = row.get("atr")
        
        # フォールバック値
        default_tp, default_sl = 40.0, 20.0
        
        if pd.isna(atr) or atr == 0:
            return default_tp, default_sl
            
        is_jpy = row["close"] > 50  # 簡易判定
        pip_value = 0.01 if is_jpy else 0.0001
        
        atr_pips = atr / pip_value
        tp_pips = atr_pips * self.atr_tp_multi
        sl_pips = atr_pips * self.atr_sl_multi
        
        # 上下限の適用
        tp_pips = max(20.0, min(tp_pips, 80.0))
        sl_pips = max(10.0, min(sl_pips, 40.0))
        
        return tp_pips, sl_pips

    def generate_signal(self, df: pd.DataFrame, index: int) -> Signal:
        if index < 1:
            return Signal.HOLD

        row = df.iloc[index]
        prev_row = df.iloc[index - 1]

        rsi = row.get("rsi")
        ema_s = row.get("ema_short")
        ema_l = row.get("ema_long")
        macd = row.get("macd")
        macd_sig = row.get("macd_signal")
        adx = row.get("adx")
        bb_upper = row.get("bb_upper")
        bb_lower = row.get("bb_lower")
        close = row.get("close")

        if pd.isna(rsi) or pd.isna(ema_s) or pd.isna(adx) or pd.isna(bb_upper):
            return Signal.HOLD

        # ─── 時間フィルター ───
        current_dt = df.index[index]
        hour_jst = (current_dt.hour + 9) % 24
        is_trading_hours = (8 <= hour_jst < 15) or (16 <= hour_jst) or (hour_jst == 0)

        # ─── トレンド継続（直近3本）判定 ───
        if index >= 3:
            bullish_3 = all(df["close"].iloc[i] > df["open"].iloc[i] for i in range(index-2, index+1))
            bearish_3 = all(df["close"].iloc[i] < df["open"].iloc[i] for i in range(index-2, index+1))
        else:
            bullish_3, bearish_3 = False, False

        # ─── エントリーシグナル（時間フィルター内のみ） ───
        if is_trading_hours and adx >= self.adx_threshold:
            # ボリンジャーバンド内判定（騙し回避）
            inside_bb = bb_lower < close < bb_upper

            # 買い条件
            ema_crossed_up = prev_row.get("ema_short") <= prev_row.get("ema_long") and ema_s > ema_l
            macd_crossed_up = prev_row.get("macd") <= prev_row.get("macd_signal") and macd > macd_sig
    
            if (ema_crossed_up or macd_crossed_up) and (ema_s > ema_l) and (macd > macd_sig) and (40 <= rsi <= 65):
                if bullish_3 and inside_bb:
                    return Signal.BUY
    
            # 売り条件
            ema_crossed_down = prev_row.get("ema_short") >= prev_row.get("ema_long") and ema_s < ema_l
            macd_crossed_down = prev_row.get("macd") >= prev_row.get("macd_signal") and macd < macd_sig
    
            if (ema_crossed_down or macd_crossed_down) and (ema_s < ema_l) and (macd < macd_sig) and (35 <= rsi <= 60):
                if bearish_3 and inside_bb:
                    return Signal.SELL

        # ─── 決済シグナル（全時間帯）───
        # ロング決済: EMAデッドクロス or RSI買われすぎ
        if ema_s < ema_l or rsi > 75:
            return Signal.CLOSE_LONG

        # ショート決済: EMAゴールデンクロス or RSI売られすぎ
        if ema_s > ema_l or rsi < 25:
            return Signal.CLOSE_SHORT

        return Signal.HOLD
