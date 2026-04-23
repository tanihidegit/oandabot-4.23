"""
バックテストエンジンの動作テストスクリプト

SMAクロス戦略でUSD_JPYの1時間足データをバックテストし、
パフォーマンスサマリーとチャートを出力する。

使い方:
  python tests/test_backtest.py
"""

import sys
import logging
from pathlib import Path

# プロジェクトルートをパスに追加
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def main() -> None:
    """SMAクロス戦略のバックテストを実行する。"""
    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║    バックテスト動作テスト - SMAクロス戦略                 ║")
    print("╚══════════════════════════════════════════════════════════╝")

    # ─── .envチェック ─────────────────────────────────────────
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        print("\n  ❌ .envファイルが見つかりません。")
        print("     copy .env.example .env で作成してください。")
        sys.exit(1)

    # ─── インポート ───────────────────────────────────────────
    from config.settings import Settings
    from core.client import OandaClient
    from backtest.data_loader import OandaDataLoader
    from backtest.engine import Backtester, BacktestConfig
    from backtest.chart import plot_backtest_results
    from strategy.sma_cross import SmaCrossStrategy

    # ─── データ取得 ───────────────────────────────────────────
    print("\n[1/4] データ取得中...")
    settings = Settings()
    client = OandaClient(settings)
    loader = OandaDataLoader(client)

    instrument = "USD_JPY"
    df = loader.fetch_and_cache(
        instrument=instrument,
        granularity="H1",
        from_date="2024-01-01T00:00:00Z",
        to_date="2024-06-30T23:59:59Z",
        cache_dir=str(PROJECT_ROOT / "data" / "cache"),
    )

    if df.empty:
        print("  ❌ データが取得できませんでした。")
        sys.exit(1)

    print(f"  ✅ {len(df)}本のローソク足を取得しました")

    # ─── バックテスト設定 ─────────────────────────────────────
    print("\n[2/4] バックテスト実行中...")

    strategy = SmaCrossStrategy(short_period=20, long_period=50)

    config = BacktestConfig(
        initial_balance=1_000_000,     # 初期資金100万円
        units=1000,                     # 1000通貨
        spread_pips=0.3,                # スプレッド0.3pips
        take_profit_pips=100.0,         # 利確100pips
        stop_loss_pips=50.0,            # 損切50pips
        max_positions=1,                # 最大1ポジション
        pip_value=0.01,                 # USD_JPYは0.01
    )

    backtester = Backtester(strategy=strategy, config=config)
    summary = backtester.run(df)

    # ─── 結果表示 ─────────────────────────────────────────────
    print("\n[3/4] 結果表示")
    print("\n" + "=" * 50)
    print("  パフォーマンスサマリー")
    print("=" * 50)

    labels = {
        "strategy": "戦略",
        "total_trades": "トレード数",
        "win_count": "勝ちトレード",
        "loss_count": "負けトレード",
        "win_rate": "勝率（%）",
        "total_pips": "合計pips",
        "net_profit": "純損益（円）",
        "total_profit": "総利益（円）",
        "total_loss": "総損失（円）",
        "profit_factor": "プロフィットファクター",
        "avg_win": "平均利益（円）",
        "avg_loss": "平均損失（円）",
        "max_drawdown": "最大DD（円）",
        "max_drawdown_pct": "最大DD（%）",
        "sharpe_ratio": "シャープレシオ",
        "initial_balance": "初期資金（円）",
        "final_balance": "最終残高（円）",
        "roi_pct": "投資収益率（%）",
    }

    for key, label in labels.items():
        value = summary.get(key, "N/A")
        if isinstance(value, float):
            print(f"  {label:<22s}: {value:>14,.2f}")
        else:
            print(f"  {label:<22s}: {value}")

    # トレード一覧（先頭10件）
    trades_df = backtester.get_trades_df()
    if not trades_df.empty:
        print(f"\n  トレード一覧（先頭10件 / 全{len(trades_df)}件）:")
        print(trades_df.head(10).to_string(index=False))

    # ─── チャート保存 ─────────────────────────────────────────
    print("\n[4/4] チャート出力中...")
    output_dir = PROJECT_ROOT / "data" / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    chart_path = str(output_dir / "backtest_sma_cross.png")

    equity_df = backtester.get_equity_df()
    plot_backtest_results(
        equity_df=equity_df,
        trades_df=trades_df,
        summary=summary,
        save_path=chart_path,
        show=False,
    )

    print(f"  ✅ チャートを保存しました: {chart_path}")
    print("\n  🎉 バックテスト完了！")
    print()


if __name__ == "__main__":
    main()
