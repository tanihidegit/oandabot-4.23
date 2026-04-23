"""
監視・通知モジュールの動作テストスクリプト

Notifier（LINE通知）のメッセージ生成テストと、
Dashboard（ターミナル表示）の単発スナップショットテストを行う。

使い方:
  python tests/test_monitor.py            # 通知テスト（ログ出力のみ）
  python tests/test_monitor.py --dashboard  # ダッシュボードも起動
"""

import sys
import logging
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


def print_header(title: str) -> None:
    """セクションヘッダー。"""
    print()
    print("=" * 56)
    print(f"  {title}")
    print("=" * 56)


def test_notifier() -> dict[str, bool]:
    """Notifierの全メッセージテンプレートをテストする。"""
    from monitor.notifier import Notifier

    results: dict[str, bool] = {}

    print_header("1. Notifier — メッセージテンプレートテスト")

    # トークン未設定でもログ出力モードで動作することを確認
    notifier = Notifier(token="")
    print(f"  LINE Notify有効: {notifier.enabled}")
    print("  ※ ログ出力モードでテストします")

    # 約定通知
    print_header("  約定通知")
    ok = notifier.notify_order_fill(
        instrument="USD_JPY",
        direction="BUY",
        units=10000,
        fill_price=150.500,
        tp_price=152.000,
        sl_price=149.500,
    )
    results["約定通知（BUY）"] = ok

    ok = notifier.notify_order_fill(
        instrument="EUR_USD",
        direction="SELL",
        units=5000,
        fill_price=1.08500,
    )
    results["約定通知（SELL）"] = ok

    # 決済通知
    print_header("  決済通知")
    ok = notifier.notify_trade_close(
        instrument="USD_JPY",
        pips=15.3,
        profit_loss=1530,
    )
    results["決済通知（利益）"] = ok

    ok = notifier.notify_trade_close(
        instrument="USD_JPY",
        pips=-8.5,
        profit_loss=-850,
    )
    results["決済通知（損失）"] = ok

    # エラー通知
    print_header("  エラー通知")
    ok = notifier.notify_error(
        error_message="API接続エラー発生",
        context="core.client.get_account_summary",
    )
    results["エラー通知"] = ok

    # 日次サマリー
    print_header("  日次サマリー通知")
    ok = notifier.notify_daily_summary(
        total_pl=5200,
        win_count=3,
        total_trades=5,
        balance=1_005_200,
    )
    results["日次サマリー通知"] = ok

    # カスタム通知
    ok = notifier.notify_custom("🛠️ テストメッセージ: システム正常動作中")
    results["カスタム通知"] = ok

    return results


def test_dashboard_snapshot() -> bool:
    """ダッシュボードの単発表示テスト。"""
    print_header("2. Dashboard — スナップショット表示")

    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        print("  ⚠️ .envファイルがないためスキップします")
        return True

    try:
        from core.client import OandaClient
        from monitor.dashboard import Dashboard
        from rich.console import Console

        client = OandaClient()
        dashboard = Dashboard(
            client=client,
            watch_instruments=["USD_JPY", "EUR_JPY"],
            refresh_interval=5.0,
        )

        # 1回だけ表示を構築してレンダリング
        console = Console()
        display = dashboard._build_display()
        console.print(display)
        print("\n  ✅ ダッシュボード表示に成功しました")
        return True

    except Exception as e:
        print(f"  ❌ ダッシュボードエラー: {e}")
        return False


def main() -> None:
    """テストメイン。"""
    print()
    print("╔══════════════════════════════════════════════════════╗")
    print("║    監視・通知モジュール テスト                        ║")
    print("╚══════════════════════════════════════════════════════╝")

    # Notifierテスト
    results = test_notifier()

    # ダッシュボードテスト（--dashboardフラグ指定時）
    if "--dashboard" in sys.argv:
        dashboard_ok = test_dashboard_snapshot()
        results["ダッシュボード表示"] = dashboard_ok
    else:
        print_header("2. Dashboard — スキップ")
        print("  --dashboard フラグを付けて実行するとテストします")
        print("  例: python tests/test_monitor.py --dashboard")

    # サマリー
    print_header("テスト結果サマリー")
    print()

    passed = failed = 0
    for name, ok in results.items():
        status = "✅ PASS" if ok else "❌ FAIL"
        print(f"  {status}  {name}")
        if ok:
            passed += 1
        else:
            failed += 1

    print(f"\n  合計: {passed + failed}件  成功: {passed}件  失敗: {failed}件")
    print()

    if failed == 0:
        print("  🎉 全テスト合格！")
        print()
        print("  ダッシュボードの常時監視を起動するには:")
        print("    python monitor/dashboard.py")
    else:
        print("  ⚠️ 一部テストが失敗しました。")
    print()


if __name__ == "__main__":
    main()
