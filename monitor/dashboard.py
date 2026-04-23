"""
OANDA FX自動売買ボット - ターミナルダッシュボード

richライブラリを使ったリアルタイム監視ダッシュボード。
5秒間隔で自動更新し、以下を表示する:
  - 口座情報（残高・証拠金・維持率）
  - 最新レート
  - 保有ポジション一覧（含み損益付き）
  - 本日のトレード一覧
  - 本日の損益合計

使い方:
  python -m monitor.dashboard
  または
  python monitor/dashboard.py
"""

import logging
import time
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.layout import Layout
from rich.panel import Panel
from rich.live import Live
from rich.text import Text
from rich import box

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))


class Dashboard:
    """
    ターミナルベースのリアルタイムダッシュボード。

    OandaClientを使ってAPIからデータを取得し、
    richライブラリで整形してターミナルに表示する。

    Attributes:
        client: OandaClientインスタンス。
        watch_instruments: 監視する通貨ペアリスト。
        refresh_interval: 更新間隔（秒）。
        console: richコンソール。
    """

    def __init__(
        self,
        client: Any = None,
        watch_instruments: list[str] | None = None,
        refresh_interval: float = 5.0,
    ) -> None:
        """
        ダッシュボードを初期化する。

        Args:
            client: OandaClientインスタンス。Noneの場合は新規作成。
            watch_instruments: 監視通貨ペアリスト。
            refresh_interval: 更新間隔（秒）。
        """
        if client is None:
            from core.client import OandaClient
            client = OandaClient()

        self.client = client
        self.watch_instruments = watch_instruments or [
            "USD_JPY", "EUR_JPY", "GBP_JPY", "EUR_USD",
        ]
        self.refresh_interval = refresh_interval
        self.console = Console()

    def run(self) -> None:
        """
        ダッシュボードを起動する（Ctrl+Cで停止）。

        Live表示で指定間隔ごとに画面を更新する。
        """
        self.console.print(
            "[bold cyan]OANDA FXダッシュボード起動中...[/] "
            f"(更新間隔: {self.refresh_interval}秒, Ctrl+Cで停止)",
        )

        try:
            with Live(
                self._build_display(),
                console=self.console,
                refresh_per_second=1,
                screen=False,
            ) as live:
                while True:
                    try:
                        live.update(self._build_display())
                    except Exception as e:
                        logger.error("表示更新エラー: %s", e)
                    time.sleep(self.refresh_interval)

        except KeyboardInterrupt:
            self.console.print("\n[yellow]ダッシュボードを停止しました[/]")

    def _build_display(self) -> Panel:
        """画面全体を構築する。"""
        now_jst = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST")

        # 各セクションのデータを取得
        account_table = self._build_account_section()
        price_table = self._build_price_section()
        position_table = self._build_position_section()
        trade_table = self._build_trade_section()

        # レイアウト構築
        from rich.columns import Columns

        top_row = Columns([account_table, price_table], equal=True, expand=True)
        content = Text()

        group = Table.grid(padding=1)
        group.add_row(top_row)
        group.add_row(position_table)
        group.add_row(trade_table)

        return Panel(
            group,
            title=f"[bold white]📊 OANDA FX ダッシュボード[/] [dim]{now_jst}[/]",
            border_style="cyan",
            box=box.DOUBLE,
        )

    def _build_account_section(self) -> Table:
        """口座情報セクションを構築する。"""
        table = Table(
            title="💰 口座情報",
            box=box.SIMPLE_HEAVY,
            show_header=False,
            expand=True,
        )
        table.add_column("項目", style="cyan", width=18)
        table.add_column("値", style="white")

        try:
            summary = self.client.get_account_summary()

            balance = float(summary.get("balance", 0))
            unrealized_pl = float(summary.get("unrealizedPL", 0))
            margin_used = float(summary.get("marginUsed", 0))
            margin_available = float(summary.get("marginAvailable", 0))
            open_count = summary.get("openTradeCount", 0)

            # 証拠金維持率
            total_margin = margin_used + margin_available
            margin_rate = (
                (total_margin / margin_used * 100)
                if margin_used > 0 else 0
            )

            equity = balance + unrealized_pl
            pl_color = "green" if unrealized_pl >= 0 else "red"
            pl_sign = "+" if unrealized_pl >= 0 else ""

            table.add_row("残高", f"¥{balance:,.0f}")
            table.add_row("有効証拠金", f"¥{equity:,.0f}")
            table.add_row(
                "含み損益",
                f"[{pl_color}]{pl_sign}¥{unrealized_pl:,.0f}[/]",
            )
            table.add_row("使用証拠金", f"¥{margin_used:,.0f}")
            table.add_row("利用可能証拠金", f"¥{margin_available:,.0f}")
            if margin_used > 0:
                mr_color = "green" if margin_rate > 200 else "yellow" if margin_rate > 100 else "red"
                table.add_row(
                    "証拠金維持率",
                    f"[{mr_color}]{margin_rate:,.1f}%[/]",
                )
            table.add_row("ポジション数", str(open_count))

        except Exception as e:
            table.add_row("エラー", f"[red]{e}[/]")

        return table

    def _build_price_section(self) -> Table:
        """最新レートセクションを構築する。"""
        table = Table(
            title="📈 最新レート",
            box=box.SIMPLE_HEAVY,
            expand=True,
        )
        table.add_column("通貨ペア", style="cyan", width=10)
        table.add_column("Bid", justify="right")
        table.add_column("Ask", justify="right")
        table.add_column("Spread", justify="right")

        try:
            prices = self.client.get_prices(self.watch_instruments)

            for price_data in prices:
                inst = price_data.get("instrument", "").replace("_", "/")
                bids = price_data.get("bids", [])
                asks = price_data.get("asks", [])

                if bids and asks:
                    bid = float(bids[0]["price"])
                    ask = float(asks[0]["price"])
                    spread = (ask - bid) * 100  # pips表示用

                    # クロス円は3桁、その他は5桁
                    fmt = ".3f" if "JPY" in inst else ".5f"

                    table.add_row(
                        inst,
                        f"{bid:{fmt}}",
                        f"{ask:{fmt}}",
                        f"{spread:.1f}",
                    )
        except Exception as e:
            table.add_row("エラー", f"[red]{e}[/]", "", "")

        return table

    def _build_position_section(self) -> Table:
        """保有ポジション一覧セクションを構築する。"""
        table = Table(
            title="📋 保有ポジション",
            box=box.SIMPLE_HEAVY,
            expand=True,
        )
        table.add_column("ID", style="dim", width=8)
        table.add_column("通貨ペア", style="cyan", width=10)
        table.add_column("方向", width=6)
        table.add_column("数量", justify="right", width=10)
        table.add_column("エントリー", justify="right", width=10)
        table.add_column("現在値", justify="right", width=10)
        table.add_column("含み損益", justify="right", width=12)

        try:
            from core.order import OrderManager
            om = OrderManager(client=self.client)
            open_trades = om.get_open_trades()

            if not open_trades:
                table.add_row(
                    "", "[dim]ポジションなし[/]", "", "", "", "", "",
                )
            else:
                for trade in open_trades:
                    tid = trade.get("id", "")[-6:]
                    inst = trade.get("instrument", "").replace("_", "/")
                    units = int(trade.get("currentUnits", 0))
                    direction = "🟢買" if units > 0 else "🔴売"
                    entry = trade.get("price", "")
                    unrealized = float(trade.get("unrealizedPL", 0))

                    pl_color = "green" if unrealized >= 0 else "red"
                    pl_sign = "+" if unrealized >= 0 else ""

                    table.add_row(
                        tid, inst, direction,
                        f"{abs(units):,}",
                        str(entry),
                        "-",
                        f"[{pl_color}]{pl_sign}¥{unrealized:,.0f}[/]",
                    )

        except Exception as e:
            table.add_row("", f"[red]{e}[/]", "", "", "", "", "")

        return table

    def _build_trade_section(self) -> Table:
        """本日のトレード集計セクションを構築する。"""
        table = Table(
            title="📊 本日のサマリー",
            box=box.SIMPLE_HEAVY,
            show_header=False,
            expand=True,
        )
        table.add_column("項目", style="cyan", width=18)
        table.add_column("値", style="white")

        try:
            summary = self.client.get_account_summary()
            balance = float(summary.get("balance", 0))
            pl = float(summary.get("pl", 0))
            financing = float(summary.get("financing", 0))

            table.add_row(
                "口座残高", f"¥{balance:,.0f}",
            )
            table.add_row(
                "累計実現損益",
                f"¥{pl:,.0f}" if pl >= 0 else f"[red]¥{pl:,.0f}[/]",
            )
            table.add_row(
                "スワップ累計", f"¥{financing:,.0f}",
            )
            table.add_row(
                "環境",
                f"[green]{self.client.settings.environment}[/]",
            )

        except Exception as e:
            table.add_row("エラー", f"[red]{e}[/]")

        return table


def main() -> None:
    """ダッシュボードのエントリーポイント。"""
    # プロジェクトルートをパスに追加
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))

    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    from core.client import OandaClient

    console = Console()

    console.print("[bold cyan]━━━ OANDA FX ダッシュボード ━━━[/]")
    console.print()

    try:
        client = OandaClient()
    except Exception as e:
        console.print(f"[red]初期化エラー: {e}[/]")
        console.print(".envファイルを確認してください。")
        sys.exit(1)

    dashboard = Dashboard(
        client=client,
        watch_instruments=["USD_JPY", "EUR_JPY", "GBP_JPY", "EUR_USD"],
        refresh_interval=5.0,
    )
    dashboard.run()


if __name__ == "__main__":
    main()
