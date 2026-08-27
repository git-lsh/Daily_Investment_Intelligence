"""로깅 설정.

모듈 이름을 `logging.py` 로 두지 않는 이유: 표준 라이브러리 `logging` 과 헷갈리기 쉽다.

이 프로젝트의 로깅 원칙:

- 로그는 **파이프라인이 어디서 무엇을 하다 멈췄는지** 알기 위한 것이다. 진행 상황을
  사람에게 보여주는 용도(print)와 구분한다
- 라이브러리 코드는 절대 핸들러를 붙이지 않는다. 핸들러 설정은 진입점(CLI)에서 한 번만 한다
- M5 에서 구조화(JSON) 로깅으로 넘어간다. 그때 갈아타기 쉽도록
  포매터 구성을 이 모듈 한 곳에 모아 둔다.
  형식 선택 설정은 실제로 구현하는 시점(M5)에 추가한다 — 지금 넣으면 동작하지 않는 설정이 된다
"""

from __future__ import annotations

import logging
import sys

from dii.config import Settings

_TEXT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 외부 라이브러리가 쏟아내는 로그를 낮춰 둔다. 우리 로그가 묻히지 않게 하기 위함.
_NOISY_LOGGERS = ("urllib3", "httpx", "httpcore", "asyncio")

# yfinance 는 심볼 조회 실패를 여러 줄의 ERROR 로 직접 출력한다. 우리 수집기가 그 상황을
# 이미 판정해 실패로 기록하므로 그대로 두면 같은 사실이 두 번, 그것도 더 지저분하게 찍힌다.
# 원인 추적이 필요할 때는 DEBUG 로 실행하면 다시 보인다.
_DOUBLE_REPORTING_LOGGERS = ("yfinance",)

_configured = False


def setup_logging(settings: Settings, *, force: bool = False) -> None:
    """루트 로거를 설정한다.

    진입점에서 **한 번만** 호출한다. 두 번 호출해도 핸들러가 중복되지 않도록 방어한다.

    Args:
        settings: 로그 레벨을 읽어올 설정 객체.
        force: 이미 설정되어 있어도 다시 설정한다. 테스트에서만 사용한다.
    """
    global _configured
    if _configured and not force:
        return

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter(fmt=_TEXT_FORMAT, datefmt=_DATE_FORMAT))

    root.addHandler(handler)
    root.setLevel(settings.log_level)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    debugging = root.level <= logging.DEBUG
    for name in _DOUBLE_REPORTING_LOGGERS:
        logging.getLogger(name).setLevel(logging.DEBUG if debugging else logging.CRITICAL)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """모듈용 로거를 반환한다. 각 모듈에서 `get_logger(__name__)` 로 쓴다."""
    return logging.getLogger(name)
