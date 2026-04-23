"""
OANDA FX自動売買ボット - リスクガードモジュール

トレード前に複数のリスク条件をチェックし、条件違反時は
注文をブロックして警告ログを出力する。

チェック項目:
  - 最大同時ポジション数
  - 1トレードあたりの最大損失率
  - 日次最大損失率
  - 日次最大トレード数
  - 取引許可時間帯
"""

import logging
from datetime import datetime, timezone, timedelta, time as dtime
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# 日本標準時のオフセット
JST = timezone(timedelta(hours=9))


@dataclass
class RiskConfig:
    """リスク管理パラメータ。"""
    max_positions: int = 2                    # 最大同時ポジション数
    max_loss_per_trade_pct: float = 2.0       # 1トレード最大損失率（%）
    max_daily_loss_pct: float = 5.0           # 日次最大損失率（%）
    max_daily_trades: int = 10                # 日次最大トレード数
    trading_hours_start: str = "07:00"        # 取引開始時刻（JST）
    trading_hours_end: str = "23:00"          # 取引終了時刻（JST）


@dataclass
class TradeRecord:
    """トレード記録。"""
    timestamp: datetime
    instrument: str
    direction: str        # "BUY" or "SELL"
    units: int
    profit_loss: float    # 実現損益（円）。未決済は0。
    status: str           # "OPEN", "CLOSED"


