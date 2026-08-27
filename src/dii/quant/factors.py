"""팩터 정의와 계산.

팩터와 가중치는 **미리 정해 고정한다.** 여러 조합을 시험해 보고 잘 나오는 것을 고르면
우연히 맞은 것을 고르게 된다(다중 검정 문제). 바꿀 때는 이유를 학습 로그에 남기고 바꾼다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from dii.processing.frames import MarketFrames
from dii.processing.indicators import (
    TRADING_DAYS,
    realized_volatility,
    relative_strength,
    trailing_return,
    volume_surge,
    window_return,
)


@dataclass(frozen=True, slots=True)
class FactorSpec:
    """팩터 하나의 명세."""

    key: str
    name: str
    weight: float
    higher_is_better: bool
    description: str

    @property
    def sign(self) -> float:
        """정규화 후 곱할 부호. 낮을수록 좋은 팩터는 뒤집는다."""
        return 1.0 if self.higher_is_better else -1.0


#: 종목 스코어를 이루는 팩터들. 가중치 합은 1.0 이다.
FACTORS: tuple[FactorSpec, ...] = (
    FactorSpec(
        key="momentum",
        name="모멘텀",
        weight=0.40,
        higher_is_better=True,
        description="6개월 수익률에서 최근 1개월 제외 (단기 반전과 섞이는 것을 피한다)",
    ),
    FactorSpec(
        key="sector_rs",
        name="섹터 상대강도",
        weight=0.25,
        higher_is_better=True,
        description="1개월 수익률 - 소속 섹터 ETF 1개월 수익률",
    ),
    FactorSpec(
        key="low_volatility",
        name="저변동성",
        weight=0.15,
        higher_is_better=False,
        description="21일 실현 변동성 (연율화). 낮을수록 높은 점수",
    ),
    FactorSpec(
        key="volume_surge",
        name="거래량 서지",
        weight=0.20,
        higher_is_better=True,
        description="ln(최근 5일 평균 거래량 / 최근 60일 평균 거래량)",
    ),
)


def compute_factors(frames: MarketFrames, sector_of: dict[str, str]) -> pd.DataFrame:
    """기준일 시점의 팩터 원시값을 계산한다.

    Args:
        frames: `as_of` 이하로 잘린 가격·거래량 행렬.
        sector_of: 종목 -> 소속 섹터 ETF 대응.

    Returns:
        행이 심볼, 열이 팩터 key 인 DataFrame. 값은 **정규화 전 원시값**이다.
        계산할 수 없는 값은 NaN 으로 남는다 (0 으로 채우지 않는다).
    """
    prices = frames.prices
    month = TRADING_DAYS["1m"]

    return_1m = trailing_return(prices, month)

    factors = pd.DataFrame(index=prices.columns, dtype="float64")
    factors["momentum"] = window_return(prices, start_lag=TRADING_DAYS["6m"], end_lag=month)
    factors["sector_rs"] = relative_strength(return_1m, return_1m, mapping=sector_of)
    factors["low_volatility"] = realized_volatility(prices, month)
    factors["volume_surge"] = volume_surge(frames.volumes)

    return factors[[spec.key for spec in FACTORS]]
