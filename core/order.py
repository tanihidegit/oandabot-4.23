"""
OANDA FX自動売買ボット - 注文管理モジュール（本番対応版）

成行注文、指値注文の作成、トレード変更・決済、待機注文の管理を行う。
全操作にリトライ機能・約定確認ロジック・CSV注文履歴保存を備える。
"""

import csv
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import oandapyV20
import oandapyV20.endpoints.orders as orders
import oandapyV20.endpoints.trades as trades
import oandapyV20.endpoints.positions as positions

from config.settings import Settings
from core.client import OandaClient

logger = logging.getLogger(__name__)

# リトライ設定
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0       # 初回リトライの待機秒数
RATE_LIMIT_DELAY = 5.0       # レート制限時の追加待機秒数


class OrderResult:
    """
    注文結果を格納するクラス。

    約定・拒否の判定結果とレスポンスデータを保持する。

    Attributes:
        success: 約定成功フラグ。
        order_id: 注文ID。
        trade_id: 約定トレードID（成行約定時のみ）。
        fill_price: 約定価格（成行約定時のみ）。
        raw_response: APIレスポンスの生データ。
        reject_reason: 拒否理由（拒否時のみ）。
    """

    def __init__(self, response: dict[str, Any]) -> None:
        """
        APIレスポンスを解析して結果を生成する。

        Args:
            response: OANDA APIの注文レスポンス辞書。
        """
        self.raw_response = response
        self.success = False
        self.order_id: str = ""
        self.trade_id: str = ""
        self.fill_price: float = 0.0
        self.reject_reason: str = ""
        self._parse(response)

    def _parse(self, response: dict[str, Any]) -> None:
        """レスポンスを解析する。"""
        # 成行約定チェック
        fill_tx = response.get("orderFillTransaction")
        if fill_tx:
            self.success = True
            self.order_id = fill_tx.get("orderID", "")
            self.trade_id = fill_tx.get("tradeOpened", {}).get("tradeID", "")
            self.fill_price = float(fill_tx.get("price", 0))
            return

        # 注文作成チェック（指値等の待機注文）
        create_tx = response.get("orderCreateTransaction")
        if create_tx:
            self.success = True
            self.order_id = create_tx.get("id", "")
            return

        # 拒否チェック
        cancel_tx = response.get("orderCancelTransaction")
        if cancel_tx:
            self.success = False
            self.order_id = cancel_tx.get("orderID", "")
            self.reject_reason = cancel_tx.get("reason", "不明")
            return

        reject_tx = response.get("orderRejectTransaction")
        if reject_tx:
            self.success = False
            self.reject_reason = reject_tx.get("rejectReason", "不明")

    def __repr__(self) -> str:
        """結果の文字列表現。"""
        if self.success:
            return (
                f"OrderResult(success=True, order_id={self.order_id}, "
                f"trade_id={self.trade_id}, fill_price={self.fill_price})"
            )
        return (
            f"OrderResult(success=False, reason='{self.reject_reason}')"
        )


