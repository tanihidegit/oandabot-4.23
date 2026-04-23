"""
OANDA FX自動売買ボット - メインエントリーポイント

全モジュール（API接続、戦略、リスク管理、注文、通知）を統合し、
自動売買ボットとして稼働させる。

使い方:
  python main.py run --strategy momentum --instrument USD_JPY --interval 300
  python main.py backtest --strategy momentum --instrument USD_JPY --from 2024-01-01 --to 2024-06-01
  python main.py optimize --strategy momentum --instrument USD_JPY --from 2024-01-01 --to 2024-06-01
  python main.py webhook --port 5000
  python main.py dashboard
  python main.py close-all
  python main.py close-all --instrument USD_JPY
"""

import argparse
import logging
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

# プロジェクトルートをパスに追加
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import Settings
from core.client import OandaClient
from core.order import OrderManager
from risk.guard import RiskGuard, RiskConfig
from risk.position_sizer import PositionSizer
from monitor.notifier import Notifier
from strategy.base import BaseStrategy, Signal

logger = logging.getLogger(__name__)
JST = timezone(timedelta(hours=9))


# ═══════════════════════════════════════════════════════════
#  戦略ファクトリ
# ═══════════════════════════════════════════════════════════

def create_strategy(name: str) -> BaseStrategy:
    """
    戦略名からインスタンスを生成する。

    Args:
        name: 戦略名（sma_cross, momentum, breakout）。

    Returns:
        BaseStrategy実装のインスタンス。
    """
    if name == "sma_cross":
        from strategy.sma_cross import SmaCrossStrategy
        return SmaCrossStrategy(short_period=20, long_period=50)
    elif name == "momentum":
        from strategy.momentum import MomentumStrategy
        return MomentumStrategy()
    elif name == "trend_follow":
        from strategy.trend_follow import TrendFollowStrategy
        return TrendFollowStrategy()
    elif name == "scalping":
        from strategy.scalping import ScalpingStrategy
        return ScalpingStrategy()
    elif name == "breakout":
        from strategy.breakout import BreakoutStrategy
        return BreakoutStrategy(
            lookback_period=20, atr_period=14, atr_multiplier=2.0,
        )
    else:
        raise ValueError(
            f"不明な戦略: '{name}'。"
            "使用可能: sma_cross, momentum, breakout"
        )


# ═══════════════════════════════════════════════════════════
#  TradingBot
# ═══════════════════════════════════════════════════════════

