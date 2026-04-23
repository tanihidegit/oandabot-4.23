"""
OANDA API 接続テストスクリプト

OANDA Practice環境への接続を検証し、以下のテストを実行する:
  1. 口座情報の取得（残高・通貨・マージン使用率）
  2. USD_JPY, EUR_JPY の現在レート取得
  3. USD_JPY の5分足ローソク足データ取得（直近10本）

使い方:
  python tests/test_connection.py
"""

import sys
import os
import logging
from pathlib import Path

# プロジェクトルートをパスに追加（tests/ から実行可能にする）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ─── ロギング設定 ───────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,  # テストスクリプトではWARNING以上のみ表示
    format="%(levelname)s: %(message)s",
)


# ─── 表示ユーティリティ ────────────────────────────────────
def print_header(title: str) -> None:
    """セクションヘッダーを表示する。"""
    width = 60
    print()
    print("=" * width)
    print(f"  {title}")
    print("=" * width)


def print_success(message: str) -> None:
    """成功メッセージを表示する。"""
    print(f"  ✅ {message}")


def print_failure(message: str) -> None:
    """失敗メッセージを表示する。"""
    print(f"  ❌ {message}")


def print_info(label: str, value: str) -> None:
    """情報を整形して表示する。"""
    print(f"  {label:<20s}: {value}")


# ─── .envファイルの存在チェック ─────────────────────────────
def check_env_file() -> bool:
    """
    .envファイルの存在を確認する。

    Returns:
        .envファイルが存在すればTrue。
    """
    env_path = PROJECT_ROOT / ".env"

    if not env_path.exists():
        print_header("⚠️  .envファイルが見つかりません")
        print()
        print("  接続テストを実行するには .env ファイルが必要です。")
        print()
        print("  【セットアップ手順】")
        print(f"  1. テンプレートをコピー:")
        print(f"     copy .env.example .env")
        print()
        print(f"  2. .env ファイルを編集して以下を設定:")
        print(f"     OANDA_ENV=practice")
        print(f"     OANDA_ACCESS_TOKEN=あなたのAPIトークン")
        print(f"     OANDA_ACCOUNT_ID=あなたの口座ID")
        print()
        print("  APIトークンの取得方法:")
        print("    OANDA Japan (https://www.oanda.jp/) にログインし、")
        print("    「APIアクセスの管理」からトークンを生成してください。")
        print("    ※ プロコース（NYサーバー）が必要です。")
        print()
        return False

    return True


# ─── テスト1: 口座情報取得 ──────────────────────────────────
def test_account_info(client) -> bool:
    """
    口座情報を取得して表示するテスト。

    Args:
        client: OandaClientインスタンス。

    Returns:
        テスト成功ならTrue。
    """
    print_header("テスト1: 口座情報の取得")

    try:
        summary = client.get_account_summary()

        balance = summary.get("balance", "N/A")
        currency = summary.get("currency", "N/A")
        margin_used = summary.get("marginUsed", "0")
        margin_available = summary.get("marginAvailable", "0")
        open_trade_count = summary.get("openTradeCount", "0")
        unrealized_pl = summary.get("unrealizedPL", "0")

        # マージン使用率を計算
        try:
            used = float(margin_used)
            available = float(margin_available)
            total_margin = used + available
            margin_rate = (used / total_margin * 100) if total_margin > 0 else 0.0
        except (ValueError, ZeroDivisionError):
            margin_rate = 0.0

        print_info("残高", f"{float(balance):,.2f} {currency}")
        print_info("通貨", currency)
        print_info("使用中マージン", f"{float(margin_used):,.2f} {currency}")
        print_info("利用可能マージン", f"{float(margin_available):,.2f} {currency}")
        print_info("マージン使用率", f"{margin_rate:.2f}%")
        print_info("未実現損益", f"{float(unrealized_pl):,.2f} {currency}")
        print_info("オープンポジション数", str(open_trade_count))

        print()
        print_success("口座情報の取得に成功しました")
        return True

    except Exception as e:
        print()
        print_failure(f"口座情報の取得に失敗しました")
        print(f"  エラー詳細: {e}")
        return False


