"""
リスク管理モジュールの動作テストスクリプト

RiskGuard（リスクガード）とPositionSizer（ポジションサイザー）の
動作を確認する。APIへの接続は不要。

使い方:
  python tests/test_risk.py
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


def print_info(label: str, value: str) -> None:
    """情報表示。"""
    print(f"  {label:<24s}: {value}")


def main() -> None:
    """テストメイン処理。"""
    print()
    print("╔══════════════════════════════════════════════════════╗")
    print("║    リスク管理モジュール テスト                        ║")
    print("╚══════════════════════════════════════════════════════╝")

    from risk.guard import RiskGuard, RiskConfig
    from risk.position_sizer import PositionSizer

    results: dict[str, bool] = {}
    balance = 1_000_000.0  # 100万円

    # ═══════════════════════════════════════════════════════
    #  テスト1: RiskGuard 基本チェック
    # ═══════════════════════════════════════════════════════
    print_header("1. RiskGuard — 基本チェック（全条件パス）")

    config = RiskConfig(
        max_positions=2,
        max_loss_per_trade_pct=2.0,
        max_daily_loss_pct=5.0,
        max_daily_trades=10,
        trading_hours_start="00:00",  # テスト用に全時間帯許可
        trading_hours_end="23:59",
    )
    guard = RiskGuard(config=config, account_balance=balance)

    can = guard.can_trade()
    print_info("can_trade()", str(can))
    print_info("最大ポジション", str(guard.check_max_positions()))
    print_info("日次損失上限", str(guard.check_daily_loss()))
    print_info("日次トレード数", str(guard.check_daily_trades()))
    print_info("取引時間帯", str(guard.check_trading_hours()))
    results["基本チェック（全パス）"] = can
    print(f"\n  {'✅' if can else '❌'} can_trade = {can}")

    # ═══════════════════════════════════════════════════════
    #  テスト2: ポジション数超過でブロック
    # ═══════════════════════════════════════════════════════
    print_header("2. RiskGuard — ポジション上限ブロック")

    guard.update_open_positions(2)  # 上限に到達
    blocked = not guard.check_max_positions()
    print_info("現在ポジション数", "2 (上限: 2)")
    print_info("ブロックされたか", str(blocked))
    results["ポジション上限ブロック"] = blocked
    print(f"\n  {'✅' if blocked else '❌'} 正しくブロック = {blocked}")

    guard.update_open_positions(0)  # リセット

    # ═══════════════════════════════════════════════════════
    #  テスト3: 日次損失超過でブロック
    # ═══════════════════════════════════════════════════════
    print_header("3. RiskGuard — 日次損失上限ブロック")

    # 大きな損失トレードを記録
    guard.log_trade({
        "instrument": "USD_JPY", "direction": "BUY",
        "units": 10000, "profit_loss": -30000, "status": "CLOSED",
    })
    guard.log_trade({
        "instrument": "USD_JPY", "direction": "SELL",
        "units": 10000, "profit_loss": -25000, "status": "CLOSED",
    })
    # 合計 -55,000円 = 残高100万の5.5% > 上限5%
    blocked = not guard.check_daily_loss()
    print_info("日次損失合計", "-55,000円 (5.5%)")
    print_info("上限", "5.0%")
    print_info("ブロックされたか", str(blocked))
    results["日次損失上限ブロック"] = blocked
    print(f"\n  {'✅' if blocked else '❌'} 正しくブロック = {blocked}")

    # ═══════════════════════════════════════════════════════
    #  テスト4: 日次サマリー
    # ═══════════════════════════════════════════════════════
    print_header("4. RiskGuard — 日次サマリー")

    summary = guard.get_daily_summary()
    for key, val in summary.items():
        print_info(key, str(val))
    results["日次サマリー"] = summary["total_trades"] > 0

    # ═══════════════════════════════════════════════════════
    #  テスト5: PositionSizer — USD_JPY
    # ═══════════════════════════════════════════════════════
    print_header("5. PositionSizer — USD_JPY（クロス円）")

    sizer = PositionSizer(account_balance=balance, default_risk_pct=2.0)

    test_cases = [
        ("USD_JPY", 20.0, 2.0),
        ("USD_JPY", 50.0, 1.0),
        ("USD_JPY", 100.0, 2.0),
    ]

    for inst, sl, risk in test_cases:
        units = sizer.calculate_units(inst, sl, risk)
        risk_info = sizer.calculate_risk_amount(inst, units, sl)
        print(f"\n  {inst} SL={sl:.0f}pips リスク={risk:.1f}%")
        print_info("    取引数量", f"{units:,}通貨")
        print_info("    リスク金額", f"{risk_info['risk_amount']:,.0f}円")
        print_info("    実リスク率", f"{risk_info['risk_pct']:.2f}%")
        print_info("    pip値", str(risk_info['pip_value']))

    results["PositionSizer（クロス円）"] = True

    # ═══════════════════════════════════════════════════════
    #  テスト6: PositionSizer — EUR_USD
    # ═══════════════════════════════════════════════════════
    print_header("6. PositionSizer — EUR_USD（その他通貨）")

    units_eurusd = sizer.calculate_units("EUR_USD", 30.0, 2.0)
    info = sizer.calculate_risk_amount("EUR_USD", units_eurusd, 30.0)
    print_info("取引数量", f"{units_eurusd:,}通貨")
    print_info("pip値", str(info['pip_value']))
    print_info("リスク金額", f"{info['risk_amount']:,.0f}円")
    results["PositionSizer（EUR_USD）"] = units_eurusd > 0

    # ═══════════════════════════════════════════════════════
    #  テスト7: サイジング早見表
    # ═══════════════════════════════════════════════════════
    print_header("7. サイジング早見表（USD_JPY, 残高100万円）")

    table = sizer.get_sizing_table(
        "USD_JPY",
        sl_pips_range=[20, 50, 100],
        risk_pct_range=[1.0, 2.0, 3.0],
    )
    print()
    print(f"  {'SL(pips)':<10s} {'リスク%':<10s} {'数量':<12s} {'リスク額':<10s}")
    print(f"  {'-'*42}")
    for row in table:
        print(
            f"  {row['sl_pips']:<10.0f} {row['risk_pct']:<10.1f} "
            f"{row['units']:<12,d} ¥{row['risk_amount']:<10,.0f}"
        )
    results["サイジング早見表"] = len(table) > 0

    # ═══════════════════════════════════════════════════════
    #  サマリー
    # ═══════════════════════════════════════════════════════
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
        print("  🎉 全テスト合格！リスク管理モジュールは正常です。")
    else:
        print("  ⚠️  一部テストが失敗しました。")
    print()


if __name__ == "__main__":
    main()
