"""yfinance 기반 일봉 수집.

설계상 지키는 것 세 가지:

1. **부분 실패는 정상이다.** 56종목 중 몇 개가 실패해도 나머지는 저장한다.
   하나 실패했다고 배치를 중단하면 매일 실패하게 된다
2. **멱등하다.** 몇 번을 실행해도 결과가 같다. 저장은 전부 UPSERT 다
3. **최근 구간은 겹쳐서 다시 받는다.** 배당·분할이 발생하면 과거 수정 종가가 소급 변경되므로,
   "마지막 저장일 다음날부터"가 아니라 그보다 며칠 앞에서부터 다시 받아 덮어쓴다
   (`docs/tech-notes/03-data-collection-yfinance.md` 3절)
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from dii.logging_setup import get_logger
from dii.storage.models import DailyBar
from dii.storage.sqlite import SqliteStorage

logger = get_logger(__name__)

#: 증분 수집 시 마지막 저장일보다 이만큼 앞에서부터 다시 받는다.
#: 수정 종가 소급 변경과, 거래일이 아닌 날 실행되는 경우를 함께 흡수한다.
DEFAULT_OVERLAP_DAYS = 7

#: 저장된 데이터가 전혀 없을 때 처음 받아 올 기간.
DEFAULT_INITIAL_PERIOD = "5y"

#: yfinance 가 돌려주는 컬럼 이름 -> DailyBar 필드 이름
_FIELD_MAP = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "volume",
}


@dataclass(frozen=True, slots=True)
class SymbolOutcome:
    """한 종목의 수집 결과."""

    symbol: str
    rows_written: int = 0
    rows_rejected: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(slots=True)
class CollectionResult:
    """수집 배치 전체의 결과."""

    outcomes: list[SymbolOutcome] = field(default_factory=list)

    @property
    def succeeded(self) -> list[SymbolOutcome]:
        return [o for o in self.outcomes if o.ok]

    @property
    def failed(self) -> list[SymbolOutcome]:
        return [o for o in self.outcomes if not o.ok]

    @property
    def rows_written(self) -> int:
        return sum(o.rows_written for o in self.outcomes)

    @property
    def rows_rejected(self) -> int:
        return sum(o.rows_rejected for o in self.outcomes)

    @property
    def is_complete_failure(self) -> bool:
        """한 종목도 못 받았다. 네트워크나 소스 자체의 문제일 가능성이 높다."""
        return bool(self.outcomes) and not self.succeeded


class PriceCollector:
    """유니버스의 일봉을 받아 저장소에 적재한다."""

    def __init__(self, storage: SqliteStorage, *, overlap_days: int = DEFAULT_OVERLAP_DAYS) -> None:
        self._storage = storage
        self._overlap_days = overlap_days

    def collect(self, symbols: list[str]) -> CollectionResult:
        """주어진 심볼들의 일봉을 받아 저장한다.

        심볼을 두 무리로 갈라 각각 요청한다.

        - **신규**: 저장된 데이터가 없는 종목. 전체 이력을 받아야 한다
        - **증분**: 이미 데이터가 있는 종목. 최근 구간만 겹쳐서 다시 받으면 된다

        한 무리로 묶어 처리하면 신규 종목이 하나만 섞여도 전체를 5년치 다시 받게 된다.
        유니버스에 종목을 추가하는 일은 드물지 않으므로 이 낭비를 감수할 이유가 없다.
        """
        result = CollectionResult()
        if not symbols:
            return result

        latest = self._storage.latest_trade_dates()
        fresh = [s for s in symbols if s not in latest]
        incremental = [s for s in symbols if s in latest]

        if fresh:
            logger.info(
                "신규 %d개 -> 전체 이력(%s): %s",
                len(fresh),
                DEFAULT_INITIAL_PERIOD,
                _preview(fresh),
            )
            result.outcomes.extend(self._collect_group(fresh, start=None))

        if incremental:
            # 가장 뒤처진 종목을 기준으로 잡는다. 가장 앞선 종목 기준으로 잡으면
            # 뒤처진 종목에 받지 못한 구간이 그대로 구멍으로 남는다.
            oldest = min(latest[s] for s in incremental)
            start = oldest - timedelta(days=self._overlap_days)
            logger.info(
                "증분 %d개 -> 가장 뒤처진 저장일 %s, 겹침 %d일이므로 %s 부터",
                len(incremental),
                oldest.isoformat(),
                self._overlap_days,
                start.isoformat(),
            )
            result.outcomes.extend(self._collect_group(incremental, start=start))

        # 결과를 요청 순서대로 되돌려, 출력이 무리 나눔에 흔들리지 않게 한다.
        order = {symbol: i for i, symbol in enumerate(symbols)}
        result.outcomes.sort(key=lambda outcome: order[outcome.symbol])
        return result

    # ------------------------------------------------------------------ 내부

    def _collect_group(self, symbols: list[str], *, start: date | None) -> list[SymbolOutcome]:
        """같은 시작일을 공유하는 심볼 무리를 한 번의 요청으로 받아 저장한다."""
        frame = self._download(symbols, start=start)
        if frame is None:
            # 요청 자체가 실패했다. 개별 종목 문제가 아니므로 무리 전체를 실패로 기록한다.
            reason = "다운로드 실패 (요청 자체가 성공하지 못함)"
            return [SymbolOutcome(symbol=s, error=reason) for s in symbols]
        return [self._store_symbol(symbol, frame) for symbol in symbols]

    def _download(self, symbols: list[str], *, start: date | None) -> pd.DataFrame | None:
        """yfinance 로 여러 종목을 한 번에 받는다.

        `auto_adjust=False` 로 두어 원본 OHLC 와 수정 종가를 **둘 다** 받는다.
        `auto_adjust=True` 로 두면 원본 가격이 수정값으로 덮여 사라진다.
        """
        kwargs = {"start": start.isoformat()} if start else {"period": DEFAULT_INITIAL_PERIOD}
        logger.info("yfinance 요청: %d 심볼, %s", len(symbols), kwargs)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                frame = yf.download(
                    symbols,
                    auto_adjust=False,
                    progress=False,
                    group_by="column",
                    threads=True,
                    **kwargs,
                )
        # 외부 경계다. 무엇이 오든 배치 전체를 죽이지 않고 실패로 기록하고 넘어간다.
        except Exception as exc:
            logger.error("yfinance 다운로드 실패: %s", exc)
            return None

        if frame is None or frame.empty:
            logger.error("yfinance 가 빈 응답을 돌려줬다")
            return None
        return frame

    def _store_symbol(self, symbol: str, frame: pd.DataFrame) -> SymbolOutcome:
        try:
            bars, rejected = _extract_bars(symbol, frame)
        except KeyError as exc:
            logger.warning("[%s] 응답에 필요한 컬럼이 없다: %s", symbol, exc)
            return SymbolOutcome(symbol=symbol, error=f"컬럼 없음: {exc}")

        if not bars:
            reason = (
                f"저장 가능한 행이 없다 (거부 {rejected}행)"
                if rejected
                else "응답에 데이터가 없다 — 잘못된 심볼이거나 상장폐지 가능성"
            )
            logger.warning("[%s] %s", symbol, reason)
            return SymbolOutcome(symbol=symbol, rows_rejected=rejected, error=reason)

        written = self._storage.upsert_daily_bars(bars)
        if rejected:
            logger.warning("[%s] %d행 저장, %d행 거부", symbol, written, rejected)
        else:
            logger.debug("[%s] %d행 저장", symbol, written)
        return SymbolOutcome(symbol=symbol, rows_written=written, rows_rejected=rejected)


def _extract_bars(symbol: str, frame: pd.DataFrame) -> tuple[list[DailyBar], int]:
    """응답 DataFrame 에서 한 종목의 일봉을 뽑아 검증한다.

    yfinance 는 심볼이 하나면 단일 인덱스 컬럼을, 여럿이면 (필드, 심볼) MultiIndex 를 돌려준다.
    두 경우를 모두 처리한다.

    Returns:
        `(유효한 일봉, 거부된 행 수)`
    """
    if isinstance(frame.columns, pd.MultiIndex):
        available = set(frame.columns.get_level_values(1))
        if symbol not in available:
            raise KeyError(symbol)
        columns = {src: frame[(src, symbol)] for src in _FIELD_MAP}
    else:
        missing = [c for c in _FIELD_MAP if c not in frame.columns]
        if missing:
            raise KeyError(", ".join(missing))
        columns = {src: frame[src] for src in _FIELD_MAP}

    bars: list[DailyBar] = []
    rejected = 0

    for timestamp in frame.index:
        values = {_FIELD_MAP[src]: series.loc[timestamp] for src, series in columns.items()}

        # 상장 전 구간은 전 필드가 NaN 으로 채워져 온다. 오류가 아니라 정상이므로 조용히 건너뛴다.
        if all(_is_nan(v) for v in values.values()):
            continue

        volume = values.pop("volume")
        bar = DailyBar(
            symbol=symbol,
            trade_date=timestamp.date(),
            volume=0 if _is_nan(volume) else int(volume),
            **{k: float(v) for k, v in values.items()},
        )

        problem = bar.validation_error()
        if problem is not None:
            logger.warning("[%s] %s 행 거부 - %s", symbol, bar.trade_date, problem)
            rejected += 1
            continue
        bars.append(bar)

    return bars, rejected


def _is_nan(value: object) -> bool:
    return isinstance(value, float) and math.isnan(value)


def _preview(symbols: list[str], limit: int = 5) -> str:
    """로그에 심볼 목록을 짧게 보여준다. 56개를 전부 찍으면 로그가 읽히지 않는다."""
    head = ", ".join(symbols[:limit])
    return head if len(symbols) <= limit else f"{head} 외 {len(symbols) - limit}개"
