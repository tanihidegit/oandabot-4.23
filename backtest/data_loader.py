"""
OANDA FX自動売買ボット - データローダーモジュール

OANDA APIからローソク足データを取得し、CSV形式での保存・読込・
キャッシュ管理を行う。APIの5000本制限を自動ページネーションで回避し、
長期間のデータも一括取得可能。
"""

import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import oandapyV20
import oandapyV20.endpoints.instruments as instruments
import pandas as pd
from dateutil import parser as dateutil_parser

from config.settings import Settings
from core.client import OandaClient

logger = logging.getLogger(__name__)

# OANDA APIの1リクエストあたりの最大ローソク足数
MAX_CANDLES_PER_REQUEST = 5000

# 時間足ごとの1本あたりの秒数（ページネーション計算用）
GRANULARITY_SECONDS: dict[str, int] = {
    "S5": 5,
    "S10": 10,
    "S15": 15,
    "S30": 30,
    "M1": 60,
    "M2": 120,
    "M4": 240,
    "M5": 300,
    "M10": 600,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H2": 7200,
    "H3": 10800,
    "H4": 14400,
    "H6": 21600,
    "H8": 28800,
    "H12": 43200,
    "D": 86400,
    "W": 604800,
    "M": 2592000,  # 約30日として計算
}


