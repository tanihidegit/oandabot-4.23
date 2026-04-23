"""
OANDA FX自動売買ボット - ポジションサイズ計算モジュール

口座残高とリスク許容率（%）から、指定されたストップロス幅に対して
適切なポジションサイズ（取引数量）を計算する。

通貨ペアの種類（クロス円/その他）を自動判定し、
1pipの値を適切に設定する。
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 1pipの価格変動量（通貨ペア種類別）
PIP_VALUES = {
    "JPY": 0.01,      # クロス円（USD_JPY, EUR_JPY等）
    "DEFAULT": 0.0001,  # その他（EUR_USD, GBP_USD等）
}


class PositionSizer:
    """
    ポジションサイズ計算クラス。

    口座残高に対するリスク率と損切り幅から、最適な取引数量を算出する。
    ケリー基準やフィックスドフラクショナル法に基づくサイジングも可能。

    Attributes:
        account_balance: 口座残高（円）。
        default_risk_pct: デフォルトのリスク率（%）。
        min_units: 最小取引単位。
        max_units: 最大取引単位。
    """

    def __init__(
        self,
        account_balance: float = 1_000_000.0,
        default_risk_pct: float = 2.0,
        min_units: int = 1,
        max_units: int = 1_000_000,
    ) -> None:
        """
        PositionSizerを初期化する。

        Args:
            account_balance: 口座残高（円）。
            default_risk_pct: デフォルトのリスク率（デフォルト2%）。
            min_units: 最小取引単位（デフォルト1）。
            max_units: 最大取引単位（デフォルト100万）。
        """
        self.account_balance = account_balance
        self.default_risk_pct = default_risk_pct
        self.min_units = min_units
        self.max_units = max_units

        logger.info(
            "PositionSizerを初期化: 残高=%.0f, デフォルトリスク=%.1f%%",
            account_balance, default_risk_pct,
        )

    def calculate_units(
        self,
        instrument: str,
        stop_loss_pips: float,
        risk_pct: float | None = None,
    ) -> int:
        """
        ポジションサイズ（取引数量）を計算する。

        リスク金額 ÷ (ストップロス幅 × 1pipの値) = 取引数量

        Args:
            instrument: 通貨ペア（例: "USD_JPY", "EUR_USD"）。
            stop_loss_pips: ストップロス幅（pips）。
            risk_pct: リスク許容率（%）。Noneの場合はデフォルト値。

        Returns:
            計算された取引数量（整数）。min_units〜max_unitsの範囲内。

        Raises:
            ValueError: stop_loss_pipsが0以下の場合。
        """
        if stop_loss_pips <= 0:
            raise ValueError(
                f"ストップロス幅は正の値を指定してください: {stop_loss_pips}"
            )

        risk = risk_pct if risk_pct is not None else self.default_risk_pct
        pip_value = self.get_pip_value(instrument)

        # リスク金額（円）
        risk_amount = self.account_balance * risk / 100

        # ストップロスの金額（1通貨あたり）
        sl_amount_per_unit = stop_loss_pips * pip_value

        # 取引数量 = リスク金額 ÷ 1通貨あたりのSL金額
        if sl_amount_per_unit <= 0:
            logger.warning("SL金額が0以下のため最小単位を返します")
            return self.min_units

        raw_units = risk_amount / sl_amount_per_unit

        # 整数に丸めて範囲内に収める
        units = int(raw_units)
        units = max(self.min_units, min(self.max_units, units))

        logger.info(
            "ポジションサイズ計算: %s, SL=%.1f pips, リスク=%.1f%% "
            "→ リスク金額=%.0f円, 取引数量=%d",
            instrument, stop_loss_pips, risk, risk_amount, units,
        )

        return units

    def calculate_risk_amount(
        self,
        instrument: str,
        units: int,
        stop_loss_pips: float,
    ) -> dict[str, float]:
        """
        指定数量でのリスク金額を計算する（逆算）。

        Args:
            instrument: 通貨ペア。
            units: 取引数量。
            stop_loss_pips: ストップロス幅（pips）。

        Returns:
            risk_amount（円）, risk_pct（%）を含む辞書。
        """
        pip_value = self.get_pip_value(instrument)
        risk_amount = units * stop_loss_pips * pip_value
        risk_pct = risk_amount / self.account_balance * 100

        return {
            "risk_amount": round(risk_amount, 2),
            "risk_pct": round(risk_pct, 4),
            "units": units,
            "stop_loss_pips": stop_loss_pips,
            "pip_value": pip_value,
        }

    @staticmethod
    def get_pip_value(instrument: str) -> float:
        """
        通貨ペアの1pipの値を自動判定する。

        クロス円（末尾がJPY）は0.01、その他は0.0001。

        Args:
            instrument: 通貨ペア（例: "USD_JPY", "EUR_USD"）。

        Returns:
            1pipの価格変動量。
        """
        if instrument.upper().endswith("JPY"):
            return PIP_VALUES["JPY"]
        return PIP_VALUES["DEFAULT"]

    def update_balance(self, new_balance: float) -> None:
        """
        口座残高を更新する。

        Args:
            new_balance: 最新の口座残高（円）。
        """
        self.account_balance = new_balance
        logger.info("PositionSizer残高更新: %.0f円", new_balance)

    def get_sizing_table(
        self,
        instrument: str,
        sl_pips_range: list[float] | None = None,
        risk_pct_range: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        """
        SL幅×リスク率のサイジング早見表を生成する。

        Args:
            instrument: 通貨ペア。
            sl_pips_range: SL幅のリスト。Noneの場合はデフォルト値。
            risk_pct_range: リスク率のリスト。Noneの場合はデフォルト値。

        Returns:
            各組み合わせの取引数量を含む辞書のリスト。
        """
        if sl_pips_range is None:
            sl_pips_range = [10, 20, 30, 50, 100]
        if risk_pct_range is None:
            risk_pct_range = [0.5, 1.0, 2.0, 3.0, 5.0]

        table = []
        for sl in sl_pips_range:
            for risk in risk_pct_range:
                units = self.calculate_units(instrument, sl, risk)
                table.append({
                    "instrument": instrument,
                    "sl_pips": sl,
                    "risk_pct": risk,
                    "units": units,
                    "risk_amount": round(
                        self.account_balance * risk / 100, 0
                    ),
                })
        return table
