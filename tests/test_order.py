"""
注文管理モジュールの動作テストスクリプト

デモ環境でUSD_JPYの最小単位（1通貨）を使い、
成行買い → トレード確認 → 決済 のフローをテストする。

使い方:
  python tests/test_order.py

注意:
  - デモ（practice）環境でのみ実行してください
  - 1通貨単位の注文のため損益への影響は極めて小さいです
"""

import sys
import time
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


def print_header(title: str) -> None:
    """セクションヘッダーを表示する。"""
    print()
    print("=" * 56)
    print(f"  {title}")
    print("=" * 56)


def print_info(label: str, value: str) -> None:
    """情報を整形して表示する。"""
    print(f"  {label:<22s}: {value}")


def main() -> None:
    """注文テストのメイン処理。"""
    print()
    print("╔══════════════════════════════════════════════════════╗")
    print("║    注文管理テスト（デモ環境・1通貨単位）              ║")
    print("║    成行買い → 確認 → 決済                           ║")
    print("╚══════════════════════════════════════════════════════╝")

    # ─── 初期化 ──────────────────────────────────────────
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        print("\n  ❌ .envファイルが見つかりません。")
        sys.exit(1)

    from config.settings import Settings
    from core.client import OandaClient
    from core.order import OrderManager

    settings = Settings()

    # 安全確認: practice環境のみ許可
    if settings.environment != "practice":
        print("\n  ❌ このテストはpractice（デモ）環境専用です。")
        print(f"     現在の環境: {settings.environment}")
        print("     .envの OANDA_ENV=practice に変更してください。")
        sys.exit(1)

    client = OandaClient(settings)

    # 注文履歴CSV保存先
    history_dir = PROJECT_ROOT / "data" / "order_history"
    history_path = history_dir / "test_orders.csv"

    om = OrderManager(client=client, history_path=str(history_path))

    results: dict[str, bool] = {}
    instrument = "USD_JPY"
    units = 1  # 最小単位

    # ═══════════════════════════════════════════════════════
    #  テスト1: 成行買い注文
    # ═══════════════════════════════════════════════════════
    print_header("テスト1: 成行買い注文（1通貨）")

    try:
        result = om.market_order(
            instrument=instrument,
            units=units,
        )

        if result.success:
            print_info("結果", "✅ 約定成功")
            print_info("注文ID", result.order_id)
            print_info("トレードID", result.trade_id)
            print_info("約定価格", f"{result.fill_price:.5f}")
            results["成行買い注文"] = True
        else:
            print_info("結果", f"❌ 拒否: {result.reject_reason}")
            results["成行買い注文"] = False

    except Exception as e:
        print(f"  ❌ エラー: {e}")
        results["成行買い注文"] = False

    if not results.get("成行買い注文"):
        print("\n  ⚠️  注文が失敗したため以降のテストをスキップします。")
        _print_summary(results, history_path)
        sys.exit(1)

    trade_id = result.trade_id

    # 少し待機（API反映待ち）
    print("\n  ⏳ 1秒待機中...")
    time.sleep(1)

    # ═══════════════════════════════════════════════════════
    #  テスト2: オープントレード確認
    # ═══════════════════════════════════════════════════════
    print_header("テスト2: オープントレード確認")

    try:
        open_trades = om.get_open_trades()
        found = False

        for t in open_trades:
            if t.get("id") == trade_id:
                found = True
                print_info("トレードID", t.get("id", ""))
                print_info("通貨ペア", t.get("instrument", ""))
                print_info("数量", t.get("currentUnits", ""))
                print_info("方向", "買い" if int(t.get("currentUnits", 0)) > 0 else "売り")
                print_info("エントリー価格", t.get("price", ""))
                print_info("未実現損益", t.get("unrealizedPL", ""))
                break

        if found:
            print("\n  ✅ トレードを確認できました")
            results["トレード確認"] = True
        else:
            print(f"\n  ❌ トレードID {trade_id} が見つかりません")
            print(f"     オープントレード数: {len(open_trades)}件")
            results["トレード確認"] = False

    except Exception as e:
        print(f"  ❌ エラー: {e}")
        results["トレード確認"] = False

    # ═══════════════════════════════════════════════════════
    #  テスト3: トレード決済
    # ═══════════════════════════════════════════════════════
    print_header("テスト3: トレード決済")

    try:
        close_response = om.close_trade(trade_id=trade_id, units="ALL")

        fill_tx = close_response.get("orderFillTransaction", {})
        realized_pl = fill_tx.get("pl", "N/A")
        close_price = fill_tx.get("price", "N/A")

        print_info("結果", "✅ 決済成功")
        print_info("決済価格", str(close_price))
        print_info("実現損益", f"{realized_pl} 円")
        results["トレード決済"] = True

    except Exception as e:
        print(f"  ❌ エラー: {e}")
        results["トレード決済"] = False

    # 少し待機
    time.sleep(1)

    # ═══════════════════════════════════════════════════════
    #  テスト4: 決済後の確認
    # ═══════════════════════════════════════════════════════
    print_header("テスト4: 決済後のオープントレード確認")

    try:
        open_trades = om.get_open_trades()
        still_open = any(t.get("id") == trade_id for t in open_trades)

        if not still_open:
            print("  ✅ トレードが正常に決済されました（残っていません）")
            results["決済後確認"] = True
        else:
            print("  ❌ トレードがまだオープン状態です")
            results["決済後確認"] = False

        print_info("残りオープントレード", f"{len(open_trades)}件")

    except Exception as e:
        print(f"  ❌ エラー: {e}")
        results["決済後確認"] = False

    # ═══════════════════════════════════════════════════════
    #  サマリー
    # ═══════════════════════════════════════════════════════
    _print_summary(results, history_path)


def _print_summary(
    results: dict[str, bool],
    history_path: Path,
) -> None:
    """テスト結果サマリーを表示する。"""
    print_header("テスト結果サマリー")
    print()

    passed = 0
    failed = 0
    for name, ok in results.items():
        status = "✅ PASS" if ok else "❌ FAIL"
        print(f"  {status}  {name}")
        if ok:
            passed += 1
        else:
            failed += 1

    print()
    print(f"  合計: {passed + failed}件  成功: {passed}件  失敗: {failed}件")

    if history_path.exists():
        print(f"\n  📄 注文履歴CSV: {history_path}")

    print()
    if failed == 0:
        print("  🎉 全テスト合格！注文管理モジュールは正常に動作しています。")
    else:
        print("  ⚠️  一部テストが失敗しました。ログを確認してください。")
    print()


if __name__ == "__main__":
    main()
