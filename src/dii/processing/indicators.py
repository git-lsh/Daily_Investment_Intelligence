"""파생 지표 계산.

전부 **순수 함수**다. DataFrame 을 받아 Series 를 돌려주고, DB 도 시각도 건드리지 않는다.
같은 입력에 항상 같은 출력이 나와야 파이프라인이 재현 가능해진다.
(`docs/tech-notes/05-quant-factor-pipeline.md` 3절 — 결정론적 파이프라인)

윈도우는 전부 **거래일 개수**다. 달력 날짜가 아니다. 주말·휴장일이 데이터에 없으므로
행 개수로 세는 것이 자연스럽고 정확하다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

#: 기간 이름 -> 거래일 수. 1년 252 거래일을 기준으로 나눈 관행값이다.
TRADING_DAYS: dict[str, int] = {
    "1w": 5,
    "1m": 21,
    "3m": 63,
    "6m": 126,
    "1y": 252,
}

#: 변동성 연율화 계수. 분산이 시간에 비례한다는 가정에서 나온다.
ANNUALIZATION = 252


def trailing_return(prices: pd.DataFrame, window: int) -> pd.Series:
    """기준일까지 `window` 거래일 동안의 단순 수익률.

    `P[t] / P[t-window] - 1`. 이력이 모자라면 NaN 이다 — 0 으로 채우지 않는다.
    0 으로 채우면 "수익률이 0이었다"는 거짓 정보가 되기 때문이다.
    """
    if len(prices) <= window:
        return pd.Series(np.nan, index=prices.columns, dtype="float64")

    end = prices.iloc[-1]
    start = prices.iloc[-1 - window]
    return _safe_ratio(end, start) - 1.0


def window_return(prices: pd.DataFrame, *, start_lag: int, end_lag: int) -> pd.Series:
    """기준일에서 `start_lag` 거래일 전부터 `end_lag` 거래일 전까지의 수익률.

    모멘텀 팩터가 **최근 구간을 제외**할 때 쓴다.
    예) 6개월 수익률에서 최근 1개월 제외 = `start_lag=126, end_lag=21`.
    최근 1개월을 빼는 것은 단기 반전 효과와 섞이는 것을 피하기 위한 표준 처리다.

    Args:
        start_lag: 구간 시작 시점 (기준일에서 몇 거래일 전인가). 더 큰 값.
        end_lag: 구간 종료 시점. `start_lag` 보다 작아야 한다.
    """
    if end_lag >= start_lag:
        raise ValueError(f"end_lag({end_lag}) 는 start_lag({start_lag}) 보다 작아야 한다")
    if len(prices) <= start_lag:
        return pd.Series(np.nan, index=prices.columns, dtype="float64")

    start = prices.iloc[-1 - start_lag]
    end = prices.iloc[-1 - end_lag]
    return _safe_ratio(end, start) - 1.0


def realized_volatility(prices: pd.DataFrame, window: int) -> pd.Series:
    """최근 `window` 거래일의 실현 변동성 (연율화).

    일간 **로그 수익률**의 표준편차에 `√252` 를 곱한다. 로그 수익률을 쓰는 이유는
    시간에 대해 더해지는 성질 때문이다.

    표본 표준편차(ddof=1)를 쓴다. 관측치가 모집단이 아니라 표본이기 때문이다.
    """
    if len(prices) <= window:
        return pd.Series(np.nan, index=prices.columns, dtype="float64")

    log_returns = np.log(prices / prices.shift(1))
    recent = log_returns.iloc[-window:]

    # 관측치가 모자란 종목은 NaN 으로 남긴다. min_periods 를 낮추면
    # 며칠치로 계산한 변동성이 정상값처럼 섞여 들어간다.
    volatility = recent.std(ddof=1, skipna=True)
    enough = recent.notna().sum() >= window * 0.8
    result: pd.Series = (volatility * np.sqrt(ANNUALIZATION)).where(enough)
    return result


def volume_surge(volumes: pd.DataFrame, *, short: int = 5, long: int = 60) -> pd.Series:
    """최근 거래량이 평소보다 얼마나 몰렸는지.

    `ln(최근 short일 평균 / 최근 long일 평균)`. 로그를 씌우는 이유는 두 가지다.

    - 거래량 배수는 아래로 1배까지지만 위로는 열려 있어 분포가 심하게 치우친다
    - 로그를 씌우면 "2배"와 "절반"이 부호만 다른 같은 크기가 되어 대칭이 된다
    """
    if len(volumes) <= long:
        return pd.Series(np.nan, index=volumes.columns, dtype="float64")

    recent = volumes.iloc[-short:].mean(skipna=True)
    baseline = volumes.iloc[-long:].mean(skipna=True)

    # 거래량 0 은 로그를 취할 수 없다. 계산 불가로 두고 NaN 을 돌려준다.
    valid = (recent > 0) & (baseline > 0)
    ratio = _safe_ratio(recent, baseline).where(valid)
    result: pd.Series = np.log(ratio)
    return result


def relative_strength(
    returns: pd.Series,
    benchmark_returns: pd.Series | float,
    *,
    mapping: dict[str, str] | None = None,
) -> pd.Series:
    """대상 수익률에서 기준 수익률을 뺀 초과 수익률.

    Args:
        returns: 종목별 수익률.
        benchmark_returns: 기준 수익률. 스칼라(시장 전체 대비)이거나,
            심볼별 Series(섹터별 대비)일 수 있다.
        mapping: 종목 -> 기준 심볼 대응. 섹터 대비를 계산할 때 준다.
            대응이 없는 종목은 NaN 이 된다.
    """
    if isinstance(benchmark_returns, float):
        return returns - benchmark_returns

    if mapping is None:
        return returns - benchmark_returns

    baseline = pd.Series(
        [benchmark_returns.get(mapping.get(symbol, ""), np.nan) for symbol in returns.index],
        index=returns.index,
        dtype="float64",
    )
    return returns - baseline


def _safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """0 나눗셈에서 inf 가 나오지 않게 막는다.

    가격은 CHECK 제약으로 양수가 보장되지만, 계산 중간값까지 그렇지는 않다.
    inf 가 한 번 섞이면 이후 평균·표준편차가 전부 오염된다.
    """
    result: pd.Series = numerator / denominator.where(denominator > 0)
    return result.replace([np.inf, -np.inf], np.nan)
