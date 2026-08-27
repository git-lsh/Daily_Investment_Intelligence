from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from dii.storage import DailyBar, SecurityKind, SqliteStorage, connect
from dii.storage.schema import SCHEMA_VERSION, apply_migrations


@pytest.fixture
def storage(tmp_path: Path) -> Iterator[SqliteStorage]:
    with connect(tmp_path / "test.sqlite3") as conn:
        store = SqliteStorage(conn)
        store.upsert_securities([("AAPL", SecurityKind.STOCK, None, "Apple")])
        yield store


def _bar(day: str, close: float = 100.0, **overrides: float) -> DailyBar:
    values: dict[str, float] = {
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "adj_close": close,
        **overrides,
    }
    return DailyBar(
        symbol="AAPL",
        trade_date=date.fromisoformat(day),
        volume=1_000,
        **values,
    )


# --------------------------------------------------------------------- 마이그레이션


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    """두 번 열어도 스키마가 다시 적용되지 않는다."""
    db = tmp_path / "m.sqlite3"
    with connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert apply_migrations(conn) == 0, "이미 최신이면 적용할 것이 없다"


def test_newer_schema_is_rejected(tmp_path: Path) -> None:
    """미래 버전 코드가 만든 DB 를 구버전 코드가 건드리지 않게 막는다."""
    db = tmp_path / "future.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.close()

    with pytest.raises(RuntimeError, match="스키마 버전"), connect(db):
        pass


# --------------------------------------------------------------------- 멱등성


def test_upsert_is_idempotent(storage: SqliteStorage) -> None:
    """M1 의 핵심 요구 — 같은 데이터를 여러 번 넣어도 행이 늘지 않는다."""
    bars = [_bar("2026-01-05"), _bar("2026-01-06")]

    storage.upsert_daily_bars(bars)
    storage.upsert_daily_bars(bars)
    storage.upsert_daily_bars(bars)

    assert storage.count_bars("AAPL") == 2


def test_upsert_overwrites_changed_values(storage: SqliteStorage) -> None:
    """배당으로 수정 종가가 소급 변경되는 상황. 기존 행을 덮어써야 한다."""
    storage.upsert_daily_bars([_bar("2026-01-05", close=100.0, adj_close=100.0)])
    storage.upsert_daily_bars([_bar("2026-01-05", close=100.0, adj_close=99.5)])

    stored = storage.get_bars("AAPL")

    assert len(stored) == 1
    assert stored[0].adj_close == 99.5
    assert stored[0].close == 100.0, "원본 종가는 그대로여야 한다"


# --------------------------------------------------------------------- 조회


def test_get_bars_is_ordered_and_range_filtered(storage: SqliteStorage) -> None:
    storage.upsert_daily_bars([_bar("2026-01-07"), _bar("2026-01-05"), _bar("2026-01-06")])

    dates = [b.trade_date.isoformat() for b in storage.get_bars("AAPL")]
    assert dates == ["2026-01-05", "2026-01-06", "2026-01-07"], "날짜 오름차순이어야 한다"

    ranged = storage.get_bars("AAPL", start=date(2026, 1, 6), end=date(2026, 1, 6))
    assert [b.trade_date.isoformat() for b in ranged] == ["2026-01-06"]


def test_latest_trade_date(storage: SqliteStorage) -> None:
    assert storage.latest_trade_date("AAPL") is None, "데이터가 없으면 None"

    storage.upsert_daily_bars([_bar("2026-01-05"), _bar("2026-01-09")])

    assert storage.latest_trade_date("AAPL") == date(2026, 1, 9)
    assert storage.latest_trade_dates() == {"AAPL": date(2026, 1, 9)}


def test_coverage(storage: SqliteStorage) -> None:
    storage.upsert_daily_bars([_bar("2026-01-05"), _bar("2026-01-09")])

    assert storage.coverage() == [("AAPL", date(2026, 1, 5), date(2026, 1, 9), 2)]


# --------------------------------------------------------------------- 제약


def test_foreign_key_is_enforced(storage: SqliteStorage) -> None:
    """SQLite 는 외래키 강제가 기본 꺼짐이다. 켜져 있는지 확인한다."""
    unknown = DailyBar(
        symbol="NOPE",
        trade_date=date(2026, 1, 5),
        open=1.0,
        high=1.0,
        low=1.0,
        close=1.0,
        adj_close=1.0,
        volume=1,
    )
    with pytest.raises(sqlite3.IntegrityError):
        storage.upsert_daily_bars([unknown])


def test_check_constraint_blocks_negative_price(storage: SqliteStorage) -> None:
    """수집기가 놓쳐도 DB 가 마지막으로 막는다."""
    with pytest.raises(sqlite3.IntegrityError):
        storage.upsert_daily_bars([_bar("2026-01-05", close=-5.0, high=-4.0, low=-6.0)])


def test_empty_write_is_a_noop(storage: SqliteStorage) -> None:
    assert storage.upsert_daily_bars([]) == 0
