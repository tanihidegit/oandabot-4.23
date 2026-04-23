"""
OANDA FX自動売買ボット - 価格取得モジュール

リアルタイム価格の取得・監視機能を提供する。
"""

import logging
import time
from typing import Any, Callable

import oandapyV20
import oandapyV20.endpoints.pricing as pricing_ep
import pandas as pd

from config.settings import Settings
from core.client import OandaClient

logger = logging.getLogger(__name__)


class PricingManager:
    """
    価格データの取得・監視を管理するクラス。

    Attributes:
        client: OandaClientインスタンス。
        settings: アプリケーション設定。
    """

    def __init__(self, client: OandaClient | None = None) -> None:
        """
        PricingManagerを初期化する。

        Args:
            client: OandaClientインスタンス。Noneの場合は新規作成する。
        """
        self.client = client or OandaClient()
        self.settings = self.client.settings
        logger.info("PricingManagerを初期化しました")

    def get_bid_ask(self, instrument: str) -> dict[str, float]:
        """
        指定通貨ペアのBid/Askスプレッド情報を取得する。

        Args:
            instrument: 通貨ペア（例: "USD_JPY"）。

        Returns:
            bid, ask, spread, midを含む辞書。

        Raises:
            ValueError: 価格データが取得できない場合。
        """
        price_data = self.client.get_current_price(instrument)

        if not price_data:
            raise ValueError(f"価格データを取得できませんでした: {instrument}")

        bids = price_data.get("bids", [])
        asks = price_data.get("asks", [])

        if not bids or not asks:
            raise ValueError(f"Bid/Askデータが不完全です: {instrument}")

        bid = float(bids[0]["price"])
        ask = float(asks[0]["price"])
        spread = ask - bid
        mid = (bid + ask) / 2

        return {
            "bid": bid,
            "ask": ask,
            "spread": round(spread, 6),
            "mid": round(mid, 6),
        }

    def get_multiple_prices(
        self, instrument_list: list[str]
    ) -> dict[str, dict[str, float]]:
        """
        複数通貨ペアのBid/Ask情報を一括取得する。

        Args:
            instrument_list: 通貨ペアのリスト。

        Returns:
            通貨ペア名をキーとした価格情報辞書の辞書。
        """
        prices = self.client.get_prices(instrument_list)
        result: dict[str, dict[str, float]] = {}

        for price_data in prices:
            name = price_data.get("instrument", "")
            bids = price_data.get("bids", [])
            asks = price_data.get("asks", [])

            if bids and asks:
                bid = float(bids[0]["price"])
                ask = float(asks[0]["price"])
                result[name] = {
                    "bid": bid,
                    "ask": ask,
                    "spread": round(ask - bid, 6),
                    "mid": round((bid + ask) / 2, 6),
                }

        logger.info("%d件の通貨ペアの価格を処理しました", len(result))
        return result

    def get_historical_prices(
        self,
        instrument: str,
        granularity: str = "D",
        count: int = 100,
        from_time: str | None = None,
        to_time: str | None = None,
    ) -> pd.DataFrame:
        """
        過去の価格データをDataFrame形式で取得する。

        Args:
            instrument: 通貨ペア（例: "USD_JPY"）。
            granularity: 時間足（"M1","M5","M15","H1","H4","D","W"）。
            count: 取得本数（最大5000）。
            from_time: 開始日時（RFC3339形式）。
            to_time: 終了日時（RFC3339形式）。

        Returns:
            OHLCVデータのDataFrame。
        """
        return self.client.get_candles(
            instrument=instrument,
            granularity=granularity,
            count=count,
            from_time=from_time,
            to_time=to_time,
        )

    def poll_price(
        self,
        instrument: str,
        interval_seconds: float = 1.0,
        callback: Callable[[dict[str, float]], None] | None = None,
        max_iterations: int | None = None,
    ) -> None:
        """
        指定通貨ペアの価格を定期的にポーリングする。

        Args:
            instrument: 通貨ペア。
            interval_seconds: ポーリング間隔（秒）。
            callback: 価格データを受け取るコールバック関数。
            max_iterations: 最大ポーリング回数。Noneで無限ループ。
        """
        logger.info(
            "%s のポーリングを開始します（間隔: %.1f秒）",
            instrument, interval_seconds,
        )
        iteration = 0
        try:
            while max_iterations is None or iteration < max_iterations:
                try:
                    price_info = self.get_bid_ask(instrument)
                    logger.debug(
                        "%s - Bid: %.5f, Ask: %.5f, Spread: %.5f",
                        instrument, price_info["bid"],
                        price_info["ask"], price_info["spread"],
                    )
                    if callback:
                        callback(price_info)
                except oandapyV20.exceptions.V20Error as e:
                    logger.error("ポーリング中にAPIエラー: %s", e)

                iteration += 1
                time.sleep(interval_seconds)
        except KeyboardInterrupt:
            logger.info("ポーリングを停止しました（%d回実行）", iteration)
