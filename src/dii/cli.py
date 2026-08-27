"""명령줄 진입점.

파이프라인의 각 단계는 여기에 하위 명령으로 붙는다.
지금은 M0 검증용 `config` 하나뿐이고, M1 에서 `collect` 가 추가된다.

로깅 설정은 **이 모듈에서만** 수행한다. (`logging_setup` 모듈 설명 참고)
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from dii import __version__
from dii.config import PROJECT_ROOT, Settings, get_settings
from dii.logging_setup import get_logger, setup_logging

logger = get_logger(__name__)


def _configure_console_encoding() -> None:
    """콘솔 출력 스트림을 UTF-8 로 고정한다.

    Windows 의 기본 콘솔 인코딩은 시스템 로캘(한국어 환경이면 cp949)이다. 이 프로젝트의
    로그와 리포트에는 한글이 들어가므로, 그대로 두면 터미널·파일 리다이렉트·CI 로그에서
    글자가 깨진다. 진입점에서 한 번 UTF-8 로 맞춰 두어 실행 환경에 관계없이 같게 보이게 한다.

    `errors="backslashreplace"` 로 두어, 인코딩 불가 문자가 있어도 예외로 파이프라인을
    멈추지 않고 이스케이프 표기로 흘려보낸다. 로깅이 작업을 죽이면 안 된다.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")


def _cmd_config(settings: Settings) -> int:
    """해석된 설정값을 출력한다. 환경변수 주입이 의도대로 됐는지 확인하는 용도."""
    settings.ensure_directories()

    rows: list[tuple[str, str]] = [
        ("version", __version__),
        ("project_root", str(PROJECT_ROOT)),
        ("app_env", settings.app_env.value),
        ("log_level", settings.log_level),
        ("data_dir", str(settings.data_dir)),
        ("db_path", str(settings.db_path)),
    ]
    width = max(len(key) for key, _ in rows)
    for key, value in rows:
        print(f"{key:<{width}}  {value}")

    logger.debug("설정 출력 완료")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dii",
        description="Daily Investment Intelligence — 투자 리서치 파이프라인",
    )
    parser.add_argument("--version", action="version", version=f"dii {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("config", help="해석된 설정값을 출력한다")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 진입점. 종료 코드를 반환한다."""
    _configure_console_encoding()
    args = build_parser().parse_args(argv)

    settings = get_settings()
    setup_logging(settings)

    if args.command == "config":
        return _cmd_config(settings)

    # argparse 가 required=True 로 막아 주므로 여기 도달하지 않는다.
    raise AssertionError(f"처리되지 않은 명령: {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
