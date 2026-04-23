"""
OANDA FX自動売買ボット - Webhookメッセージパーサー

TradingViewアラートのJSONメッセージを解析し、
注文パラメータに変換する。

対応メッセージ形式:
  {"action": "buy",   "instrument": "USD_JPY", "units": 10000}
  {"action": "sell",  "instrument": "USD_JPY", "units": 10000}
  {"action": "close", "instrument": "USD_JPY"}
"""

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# 許可されるアクション
VALID_ACTIONS = {"buy", "sell", "close"}

# 許可される通貨ペア（不正なペアをブロック）
VALID_INSTRUMENTS = {
    "USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY", "CHF_JPY",
    "CAD_JPY", "NZD_JPY", "EUR_USD", "GBP_USD", "AUD_USD",
}


@dataclass
class WebhookOrder:
    """
    パース済みのWebhook注文データ。

    Attributes:
        action: アクション（buy, sell, close）。
        instrument: 通貨ペア。
        units: 取引数量（closeの場合は0）。
        tp_price: 利確価格（任意）。
        sl_price: 損切価格（任意）。
        comment: 補足コメント（任意）。
    """
    action: str
    instrument: str
    units: int = 0
    tp_price: float | None = None
    sl_price: float | None = None
    comment: str = ""


class WebhookParser:
    """
    TradingViewアラートメッセージのパーサー。

    受信したJSONを検証し、WebhookOrderオブジェクトに変換する。

    Attributes:
        allowed_instruments: 許可する通貨ペアのセット。
    """

    def __init__(
        self,
        allowed_instruments: set[str] | None = None,
    ) -> None:
        """
        パーサーを初期化する。

        Args:
            allowed_instruments: 許可する通貨ペア。Noneの場合はデフォルトセット。
        """
        self.allowed_instruments = allowed_instruments or VALID_INSTRUMENTS
        logger.info(
            "WebhookParserを初期化: 許可通貨ペア=%d種",
            len(self.allowed_instruments),
        )

    def parse(self, data: Any) -> WebhookOrder:
        """
        メッセージデータを解析してWebhookOrderを返す。

        Args:
            data: 受信したリクエストデータ（dict or str）。

        Returns:
            WebhookOrder: パース済みの注文データ。

        Raises:
            ValueError: バリデーションエラー時。
        """
        # 文字列の場合はJSON解析
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError as e:
                raise ValueError(f"JSONの解析に失敗しました: {e}")

        if not isinstance(data, dict):
            raise ValueError("リクエストデータは辞書形式である必要があります")

        # アクションの検証
        action = data.get("action", "").lower().strip()
        if action not in VALID_ACTIONS:
            raise ValueError(
                f"不正なアクション: '{action}'。"
                f"使用可能: {', '.join(VALID_ACTIONS)}"
            )

        # 通貨ペアの検証
        instrument = data.get("instrument", "").upper().strip()
        if not instrument:
            raise ValueError("instrumentが指定されていません")
        if instrument not in self.allowed_instruments:
            raise ValueError(
                f"許可されていない通貨ペア: '{instrument}'"
            )

        # 取引数量の検証（close以外は必須）
        units = 0
        if action != "close":
            try:
                units = int(data.get("units", 0))
            except (TypeError, ValueError):
                raise ValueError("unitsは整数で指定してください")
            if units <= 0:
                raise ValueError(f"unitsは正の整数を指定してください: {units}")

        # オプションパラメータ
        tp_price = self._parse_float(data.get("tp_price"))
        sl_price = self._parse_float(data.get("sl_price"))
        comment = str(data.get("comment", ""))

        order = WebhookOrder(
            action=action,
            instrument=instrument,
            units=units,
            tp_price=tp_price,
            sl_price=sl_price,
            comment=comment,
        )

        logger.info(
            "Webhookメッセージをパース: action=%s, instrument=%s, units=%d",
            order.action, order.instrument, order.units,
        )
        return order

    @staticmethod
    def _parse_float(value: Any) -> float | None:
        """値をfloatに変換する。無効値はNone。"""
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