class TradingBot:
    """
    FX自動売買ボット本体。

    戦略のシグナルに基づき、リスク管理を適用した上で
    自動的に注文を発行する。

    Attributes:
        client: OANDA APIクライアント。
        strategy: 売買戦略。
        order_manager: 注文管理。
        risk_guard: リスクガード。
        position_sizer: ポジションサイザー。
        notifier: 通知。
        instrument: 対象通貨ペア。
        interval: チェック間隔（秒）。
        running: 稼働中フラグ。
    """

    def __init__(
        self,
        strategy_name: str,
        instrument: str = "USD_JPY",
        interval: int = 300,
        units_override: int | None = None,
    ) -> None:
        """
        ボットを初期化する。

        Args:
            strategy_name: 戦略名。
            instrument: 対象通貨ペア。
            interval: チェック間隔（秒）。
            units_override: 取引数量の固定値。Noneの場合PositionSizerで計算。
        """
        self.settings = Settings()
        self.client = OandaClient(self.settings)
        self.strategy = create_strategy(strategy_name)
        self.instrument = instrument
        self.interval = interval
        self.units_override = units_override
        self.running = False

        # 口座残高取得
        summary = self.client.get_account_summary()
        balance = float(summary.get("balance", 1_000_000))

        # 各モジュール初期化
        history_path = PROJECT_ROOT / "data" / "order_history" / "bot_orders.csv"
        self.order_manager = OrderManager(
            client=self.client, history_path=str(history_path),
        )
        self.risk_guard = RiskGuard(
            config=RiskConfig(
                max_positions=2,
                max_loss_per_trade_pct=2.0,
                max_daily_loss_pct=5.0,
                max_daily_trades=10,
                trading_hours_start="07:00",
                trading_hours_end="23:00",
            ),
            account_balance=balance,
        )
        self.position_sizer = PositionSizer(
            account_balance=balance, default_risk_pct=2.0,
        )
        self.notifier = Notifier()

        # グレースフルシャットダウン用
        self._setup_signal_handlers()

    def _setup_signal_handlers(self) -> None:
        """Ctrl+Cでの安全停止を設定する。"""
        def handler(signum, frame):
            logger.info("停止シグナルを受信しました（%s）", signum)
            self.running = False

        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)

    def _print_startup_banner(self) -> None:
        """起動バナーと設定サマリーを表示する。"""
        now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
        env = self.settings.environment
        units_info = (
            f"{self.units_override}通貨（固定）"
            if self.units_override
            else "自動計算"
        )

        print()
        print("╔══════════════════════════════════════════════════════════╗")
        print("║         OANDA FX 自動売買ボット 起動                     ║")
        print("╚══════════════════════════════════════════════════════════╝")
        print()
        print(f"  環境: {env} | 戦略: {self.strategy.name} "
              f"| 通貨: {self.instrument} | 間隔: {self.interval}秒")
        print(f"  取引数量: {units_info}")
        print(f"  起動時刻: {now} JST")
        print(f"  残高: ¥{self.risk_guard.account_balance:,.0f}")
        print()

        if self.settings.is_live:
            print("  ⚠️  【本番環境】で稼働しています！")
            print()

        print("  Ctrl+C で安全に停止します")
        print("  " + "─" * 50)

    def run(self) -> None:
        """
        メインループを開始する。

        指定間隔でデータ取得→シグナル判定→注文実行を繰り返す。
        Ctrl+Cで安全に停止する。
        """
        self._print_startup_banner()
        self.running = True

        # 起動通知
        self.notifier.notify_custom(
            f"🚀 ボット起動: {self.strategy.name} | "
            f"{self.instrument} | {self.settings.environment}"
        )

        # 戦略に必要なデータ本数（ウォームアップ）
        warmup_bars = 100
        loop_count = 0

        while self.running:
            loop_count += 1
            now_str = datetime.now(JST).strftime("%H:%M:%S")

            try:
                logger.info("── ループ #%d (%s) ──", loop_count, now_str)

                # 1. ローソク足データ取得
                granularity = self._interval_to_granularity(self.interval)
                df = self.client.get_candles(
                    instrument=self.instrument,
                    granularity=granularity,
                    count=warmup_bars,
                )

                if df.empty:
                    logger.warning("データが空です（市場クローズ中の可能性）")
                    self._sleep()
                    continue

                # 2. テクニカル指標計算 + シグナル生成
                df = self.strategy.prepare(df)
                current_signal = self.strategy.generate_signal(df, len(df) - 1)
                current_price = df.iloc[-1]["close"]

                logger.info(
                    "シグナル: %s | 価格: %.3f | データ: %d本",
                    current_signal.name, current_price, len(df),
                )

                # 3. シグナルに基づく注文処理
                self._process_signal(current_signal, current_price)

                # 4. オープンポジション状態の更新
                open_trades = self.order_manager.get_open_trades()
                self.risk_guard.update_open_positions(len(open_trades))

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error("ループ中にエラー発生: %s", e)
                self.notifier.notify_error(str(e), "main_loop")

            # 5. 次のサイクルまで待機
            self._sleep()

        # 停止処理
        self.stop()

    def _process_signal(
        self, sig: Signal, current_price: float,
    ) -> None:
        """シグナルに基づいて注文処理を行う。"""
        # HOLD / 不明シグナルは無視
        if sig == Signal.HOLD:
            return

        # 決済シグナル
        if sig in (Signal.CLOSE, Signal.CLOSE_LONG, Signal.CLOSE_SHORT):
            self._handle_close_signal(sig)
            return

        # エントリーシグナル（BUY / SELL）
        if sig not in (Signal.BUY, Signal.SELL):
            return

        # リスクチェック
        if not self.risk_guard.can_trade():
            logger.info("リスクガードによりトレードをスキップ")
            return

        # 取引数量の決定
        if self.units_override:
            units = self.units_override
        else:
            sl_pips = 50.0  # デフォルトSL幅
            units = self.position_sizer.calculate_units(
                self.instrument, sl_pips, risk_pct=2.0,
            )

        # 反対ポジションがあれば先に決済
        open_trades = self.order_manager.get_open_trades()
        for trade in open_trades:
            if trade.get("instrument") != self.instrument:
                continue
            trade_units = int(trade.get("currentUnits", 0))
            is_long = trade_units > 0
            if (sig == Signal.BUY and not is_long) or (sig == Signal.SELL and is_long):
                tid = trade.get("id", "")
                self.order_manager.close_trade(tid)
                self.notifier.notify_trade_close(
                    instrument=self.instrument, pips=0,
                    profit_loss=float(trade.get("unrealizedPL", 0)),
                )

        # 注文発行
        order_units = units if sig == Signal.BUY else -units
        result = self.order_manager.market_order(
            instrument=self.instrument,
            units=order_units,
        )

        if result.success:
            direction = "BUY" if sig == Signal.BUY else "SELL"
            self.notifier.notify_order_fill(
                instrument=self.instrument,
                direction=direction,
                units=abs(order_units),
                fill_price=result.fill_price,
            )
            self.risk_guard.log_trade({
                "instrument": self.instrument,
                "direction": direction,
                "units": abs(order_units),
                "profit_loss": 0,
                "status": "OPEN",
            })
            logger.info(
                "✅ 注文約定: %s %s %d @ %.3f",
                direction, self.instrument, abs(order_units), result.fill_price,
            )
        else:
            logger.warning("❌ 注文拒否: %s", result.reject_reason)
            self.notifier.notify_error(
                f"注文拒否: {result.reject_reason}", "order",
            )

    def _handle_close_signal(self, sig: Signal) -> None:
        """決済シグナルを処理する。"""
        open_trades = self.order_manager.get_open_trades()
        for trade in open_trades:
            if trade.get("instrument") != self.instrument:
                continue

            trade_units = int(trade.get("currentUnits", 0))
            is_long = trade_units > 0

            should_close = (
                sig == Signal.CLOSE
                or (sig == Signal.CLOSE_LONG and is_long)
                or (sig == Signal.CLOSE_SHORT and not is_long)
            )

            if should_close:
                tid = trade.get("id", "")
                resp = self.order_manager.close_trade(tid)
                fill_tx = resp.get("orderFillTransaction", {})
                pl = float(fill_tx.get("pl", 0))
                self.notifier.notify_trade_close(
                    instrument=self.instrument, pips=0, profit_loss=pl,
                )
                self.risk_guard.log_trade({
                    "instrument": self.instrument,
                    "direction": "CLOSE",
                    "units": abs(trade_units),
                    "profit_loss": pl,
                    "status": "CLOSED",
                })

    def stop(self, close_positions: bool = False) -> None:
        """
        ボットを安全に停止する。

        Args:
            close_positions: Trueの場合、全ポジションを決済して停止。
        """
        self.running = False
        print()
        print("  ⏹️  ボットを停止しています...")

        if close_positions:
            print("  全ポジションを決済中...")
            results = self.order_manager.close_all(instrument=self.instrument)
            print(f"  → {len(results)}件のポジションを決済しました")

        # 日次サマリー
        summary = self.risk_guard.get_daily_summary()
        self.notifier.notify_daily_summary(
            total_pl=summary["total_pl"],
            win_count=summary["win_count"],
            total_trades=summary["closed_trades"],
            balance=summary["account_balance"],
        )

        self.notifier.notify_custom("⏹️ ボットを停止しました")
        print("  ✅ 安全に停止しました")
        print()

    def _sleep(self) -> None:
        """次のサイクルまで待機する（中断可能）。"""
        for _ in range(self.interval):
            if not self.running:
                break
            time.sleep(1)

    @staticmethod
    def _interval_to_granularity(interval: int) -> str:
        """間隔秒数をOANDAの時間足文字列に変換する。"""
        mapping = {
            5: "S5", 10: "S10", 15: "S15", 30: "S30",
            60: "M1", 120: "M2", 240: "M4", 300: "M5",
            600: "M10", 900: "M15", 1800: "M30",
            3600: "H1", 7200: "H2", 14400: "H4",
            86400: "D",
        }
        return mapping.get(interval, "M5")