class OandaDataLoader:
    """
    OANDA APIからの過去データ取得・キャッシュ管理クラス。

    APIの5000本制限を自動的にハンドリングし、長期間のデータを
    複数リクエストに分割して取得する。取得したデータはCSV形式で
    保存・読込が可能。

    Attributes:
        client: OandaClientインスタンス。
        settings: アプリケーション設定。
    """

    def __init__(self, client: OandaClient | None = None) -> None:
        """
        OandaDataLoaderを初期化する。

        Args:
            client: OandaClientインスタンス。Noneの場合は新規作成する。
        """
        self.client = client or OandaClient()
        self.settings = self.client.settings
        logger.info("OandaDataLoaderを初期化しました")

    def fetch_candles(
        self,
        instrument: str,
        granularity: str,
        from_date: str,
        to_date: str,
        price: str = "M",
    ) -> pd.DataFrame:
        """
        指定期間のローソク足データを取得する（自動ページネーション対応）。

        OANDA APIの1リクエストあたり5000本の制限を自動的にハンドリングし、
        必要に応じて複数回のAPIリクエストを発行してデータを結合する。

        Args:
            instrument: 通貨ペア（例: "USD_JPY", "EUR_USD"）。
            granularity: 時間足（例: "M1", "M5", "M15", "H1", "H4", "D"）。
            from_date: 取得開始日時（RFC3339形式、例: "2024-01-01T00:00:00Z"）。
            to_date: 取得終了日時（RFC3339形式、例: "2024-06-30T23:59:59Z"）。
            price: 価格種別。"M"=中間値, "B"=Bid, "A"=Ask。

        Returns:
            ローソク足データのDataFrame。カラム:
            - time（インデックス）: 日時（UTC）
            - open: 始値
            - high: 高値
            - low: 安値
            - close: 終値
            - volume: 出来高

        Raises:
            ValueError: 無効な時間足が指定された場合。
            oandapyV20.exceptions.V20Error: API通信エラー時。
        """
        if granularity not in GRANULARITY_SECONDS:
            raise ValueError(
                f"無効な時間足です: '{granularity}'。"
                f"有効な値: {', '.join(GRANULARITY_SECONDS.keys())}"
            )

        # 日時文字列をdatetimeに変換
        start_dt = dateutil_parser.parse(from_date).replace(tzinfo=timezone.utc)
        end_dt = dateutil_parser.parse(to_date).replace(tzinfo=timezone.utc)

        if start_dt >= end_dt:
            raise ValueError(
                f"開始日時が終了日時以降です: {from_date} >= {to_date}"
            )

        logger.info(
            "データ取得を開始します: %s %s (%s 〜 %s)",
            instrument, granularity, from_date, to_date,
        )

        # ページネーション用の時間間隔を計算
        seconds_per_candle = GRANULARITY_SECONDS[granularity]
        max_span_seconds = MAX_CANDLES_PER_REQUEST * seconds_per_candle
        max_span = timedelta(seconds=max_span_seconds)

        all_frames: list[pd.DataFrame] = []
        current_start = start_dt
        request_count = 0

        while current_start < end_dt:
            # このリクエストの終了日時を計算（最大5000本分）
            current_end = min(current_start + max_span, end_dt)

            request_count += 1
            logger.info(
                "  リクエスト #%d: %s 〜 %s",
                request_count,
                current_start.isoformat(),
                current_end.isoformat(),
            )

            # APIリクエストを実行
            df_chunk = self._fetch_single_batch(
                instrument=instrument,
                granularity=granularity,
                from_time=current_start.isoformat(),
                to_time=current_end.isoformat(),
                price=price,
            )

            if not df_chunk.empty:
                all_frames.append(df_chunk)
                logger.info(
                    "  → %d本取得しました",
                    len(df_chunk),
                )
            else:
                logger.warning(
                    "  → データなし（市場クローズ期間の可能性）"
                )

            # 次のリクエストの開始日時を更新
            current_start = current_end

        # 全チャンクを結合
        if not all_frames:
            logger.warning(
                "指定期間にデータが存在しません: %s %s (%s 〜 %s)",
                instrument, granularity, from_date, to_date,
            )
            return pd.DataFrame()

        df = pd.concat(all_frames)

        # 重複行を除去（ページ境界で重複する可能性があるため）
        df = df[~df.index.duplicated(keep="first")]

        # 時系列順にソート
        df.sort_index(inplace=True)

        # completeカラムを除去（バックテスト用には不要）
        if "complete" in df.columns:
            df.drop(columns=["complete"], inplace=True)

        logger.info(
            "データ取得完了: %s %s 合計%d本（%dリクエスト）",
            instrument, granularity, len(df), request_count,
        )

        return df

    def _fetch_single_batch(
        self,
        instrument: str,
        granularity: str,
        from_time: str,
        to_time: str,
        price: str = "M",
    ) -> pd.DataFrame:
        """
        1回のAPIリクエストでローソク足データを取得する（内部メソッド）。

        Args:
            instrument: 通貨ペア。
            granularity: 時間足。
            from_time: 開始日時（RFC3339形式）。
            to_time: 終了日時（RFC3339形式）。
            price: 価格種別。

        Returns:
            ローソク足データのDataFrame。
        """
        params: dict[str, Any] = {
            "granularity": granularity,
            "price": price,
            "from": from_time,
            "to": to_time,
        }

        endpoint = instruments.InstrumentsCandles(
            instrument=instrument,
            params=params,
        )

        try:
            response = self.client.api.request(endpoint)
            candles = response.get("candles", [])

            if not candles:
                return pd.DataFrame()

            # 価格キーの決定
            price_key = "mid"
            if price == "B":
                price_key = "bid"
            elif price == "A":
                price_key = "ask"

            # レコードに変換
            records = []
            for candle in candles:
                price_data = candle.get(price_key, {})
                records.append({
                    "time": dateutil_parser.parse(candle["time"]),
                    "open": float(price_data.get("o", 0)),
                    "high": float(price_data.get("h", 0)),
                    "low": float(price_data.get("l", 0)),
                    "close": float(price_data.get("c", 0)),
                    "volume": int(candle.get("volume", 0)),
                    "complete": candle.get("complete", False),
                })

            df = pd.DataFrame(records)
            df["time"] = pd.to_datetime(df["time"], utc=True)
            df.set_index("time", inplace=True)

            return df

        except oandapyV20.exceptions.V20Error as e:
            logger.error(
                "ローソク足データの取得に失敗しました（%s %s）: %s",
                instrument, granularity, e,
            )
            raise

    def save_to_csv(self, df: pd.DataFrame, filepath: str | Path) -> None:
        """
        DataFrameをCSVファイルに保存する。

        Args:
            df: 保存するDataFrame。
            filepath: 保存先のファイルパス。

        Raises:
            ValueError: DataFrameが空の場合。
        """
        if df.empty:
            raise ValueError("空のDataFrameは保存できません")

        filepath = Path(filepath)

        # 親ディレクトリが存在しない場合は作成
        filepath.parent.mkdir(parents=True, exist_ok=True)

        df.to_csv(filepath, encoding="utf-8")

        logger.info(
            "CSVファイルに保存しました: %s（%d行）",
            filepath, len(df),
        )

    def load_from_csv(self, filepath: str | Path) -> pd.DataFrame:
        """
        CSVファイルからDataFrameを読み込む。

        Args:
            filepath: 読み込むCSVファイルのパス。

        Returns:
            読み込んだDataFrame。timeカラムをUTC datetimeインデックスに設定。

        Raises:
            FileNotFoundError: ファイルが存在しない場合。
        """
        filepath = Path(filepath)

        if not filepath.exists():
            raise FileNotFoundError(
                f"CSVファイルが見つかりません: {filepath}"
            )

        df = pd.read_csv(filepath, index_col="time", parse_dates=True)

        # インデックスをUTCに変換（タイムゾーン情報がない場合）
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")

        logger.info(
            "CSVファイルを読み込みました: %s（%d行）",
            filepath, len(df),
        )

        return df

    def fetch_and_cache(
        self,
        instrument: str,
        granularity: str,
        from_date: str,
        to_date: str,
        cache_dir: str | Path = "data/cache",
        price: str = "M",
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """
        キャッシュを活用してローソク足データを取得する。

        キャッシュファイルが存在すればCSVから読み込み、存在しなければ
        APIから取得してCSVに保存する。

        Args:
            instrument: 通貨ペア（例: "USD_JPY"）。
            granularity: 時間足（例: "H1"）。
            from_date: 取得開始日時（RFC3339形式）。
            to_date: 取得終了日時（RFC3339形式）。
            cache_dir: キャッシュファイルの保存先ディレクトリ。
            price: 価格種別。
            force_refresh: Trueの場合、キャッシュを無視してAPI取得する。

        Returns:
            ローソク足データのDataFrame。
        """
        cache_dir = Path(cache_dir)

        # キャッシュファイル名を生成
        # 日時からファイル名に使えない文字を除去
        from_safe = from_date[:10].replace("-", "")
        to_safe = to_date[:10].replace("-", "")
        cache_filename = (
            f"{instrument}_{granularity}_{from_safe}_{to_safe}.csv"
        )
        cache_path = cache_dir / cache_filename

        # キャッシュが存在し、強制更新でなければCSVから読み込み
        if cache_path.exists() and not force_refresh:
            logger.info("キャッシュから読み込みます: %s", cache_path)
            return self.load_from_csv(cache_path)

        # APIからデータを取得
        logger.info("APIからデータを取得します（キャッシュ: %s）", cache_path)
        df = self.fetch_candles(
            instrument=instrument,
            granularity=granularity,
            from_date=from_date,
            to_date=to_date,
            price=price,
        )

        # 取得成功時にキャッシュとして保存
        if not df.empty:
            self.save_to_csv(df, cache_path)
            logger.info("キャッシュに保存しました: %s", cache_path)
        else:
            logger.warning("データが空のためキャッシュは保存しません")

        return df
