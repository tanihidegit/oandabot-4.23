"""
OANDA FX自動売買ボット - 通知モジュール

LINE Notifyを使った売買通知を提供する。
注文約定、決済、日次サマリー、エラー発生時に通知を送信する。

LINE Notifyトークンは.envの LINE_NOTIFY_TOKEN で管理する。
トークン未設定時はログ出力のみ（通知スキップ）。
"""

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# LINE Notify API エンドポイント
LINE_NOTIFY_URL = "https://notify-api.line.me/api/notify"

# 日本標準時
JST = timezone(timedelta(hours=9))


class Notifier:
    """
    LINE Notify通知クラス。

    トレードイベントに応じて整形されたメッセージをLINEに送信する。
    トークン未設定時はログ出力のみで動作する（エラーにはならない）。

    Attributes:
        token: LINE Notifyのアクセストークン。
        enabled: 通知が有効かどうか。
    """

    def __init__(self, token: str | None = None) -> None:
        """
        Notifierを初期化する。

        Args:
            token: LINE Notifyトークン。Noneの場合は.envから取得。
        """
        if token is None:
            load_dotenv()
            token = os.getenv("LINE_NOTIFY_TOKEN", "")

        self.token = token or ""
        self.enabled = bool(
            self.token
            and self.token != "your_line_notify_token_here"
        )

        if self.enabled:
            logger.info("LINE Notify通知を有効化しました")
        else:
            logger.info("LINE Notifyトークン未設定 — ログ出力のみで動作します")

    # ═══════════════════════════════════════════════════════
    #  通知メソッド
    # ═══════════════════════════════════════════════════════

    def notify_order_fill(
        self,
        instrument: str,
        direction: str,
        units: int,
        fill_price: float,
        tp_price: float | None = None,
        sl_price: float | None = None,
    ) -> bool:
        """
        注文約定通知を送信する。

        Args:
            instrument: 通貨ペア。
            direction: "BUY" or "SELL"。
            units: 取引数量。
            fill_price: 約定価格。
            tp_price: 利確価格。
            sl_price: 損切価格。

        Returns:
            送信成功ならTrue。
        """
        emoji = "🟢" if direction == "BUY" else "🔴"
        display_pair = instrument.replace("_", "/")

        parts = [
            f"{emoji} {direction} {display_pair}",
            f"{units:,} @ {fill_price:.3f}",
        ]

        details = []
        if tp_price is not None:
            details.append(f"TP:{tp_price:.3f}")
        if sl_price is not None:
            details.append(f"SL:{sl_price:.3f}")

        if details:
            parts.append("| " + " ".join(details))

        message = " ".join(parts)
        return self._send(message)

    def notify_trade_close(
        self,
        instrument: str,
        pips: float,
        profit_loss: float,
        direction: str = "",
    ) -> bool:
        """
        トレード決済通知を送信する。

        Args:
            instrument: 通貨ペア。
            pips: 獲得pips。
            profit_loss: 実現損益（円）。
            direction: 元の方向（"BUY" or "SELL"）。

        Returns:
            送信成功ならTrue。
        """
        display_pair = instrument.replace("_", "/")
        sign = "+" if pips >= 0 else ""
        pl_sign = "+" if profit_loss >= 0 else ""

        message = (
            f"🔵 CLOSE {display_pair} "
            f"{sign}{pips:.1f} pips "
            f"({pl_sign}¥{profit_loss:,.0f})"
        )
        return self._send(message)

    def notify_error(
        self,
        error_message: str,
        context: str = "",
    ) -> bool:
        """
        エラー通知を送信する。

        Args:
            error_message: エラーメッセージ。
            context: エラー発生箇所の補足情報。

        Returns:
            送信成功ならTrue。
        """
        parts = [f"🔴 [ERROR] {error_message}"]
        if context:
            parts.append(f"({context})")

        message = " ".join(parts)
        return self._send(message)

    def notify_daily_summary(
        self,
        total_pl: float,
        win_count: int,
        total_trades: int,
        balance: float | None = None,
    ) -> bool:
        """
        日次サマリー通知を送信する。

        Args:
            total_pl: 当日合計損益（円）。
            win_count: 勝ちトレード数。
            total_trades: 総トレード数。
            balance: 現在の口座残高（任意）。

        Returns:
            送信成功ならTrue。
        """
        pl_sign = "+" if total_pl >= 0 else ""
        win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0

        parts = [
            f"📊 日次レポート",
            f"| 損益: {pl_sign}¥{total_pl:,.0f}",
            f"| 勝率: {win_count}/{total_trades} ({win_rate:.0f}%)",
        ]

        if balance is not None:
            parts.append(f"| 残高: ¥{balance:,.0f}")

        message = " ".join(parts)
        return self._send(message)

    def notify_custom(self, message: str) -> bool:
        """
        カスタムメッセージを送信する。

        Args:
            message: 送信するメッセージ文字列。

        Returns:
            送信成功ならTrue。
        """
        return self._send(message)

    # ═══════════════════════════════════════════════════════
    #  内部送信処理
    # ═══════════════════════════════════════════════════════

    def _send(self, message: str) -> bool:
        """
        LINE Notify APIにメッセージを送信する。

        トークン未設定時はログ出力のみ。

        Args:
            message: 送信するメッセージ。

        Returns:
            送信成功ならTrue。未設定時もTrue（エラーではない）。
        """
        timestamp = datetime.now(JST).strftime("%H:%M:%S")
        full_message = f"\n[{timestamp}] {message}"

        # ログには常に出力
        logger.info("通知: %s", message)

        if not self.enabled:
            logger.debug("LINE Notify未設定のためスキップ")
            return True

        try:
            headers = {
                "Authorization": f"Bearer {self.token}",
            }
            data = {"message": full_message}

            response = requests.post(
                LINE_NOTIFY_URL,
                headers=headers,
                data=data,
                timeout=10,
            )

            if response.status_code == 200:
                logger.debug("LINE通知送信成功")
                return True
            else:
                logger.warning(
                    "LINE通知送信失敗: HTTP %d - %s",
                    response.status_code, response.text,
                )
                return False

        except requests.exceptions.Timeout:
            logger.error("LINE通知タイムアウト")
            return False
        except requests.exceptions.RequestException as e:
            logger.error("LINE通知送信エラー: %s", e)
            return False
