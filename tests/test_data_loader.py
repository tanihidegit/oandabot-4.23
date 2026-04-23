"""
データローダーの動作テストスクリプト

USD_JPYの2024年1月〜6月の1時間足データを取得し、
データの概要を表示する。

使い方:
  python tests/test_data_loader.py
"""

import sys
import logging
from pathlib import Path

# プロジェクトルートをパスに追加
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def print_header(title: str) -> None:
    """セクションヘッダーを表示する。"""
    width = 60
    print()
    print("=" * width)
    print(f"  {title}")
    print("=" * width)


def print_info(label: str, value: str) -> None:
    """情報を整形して表示する。"""
    print(f"  {label:<20s}: {value}")


def main() -> None:
    """
    データローダーのテストを実行する。

    USD_JPYの2024年1月〜6月の1時間足をOANDA APIから取得し、
    データの概要（件数、期間、OHLCV統計、先頭・末尾レコード）を表示する。
    """
    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║       データローダー テスト                              ║")
    print("║       USD_JPY 1時間足 (2024/01 〜 2024/06)              ║")
    print("╚══════════════════════════════════════════════════════════╝")

    # ─── .envチェック ────────────────────────────────────────
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        print()
        print("  ❌ .envファイルが見つかりません。")
        print("     先に .env.example をコピーして設定してください:")
        print("     copy .env.example .env")
        sys.exit(1)

    # ─── 初期化 ──────────────────────────────────────────────
    print_header("1. 初期化")

    try:
        from config.settings import Settings
        from core.client import OandaClient
        from backtest.data_loader import OandaDataLoader

        settings = Settings()
        client = OandaClient(settings)
        loader = OandaDataLoader(client)
        print("  ✅ データローダーを初期化しました")
    except Exception as e:
        print(f"  ❌ 初期化に失敗しました: {e}")
        sys.exit(1)

    # ─── テストパラメータ ────────────────────────────────────
    instrument = "USD_JPY"
    granularity = "H1"
    from_date = "2024-01-01T00:00:00Z"
    to_date = "2024-06-30T23:59:59Z"
    cache_dir = PROJECT_ROOT / "data" / "cache"

    print_header("2. データ取得パラメータ")
    print_info("通貨ペア", instrument)
    print_info("時間足", granularity)
    print_info("取得開始", from_date)
    print_info("取得終了", to_date)
    print_info("キャッシュ先", str(cache_dir))

    # ─── データ取得（キャッシュ対応）─────────────────────────
    print_header("3. データ取得中...")
    print("  ※ 初回はAPIから取得するため時間がかかります。")
    print("  ※ 2回目以降はキャッシュ（CSV）から高速に読み込みます。")
    print()

    try:
        df = loader.fetch_and_cache(
            instrument=instrument,
            granularity=granularity,
            from_date=from_date,
            to_date=to_date,
            cache_dir=str(cache_dir),
        )
    except Exception as e:
        print(f"  ❌ データ取得に失敗しました: {e}")
        print()
        print("  考えられる原因:")
        print("    - APIトークンが無効または期限切れ")
        print("    - インターネット接続の問題")
        print("    - OANDA APIサーバーのメンテナンス")
        sys.exit(1)

    if df.empty:
        print("  ⚠️  取得したデータが空です。")
        print("     指定期間のデータが存在しない可能性があります。")
        sys.exit(1)

    print(f"  ✅ {len(df)}本のローソク足データを取得しました")

    # ─── データ概要 ──────────────────────────────────────────
    print_header("4. データ概要")

    print_info("レコード数", f"{len(df):,}本")
    print_info("期間（開始）", str(df.index[0]))
    print_info("期間（終了）", str(df.index[-1]))
    print_info("カラム", ", ".join(df.columns.tolist()))

    # 欠損値チェック
    null_counts = df.isnull().sum()
    total_nulls = null_counts.sum()
    print_info("欠損値", f"{total_nulls}件")

    # ─── 統計サマリー ────────────────────────────────────────
    print_header("5. 統計サマリー（OHLCV）")
    print()
    print(df.describe().to_string())

    # ─── 価格レンジ ──────────────────────────────────────────
    print_header("6. 価格レンジ")
    print_info("最高値", f"{df['high'].max():.3f}")
    print_info("最安値", f"{df['low'].min():.3f}")
    print_info("レンジ幅", f"{df['high'].max() - df['low'].min():.3f}")
    print_info("平均終値", f"{df['close'].mean():.3f}")
    print_info("合計出来高", f"{df['volume'].sum():,.0f}")

    # ─── 先頭5行 ─────────────────────────────────────────────
    print_header("7. 先頭5行")
    print()
    print(df.head().to_string())

    # ─── 末尾5行 ─────────────────────────────────────────────
    print_header("8. 末尾5行")
    print()
    print(df.tail().to_string())

    # ─── CSV保存確認 ─────────────────────────────────────────
    print_header("9. キャッシュファイル確認")
    cache_files = list(cache_dir.glob("*.csv"))
    if cache_files:
        for f in cache_files:
            size_kb = f.stat().st_size / 1024
            print(f"  📄 {f.name} ({size_kb:.1f} KB)")
    else:
        print("  キャッシュファイルはありません")

    # ─── 完了 ────────────────────────────────────────────────
    print_header("テスト完了")
    print()
    print("  🎉 データローダーは正常に動作しています！")
    print()
    print("  このデータを使って次のステップに進めます:")
    print("    - テクニカル指標の計算（pandas_ta）")
    print("    - バックテストエンジンの実装")
    print("    - 売買シグナルの検証")
    print()


if __name__ == "__main__":
    main()