# ═══════════════════════════════════════════════════════════
#  CLIコマンド実行
# ═══════════════════════════════════════════════════════════

def cmd_run(args: argparse.Namespace) -> None:
    """runコマンドを実行する。"""
    bot = TradingBot(
        strategy_name=args.strategy,
        instrument=args.instrument,
        interval=args.interval,
        units_override=args.units,
    )
    bot.run()


def cmd_backtest(args: argparse.Namespace) -> None:
    """backtestコマンドを実行する。"""
    from backtest.data_loader import OandaDataLoader
    from backtest.engine import Backtester, BacktestConfig
    from backtest.chart import plot_backtest_results

    print(f"\n  バックテスト: {args.strategy} | {args.instrument}")
    print(f"  期間: {args.from_date} ～ {args.to_date}")

    settings = Settings()
    client = OandaClient(settings)
    loader = OandaDataLoader(client)

    from datetime import datetime, timezone
    
    req_to_date = f"{args.to_date}T23:59:59Z"
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if req_to_date > now_utc:
        req_to_date = now_utc

    df = loader.fetch_and_cache(
        instrument=args.instrument,
        granularity=args.granularity,
        from_date=f"{args.from_date}T00:00:00Z",
        to_date=req_to_date,
        cache_dir=str(PROJECT_ROOT / "data" / "cache"),
    )

    if df.empty:
        print("  ❌ データが取得できませんでした")
        return

    strategy = create_strategy(args.strategy)
    config = BacktestConfig(
        initial_balance=1_000_000,
        units=1000,
        spread_pips=0.3,
        take_profit_pips=100.0,
        stop_loss_pips=50.0,
        pip_value=0.01 if "JPY" in args.instrument else 0.0001,
    )

    bt = Backtester(strategy=strategy, config=config)
    summary = bt.run(df)

    print("\n  ── パフォーマンスサマリー ──")
    labels = {
        "total_trades": "トレード数", "win_rate": "勝率(%)",
        "net_profit": "純損益(円)", "profit_factor": "PF",
        "max_drawdown_pct": "最大DD(%)", "sharpe_ratio": "Sharpe",
        "roi_pct": "ROI(%)",
    }
    for key, label in labels.items():
        val = summary.get(key, "N/A")
        if isinstance(val, float):
            print(f"  {label:<16s}: {val:>12,.2f}")
        else:
            print(f"  {label:<16s}: {val}")

    # チャート保存
    output_dir = PROJECT_ROOT / "data" / "results"
    output_dir.mkdir(parents=True, exist_ok=True)

    equity_df = bt.get_equity_df()
    trades_df = bt.get_trades_df()

    chart_path = str(output_dir / f"bt_{args.strategy}_{args.instrument}.png")
    plot_backtest_results(
        equity_df=equity_df,
        trades_df=trades_df,
        summary=summary,
        save_path=chart_path,
        show=False,
    )
    print(f"\n  📊 チャート保存: {chart_path}")

    # HTMLレポート生成
    from backtest.report import generate_html_report
    html_path = str(output_dir / f"bt_{args.strategy}_{args.instrument}.html")
    generate_html_report(
        equity_df=equity_df,
        trades_df=trades_df,
        summary=summary,
        save_path=html_path,
    )
    print(f"  📄 HTMLレポート: {html_path}")
    print()


