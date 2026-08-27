"""횡단면 스코어링과 as-of 조회 테스트.

M2 의 완료 조건인 **룩어헤드 차단**은 마지막 클래스에서 실제 저장소로 검증한다.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dii.processing.frames import InsufficientDataError, MarketFrames, load_frames
from dii.quant.factors import FACTORS
from dii.quant.scoring import cross_sectional_zscore, rank_sectors, score_stocks
from dii.storage import DailyBar, SecurityKind, SqliteStorage, connect

# --------------------------------------------------------------------- 정규화


def test_zscore_centers_and_scales() -> None:
    result = cross_sectional_zscore(pd.Series({"A": 1.0, "B": 2.0, "C": 3.0}))

    assert result.mean() == pytest.approx(0.0)
    assert result["B"] == pytest.approx(0.0)
    assert result["C"] > 0 > result["A"]


def test_zscore_clips_outliers() -> None:
    """한 종목이 폭등해도 다른 팩터를 혼자 압도하지 못하게 자른다."""
    values = pd.Series({f"S{i}": 1.0 for i in range(30)} | {"OUT": 1000.0})

    result = cross_sectional_zscore(values, clip=3.0)

    assert result["OUT"] == pytest.approx(3.0)


def test_zscore_with_zero_variance_is_neutral() -> None:
    """모두 같은 값이면 정규화가 정의되지 않는다. inf 대신 중립(0)."""
    result = cross_sectional_zscore(pd.Series({"A": 5.0, "B": 5.0, "C": 5.0}))

    assert (result == 0.0).all()


def test_zscore_ignores_nan_when_computing_stats() -> None:
    result = cross_sectional_zscore(pd.Series({"A": 1.0, "B": 3.0, "C": np.nan}))

    assert math.isnan(result["C"]), "값이 없으면 z 도 없다"
    assert result["A"] == pytest.approx(-result["B"])


def test_zscore_needs_at_least_two_values() -> None:
    result = cross_sectional_zscore(pd.Series({"A": 1.0, "B": np.nan}))

    assert result.isna().all()


# --------------------------------------------------------------------- 스코어


def _frames(
    days: int = 200, symbols: tuple[str, ...] = ("XLK", "AAA", "BBB", "CCC")
) -> MarketFrames:
    """종목마다 다른 기울기로 오르는 가짜 시장. 랭킹이 결정적으로 나온다.

    섹터 상대강도를 계산하려면 기준이 되는 섹터 ETF 도 행렬에 있어야 하므로 XLK 를 포함한다.
    """
    index = pd.date_range("2025-01-01", periods=days, freq="B")
    prices = pd.DataFrame(
        {s: 100.0 * (1.0 + 0.001 * (i + 1)) ** np.arange(days) for i, s in enumerate(symbols)},
        index=index,
    )
    volumes = pd.DataFrame({s: np.full(days, 1_000.0) for s in symbols}, index=index)
    return MarketFrames(prices=prices, volumes=volumes, as_of=index[-1].date())


def test_score_ranks_stronger_momentum_higher() -> None:
    frames = _frames()
    sector_of = {"AAA": "XLK", "BBB": "XLK", "CCC": "XLK"}

    table = score_stocks(frames, sector_of, universe=["AAA", "BBB", "CCC"])

    assert [s.symbol for s in table.scores] == ["CCC", "BBB", "AAA"]


def test_contributions_sum_to_score() -> None:
    """표에 찍히는 기여도의 합이 점수와 같아야 한다. 설명 가능성의 최소 조건이다."""
    frames = _frames()
    sector_of = dict.fromkeys(("AAA", "BBB", "CCC"), "XLK")

    for item in score_stocks(frames, sector_of).scores:
        assert sum(item.contributions.values()) == pytest.approx(item.score)


def test_full_coverage_when_all_factors_available() -> None:
    frames = _frames()
    sector_of = dict.fromkeys(("AAA", "BBB", "CCC"), "XLK")

    for item in score_stocks(frames, sector_of).scores:
        assert item.coverage == pytest.approx(1.0)


def test_short_history_is_skipped_not_zero_filled() -> None:
    """이력이 짧아 팩터를 못 구하면 0 으로 채우지 않고 점수를 보류한다."""
    frames = _frames(days=30)
    sector_of = dict.fromkeys(("AAA", "BBB", "CCC"), "XLK")

    table = score_stocks(frames, sector_of)

    assert table.scores == []
    assert len(table.skipped) == 3
    assert all("팩터 부족" in reason for _, reason in table.skipped)


def test_weights_sum_to_one() -> None:
    """가중치 합이 1 이 아니면 점수 스케일의 의미가 흐려진다."""
    assert sum(spec.weight for spec in FACTORS) == pytest.approx(1.0)


def test_low_volatility_factor_is_inverted() -> None:
    """저변동성은 낮을수록 좋은 유일한 팩터다."""
    spec = next(s for s in FACTORS if s.key == "low_volatility")

    assert not spec.higher_is_better
    assert spec.sign == -1.0


# --------------------------------------------------------------------- 섹터


def test_sector_ranking_uses_excess_return() -> None:
    """절대 수익률이 아니라 벤치마크 대비로 줄 세운다."""
    frames = _frames(symbols=("SPY", "XLA", "XLB"))

    ranking = rank_sectors(frames, [("XLA", "가"), ("XLB", "나")], "SPY")

    assert [r.etf for r in ranking.rows] == ["XLB", "XLA"]
    assert ranking.rows[0].excess_1m > 0, "벤치마크보다 많이 올랐다"


def test_sector_ranking_skips_unknown_etf() -> None:
    frames = _frames(symbols=("SPY", "XLA"))

    ranking = rank_sectors(frames, [("XLA", "가"), ("NOPE", "없음")], "SPY")

    assert [r.etf for r in ranking.rows] == ["XLA"]


# --------------------------------------------------------------- as-of 조회


@pytest.fixture
def loaded_storage(tmp_path: Path) -> Iterator[SqliteStorage]:
    """2026-01-05 부터 60 거래일치가 든 저장소."""
    with connect(tmp_path / "asof.sqlite3") as conn:
        storage = SqliteStorage(conn)
        storage.upsert_securities(
            [("AAA", SecurityKind.STOCK, None, None), ("BBB", SecurityKind.STOCK, None, None)]
        )
        start = date(2026, 1, 5)
        bars = []
        for symbol in ("AAA", "BBB"):
            for offset in range(60):
                day = start + timedelta(days=offset)
                if day.weekday() >= 5:  # 주말은 거래일이 아니다
                    continue
                price = 100.0 + offset
                bars.append(
                    DailyBar(
                        symbol=symbol,
                        trade_date=day,
                        open=price,
                        high=price + 1,
                        low=price - 1,
                        close=price,
                        adj_close=price,
                        volume=1_000,
                    )
                )
        storage.upsert_daily_bars(bars)
        yield storage


def test_load_frames_excludes_future_data(loaded_storage: SqliteStorage) -> None:
    """M2 의 핵심 — as_of 이후 데이터가 행렬에 들어오면 안 된다."""
    as_of = date(2026, 2, 2)

    frames = load_frames(loaded_storage, ["AAA", "BBB"], as_of)

    assert frames.prices.index.max().date() <= as_of
    assert frames.as_of <= as_of


def test_load_frames_snaps_non_trading_day_backwards(loaded_storage: SqliteStorage) -> None:
    """주말을 넘겨도 직전 거래일로 당겨져야 한다. 스케줄러가 토요일에 돌 수 있다."""
    saturday = date(2026, 1, 10)
    assert saturday.weekday() == 5

    frames = load_frames(loaded_storage, ["AAA"], saturday)

    assert frames.as_of == date(2026, 1, 9), "직전 금요일"


def test_load_frames_defaults_to_last_stored_day(loaded_storage: SqliteStorage) -> None:
    frames = load_frames(loaded_storage, ["AAA"], None)

    assert frames.as_of == loaded_storage.latest_trade_date("AAA")


def test_load_frames_before_any_data_raises(loaded_storage: SqliteStorage) -> None:
    with pytest.raises(InsufficientDataError, match="이전에 저장된 시세가 없다"):
        load_frames(loaded_storage, ["AAA"], date(2020, 1, 1))


def test_load_frames_rejects_empty_symbols(loaded_storage: SqliteStorage) -> None:
    with pytest.raises(InsufficientDataError, match="대상 심볼이 없다"):
        load_frames(loaded_storage, [], None)


def test_asof_result_matches_truncated_storage(
    loaded_storage: SqliteStorage, tmp_path: Path
) -> None:
    """룩어헤드가 없다는 것의 실질적 증명.

    (1) 전체 데이터에 as_of 를 지정한 결과와
    (2) as_of 이후를 물리적으로 지운 저장소의 결과가
    같아야 한다. 다르다면 어딘가에서 미래를 보고 있다는 뜻이다.
    """
    as_of = date(2026, 2, 2)
    symbols = ["AAA", "BBB"]

    with_future = load_frames(loaded_storage, symbols, as_of)

    with connect(tmp_path / "truncated.sqlite3") as conn:
        truncated_storage = SqliteStorage(conn)
        truncated_storage.upsert_securities([(s, SecurityKind.STOCK, None, None) for s in symbols])
        kept = [
            bar
            for symbol in symbols
            for bar in loaded_storage.get_bars(symbol)
            if bar.trade_date <= as_of
        ]
        truncated_storage.upsert_daily_bars(kept)
        without_future = load_frames(truncated_storage, symbols, as_of)

        assert without_future.as_of == with_future.as_of
        pd.testing.assert_frame_equal(without_future.prices, with_future.prices)
