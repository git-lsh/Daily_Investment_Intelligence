"""수집기 테스트.

네트워크를 타지 않는다. yfinance 응답 형태의 DataFrame 을 직접 만들어 주입한다.
외부 서비스에 의존하는 테스트는 상대가 느리거나 죽으면 같이 실패해, 무엇이 깨졌는지 알려주지 못한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from dii.collect.prices import PriceCollector, _extract_bars
from dii.storage import SecurityKind, SqliteStorage, connect

_FIELDS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]


@pytest.fixture
def storage(tmp_path: Path) -> Iterator[SqliteStorage]:
    with connect(tmp_path / "collect.sqlite3") as conn:
        store = SqliteStorage(conn)
        store.upsert_securities(
            [
                ("AAPL", SecurityKind.STOCK, None, "Apple"),
                ("MSFT", SecurityKind.STOCK, None, "Microsoft"),
            ]
        )
        yield store


def _frame(symbols: list[str], days: list[str], base: float = 100.0) -> pd.DataFrame:
    """yfinance 의 다중 종목 응답과 같은 (필드, 심볼) MultiIndex DataFrame 을 만든다."""
    index = pd.DatetimeIndex([pd.Timestamp(d) for d in days])
    data: dict[tuple[str, str], list[float]] = {}
    for offset, symbol in enumerate(symbols):
        price = base + offset
        data[("Open", symbol)] = [price] * len(days)
        data[("High", symbol)] = [price + 1] * len(days)
        data[("Low", symbol)] = [price - 1] * len(days)
        data[("Close", symbol)] = [price] * len(days)
        data[("Adj Close", symbol)] = [price] * len(days)
        data[("Volume", symbol)] = [1000.0] * len(days)
    frame = pd.DataFrame(data, index=index)
    frame.columns = pd.MultiIndex.from_tuples(frame.columns)
    return frame


# ------------------------------------------------------------------ 응답 파싱


def test_extract_bars_from_multi_symbol_frame() -> None:
    bars, rejected = _extract_bars("AAPL", _frame(["AAPL", "MSFT"], ["2026-01-05", "2026-01-06"]))

    assert rejected == 0
    assert [b.trade_date for b in bars] == [date(2026, 1, 5), date(2026, 1, 6)]
    assert bars[0].symbol == "AAPL"
    assert bars[0].volume == 1000


def test_extract_bars_from_single_symbol_frame() -> None:
    """심볼이 하나면 yfinance 는 MultiIndex 가 아닌 평범한 컬럼을 돌려준다."""
    index = pd.DatetimeIndex([pd.Timestamp("2026-01-05")])
    frame = pd.DataFrame({f: [100.0] for f in _FIELDS}, index=index)

    bars, rejected = _extract_bars("AAPL", frame)

    assert rejected == 0
    assert len(bars) == 1


def test_extract_bars_skips_pre_listing_nan_rows() -> None:
    """상장 전 구간은 전 필드가 NaN 이다. 오류가 아니므로 거부 집계에 넣지 않는다."""
    frame = _frame(["AAPL"], ["2026-01-05", "2026-01-06"])
    frame.iloc[0, :] = float("nan")

    bars, rejected = _extract_bars("AAPL", frame)

    assert len(bars) == 1
    assert rejected == 0, "상장 전 빈 구간은 거부가 아니라 정상"


def test_extract_bars_rejects_impossible_prices() -> None:
    """high 가 low 보다 낮은 행은 저장하지 않고 거부로 센다."""
    frame = _frame(["AAPL"], ["2026-01-05", "2026-01-06"])
    frame.loc[frame.index[0], ("High", "AAPL")] = 1.0

    bars, rejected = _extract_bars("AAPL", frame)

    assert len(bars) == 1
    assert rejected == 1


def test_extract_bars_raises_for_unknown_symbol() -> None:
    with pytest.raises(KeyError):
        _extract_bars("TSLA", _frame(["AAPL"], ["2026-01-05"]))


# ------------------------------------------------------------------ 수집 흐름


def test_collect_is_idempotent(storage: SqliteStorage, monkeypatch: pytest.MonkeyPatch) -> None:
    """M1 완료 조건 — 두 번 실행해도 중복 행이 생기지 않는다."""
    frame = _frame(["AAPL", "MSFT"], ["2026-01-05", "2026-01-06"])
    collector = PriceCollector(storage)
    monkeypatch.setattr(collector, "_download", lambda symbols, *, start: frame)

    first = collector.collect(["AAPL", "MSFT"])
    second = collector.collect(["AAPL", "MSFT"])

    assert first.rows_written == 4
    assert second.rows_written == 4, "UPSERT 는 매번 같은 행 수를 쓴다"
    assert storage.count_bars() == 4, "그래도 저장된 행 수는 늘지 않는다"


def test_collect_splits_fresh_and_incremental(
    storage: SqliteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """이미 데이터가 있는 종목과 없는 종목을 따로 요청해야 한다.

    묶어서 요청하면 신규 종목 하나 때문에 전체를 전체 이력으로 다시 받는다.
    """
    collector = PriceCollector(storage)
    calls: list[tuple[tuple[str, ...], date | None]] = []

    def fake_download(symbols: list[str], *, start: date | None) -> pd.DataFrame:
        calls.append((tuple(symbols), start))
        return _frame(symbols, ["2026-01-05", "2026-01-06"])

    monkeypatch.setattr(collector, "_download", fake_download)

    collector.collect(["AAPL"])  # AAPL 만 먼저 채워 둔다
    calls.clear()
    collector.collect(["AAPL", "MSFT"])

    assert len(calls) == 2, "신규와 증분이 각각 한 번씩 요청되어야 한다"
    grouped = {symbols: start for symbols, start in calls}
    assert grouped[("MSFT",)] is None, "신규 종목은 전체 이력을 받는다"
    assert grouped[("AAPL",)] is not None, "기존 종목은 시작일이 지정된다"


def test_collect_preserves_requested_order(
    storage: SqliteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """무리를 나눠 요청해도 결과 순서는 요청 순서를 따른다."""
    collector = PriceCollector(storage)
    monkeypatch.setattr(
        collector,
        "_download",
        lambda symbols, *, start: _frame(symbols, ["2026-01-05"]),
    )

    collector.collect(["AAPL"])
    result = collector.collect(["MSFT", "AAPL"])

    assert [o.symbol for o in result.outcomes] == ["MSFT", "AAPL"]


def test_partial_failure_does_not_stop_the_batch(
    storage: SqliteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """한 종목이 응답에 없어도 나머지는 저장되어야 한다."""
    collector = PriceCollector(storage)
    # 응답에 AAPL 만 들어 있다. MSFT 는 소스가 돌려주지 않은 상황.
    monkeypatch.setattr(
        collector,
        "_download",
        lambda symbols, *, start: _frame(["AAPL"], ["2026-01-05"]),
    )

    result = collector.collect(["AAPL", "MSFT"])

    assert [o.symbol for o in result.succeeded] == ["AAPL"]
    assert [o.symbol for o in result.failed] == ["MSFT"]
    assert storage.count_bars("AAPL") == 1, "성공한 종목은 저장되어야 한다"
    assert not result.is_complete_failure


def test_download_failure_marks_every_symbol(
    storage: SqliteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    collector = PriceCollector(storage)
    monkeypatch.setattr(collector, "_download", lambda symbols, *, start: None)

    result = collector.collect(["AAPL", "MSFT"])

    assert result.is_complete_failure
    assert len(result.failed) == 2
    assert storage.count_bars() == 0


def test_overlap_days_shifts_start_backwards(
    storage: SqliteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """증분 수집은 마지막 저장일보다 겹침 일수만큼 앞에서 시작해야 한다.

    배당·분할로 과거 수정 종가가 소급 변경되기 때문이다.
    """
    collector = PriceCollector(storage, overlap_days=3)
    seen: list[date | None] = []

    def fake_download(symbols: list[str], *, start: date | None) -> pd.DataFrame:
        seen.append(start)
        return _frame(symbols, ["2026-01-05"])

    monkeypatch.setattr(collector, "_download", fake_download)

    collector.collect(["AAPL"])
    collector.collect(["AAPL"])

    assert seen[0] is None, "처음에는 전체 이력"
    assert seen[1] == date(2026, 1, 2), "마지막 저장일(1/5)에서 3일 앞"


def test_empty_symbol_list(storage: SqliteStorage) -> None:
    result = PriceCollector(storage).collect([])

    assert result.outcomes == []
    assert not result.is_complete_failure