def cmd_dashboard(args: argparse.Namespace) -> None:
    """dashboardコマンドを実行する。"""
    from monitor.dashboard import Dashboard
    client = OandaClient(Settings())
    dashboard = Dashboard(client=client, refresh_interval=5.0)
    dashboard.run()


def cmd_close_all(args: argparse.Namespace) -> None:
    """close-allコマンドを実行する。"""
    settings = Settings()
    client = OandaClient(settings)
    om = OrderManager(client=client)
    notifier = Notifier()

    instrument = args.instrument
    label = instrument or "全通貨ペア"
    print(f"\n  全ポジション決済: {label}")

    results = om.close_all(instrument=instrument)
    print(f"  → {len(results)}件のポジションを決済しました")

    for resp in results:
        fill_tx = resp.get("orderFillTransaction", {})
        pl = fill_tx.get("pl", "0")
        print(f"    P&L: {pl}")

    notifier.notify_custom(f"🔒 全ポジション決済完了: {label} ({len(results)}件)")
    print()


def cmd_webhook(args: argparse.Namespace) -> None:
    """webhookコマンドを実行する。Flaskサーバーを起動する。"""
    from webhook.server import create_app

    port = args.port
    host = args.host

    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║         TradingView Webhook サーバー 起動                 ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()
    print(f"  エンドポイント: http://{host}:{port}/webhook")
    print(f"  ヘルスチェック: http://{host}:{port}/health")
    print()
    print("  ngrokで外部公開する場合:")
    print(f"    ngrok http {port}")
    print()
    print("  Ctrl+C で停止します")
    print("  " + "─" * 50)

    app = create_app()
    app.run(host=host, port=port, debug=False)