class RiskGuard:
    """
    リスクガード。

    トレード前に全リスク条件をチェックし、違反があれば
    注文をブロックする。全チェック結果はログに記録される。

    Attributes:
        config: リスク管理パラメータ。
        account_balance: 当日開始時の口座残高。
        daily_records: 当日のトレード記録リスト。
        current_date: 記録対象の日付。
        open_position_count: 現在のオープンポジション数。
    """

    def __init__(
        self,
        config: RiskConfig | None = None,
        account_balance: float = 1_000_000.0,
    ) -> None:
        """
        RiskGuardを初期化する。

        Args:
            config: リスク管理パラメータ。Noneの場合はデフォルト値。
            account_balance: 口座残高（円）。
        """
        self.config = config or RiskConfig()
        self.account_balance = account_balance
        self.daily_records: list[TradeRecord] = []
        self.current_date: str = self._today_str()
        self.open_position_count: int = 0

        logger.info(
            "RiskGuardを初期化: 残高=%.0f, 最大ポジション=%d, "
            "日次損失上限=%.1f%%, 取引時間=%s-%s JST",
            account_balance, self.config.max_positions,
            self.config.max_daily_loss_pct,
            self.config.trading_hours_start, self.config.trading_hours_end,
        )

    # ═══════════════════════════════════════════════════════
    #  メインチェック
    # ═══════════════════════════════════════════════════════

    def can_trade(self) -> bool:
        """
        全リスク条件をチェックし、トレード可否を返す。

        いずれかの条件に違反した場合はFalseを返し、
        警告ログを出力する。

        Returns:
            全条件をクリアすればTrue。
        """
        self._reset_if_new_day()

        checks = {
            "最大ポジション数": self.check_max_positions(),
            "日次損失上限": self.check_daily_loss(),
            "日次トレード数": self.check_daily_trades(),
            "取引時間帯": self.check_trading_hours(),
        }

        all_ok = all(checks.values())

        if not all_ok:
            failed = [name for name, ok in checks.items() if not ok]
            logger.warning(
                "🚫 トレードブロック: %s", ", ".join(failed),
            )
        else:
            logger.debug("✅ リスクチェック全項目パス")

        return all_ok

    # ═══════════════════════════════════════════════════════
    #  個別チェック
    # ═══════════════════════════════════════════════════════

    def check_max_positions(self) -> bool:
        """
        最大同時ポジション数をチェックする。

        Returns:
            ポジション数が上限以下ならTrue。
        """
        ok = self.open_position_count < self.config.max_positions
        if not ok:
            logger.warning(
                "⚠️ 最大ポジション数到達: %d/%d",
                self.open_position_count, self.config.max_positions,
            )
        return ok

    def check_daily_loss(self) -> bool:
        """
        日次最大損失率をチェックする。

        当日の全決済トレードの損失合計が口座残高×上限%を超えていないか確認。

        Returns:
            損失率が上限以下ならTrue。
        """
        self._reset_if_new_day()

        daily_loss = sum(
            r.profit_loss for r in self.daily_records
            if r.status == "CLOSED" and r.profit_loss < 0
        )
        loss_pct = abs(daily_loss) / self.account_balance * 100

        max_loss = self.account_balance * self.config.max_daily_loss_pct / 100
        ok = abs(daily_loss) < max_loss

        if not ok:
            logger.warning(
                "⚠️ 日次損失上限到達: %.0f円 (%.2f%%) / 上限%.0f円 (%.1f%%)",
                abs(daily_loss), loss_pct, max_loss, self.config.max_daily_loss_pct,
            )
        return ok

    def check_daily_trades(self) -> bool:
        """
        日次最大トレード数をチェックする。

        Returns:
            トレード数が上限未満ならTrue。
        """
        self._reset_if_new_day()
        trade_count = len(self.daily_records)
        ok = trade_count < self.config.max_daily_trades

        if not ok:
            logger.warning(
                "⚠️ 日次トレード数上限到達: %d/%d",
                trade_count, self.config.max_daily_trades,
            )
        return ok

    def check_trading_hours(self) -> bool:
        """
        取引許可時間帯をチェックする（JST基準）。

        Returns:
            現在時刻が取引許可時間帯内ならTrue。
        """
        now_jst = datetime.now(JST)
        current_time = now_jst.time()

        start = self._parse_time(self.config.trading_hours_start)
        end = self._parse_time(self.config.trading_hours_end)

        if start <= end:
            ok = start <= current_time <= end
        else:
            # 日をまたぐ場合（例: 22:00-06:00）
            ok = current_time >= start or current_time <= end

        if not ok:
            logger.warning(
                "⚠️ 取引時間外: 現在%s JST（許可: %s-%s）",
                current_time.strftime("%H:%M"),
                self.config.trading_hours_start,
                self.config.trading_hours_end,
            )
        return ok

    def get_max_loss_amount(self) -> float:
        """
        1トレードあたりの最大許容損失額（円）を返す。

        Returns:
            口座残高 × max_loss_per_trade_pct / 100。
        """
        return self.account_balance * self.config.max_loss_per_trade_pct / 100

    # ═══════════════════════════════════════════════════════
    #  トレード記録
    # ═══════════════════════════════════════════════════════

    def log_trade(self, trade_info: dict[str, Any]) -> None:
        """
        トレードを記録する。

        Args:
            trade_info: トレード情報辞書。キー:
                - instrument: 通貨ペア
                - direction: "BUY" or "SELL"
                - units: 取引数量
                - profit_loss: 実現損益（決済時。未決済は0）
                - status: "OPEN" or "CLOSED"
        """
        self._reset_if_new_day()

        record = TradeRecord(
            timestamp=datetime.now(timezone.utc),
            instrument=trade_info.get("instrument", ""),
            direction=trade_info.get("direction", ""),
            units=trade_info.get("units", 0),
            profit_loss=trade_info.get("profit_loss", 0.0),
            status=trade_info.get("status", "OPEN"),
        )
        self.daily_records.append(record)

        if record.status == "OPEN":
            self.open_position_count += 1
        elif record.status == "CLOSED":
            self.open_position_count = max(0, self.open_position_count - 1)

        logger.info(
            "トレード記録: %s %s %d units (P&L: %.0f, status: %s)",
            record.instrument, record.direction, record.units,
            record.profit_loss, record.status,
        )

    def update_balance(self, new_balance: float) -> None:
        """
        口座残高を更新する。

        Args:
            new_balance: 最新の口座残高（円）。
        """
        self.account_balance = new_balance
        logger.info("口座残高を更新しました: %.0f円", new_balance)

    def update_open_positions(self, count: int) -> None:
        """
        オープンポジション数を外部から更新する。

        Args:
            count: 現在のオープンポジション数。
        """
        self.open_position_count = count

    # ═══════════════════════════════════════════════════════
    #  日次サマリー
    # ═══════════════════════════════════════════════════════

    def get_daily_summary(self) -> dict[str, Any]:
        """
        当日のトレードサマリーを返す。

        Returns:
            日次サマリー辞書。
        """
        self._reset_if_new_day()

        closed = [r for r in self.daily_records if r.status == "CLOSED"]
        total_pl = sum(r.profit_loss for r in closed)
        wins = [r for r in closed if r.profit_loss > 0]
        losses = [r for r in closed if r.profit_loss <= 0]

        return {
            "date": self.current_date,
            "total_trades": len(self.daily_records),
            "closed_trades": len(closed),
            "open_positions": self.open_position_count,
            "total_pl": round(total_pl, 2),
            "win_count": len(wins),
            "loss_count": len(losses),
            "daily_loss_pct": round(
                abs(min(total_pl, 0)) / self.account_balance * 100, 2
            ),
            "max_daily_loss_pct": self.config.max_daily_loss_pct,
            "remaining_trades": max(
                0, self.config.max_daily_trades - len(self.daily_records)
            ),
            "account_balance": self.account_balance,
        }

    # ═══════════════════════════════════════════════════════
    #  内部ヘルパー
    # ═══════════════════════════════════════════════════════

    def _reset_if_new_day(self) -> None:
        """日付が変わったら日次記録をリセットする。"""
        today = self._today_str()
        if today != self.current_date:
            logger.info("日次リセット: %s → %s", self.current_date, today)
            self.daily_records = []
            self.current_date = today

    @staticmethod
    def _today_str() -> str:
        """現在日付のJST文字列を返す。"""
        return datetime.now(JST).strftime("%Y-%m-%d")

    @staticmethod
    def _parse_time(time_str: str) -> dtime:
        """HH:MM形式の文字列をtimeオブジェクトに変換する。"""
        parts = time_str.split(":")
        return dtime(int(parts[0]), int(parts[1]))
