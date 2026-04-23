# OANDA FX 自動売買ボット

OANDA v20 REST APIを使用したPython製のFX自動売買システム。

## 機能一覧

| モジュール | 機能 |
|---|---|
| **core/** | API接続、価格取得、注文管理（リトライ・約定確認付き） |
| **strategy/** | SMAクロス、モメンタム(RSI+EMA)、ブレイクアウト(ATR)、シグナル統合 |
| **backtest/** | ヒストリカルデータ取得、バックテストエンジン、チャート出力 |
| **risk/** | リスクガード（ポジション数/日次損失/取引時間帯制限）、ポジションサイザー |
| **monitor/** | LINE Notify通知、richターミナルダッシュボード |
| **webhook/** | TradingView Webhook連携（Flask） |

## セットアップ

### 1. 環境構築

```bash
cd oanda-fx-bot
python -m venv venv
venv\Scripts\activate      # Windows
pip install -r requirements.txt
```

### 2. 環境変数設定

```bash
copy .env.example .env
```

`.env` を編集して以下を設定:
- `OANDA_ACCESS_TOKEN`: OANDAのAPIトークン
- `OANDA_ACCOUNT_ID`: 口座ID
- `OANDA_ENV`: `practice`（デモ）or `live`（本番）
- `LINE_NOTIFY_TOKEN`: LINE通知トークン（任意）
- `WEBHOOK_SECRET`: Webhook認証キー（任意）

## CLI コマンド

```bash
# 自動売買ボット起動
python main.py run --strategy momentum --instrument USD_JPY --interval 300
python main.py run --strategy breakout --instrument EUR_JPY --interval 60 --units 1000

# バックテスト
python main.py backtest --strategy momentum --instrument USD_JPY --from 2024-01-01 --to 2024-06-01

# リアルタイムダッシュボード
python main.py dashboard

# TradingView Webhookサーバー
python main.py webhook --port 5000

# 全ポジション決済
python main.py close-all
python main.py close-all --instrument USD_JPY
```

## TradingView Webhook 連携

### 概要

TradingViewのアラート機能からHTTP POSTでシグナルを送信し、
自動的にOANDAで注文を実行する。

### セットアップ手順

#### 1. Webhookサーバーを起動

```bash
python main.py webhook --port 5000
```

#### 2. ngrokで外部公開

ローカルサーバーをインターネットに公開するため [ngrok](https://ngrok.com/) を使用:

```bash
# ngrokのインストール（初回のみ）
# https://ngrok.com/download からダウンロード

# ngrokでポートを公開
ngrok http 5000
```

表示されるURLをメモ（例: `https://abcd1234.ngrok-free.app`）

#### 3. TradingViewでアラートを設定

1. TradingViewでチャートを開く
2. アラートを作成（Alt + A）
3. **「通知」タブ** を開く
4. **「Webhook URL」** にチェックを入れ、URLを入力:
   ```
   https://abcd1234.ngrok-free.app/webhook
   ```
5. **「メッセージ」** に以下のJSON形式で入力:

**買い注文:**
```json
{
  "action": "buy",
  "instrument": "USD_JPY",
  "units": 10000,
  "secret": "your_webhook_secret_here"
}
```

**売り注文:**
```json
{
  "action": "sell",
  "instrument": "USD_JPY",
  "units": 10000,
  "tp_price": 152.000,
  "sl_price": 149.500,
  "secret": "your_webhook_secret_here"
}
```

**決済:**
```json
{
  "action": "close",
  "instrument": "USD_JPY",
  "secret": "your_webhook_secret_here"
}
```

> **重要**: `secret` の値は `.env` の `WEBHOOK_SECRET` と同じ値にしてください。

#### 4. Pine Scriptで動的メッセージを使う例

```pinescript
//@version=5
strategy("My Strategy", overlay=true)

// ... 戦略ロジック ...

if (longCondition)
    strategy.entry("Long", strategy.long,
        alert_message='{"action":"buy","instrument":"USD_JPY","units":10000,"secret":"your_secret"}')

if (shortCondition)
    strategy.entry("Short", strategy.short,
        alert_message='{"action":"sell","instrument":"USD_JPY","units":10000,"secret":"your_secret"}')

if (closeCondition)
    strategy.close_all(
        alert_message='{"action":"close","instrument":"USD_JPY","secret":"your_secret"}')
```

### Webhook動作確認（curlテスト）

```bash
# ヘルスチェック
curl http://localhost:5000/health

# 買い注文テスト（1通貨）
curl -X POST http://localhost:5000/webhook \
  -H "Content-Type: application/json" \
  -d '{"action":"buy","instrument":"USD_JPY","units":1,"secret":"your_webhook_secret_here"}'

# 決済テスト
curl -X POST http://localhost:5000/webhook \
  -H "Content-Type: application/json" \
  -d '{"action":"close","instrument":"USD_JPY","secret":"your_webhook_secret_here"}'
```

### セキュリティ注意事項

- `WEBHOOK_SECRET` は必ず複雑なランダム文字列に変更してください
- ngrokの無料プランはURL が毎回変わります。固定ドメインが必要な場合は有料プラン推奨
- 本番環境では HTTPS を使用してください
- ファイアウォールで不要なポートを閉じてください

## テストスクリプト

```bash
python tests/test_connection.py    # API接続テスト
python tests/test_data_loader.py   # データローダーテスト
python tests/test_backtest.py      # バックテストテスト
python tests/test_order.py         # 注文テスト（デモ環境）
python tests/test_strategies.py    # 戦略テスト
python tests/test_risk.py          # リスク管理テスト
python tests/test_monitor.py       # 通知テスト
```

## プロジェクト構成

```
oanda-fx-bot/
├── main.py                 # メインエントリーポイント（CLI）
├── config/settings.py      # 環境変数管理
├── core/
│   ├── client.py           # OANDA API接続クライアント
│   ├── pricing.py          # 価格取得
│   └── order.py            # 注文管理（リトライ・CSV履歴）
├── strategy/
│   ├── base.py             # 戦略基底クラス・Signal列挙型
│   ├── sma_cross.py        # SMAクロス戦略
│   ├── momentum.py         # RSI+EMAモメンタム戦略
│   ├── breakout.py         # レンジブレイクアウト戦略
│   └── signals.py          # シグナル統合
├── backtest/
│   ├── data_loader.py      # ヒストリカルデータ取得
│   ├── engine.py           # バックテストエンジン
│   └── chart.py            # チャート可視化
├── risk/
│   ├── guard.py            # リスクガード
│   └── position_sizer.py   # ポジションサイザー
├── monitor/
│   ├── notifier.py         # LINE Notify通知
│   └── dashboard.py        # ターミナルダッシュボード
├── webhook/
│   ├── server.py           # Flask Webhookサーバー
│   └── parser.py           # メッセージパーサー
├── tests/                  # テストスクリプト
├── .env.example            # 環境変数テンプレート
├── requirements.txt        # 依存パッケージ
└── .gitignore
```

## ライセンス

個人利用を想定しています。投資は自己責任で行ってください。