def cmd_optimize(args: argparse.Namespace) -> None:
    """optimizeコマンドを実行する。"""
    from backtest.data_loader import OandaDataLoader
    from backtest.optimizer import GridSearchOptimizer, WalkForwardAnalyzer, plot_optimization_heatmap, plot_walk_forward
    from backtest.engine import BacktestConfig

    print(f"\n  パラメータ最適化: {args.strategy} | {args.instrument}")
    print(f"  期間: {args.from_date} ～ {args.to_date}")

    settings = Settings()
    client = OandaClient(settings)
    loader = OandaDataLoader(client)

    from datetime import datetime, timezone

    req_to_date = f"{args.to_date}T23:59:59Z"
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if req_to_date > now_utc:
        req_to_date = now_utc

    df = loader.fetch_and_cache(
        instrument=args.instrument,
        granularity=args.granularity,
        from_date=f"{args.from_date}T00:00:00Z",
        to_date=req_to_date,
        cache_dir=str(PROJECT_ROOT / "data" / "cache"),
    )

    if df.empty:
        print("  ❌ データが取得できませんでした")
        return

    bt_config = BacktestConfig(
        initial_balance=1_000_000,
        units=1000,
        spread_pips=0.3,
        take_profit_pips=100.0,
        stop_loss_pips=50.0,
        pip_value=0.01 if "JPY" in args.instrument else 0.0001,
    )

    if args.method == "grid":
        optimizer = GridSearchOptimizer(
            strategy_name=args.strategy,
            metric=args.metric,
            backtest_config=bt_config,
        )
        result = optimizer.run(df)
        
        if result.best_params and len(optimizer.param_keys) >= 2:
            param_x = optimizer.param_keys[0]
            param_y = optimizer.param_keys[1]
            plot_optimization_heatmap(result, param_x, param_y)

    elif args.method == "walkforward":
        analyzer = WalkForwardAnalyzer(
            strategy_name=args.strategy,
            metric=args.metric,
            n_windows=args.windows,
            backtest_config=bt_config,
        )
        result = analyzer.run(df)
        plot_walk_forward(result)


