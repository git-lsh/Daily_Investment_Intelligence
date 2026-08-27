from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from dii.config import AppEnv, Settings


def _settings(**overrides: object) -> Settings:
    """`.env` 를 읽지 않는 설정 객체. 테스트는 명시한 값만 보고 판단해야 한다."""
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg, arg-type]


def test_defaults() -> None:
    settings = _settings()
    assert settings.app_env is AppEnv.LOCAL
    assert settings.log_level == "INFO"


def test_env_var_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """`DII_` 접두사 환경변수가 기본값을 덮어쓰는지 — 설정 주입의 핵심 동작."""
    monkeypatch.setenv("DII_APP_ENV", "prod")
    monkeypatch.setenv("DII_LOG_LEVEL", "warning")

    settings = _settings()

    assert settings.app_env is AppEnv.PROD
    assert settings.log_level == "WARNING", "소문자로 넣어도 정규화되어야 한다"


def test_unknown_log_level_fails_fast() -> None:
    """잘못된 설정은 실행 중이 아니라 기동 시점에 터져야 한다."""
    with pytest.raises(ValidationError, match="알 수 없는 로그 레벨"):
        _settings(log_level="LOUD")


def test_db_path_is_under_data_dir(tmp_path: Path) -> None:
    settings = _settings(data_dir=tmp_path)
    assert settings.db_path.parent == tmp_path


def test_ensure_directories_is_idempotent(tmp_path: Path) -> None:
    settings = _settings(data_dir=tmp_path / "nested" / "data")

    settings.ensure_directories()
    settings.ensure_directories()  # 두 번 불러도 실패하지 않는다

    assert settings.data_dir.is_dir()
