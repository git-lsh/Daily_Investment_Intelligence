from __future__ import annotations

import pytest

from dii import __version__
from dii.cli import main


def test_config_command_prints_resolved_settings(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["config"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "app_env" in out
    assert "db_path" in out


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])

    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_is_an_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([])

    assert excinfo.value.code != 0
