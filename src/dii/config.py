"""애플리케이션 설정.

모든 설정은 **환경변수 또는 `.env` 파일**에서 주입받는다. 값을 코드에 하드코딩하지 않는다.
(12-factor app 의 config 원칙 — 환경마다 달라지는 것은 코드가 아니라 환경에 둔다)

환경변수는 `DII_` 접두사를 붙인다. 예) `DII_LOG_LEVEL=DEBUG`
"""

from __future__ import annotations

import logging
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# src/dii/config.py → src/dii → src → 프로젝트 루트
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]


class AppEnv(StrEnum):
    """실행 환경. 로컬 개발과 배포 환경의 동작 차이를 이 값으로 가른다."""

    LOCAL = "local"
    PROD = "prod"


class Settings(BaseSettings):
    """환경에서 주입받는 애플리케이션 설정."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="DII_",
        extra="ignore",
    )

    app_env: AppEnv = AppEnv.LOCAL
    """실행 환경 (local / prod)."""

    log_level: str = "INFO"
    """루트 로거 레벨. DEBUG / INFO / WARNING / ERROR / CRITICAL."""

    data_dir: Path = PROJECT_ROOT / "data"
    """수집 데이터와 로컬 DB 가 놓이는 디렉토리. 저장소에는 커밋하지 않는다."""

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> object:
        """`debug` 처럼 소문자로 넣어도 받아주고, 알 수 없는 값은 즉시 실패시킨다."""
        if not isinstance(value, str):
            return value
        normalized = value.strip().upper()
        if normalized not in logging.getLevelNamesMapping():
            valid = ", ".join(sorted(logging.getLevelNamesMapping()))
            raise ValueError(f"알 수 없는 로그 레벨 {value!r}. 가능한 값: {valid}")
        return normalized

    @property
    def db_path(self) -> Path:
        """M1 에서 사용할 SQLite 파일 경로. (M3 에서 PostgreSQL 로 이전 예정)"""
        return self.data_dir / "dii.sqlite3"

    def ensure_directories(self) -> None:
        """설정이 가리키는 디렉토리를 만들어 둔다. 이미 있으면 아무 일도 하지 않는다."""
        self.data_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """설정 싱글턴.

    프로세스마다 한 번만 읽는다. 테스트에서 환경변수를 바꿔 다시 읽어야 하면
    `get_settings.cache_clear()` 를 호출한다.
    """
    return Settings()
