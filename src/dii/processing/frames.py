"""저장 형식(long) → 분석 형식(wide) 변환.

`daily_price` 는 한 행이 한 종목의 하루다. 분석은 "행=날짜, 열=종목" 행렬에서 하는 편이
훨씬 짧고 빠르다. 그 변환이 여기서 일어난다.
(`docs/tech-notes/04-storage-sqlite.md` 3절 — 저장 형식과 분석 형식은 다르다)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import pandas as pd

from dii.logging_setup import get_logger
from dii.storage.sqlite import SqliteStorage

logger = get_logger(__name__)


class InsufficientDataError(Exception):
    """계산에 필요한 데이터가 저장소에 없을 때."""


@dataclass(frozen=True, slots=True)
class MarketFrames:
    """분석에 쓰는 가격·거래량 행렬.

    두 행렬 모두 인덱스가 거래일(오름차순), 컬럼이 심볼이다.
    **마지막 행이 기준일(as_of 이하의 마지막 거래일)** 이라는 것이 이 자료구조의 계약이다.
    """

    prices: pd.DataFrame
    """수정 종가. 수익률 계산은 반드시 이걸 쓴다 (원본 종가로 하면 분할일에 가짜 폭락이 잡힌다)."""

    volumes: pd.DataFrame

    as_of: date
    """실제 기준일. 요청한 날짜가 휴장일이면 그 이전의 마지막 거래일로 당겨져 있다."""

    def __post_init__(self) -> None:
        # 인덱스 타입을 계약으로 고정한다. object dtype 으로 두면 파이썬 date 객체가 들어가
        # 느리고, 테스트에서 만든 행렬과 실제 행렬의 타입이 달라져 차이가 숨는다.
        if not isinstance(self.prices.index, pd.DatetimeIndex):
            got = type(self.prices.index).__name__
            raise TypeError(f"prices 의 인덱스는 DatetimeIndex 여야 한다 (받은 것: {got})")

    @property
    def symbols(self) -> list[str]:
        return list(self.prices.columns)

    def history_length(self, symbol: str) -> int:
        """해당 종목에 실제 값이 있는 거래일 수. 상장 초기 종목을 걸러내는 데 쓴다."""
        return int(self.prices[symbol].notna().sum())


def load_frames(
    storage: SqliteStorage, symbols: Sequence[str], as_of: date | None = None
) -> MarketFrames:
    """`as_of` 시점까지의 시세를 읽어 분석용 행렬로 만든다.

    Args:
        storage: 조회할 저장소.
        symbols: 대상 심볼.
        as_of: 기준 날짜. None 이면 저장된 마지막 거래일을 쓴다.
               거래일이 아닌 날짜를 넘기면 그 이전의 마지막 거래일로 당겨진다.

    Raises:
        InsufficientDataError: 기준일 이하에 데이터가 전혀 없을 때.

    이 함수가 **룩어헤드를 막는 유일한 관문**이다. 여기서 미래를 잘라 내므로
    반환된 행렬을 받는 쪽은 미래를 볼 방법이 없다.
    """
    if not symbols:
        raise InsufficientDataError("대상 심볼이 없다")

    resolved = _resolve_as_of(storage, as_of)
    bars = storage.get_bars_asof(symbols, resolved)
    if not bars:
        raise InsufficientDataError(f"{resolved.isoformat()} 이하에 저장된 시세가 없다")

    frame = pd.DataFrame(
        {
            "symbol": [b.symbol for b in bars],
            # object dtype 을 피하려고 Timestamp 로 올린다. MarketFrames 의 계약이다.
            "trade_date": pd.to_datetime([b.trade_date for b in bars]),
            "adj_close": [b.adj_close for b in bars],
            "volume": [b.volume for b in bars],
        }
    )

    prices = frame.pivot(index="trade_date", columns="symbol", values="adj_close").sort_index()
    volumes = frame.pivot(index="trade_date", columns="symbol", values="volume").sort_index()

    # 데이터가 하나도 없는 심볼은 pivot 결과에 컬럼조차 생기지 않는다.
    # 이후 코드가 "요청한 심볼은 다 있다"고 가정하지 않도록 여기서 알린다.
    missing = [s for s in symbols if s not in prices.columns]
    if missing:
        logger.warning(
            "시세가 없어 분석에서 빠지는 심볼 %d개: %s", len(missing), ", ".join(missing)
        )

    actual_as_of = prices.index[-1].date()
    logger.info(
        "분석 행렬 구성: %d 심볼 x %d 거래일, 기준일 %s",
        len(prices.columns),
        len(prices),
        actual_as_of.isoformat(),
    )
    return MarketFrames(prices=prices, volumes=volumes, as_of=actual_as_of)


def _resolve_as_of(storage: SqliteStorage, as_of: date | None) -> date:
    """기준 날짜를 실제 거래일로 맞춘다.

    주말이나 휴장일을 넘겨도 동작해야 한다. 스케줄러가 토요일에 돌 수도 있고,
    사용자가 임의의 날짜를 물어볼 수도 있기 때문이다.
    """
    if as_of is None:
        resolved = storage.last_trade_date_on_or_before(date.max)
        if resolved is None:
            raise InsufficientDataError("저장소에 시세가 없다. `dii collect` 를 먼저 실행한다")
        return resolved

    resolved = storage.last_trade_date_on_or_before(as_of)
    if resolved is None:
        raise InsufficientDataError(f"{as_of.isoformat()} 이전에 저장된 시세가 없다")
    if resolved != as_of:
        logger.info(
            "%s 는 거래일이 아니다 -> 직전 거래일 %s 를 기준으로 삼는다",
            as_of.isoformat(),
            resolved.isoformat(),
        )
    return resolved
