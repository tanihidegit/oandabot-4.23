"""
戦略モジュールの動作テストスクリプト

各戦略（SMAクロス、モメンタム、ブレイクアウト、シグナル統合）の
シグナル出力を確認する。APIからデータを取得してテストする。

使い方:
  python tests/test_strategies.py
"""

import sys
import logging
from pathlib import Path
from collections import Counter

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


def print_header(title: str) -> None:
    """セクションヘッダーを表示する。"""
    print()
    print("=" * 56)
    print(f"  {title}")
    print("=" * 56)


def test_strategy(name: str, strategy, df, sample_count: int = 5) -> dict:
    """
    個別戦略のシグナル出力をテストする。

    Args:
        name: 表示用の戦略名。
        strategy: BaseStrategy実装のインスタンス。
        df: ローソク足データ。
        sample_count: 表示するシグナルサンプル数。

    Returns:
        シグナル集計辞書。
    """
    from strategy.base import Signal

    print_header(name)
    print(f"  戦略: {strategy}")

    # 指標計算
    prepared = strategy.prepare(df)
    print(f"  指標計算後カラム: {list(prepared.columns)}")

    # 全行のシグナルを生成
    signals = []
    for i in range(len(prepared)):
        sig = strategy.generate_signal(prepared, i)
        signals.append(sig)

    # 集計
    counts = Counter(sig.name for sig in signals)
    print(f"\n  シグナル集計（全{len(signals)}本）:")
    for sig_name in ["BUY", "SELL", "CLOSE_LONG", "CLOSE_SHORT", "CLOSE", "HOLD"]:
        c = counts.get(sig_name, 0)
        if c > 0:
            pct = c / len(signals) * 100
            print(f"    {sig_name:<14s}: {c:>5d}回 ({pct:.1f}%)")

    # HOLD以外のサンプル表示
    print(f"\n  シグナルサンプル（HOLD以外、先頭{sample_count}件）:")
    shown = 0
    for i, sig in enumerate(signals):
        if sig != Signal.HOLD and shown < sample_count:
            row = prepared.iloc[i]
            time_str = str(prepared.index[i])[:16]
            print(
                f"    [{time_str}] {sig.name:<14s} "
                f"close={row['close']:.3f}"
            )
            shown += 1

    if shown == 0:
        print("    （HOLD以外のシグナルなし）")

    return dict(counts)


def test_aggregator(strategies, df) -> None:
    """シグナル統合のテストを行う。"""
    from strategy.base import Signal
    from strategy.signals import SignalAggregator

    print_header("シグナル統合テスト（全戦略一致時のみ発行）")

    # 全戦略一致モード
    aggregator = SignalAggregator(strategies, min_agreement=len(strategies))
    prepared = aggregator.prepare(df)

    signals = []
    for i in range(len(prepared)):
        sig = aggregator.generate_signal(prepared, i)
        signals.append(sig)

    counts = Counter(sig.name for sig in signals)
    print(f"  統合戦略: {[s.name for s in strategies]}")
    print(f"  最低合意数: {aggregator.min_agreement}/{len(strategies)}")
    print(f"\n  統合シグナル集計（全{len(signals)}本）:")
    for sig_name in ["BUY", "SELL", "CLOSE_LONG", "CLOSE_SHORT", "HOLD"]:
        c = counts.get(sig_name, 0)
        if c > 0:
            print(f"    {sig_name:<14s}: {c:>5d}回")

    # 合意サンプル
    print(f"\n  合意シグナルのサンプル（先頭5件）:")
    shown = 0
    for i, sig in enumerate(signals):
        if sig != Signal.HOLD and shown < 5:
            detail = aggregator.get_individual_signals(prepared, i)
            detail_str = ", ".join(f"{k}={v.name}" for k, v in detail.items())
            time_str = str(prepared.index[i])[:16]
            print(f"    [{time_str}] 統合={sig.name}")
            print(f"      個別: {detail_str}")
            shown += 1

    if shown == 0:
        print("    （合意シグナルなし — フィルタリングが機能しています）")

    # 2戦略以上の合意モードもテスト
    if len(strategies) >= 3:
        print_header("シグナル統合テスト（2戦略以上で発行）")
        agg2 = SignalAggregator(strategies, min_agreement=2)
        prepared2 = agg2.prepare(df)

        signals2 = []
        for i in range(len(prepared2)):
            signals2.append(agg2.generate_signal(prepared2, i))

        counts2 = Counter(sig.name for sig in signals2)
        print(f"  最低合意数: 2/{len(strategies)}")
        for sig_name in ["BUY", "SELL", "CLOSE_LONG", "CLOSE_SHORT", "HOLD"]:
            c = counts2.get(sig_name, 0)
            if c > 0:
                print(f"    {sig_name:<14s}: {c:>5d}回")


def main() -> None:
    """戦略テストのメイン処理。"""
    print()
    print("╔══════════════════════════════════════════════════════╗")
    print("║    戦略モジュール テスト                              ║")
    print("║    各戦略のシグナル出力を確認                         ║")
    print("╚══════════════════════════════════════════════════════╝")

    # .envチェック
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        print("\n  ❌ .envファイルが見つかりません。")
        sys.exit(1)

    # データ取得
    from config.settings import Settings
    from core.client import OandaClient
    from backtest.data_loader import OandaDataLoader
    from strategy.sma_cross import SmaCrossStrategy
    from strategy.momentum import MomentumStrategy
    from strategy.breakout import BreakoutStrategy

    print_header("データ取得")
    settings = Settings()
    client = OandaClient(settings)
    loader = OandaDataLoader(client)

    df = loader.fetch_and_cache(
        instrument="USD_JPY",
        granularity="H1",
        from_date="2024-01-01T00:00:00Z",
        to_date="2024-03-31T23:59:59Z",
        cache_dir=str(PROJECT_ROOT / "data" / "cache"),
    )

    if df.empty:
        print("  ❌ データが取得できませんでした。")
        sys.exit(1)
    print(f"  ✅ {len(df)}本のデータを取得")

    # 各戦略のテスト
    sma = SmaCrossStrategy(short_period=20, long_period=50)
    momentum = MomentumStrategy(
        rsi_period=14, rsi_overbought=70, rsi_oversold=30,
        ema_short=20, ema_long=50,
    )
    breakout = BreakoutStrategy(
        lookback_period=20, atr_period=14, atr_multiplier=2.0,
    )

    test_strategy("1. SMAクロス戦略", sma, df)
    test_strategy("2. モメンタム戦略（RSI + EMA）", momentum, df)
    test_strategy("3. ブレイクアウト戦略（ATRストップ）", breakout, df)

    # シグナル統合テスト
    test_aggregator([sma, momentum, breakout], df)

    # 完了
    print_header("テスト完了")
    print("  🎉 全戦略のシグナル出力を確認しました！")
    print()


if __name__ == "__main__":
    main()
