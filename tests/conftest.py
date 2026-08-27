from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from dii.config import get_settings

# 실패 시 pytest 가 찍는 로그·assert 메시지에 한글이 섞인다. Windows 콘솔 기본 인코딩(cp949)
# 으로는 깨져서 읽을 수 없으므로, 수집 시점에 한 번 UTF-8 로 맞춰 둔다.
# (애플리케이션 쪽은 dii.cli 진입점에서 같은 일을 한다)
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="backslashreplace")


@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """테스트가 개발자의 실제 환경변수나 `.env` 에 영향받지 않게 한다.

    설정은 프로세스 단위로 캐시되므로 테스트마다 캐시를 비운다.
    데이터 디렉토리는 임시 경로로 돌려, 테스트가 실제 `data/` 를 건드리지 않게 한다.
    """
    for key in [k for k in os.environ if k.startswith("DII_")]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DII_DATA_DIR", str(tmp_path / "data"))

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
