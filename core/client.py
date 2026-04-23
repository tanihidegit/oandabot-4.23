"""
OANDA FX自動売買ボット - APIクライアントモジュール

oandapyV20のAPIクラスをラップし、OANDA v20 REST APIとの通信を管理する。
口座情報取得、現在レート取得、ローソク足データ取得などの機能を提供する。
"""

import logging
from typing import Any

import oandapyV20
import oandapyV20.endpoints.accounts as accounts
import oandapyV20.endpoints.instruments as instruments
import oandapyV20.endpoints.pricing as pricing_ep
import pandas as pd
from dateutil import parser as dateutil_parser

from config.settings import Settings

logger = logging.getLogger(__name__)


class OandaClient:
    """
    OANDA v20 REST APIクライアント。

    oandapyV20ライブラリをラップし、よく使うAPI操作を
    シンプルなメソッドとして提供する。

    Attributes:
        settings: アプリケーション設定。
        api: oandapyV20のAPIインスタンス。
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """
        クライアントを初期化する。

        Args:
            settings: 設定オブジェクト。Noneの場合は新規にSettingsを生成する。
        """
        self.settings = settings or Settings()
        self.api = oandapyV20.API(
            access_token=self.settings.access_token,
            environment=self.settings.environment,
        )
        logger.info(
            "OandaClientを初期化しました（環境: %s）",
            self.settings.environment,
        )

    def get_account_summary(self) -> dict[str, Any]:
        """
        口座のサマリー情報を取得する。

        残高、証拠金、含み損益などの口座概要情報を返す。

        Returns:
            口座サマリー情報の辞書。主なキー:
            - balance: 残高
            - unrealizedPL: 未実現損益
            - marginUsed: 使用中証拠金
            - marginAvailable: 利用可能証拠金
            - openTradeCount: オープンポジション数

        Raises:
            oandapyV20.exceptions.V20Error: API通信エラー時。
        """
        endpoint = accounts.AccountSummary(accountID=self.settings.account_id)

        try:
            response = self.api.request(endpoint)
            summary = response.get("account", {})
            logger.info(
                "口座サマリーを取得しました（残高: %s, 未実現P&L: %s）",
                summary.get("balance", "N/A"),
                summary.get("unrealizedPL", "N/A"),
            )
            return summary
        except oandapyV20.exceptions.V20Error as e:
            logger.error("口座サマリーの取得に失敗しました: %s", e)
            raise

    def get_account_details(self) -> dict[str, Any]:
        """
        口座の詳細情報を取得する。

        サマリーに加え、オープンポジションやオーダーの詳細も含む
        完全な口座情報を返す。

        Returns:
            口座詳細情報の辞書。

        Raises:
            oandapyV20.exceptions.V20Error: API通信エラー時。
        """
        endpoint = accounts.AccountDetails(accountID=self.settings.account_id)

        try:
            response = self.api.request(endpoint)
            details = response.get("account", {})
            logger.info("口座詳細情報を取得しました")
            return details
        except oandapyV20.exceptions.V20Error as e:
            logger.error("口座詳細情報の取得に失敗しました: %s", e)
            raise

    def get_current_price(self, instrument: str) -> dict[str, Any]:
        """
        指定通貨ペアの現在レートを取得する。

        Args:
            instrument: 通貨ペア（例: "USD_JPY", "EUR_USD"）。

        Returns:
            価格情報の辞書。主なキー:
            - instrument: 通貨ペア名
            - asks: 売値リスト（price, liquidity）
            - bids: 買値リスト（price, liquidity）
            - closeoutAsk: クローズアウト売値
            - closeoutBid: クローズアウト買値

        Raises:
            oandapyV20.exceptions.V20Error: API通信エラー時。
        """
        params = {"instruments": instrument}
        endpoint = pricing_ep.PricingInfo(
            accountID=self.settings.account_id,
            params=params,
        )

        try:
            response = self.api.request(endpoint)
            prices = response.get("prices", [])
            if prices:
                price_data = prices[0]
                logger.info(
                    "%s 現在レート - Bid: %s, Ask: %s",
                    instrument,
                    price_data.get("bids", [{}])[0].get("price", "N/A"),
                    price_data.get("asks", [{}])[0].get("price", "N/A"),
                )
                return price_data
            else:
                logger.warning("価格データが空です: %s", instrument)
                return {}
        except oandapyV20.exceptions.V20Error as e:
            logger.error("現在レートの取得に失敗しました（%s）: %s", instrument, e)
            raise

    def get_prices(self, instrument_list: list[str]) -> list[dict[str, Any]]:
        """
        複数通貨ペアの現在レートを一括取得する。

        Args:
            instrument_list: 通貨ペアのリスト（例: ["USD_JPY", "EUR_USD"]）。

        Returns:
            各通貨ペアの価格情報辞書のリスト。

        Raises:
            oandapyV20.exceptions.V20Error: API通信エラー時。
        """
        instruments_str = ",".join(instrument_list)
        params = {"instruments": instruments_str}
        endpoint = pricing_ep.PricingInfo(
            accountID=self.settings.account_id,
            params=params,
        )

        try:
            response = self.api.request(endpoint)
            prices = response.get("prices", [])
            logger.info("%d件の通貨ペアの価格を取得しました", len(prices))
            return prices
        except oandapyV20.exceptions.V20Error as e:
            logger.error("複数通貨ペアの価格取得に失敗しました: %s", e)
            raise

    def get_candles(
        self,
        instrument: str,
        granularity: str = "H1",
        count: int = 100,
        from_time: str | None = None,
        to_time: str | None = None,
        price: str = "M",
    ) -> pd.DataFrame:
        """
        指定通貨ペアのローソク足データを取得する。

        Args:
            instrument: 通貨ペア（例: "USD_JPY"）。
            granularity: 時間足（例: "M1", "M5", "M15", "H1", "H4", "D", "W"）。
            count: 取得するローソク足の本数（最大5000）。
                   from_time指定時は無視される。
            from_time: 取得開始日時（RFC3339形式、例: "2024-01-01T00:00:00Z"）。
            to_time: 取得終了日時（RFC3339形式）。
            price: 価格の種類。"M"=中間値, "B"=Bid, "A"=Ask, "BA"=Bid+Ask。

        Returns:
            ローソク足データのDataFrame。カラム:
            - time: 日時
            - open: 始値
            - high: 高値
            - low: 安値
            - close: 終値
            - volume: 出来高
            - complete: 確定足かどうか

        Raises:
            oandapyV20.exceptions.V20Error: API通信エラー時。
        """
        params: dict[str, Any] = {
            "granularity": granularity,
            "price": price,
        }

        if from_time:
            params["from"] = from_time
            if to_time:
                params["to"] = to_time
        else:
            params["count"] = count

        endpoint = instruments.InstrumentsCandles(
            instrument=instrument,
            params=params,
        )

        try:
            response = self.api.request(endpoint)
            candles = response.get("candles", [])

            if not candles:
                logger.warning(
                    "ローソク足データが空です: %s (%s)", instrument, granularity
                )
                return pd.DataFrame()

            # DataFrameに変換
            records = []
            price_key = "mid"  # デフォルトは中間値
            if price == "B":
                price_key = "bid"
            elif price == "A":
                price_key = "ask"

            for candle in candles:
                price_data = candle.get(price_key, {})
                records.append(
                    {
                        "time": dateutil_parser.parse(candle["time"]),
                        "open": float(price_data.get("o", 0)),
                        "high": float(price_data.get("h", 0)),
                        "low": float(price_data.get("l", 0)),
                        "close": float(price_data.get("c", 0)),
                        "volume": int(candle.get("volume", 0)),
                        "complete": candle.get("complete", False),
                    }
                )

            df = pd.DataFrame(records)
            df.set_index("time", inplace=True)

            logger.info(
                "ローソク足データを取得しました: %s %s %d本",
                instrument,
                granularity,
                len(df),
            )
            return df

        except oandapyV20.exceptions.V20Error as e:
            logger.error(
                "ローソク足データの取得に失敗しました（%s %s）: %s",
                instrument,
                granularity,
                e,
            )
            raise

    def get_instruments(self) -> list[dict[str, Any]]:
        """
        取引可能な通貨ペア（商品）の一覧を取得する。

        Returns:
            通貨ペア情報の辞書リスト。各辞書のキー:
            - name: 通貨ペア名（例: "USD_JPY"）
            - type: 商品タイプ（例: "CURRENCY"）
            - displayName: 表示名（例: "USD/JPY"）

        Raises:
            oandapyV20.exceptions.V20Error: API通信エラー時。
        """
        endpoint = accounts.AccountInstruments(
            accountID=self.settings.account_id,
        )

        try:
            response = self.api.request(endpoint)
            instrument_list = response.get("instruments", [])
            logger.info("取引可能通貨ペア: %d件", len(instrument_list))
            return instrument_list
        except oandapyV20.exceptions.V20Error as e:
            logger.error("通貨ペア一覧の取得に失敗しました: %s", e)
            raise