class OrderManager:
    """
    注文の作成・管理を行うクラス（本番対応版）。

    全操作にリトライ機能（最大3回、レート制限時バックオフ）を備え、
    約定確認ロジックでFill/Cancelを判定する。
    オプションで注文履歴をCSVに追記保存する。

    Attributes:
        client: OandaClientインスタンス。
        settings: アプリケーション設定。
        history_path: 注文履歴CSVの保存先パス。Noneで無効。
    """

    def __init__(
        self,
        client: OandaClient | None = None,
        history_path: str | Path | None = None,
    ) -> None:
        """
        OrderManagerを初期化する。

        Args:
            client: OandaClientインスタンス。Noneの場合は新規作成する。
            history_path: 注文履歴CSVの保存先パス。Noneの場合は保存しない。
        """
        self.client = client or OandaClient()
        self.settings = self.client.settings
        self.history_path: Path | None = None

        if history_path:
            self.history_path = Path(history_path)
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_csv_header()

        logger.info("OrderManagerを初期化しました（履歴CSV: %s）", self.history_path)

    # ═══════════════════════════════════════════════════════
    #  注文発行
    # ═══════════════════════════════════════════════════════

    def market_order(
        self,
        instrument: str,
        units: int,
        tp_price: float | None = None,
        sl_price: float | None = None,
    ) -> OrderResult:
        """
        成行注文を発行する。

        Args:
            instrument: 通貨ペア（例: "USD_JPY"）。
            units: 取引数量。正=買い、負=売り。
            tp_price: 利確価格（任意）。
            sl_price: 損切価格（任意）。

        Returns:
            OrderResult: 約定結果。
        """
        order_body: dict[str, Any] = {
            "type": "MARKET",
            "instrument": instrument,
            "units": str(units),
        }
        self._attach_tp_sl(order_body, tp_price, sl_price)

        logger.info(
            "成行注文を発行します: %s %+d units (TP=%s, SL=%s)",
            instrument, units, tp_price, sl_price,
        )

        result = self._submit_order(order_body)
        self._log_history("MARKET", instrument, units, result, tp_price, sl_price)
        return result

    def limit_order(
        self,
        instrument: str,
        units: int,
        price: float,
        tp_price: float | None = None,
        sl_price: float | None = None,
        time_in_force: str = "GTC",
    ) -> OrderResult:
        """
        指値注文を発行する。

        Args:
            instrument: 通貨ペア。
            units: 取引数量。正=買い、負=売り。
            price: 指値価格。
            tp_price: 利確価格（任意）。
            sl_price: 損切価格（任意）。
            time_in_force: 有効期間。"GTC"=取消まで, "GFD"=当日のみ。

        Returns:
            OrderResult: 注文登録結果。
        """
        order_body: dict[str, Any] = {
            "type": "LIMIT",
            "instrument": instrument,
            "units": str(units),
            "price": self._fmt(price),
            "timeInForce": time_in_force,
        }
        self._attach_tp_sl(order_body, tp_price, sl_price)

        logger.info(
            "指値注文を発行します: %s %+d @ %.5f (TP=%s, SL=%s)",
            instrument, units, price, tp_price, sl_price,
        )

        result = self._submit_order(order_body)
        self._log_history("LIMIT", instrument, units, result, tp_price, sl_price)
        return result

    # ═══════════════════════════════════════════════════════
    #  トレード操作
    # ═══════════════════════════════════════════════════════

    def modify_trade(
        self,
        trade_id: str,
        tp_price: float | None = None,
        sl_price: float | None = None,
        trailing_stop_distance: float | None = None,
    ) -> dict[str, Any]:
        """
        既存トレードのTP/SL/トレーリングストップを変更する。

        Args:
            trade_id: 変更するトレードのID。
            tp_price: 新しい利確価格。Noneで変更なし。
            sl_price: 新しい損切価格。Noneで変更なし。
            trailing_stop_distance: トレーリングストップ距離（pips相当の価格差）。

        Returns:
            APIレスポンスの辞書。
        """
        data: dict[str, Any] = {}

        if tp_price is not None:
            data["takeProfit"] = {"price": self._fmt(tp_price)}
        if sl_price is not None:
            data["stopLoss"] = {"price": self._fmt(sl_price)}
        if trailing_stop_distance is not None:
            data["trailingStopLoss"] = {
                "distance": self._fmt(trailing_stop_distance),
            }

        if not data:
            raise ValueError("変更するパラメータが指定されていません")

        logger.info(
            "トレード変更: ID=%s, TP=%s, SL=%s, TSL=%s",
            trade_id, tp_price, sl_price, trailing_stop_distance,
        )

        def _request() -> dict[str, Any]:
            endpoint = trades.TradeCRCDO(
                accountID=self.settings.account_id,
                tradeID=trade_id,
                data=data,
            )
            return self.client.api.request(endpoint)

        response = self._retry(_request, "トレード変更")
        logger.info("トレードを変更しました: ID=%s", trade_id)
        return response

    def close_trade(
        self,
        trade_id: str,
        units: str = "ALL",
    ) -> dict[str, Any]:
        """
        指定トレードを決済する。

        Args:
            trade_id: 決済するトレードのID。
            units: 決済数量。"ALL"で全決済、数値文字列で部分決済。

        Returns:
            決済レスポンスの辞書。
        """
        data = {"units": units}

        logger.info("トレード決済: ID=%s, units=%s", trade_id, units)

        def _request() -> dict[str, Any]:
            endpoint = trades.TradeClose(
                accountID=self.settings.account_id,
                tradeID=trade_id,
                data=data,
            )
            return self.client.api.request(endpoint)

        response = self._retry(_request, "トレード決済")

        # 決済結果の確認
        close_tx = response.get("orderFillTransaction", {})
        realized_pl = close_tx.get("pl", "0")
        logger.info(
            "トレードを決済しました: ID=%s, 実現損益=%s",
            trade_id, realized_pl,
        )

        self._log_history_close(trade_id, units, realized_pl)
        return response

    def close_all(
        self,
        instrument: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        全ポジションを決済する。通貨ペア指定で絞り込み可。

        Args:
            instrument: 対象通貨ペア。Noneの場合は全通貨ペアを決済。

        Returns:
            各トレードの決済レスポンスリスト。
        """
        open_trades = self.get_open_trades()

        if instrument:
            open_trades = [
                t for t in open_trades if t.get("instrument") == instrument
            ]

        if not open_trades:
            logger.info("決済対象のトレードがありません")
            return []

        logger.info(
            "全ポジション決済を開始します: %d件 (通貨ペア: %s)",
            len(open_trades), instrument or "全て",
        )

        results = []
        for trade in open_trades:
            tid = trade.get("id", "")
            try:
                result = self.close_trade(tid, "ALL")
                results.append(result)
            except Exception as e:
                logger.error("トレード %s の決済に失敗: %s", tid, e)

        logger.info("全ポジション決済完了: %d/%d件", len(results), len(open_trades))
        return results

    # ═══════════════════════════════════════════════════════
    #  照会系
    # ═══════════════════════════════════════════════════════

    def get_open_trades(self) -> list[dict[str, Any]]:
        """
        保有中のトレード一覧を取得する。

        Returns:
            オープントレードの辞書リスト。
        """
        def _request() -> dict[str, Any]:
            endpoint = trades.OpenTrades(
                accountID=self.settings.account_id,
            )
            return self.client.api.request(endpoint)

        response = self._retry(_request, "オープントレード取得")
        trade_list = response.get("trades", [])
        logger.info("オープントレード: %d件", len(trade_list))
        return trade_list

    def get_pending_orders(self) -> list[dict[str, Any]]:
        """
        待機中（未約定）の注文一覧を取得する。

        Returns:
            待機注文の辞書リスト。
        """
        def _request() -> dict[str, Any]:
            endpoint = orders.OrdersPending(
                accountID=self.settings.account_id,
            )
            return self.client.api.request(endpoint)

        response = self._retry(_request, "待機注文取得")
        order_list = response.get("orders", [])
        logger.info("待機注文: %d件", len(order_list))
        return order_list

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        """
        待機注文をキャンセルする。

        Args:
            order_id: キャンセルする注文のID。

        Returns:
            キャンセルレスポンスの辞書。
        """
        logger.info("注文キャンセル: ID=%s", order_id)

        def _request() -> dict[str, Any]:
            endpoint = orders.OrderCancel(
                accountID=self.settings.account_id,
                orderID=order_id,
            )
            return self.client.api.request(endpoint)

        response = self._retry(_request, "注文キャンセル")
        logger.info("注文をキャンセルしました: ID=%s", order_id)
        return response

    # ═══════════════════════════════════════════════════════
    #  内部ヘルパー
    # ═══════════════════════════════════════════════════════

    @staticmethod
    def _fmt(price: float) -> str:
        """価格を5桁文字列にフォーマットする。"""
        return f"{price:.5f}"

    @staticmethod
    def _attach_tp_sl(
        order_body: dict[str, Any],
        tp_price: float | None,
        sl_price: float | None,
    ) -> None:
        """注文ボディにTP/SLを付加する。"""
        if tp_price is not None:
            order_body["takeProfitOnFill"] = {
                "price": f"{tp_price:.5f}",
            }
        if sl_price is not None:
            order_body["stopLossOnFill"] = {
                "price": f"{sl_price:.5f}",
            }

    def _submit_order(self, order_body: dict[str, Any]) -> OrderResult:
        """
        注文をAPIに送信し、約定結果を返す（リトライ付き）。

        Args:
            order_body: 注文データの辞書（"order"キーの中身）。

        Returns:
            OrderResult: 約定結果。
        """
        data = {"order": order_body}

        def _request() -> dict[str, Any]:
            endpoint = orders.OrderCreate(
                accountID=self.settings.account_id,
                data=data,
            )
            return self.client.api.request(endpoint)

        response = self._retry(_request, "注文発行")
        result = OrderResult(response)

        if result.success:
            logger.info(
                "注文が受理されました: order_id=%s, trade_id=%s, price=%s",
                result.order_id, result.trade_id, result.fill_price,
            )
        else:
            logger.warning(
                "注文が拒否されました: reason=%s", result.reject_reason,
            )

        return result

    def _retry(
        self,
        func: Any,
        operation_name: str,
    ) -> dict[str, Any]:
        """
        APIリクエストをリトライ付きで実行する。

        V20Errorが発生した場合、最大MAX_RETRIES回リトライする。
        レート制限エラー時は追加の待機時間を取る。

        Args:
            func: 実行するAPIリクエスト関数。
            operation_name: 操作名（ログ用）。

        Returns:
            APIレスポンスの辞書。

        Raises:
            oandapyV20.exceptions.V20Error: リトライ上限を超えた場合。
        """
        last_error = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return func()

            except oandapyV20.exceptions.V20Error as e:
                last_error = e
                error_msg = str(e)

                # レート制限チェック（HTTP 429）
                is_rate_limit = "429" in error_msg or "Rate" in error_msg

                if attempt < MAX_RETRIES:
                    delay = RETRY_BASE_DELAY * attempt
                    if is_rate_limit:
                        delay += RATE_LIMIT_DELAY
                        logger.warning(
                            "%s: レート制限エラー（リトライ %d/%d, %.1f秒待機）",
                            operation_name, attempt, MAX_RETRIES, delay,
                        )
                    else:
                        logger.warning(
                            "%s: APIエラー（リトライ %d/%d, %.1f秒待機）: %s",
                            operation_name, attempt, MAX_RETRIES, delay, e,
                        )
                    time.sleep(delay)
                else:
                    logger.error(
                        "%s: リトライ上限到達（%d回）: %s",
                        operation_name, MAX_RETRIES, e,
                    )

        raise last_error  # type: ignore[misc]

    # ═══════════════════════════════════════════════════════
    #  CSV注文履歴
    # ═══════════════════════════════════════════════════════

    def _init_csv_header(self) -> None:
        """CSV履歴ファイルのヘッダーを初期化する（ファイルが無い場合のみ）。"""
        if self.history_path and not self.history_path.exists():
            with open(self.history_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp", "action", "order_type", "instrument",
                    "units", "order_id", "trade_id", "fill_price",
                    "tp_price", "sl_price", "success", "reject_reason",
                    "realized_pl",
                ])

    def _log_history(
        self,
        order_type: str,
        instrument: str,
        units: int,
        result: OrderResult,
        tp_price: float | None = None,
        sl_price: float | None = None,
    ) -> None:
        """注文履歴をCSVに追記する。"""
        if not self.history_path:
            return

        try:
            with open(self.history_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    datetime.now(timezone.utc).isoformat(),
                    "ORDER",
                    order_type,
                    instrument,
                    units,
                    result.order_id,
                    result.trade_id,
                    result.fill_price,
                    tp_price or "",
                    sl_price or "",
                    result.success,
                    result.reject_reason,
                    "",
                ])
        except Exception as e:
            logger.error("注文履歴の書き込みに失敗しました: %s", e)

    def _log_history_close(
        self,
        trade_id: str,
        units: str,
        realized_pl: str,
    ) -> None:
        """決済履歴をCSVに追記する。"""
        if not self.history_path:
            return

        try:
            with open(self.history_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    datetime.now(timezone.utc).isoformat(),
                    "CLOSE",
                    "",
                    "",
                    units,
                    "",
                    trade_id,
                    "",
                    "",
                    "",
                    True,
                    "",
                    realized_pl,
                ])
        except Exception as e:
            logger.error("決済履歴の書き込みに失敗しました: %s", e)
