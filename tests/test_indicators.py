"""파생 지표 테스트.

전부 손으로 계산할 수 있는 작은 입력을 쓴다. 지표 함수는 순수 함수이므로
DB 도 네트워크도 필요 없다.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from dii.processing.indicators import (
    realized_volatility,
    relative_strength,
    trailing_return,
    volume_surge,
    window_return,
)


def _prices(values: list[float], symbol: str = "A") -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=len(values), freq="D")
    return pd.DataFrame({symbol: values}, index=index)


# ------------------------------------------------------------------ 수익률


def test_trailing_return_is_simple_return() -> None:
    """마지막 값 / window 칸 전 값 - 1."""
    result = trailing_return(_prices([100.0, 110.0, 121.0]), window=2)

    assert result["A"] == pytest.approx(0.21)


def test_trailing_return_needs_enough_history() -> None:
    """이력이 모자라면 NaN 이다. 0 으로 채우면 '수익률 0'이라는 거짓말이 된다."""
    result = trailing_return(_prices([100.0, 110.0]), window=5)

    assert math.isnan(result["A"])


def test_window_return_excludes_recent_period() -> None:
    """모멘텀이 최근 구간을 제외할 때 쓰는 계산.

    가격 [100, 200, 400, 800] 에서 start_lag=3, end_lag=1 이면
    인덱스 0(=100) 부터 인덱스 2(=400) 까지 → 3.0
    """
    result = window_return(_prices([100.0, 200.0, 400.0, 800.0]), start_lag=3, end_lag=1)

    assert result["A"] == pytest.approx(3.0)
    assert "최근 구간이 빠졌다" and result["A"] != pytest.approx(7.0)


def test_window_return_rejects_inverted_lags() -> None:
    with pytest.raises(ValueError, match="작아야 한다"):
        window_return(_prices([1.0] * 10), start_lag=5, end_lag=5)


# ------------------------------------------------------------------ 변동성


def test_realized_volatility_is_zero_for_constant_prices() -> None:
    result = realized_volatility(_prices([100.0] * 30), window=21)

    assert result["A"] == pytest.approx(0.0)


def test_realized_volatility_is_annualized() -> None:
    """일간 로그수익률 표준편차에 sqrt(252) 를 곱한 값인지 직접 계산과 비교한다."""
    rng = np.random.default_rng(42)
    values = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 60)))
    prices = _prices(list(values))

    result = realized_volatility(prices, window=21)

    log_returns = np.diff(np.log(values))[-21:]
    expected = float(np.std(log_returns, ddof=1) * np.sqrt(252))
    assert result["A"] == pytest.approx(expected, rel=1e-9)


def test_realized_volatility_needs_enough_observations() -> None:
    result = realized_volatility(_prices([100.0, 101.0, 102.0]), window=21)

    assert math.isnan(result["A"])


# ------------------------------------------------------------------ 거래량


def test_volume_surge_is_zero_when_flat() -> None:
    volumes = pd.DataFrame({"A": [1000.0] * 70}, index=pd.date_range("2026-01-01", periods=70))

    result = volume_surge(volumes)

    assert result["A"] == pytest.approx(0.0)


def test_volume_surge_is_log_ratio() -> None:
    """최근 5일만 2배로 뛴 경우. 로그 비율이므로 양수여야 한다."""
    values = [1000.0] * 65 + [2000.0] * 5
    volumes = pd.DataFrame({"A": values}, index=pd.date_range("2026-01-01", periods=70))

    result = volume_surge(volumes, short=5, long=60)

    recent, baseline = 2000.0, float(np.mean(values[-60:]))
    assert result["A"] == pytest.approx(math.log(recent / baseline))
    assert result["A"] > 0


def test_volume_surge_handles_zero_volume() -> None:
    """거래량 0 은 로그를 취할 수 없다. inf 가 아니라 NaN 이어야 한다."""
    volumes = pd.DataFrame({"A": [0.0] * 70}, index=pd.date_range("2026-01-01", periods=70))

    result = volume_surge(volumes)

    assert math.isnan(result["A"])


# ------------------------------------------------------------------ 상대강도


def test_relative_strength_against_scalar() -> None:
    returns = pd.Series({"AAPL": 0.10, "MSFT": 0.02})

    result = relative_strength(returns, 0.05)

    assert result["AAPL"] == pytest.approx(0.05)
    assert result["MSFT"] == pytest.approx(-0.03)


def test_relative_strength_against_sector() -> None:
    """종목마다 다른 기준(소속 섹터 ETF)에서 뺀다."""
    returns = pd.Series({"AAPL": 0.10, "JPM": 0.10, "XLK": 0.08, "XLF": 0.03})

    result = relative_strength(returns, returns, mapping={"AAPL": "XLK", "JPM": "XLF"})

    assert result["AAPL"] == pytest.approx(0.02)
    assert result["JPM"] == pytest.approx(0.07), "같은 수익률이어도 섹터가 약하면 상대강도가 높다"


def test_relative_strength_without_mapping_is_nan() -> None:
    returns = pd.Series({"AAPL": 0.10, "XLK": 0.08})

    result = relative_strength(returns, returns, mapping={})

    assert math.isnan(result["AAPL"])
