"""저장 계층이 주고받는 자료형.

수집기(pandas DataFrame)와 저장소(SQL 행) 사이의 공용어다. 이 타입을 두는 이유는
바깥 코드가 pandas 에도, sqlite3 의 튜플에도 의존하지 않게 하기 위해서다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
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


class DocumentSource(StrEnum):
    """문서가 어디서 왔는지."""

    SEC = "sec"
    NEWS = "news"


@dataclass(frozen=True, slots=True)
class Document:
    """뉴스 기사 또는 SEC 공시 한 건.

    원문 전체는 저장하지 않는다. 제목·요약·링크만 보관하고, 필요하면 URL 로 원문을 본다.
    저작권 문제를 피하고, 검색 목적에는 요약으로 충분하기 때문이다.
    """

    source: DocumentSource
    external_id: str
    """소스가 주는 고유 식별자. SEC 는 accession number, 뉴스는 기사 id.
    `(source, external_id)` 가 유일 제약이라 재수집해도 중복되지 않는다."""

    title: str
    url: str
    published_at: datetime
    """**UTC** 기준. 날짜만 아는 공시는 그날 자정(UTC)으로 둔다."""

    symbols: tuple[str, ...]
    """이 문서가 엮이는 종목들. 기사 하나가 여러 종목을 언급할 수 있다."""

    doc_type: str | None = None
    """SEC 는 서식 종류(8-K, 10-Q...), 뉴스는 발행처."""

    summary: str | None = None

    def validation_error(self) -> str | None:
        """저장할 만한 문서인지. 문제가 없으면 None."""
        if not self.external_id.strip():
            return "external_id 가 비었다"
        if not self.title.strip():
            return "제목이 비었다"
        if not self.url.strip():
            return "url 이 비었다"
        if not self.symbols:
            return "엮인 종목이 없다"
        if self.published_at.tzinfo is None:
            return "published_at 에 타임존이 없다 (UTC 로 주어야 한다)"
        return None