# ─── テスト2: 現在レート取得 ─────────────────────────────────
def test_current_prices(client) -> bool:
    """
    USD_JPY, EUR_JPYの現在レートを取得して表示するテスト。

    Args:
        client: OandaClientインスタンス。

    Returns:
        テスト成功ならTrue。
    """
    print_header("テスト2: 現在レートの取得（USD_JPY, EUR_JPY）")

    target_instruments = ["USD_JPY", "EUR_JPY"]
    all_success = True

    for instrument in target_instruments:
        try:
            price_data = client.get_current_price(instrument)

            bids = price_data.get("bids", [])
            asks = price_data.get("asks", [])

            if not bids or not asks:
                print_failure(f"{instrument}: Bid/Askデータが空です")
                all_success = False
                continue

            bid = float(bids[0]["price"])
            ask = float(asks[0]["price"])
            spread = ask - bid

            print()
            print(f"  【{instrument}】")
            print_info("  Bid（買値）", f"{bid:.5f}")
            print_info("  Ask（売値）", f"{ask:.5f}")
            print_info("  Spread", f"{spread:.5f} ({spread * 100:.2f} pips)")

        except Exception as e:
            print()
            print_failure(f"{instrument} のレート取得に失敗しました")
            print(f"  エラー詳細: {e}")
            all_success = False

    print()
    if all_success:
        print_success("全通貨ペアの現在レート取得に成功しました")
    else:
        print_failure("一部の通貨ペアでレート取得に失敗しました")

    return all_success


# ─── テスト3: ローソク足データ取得 ──────────────────────────
def test_candle_data(client) -> bool:
    """
    USD_JPYの5分足ローソク足データ（直近10本）を取得して表示するテスト。

    Args:
        client: OandaClientインスタンス。

    Returns:
        テスト成功ならTrue。
    """
    print_header("テスト3: ローソク足データの取得（USD_JPY 5分足 直近10本）")

    try:
        df = client.get_candles(
            instrument="USD_JPY",
            granularity="M5",
            count=10,
        )

        if df.empty:
            print_failure("ローソク足データが空です（市場がクローズ中の可能性があります）")
            return False

        # DataFrameを見やすくフォーマット
        display_df = df.copy()
        display_df.index = display_df.index.strftime("%Y-%m-%d %H:%M")
        display_df.index.name = "日時"
        display_df.columns = ["始値", "高値", "安値", "終値", "出来高", "確定"]

        print()
        print(display_df.to_string())
        print()
        print_info("取得件数", f"{len(df)}本")
        print_info("期間", f"{df.index[0]} ～ {df.index[-1]}")
        print()
        print_success("ローソク足データの取得に成功しました")
        return True

    except Exception as e:
        print()
        print_failure("ローソク足データの取得に失敗しました")
        print(f"  エラー詳細: {e}")
        return False


# ─── メイン処理 ─────────────────────────────────────────────
def main() -> None:
    """
    接続テストのメイン処理。

    全テストを順番に実行し、最後にサマリーを表示する。
    """
    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║       OANDA FX自動売買ボット - 接続テスト               ║")
    print("╚══════════════════════════════════════════════════════════╝")

    # .envファイルの存在チェック
    if not check_env_file():
        sys.exit(1)

    # 設定読み込みとクライアント初期化
    print_header("初期化: 設定読み込み & API接続")

    try:
        from config.settings import Settings
        from core.client import OandaClient

        settings = Settings()
        print_info("環境", settings.environment)
        print_info("REST URL", settings.rest_url)
        print_info("口座ID", settings._mask_account_id())
        print()
        print_success("設定の読み込みに成功しました")

        client = OandaClient(settings)
        print_success("APIクライアントの初期化に成功しました")

    except ValueError as e:
        print()
        print_failure(f"設定エラー: {e}")
        print()
        print("  .envファイルの内容を確認してください。")
        print("  テンプレート: .env.example")
        sys.exit(1)
    except ImportError as e:
        print()
        print_failure(f"モジュールのインポートに失敗しました: {e}")
        print()
        print("  依存パッケージがインストールされていない可能性があります。")
        print("  以下のコマンドを実行してください:")
        print("    pip install -r requirements.txt")
        sys.exit(1)

    # ─── テスト実行 ──────────────────────────────────────────
    results: dict[str, bool] = {}

    results["口座情報取得"] = test_account_info(client)
    results["現在レート取得"] = test_current_prices(client)
    results["ローソク足データ取得"] = test_candle_data(client)

    # ─── サマリー表示 ────────────────────────────────────────
    print_header("テスト結果サマリー")
    print()

    passed = 0
    failed = 0

    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status}  {test_name}")
        if result:
            passed += 1
        else:
            failed += 1

    print()
    print(f"  合計: {passed + failed}件  成功: {passed}件  失敗: {failed}件")
    print()

    if failed == 0:
        print("  🎉 全てのテストに合格しました！OANDA APIへの接続は正常です。")
        print("  次のステップ: 売買戦略の実装に進みましょう。")
    else:
        print("  ⚠️  一部のテストが失敗しました。")
        print("  以下を確認してください:")
        print("    - .envのAPIトークンが正しいか")
        print("    - .envの口座IDが正しいか")
        print("    - インターネット接続が有効か")
        print("    - OANDA APIサーバーが稼働中か")
        print("    - 市場がオープンしているか（ローソク足テストの場合）")

    print()

    # 終了コード（CI/CD対応）
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