# ═══════════════════════════════════════════════════════════
#  CLI定義
# ═══════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    """CLIパーサーを構築する。"""
    parser = argparse.ArgumentParser(
        description="OANDA FX自動売買ボット",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="実行コマンド")

    # run
    p_run = subparsers.add_parser("run", help="ボットを起動する")
    p_run.add_argument(
        "--strategy", "-s", default="scalping",
        choices=["sma_cross", "momentum", "breakout", "trend_follow", "scalping"],
        help="使用する戦略（デフォルト: scalping）",
    )
    p_run.add_argument(
        "--granularity", "-g", default="M5",
        help="時間足（デフォルト: M5）",
    )
    p_run.add_argument(
        "--instrument", "-i", default="USD_JPY",
        help="対象通貨ペア（デフォルト: USD_JPY）",
    )
    p_run.add_argument(
        "--interval", "-t", type=int, default=300,
        help="チェック間隔（秒, デフォルト: 300）",
    )
    p_run.add_argument(
        "--units", "-u", type=int, default=None,
        help="取引数量を固定する（デフォルト: 自動計算）",
    )

    # backtest
    p_bt = subparsers.add_parser("backtest", help="バックテストを実行する")
    p_bt.add_argument("--strategy", "-s", default="scalping",
                       choices=["sma_cross", "momentum", "breakout", "trend_follow", "scalping"])
    p_bt.add_argument("--instrument", "-i", default="USD_JPY")
    p_bt.add_argument("--granularity", "-g", default="M5")
    p_bt.add_argument("--from", dest="from_date", default="2024-01-01",
                       help="開始日 (YYYY-MM-DD)")
    p_bt.add_argument("--to", dest="to_date", default="2024-06-01",
                       help="終了日 (YYYY-MM-DD)")

    # optimize
    p_opt = subparsers.add_parser("optimize", help="パラメータ最適化を実行する")
    p_opt.add_argument("--strategy", "-s", default="scalping",
                       choices=["sma_cross", "momentum", "breakout", "trend_follow", "scalping"])
    p_opt.add_argument("--instrument", "-i", default="USD_JPY")
    p_opt.add_argument("--granularity", "-g", default="M5")
    p_opt.add_argument("--from", dest="from_date", default="2024-01-01",
                       help="開始日 (YYYY-MM-DD)")
    p_opt.add_argument("--to", dest="to_date", default="2024-06-01",
                       help="終了日 (YYYY-MM-DD)")
    p_opt.add_argument("--method", "-m", default="grid",
                       choices=["grid", "walkforward"],
                       help="最適化手法（grid または walkforward）")
    p_opt.add_argument("--metric", default="sharpe_ratio",
                       choices=["sharpe_ratio", "profit_factor", "net_profit", "win_rate"],
                       help="最適化対象のメトリクス")
    p_opt.add_argument("--windows", type=int, default=5,
                       help="ウォークフォワードのウィンドウ数（デフォルト: 5）")

    # dashboard
    subparsers.add_parser("dashboard", help="リアルタイムダッシュボードを起動する")

    # close-all
    # webhook
    p_wh = subparsers.add_parser("webhook", help="TradingView Webhookサーバーを起動する")
    p_wh.add_argument(
        "--port", "-p", type=int, default=5000,
        help="待受ポート（デフォルト: 5000）",
    )
    p_wh.add_argument(
        "--host", default="0.0.0.0",
        help="バインドホスト（デフォルト: 0.0.0.0）",
    )

    # close-all
    p_close = subparsers.add_parser("close-all", help="全ポジションを決済する")
    p_close.add_argument("--instrument", "-i", default=None,
                          help="対象通貨ペア（未指定で全通貨）")

    return parser


def main() -> None:
    """メインエントリーポイント。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    commands = {
        "run": cmd_run,
        "backtest": cmd_backtest,
        "optimize": cmd_optimize,
        "webhook": cmd_webhook,
        "dashboard": cmd_dashboard,
        "close-all": cmd_close_all,
    }

    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
