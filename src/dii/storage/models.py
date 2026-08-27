"""저장 계층이 주고받는 자료형.

수집기(pandas DataFrame)와 저장소(SQL 행) 사이의 공용어다. 이 타입을 두는 이유는
바깥 코드가 pandas 에도, sqlite3 의 튜플에도 의존하지 않게 하기 위해서다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class SecurityKind(StrEnum):
    """종목의 역할. 분석 시 벤치마크·섹터·개별 종목을 구분해야 한다."""

    BENCHMARK = "benchmark"
    SECTOR_ETF = "sector_etf"
    STOCK = "stock"


@dataclass(frozen=True, slots=True)
class DailyBar:
    """하루치 시세 한 건.

    `close` 는 원본 종가, `adj_close` 는 배당·분할이 반영된 수정 종가다.
    둘을 모두 보관하는 이유: 수정값은 나중에 소급해서 바뀌지만 원본은 불변이기 때문이다.
    (`docs/tech-notes/03-data-collection-yfinance.md` 3절 참고)
    """

    symbol: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    adj_close: float
    volume: int

    def validation_error(self) -> str | None:
        """값이 시세로서 말이 되는지 검사한다. 문제가 없으면 None.

        DB 의 CHECK 제약이 마지막 방어선이라면 이쪽이 첫 방어선이다.
        여기서 걸러야 "왜 걸렀는지"를 로그로 남길 수 있다.
        """
        prices = {"open": self.open, "high": self.high, "low": self.low, "close": self.close}
        for name, value in {**prices, "adj_close": self.adj_close}.items():
            if value != value:  # NaN 은 자기 자신과 같지 않다
                return f"{name} 이 NaN"
            if value <= 0:
                return f"{name} 이 0 이하 ({value})"
        if self.volume < 0:
            return f"volume 이 음수 ({self.volume})"
        if self.high < max(self.open, self.close, self.low):
            return f"high({self.high}) 가 다른 가격보다 낮다"
        if self.low > min(self.open, self.close, self.high):
            return f"low({self.low}) 가 다른 가격보다 높다"
        return None
