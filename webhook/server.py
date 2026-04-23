"""
OANDA FX自動売買ボット - Webhookサーバー

TradingViewからのアラート通知をHTTP POSTで受信し、
OANDA APIを通じて注文を実行するFlaskサーバー。

セキュリティ:
  - WEBHOOK_SECRET（.env設定）による認証
  - JSONバリデーション
  - 許可通貨ペアのホワイトリスト

使い方:
  python main.py webhook --port 5000
"""

import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from flask import Flask, request, jsonify
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import Settings
from core.client import OandaClient
from core.order import OrderManager
from risk.guard import RiskGuard, RiskConfig
from monitor.notifier import Notifier
from webhook.parser import WebhookParser

logger = logging.getLogger(__name__)
JST = timezone(timedelta(hours=9))


def create_app() -> Flask:
    """
    Flask Webhookアプリケーションを生成する。

    Returns:
        設定済みのFlaskアプリ。
    """
    load_dotenv()
    app = Flask(__name__)

    # ─── 設定 ─────────────────────────────────────────
    webhook_secret = os.getenv("WEBHOOK_SECRET", "")
    if not webhook_secret or webhook_secret == "your_webhook_secret_here":
        logger.warning(
            "⚠️ WEBHOOK_SECRETが未設定です。"
            ".envに安全な秘密キーを設定してください。"
        )

    # ─── モジュール初期化 ─────────────────────────────
    settings = Settings()
    client = OandaClient(settings)

    summary = client.get_account_summary()
    balance = float(summary.get("balance", 1_000_000))

    order_manager = OrderManager(
        client=client,
        history_path=str(PROJECT_ROOT / "data" / "order_history" / "webhook_orders.csv"),
    )
    risk_guard = RiskGuard(
        config=RiskConfig(), account_balance=balance,
    )
    notifier = Notifier()
    parser = WebhookParser()

    # ─── ルーティング ─────────────────────────────────

    @app.route("/webhook", methods=["POST"])
    def webhook_handler():
        """
        TradingViewからのWebhookを受信し注文を実行する。

        リクエストボディ（JSON）:
          {"action": "buy/sell/close", "instrument": "USD_JPY", "units": 10000, "secret": "xxx"}

        Returns:
            JSON: 処理結果。
        """
        # 1. Content-Typeチェック
        if not request.is_json:
            logger.warning("非JSONリクエストを拒否しました")
            return jsonify({"error": "Content-Type must be application/json"}), 400

        data = request.get_json(silent=True)
        if data is None:
            return jsonify({"error": "JSONの解析に失敗しました"}), 400

        # 2. 秘密キー認証
        if webhook_secret:
            received_secret = data.get("secret", "")
            if received_secret != webhook_secret:
                logger.warning("認証失敗: 不正なsecretキー")
                return jsonify({"error": "認証失敗: 不正なsecretキー"}), 401

        now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
        logger.info("Webhook受信 [%s]: %s", now_str, data)

        # 3. メッセージパース
        try:
            order = parser.parse(data)
        except ValueError as e:
            logger.warning("バリデーションエラー: %s", e)
            return jsonify({"error": str(e)}), 400

        # 4. リスクチェック（close以外）
        if order.action != "close" and not risk_guard.can_trade():
            msg = "リスクガードによりブロックされました"
            logger.warning(msg)
            notifier.notify_error(msg, "webhook")
            return jsonify({"error": msg}), 403

        # 5. 注文実行
        try:
            result = _execute_order(
                order_manager, notifier, risk_guard, order,
            )
            return jsonify(result), 200

        except Exception as e:
            logger.error("注文実行エラー: %s", e)
            notifier.notify_error(str(e), "webhook_order")
            return jsonify({"error": str(e)}), 500

    @app.route("/health", methods=["GET"])
    def health_check():
        """ヘルスチェック用エンドポイント。"""
        return jsonify({
            "status": "ok",
            "environment": settings.environment,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }), 200

    return app


def _execute_order(
    om: OrderManager,
    notifier: Notifier,
    risk_guard: RiskGuard,
    order: Any,
) -> dict[str, Any]:
    """
    パース済み注文を実行する。

    Args:
        om: OrderManager。
        notifier: Notifier。
        risk_guard: RiskGuard。
        order: WebhookOrderオブジェクト。

    Returns:
        実行結果の辞書。
    """
    if order.action == "close":
        # 全決済
        results = om.close_all(instrument=order.instrument)
        total_pl = 0.0
        for resp in results:
            fill_tx = resp.get("orderFillTransaction", {})
            total_pl += float(fill_tx.get("pl", 0))

        notifier.notify_trade_close(
            instrument=order.instrument, pips=0, profit_loss=total_pl,
        )
        risk_guard.log_trade({
            "instrument": order.instrument,
            "direction": "CLOSE",
            "units": 0,
            "profit_loss": total_pl,
            "status": "CLOSED",
        })

        return {
            "status": "closed",
            "instrument": order.instrument,
            "closed_count": len(results),
            "total_pl": total_pl,
        }

    # buy / sell
    units = order.units if order.action == "buy" else -order.units

    result = om.market_order(
        instrument=order.instrument,
        units=units,
        tp_price=order.tp_price,
        sl_price=order.sl_price,
    )

    direction = "BUY" if order.action == "buy" else "SELL"

    if result.success:
        notifier.notify_order_fill(
            instrument=order.instrument,
            direction=direction,
            units=abs(units),
            fill_price=result.fill_price,
            tp_price=order.tp_price,
            sl_price=order.sl_price,
        )
        risk_guard.log_trade({
            "instrument": order.instrument,
            "direction": direction,
            "units": abs(units),
            "profit_loss": 0,
            "status": "OPEN",
        })

        return {
            "status": "filled",
            "order_id": result.order_id,
            "trade_id": result.trade_id,
            "fill_price": result.fill_price,
            "direction": direction,
            "instrument": order.instrument,
            "units": abs(units),
        }
    else:
        notifier.notify_error(
            f"Webhook注文拒否: {result.reject_reason}", "webhook",
        )
        return {
            "status": "rejected",
            "reason": result.reject_reason,
        }
