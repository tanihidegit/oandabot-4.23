"""
OANDA FX自動売買ボット - 設定管理モジュール

dotenvで.envファイルを読み込み、OANDA APIの接続設定を提供する。
OANDA_ENV の値に応じてデモ環境・本番環境のURLを自動切替する。
"""

import os
import logging
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


class Settings:
    """
    アプリケーション設定クラス。

    .envファイルから環境変数を読み込み、OANDA APIへの接続に必要な
    設定値を一元管理する。
    """

    # OANDA API接続URL定義
    _ENVIRONMENTS: dict[str, dict[str, str]] = {
        "practice": {
            "rest": "https://api-fxpractice.oanda.com",
            "stream": "https://stream-fxpractice.oanda.com",
        },
        "live": {
            "rest": "https://api-fxtrade.oanda.com",
            "stream": "https://stream-fxtrade.oanda.com",
        },
    }

    def __init__(self, env_path: str | None = None) -> None:
        """
        設定を初期化する。

        Args:
            env_path: .envファイルのパス。Noneの場合はプロジェクトルートの.envを使用。
        """
        if env_path is None:
            # プロジェクトルート（このファイルの2階層上）の.envを探す
            project_root = Path(__file__).resolve().parent.parent
            env_path = str(project_root / ".env")

        # .envファイルの読み込み
        if Path(env_path).exists():
            load_dotenv(env_path)
            logger.info(".envファイルを読み込みました: %s", env_path)
        else:
            logger.warning(".envファイルが見つかりません: %s", env_path)

        # 環境変数から設定値を取得
        self.environment: str = os.getenv("OANDA_ENV", "practice")
        self.access_token: str = os.getenv("OANDA_ACCESS_TOKEN", "")
        self.account_id: str = os.getenv("OANDA_ACCOUNT_ID", "")

        # 設定値のバリデーション
        self._validate()

        logger.info(
            "設定を読み込みました（環境: %s, 口座ID: %s）",
            self.environment,
            self._mask_account_id(),
        )

    def _validate(self) -> None:
        """
        設定値のバリデーションを実行する。

        Raises:
            ValueError: 必須の設定値が未設定、または無効な値の場合。
        """
        if self.environment not in self._ENVIRONMENTS:
            raise ValueError(
                f"無効なOANDA_ENV: '{self.environment}'。"
                f"'practice' または 'live' を指定してください。"
            )

        if not self.access_token or self.access_token == "your_token_here":
            raise ValueError(
                "OANDA_ACCESS_TOKENが未設定です。"
                ".envファイルに有効なAPIトークンを設定してください。"
            )

        if not self.account_id or self.account_id == "xxx-xxx-xxxxxxx-xxx":
            raise ValueError(
                "OANDA_ACCOUNT_IDが未設定です。"
                ".envファイルに有効な口座IDを設定してください。"
            )

    def _mask_account_id(self) -> str:
        """
        ログ出力用に口座IDをマスキングする。

        Returns:
            マスキングされた口座ID文字列。
        """
        if len(self.account_id) > 8:
            return self.account_id[:4] + "****" + self.account_id[-4:]
        return "****"

    @property
    def rest_url(self) -> str:
        """
        REST APIのベースURLを取得する。

        Returns:
            環境に応じたREST APIのURL。
        """
        return self._ENVIRONMENTS[self.environment]["rest"]

    @property
    def stream_url(self) -> str:
        """
        ストリーミングAPIのベースURLを取得する。

        Returns:
            環境に応じたストリーミングAPIのURL。
        """
        return self._ENVIRONMENTS[self.environment]["stream"]

    @property
    def is_live(self) -> bool:
        """
        本番環境かどうかを判定する。

        Returns:
            本番環境の場合True。
        """
        return self.environment == "live"

    def __repr__(self) -> str:
        """設定の文字列表現を返す。"""
        return (
            f"Settings(environment='{self.environment}', "
            f"account_id='{self._mask_account_id()}', "
            f"rest_url='{self.rest_url}')"
        )
