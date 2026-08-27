"""횡단면 정규화와 랭킹.

정규화의 평균·표준편차는 **그날의 유니버스 안에서만** 구한다. 시계열 전체로 정규화하면
그 자체가 룩어헤드다 — 과거 시점을 평가하면서 미래의 분포를 쓰는 것이 되기 때문이다.
(`docs/tech-notes/05-quant-factor-pipeline.md` 3절)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from dii.logging_setup import get_logger
from dii.processing.frames import MarketFrames
from dii.processing.indicators import TRADING_DAYS, trailing_return
from dii.quant.factors import FACTORS, compute_factors

logger = get_logger(__name__)

#: z-score 를 자르는 범위. 한 종목이 폭등해 z 가 8 이 되면 다른 팩터를 혼자 압도한다.
Z_CLIP = 3.0

#: 유효한 팩터의 가중치 합이 이 값에 못 미치면 점수를 매기지 않는다.
#: 근거가 절반도 없는 점수를 내놓느니 "판단 보류"가 낫다.
MIN_VALID_WEIGHT = 0.5


@dataclass(frozen=True, slots=True)
class StockScore:
    """종목 하나의 스코어."""

    symbol: str
    sector_etf: str | None
    score: float
    contributions: dict[str, float]
    """팩터 key -> 이 종목 점수에 기여한 값. 리포트에서 '왜 높은가'를 설명하는 근거다."""

    raw: dict[str, float]
    """팩터 key -> 정규화 전 원시값. NaN 은 계산 불가를 뜻한다."""

    coverage: float
    """점수 계산에 실제로 쓰인 가중치 비율. 1.0 이면 모든 팩터가 유효했다."""


@dataclass(frozen=True, slots=True)
class ScoreTable:
    """기준일 하나에 대한 종목 스코어 전체."""

    as_of: date
    scores: list[StockScore]
    """점수 내림차순."""

    skipped: list[tuple[str, str]]
    """점수를 매기지 않은 `(심볼, 사유)`."""

    def top(self, n: int) -> list[StockScore]:
        return self.scores[:n]

    def bottom(self, n: int) -> list[StockScore]:
        return self.scores[-n:][::-1]


@dataclass(frozen=True, slots=True)
class SectorRow:
    """섹터 하나의 수익률."""

    etf: str
    name_ko: str
    return_1w: float
    return_1m: float
    return_3m: float
    excess_1m: float
    """1개월 수익률 - 벤치마크 1개월 수익률. 랭킹 기준."""


@dataclass(frozen=True, slots=True)
class SectorRanking:
    as_of: date
    benchmark: str
    benchmark_return_1m: float
    rows: list[SectorRow]
    """초과 수익률 내림차순."""


def cross_sectional_zscore(values: pd.Series, *, clip: float = Z_CLIP) -> pd.Series:
    """한 시점의 값들을 z-score 로 정규화하고 극단값을 자른다.

    표준편차가 0 이면(모든 종목이 같은 값) 정규화가 정의되지 않는다.
    그때는 전부 0(중립)으로 둔다 — 나눗셈으로 inf 를 만드는 것보다 낫다.
    """
    valid = values.dropna()
    if len(valid) < 2:
        return pd.Series(np.nan, index=values.index, dtype="float64")

    std = valid.std(ddof=1)
    if not np.isfinite(std) or std == 0:
        return pd.Series(0.0, index=values.index, dtype="float64").where(values.notna())

    z: pd.Series = (values - valid.mean()) / std
    return z.clip(lower=-clip, upper=clip)


def score_stocks(
    frames: MarketFrames, sector_of: dict[str, str], *, universe: list[str] | None = None
) -> ScoreTable:
    """기준일 시점의 종목 스코어를 계산한다.

    Args:
        frames: `as_of` 이하로 잘린 시장 데이터.
        sector_of: 종목 -> 소속 섹터 ETF 대응.
        universe: 점수를 매길 대상. None 이면 `sector_of` 의 키를 쓴다.
            **정규화는 이 집합 안에서 이뤄진다** — 섹터 ETF 나 벤치마크가 섞이면
            평균이 왜곡되므로 개별 종목만 넣어야 한다.
    """
    targets = universe if universe is not None else sorted(sector_of)
    available = [s for s in targets if s in frames.prices.columns]

    raw = compute_factors(frames, sector_of).loc[available]

    # 정규화는 팩터별로, 대상 종목 안에서만.
    normalized = pd.DataFrame(index=raw.index, dtype="float64")
    for spec in FACTORS:
        normalized[spec.key] = cross_sectional_zscore(raw[spec.key]) * spec.sign

    scores: list[StockScore] = []
    skipped: list[tuple[str, str]] = []

    for symbol in available:
        row = normalized.loc[symbol]
        valid_weight = sum(spec.weight for spec in FACTORS if pd.notna(row[spec.key]))

        if valid_weight < MIN_VALID_WEIGHT:
            missing = [spec.name for spec in FACTORS if pd.isna(row[spec.key])]
            skipped.append((symbol, f"팩터 부족 ({', '.join(missing)})"))
            continue

        # 값이 없는 팩터는 가중치에서 빼고 남은 것으로 다시 정규화한다.
        # 0 으로 채우면 "평균 수준"이라는 거짓 정보를 넣는 것이 된다.
        contributions = {
            spec.key: float(row[spec.key]) * spec.weight / valid_weight
            for spec in FACTORS
            if pd.notna(row[spec.key])
        }
        scores.append(
            StockScore(
                symbol=symbol,
                sector_etf=sector_of.get(symbol),
                score=sum(contributions.values()),
                contributions=contributions,
                raw={k: float(v) for k, v in raw.loc[symbol].items()},
                coverage=valid_weight,
            )
        )

    scores.sort(key=lambda s: s.score, reverse=True)

    if skipped:
        logger.info(
            "점수를 매기지 않은 종목 %d개: %s", len(skipped), ", ".join(s for s, _ in skipped)
        )

    return ScoreTable(as_of=frames.as_of, scores=scores, skipped=skipped)


def rank_sectors(
    frames: MarketFrames, sectors: list[tuple[str, str]], benchmark: str
) -> SectorRanking:
    """섹터 ETF 를 벤치마크 대비 초과 수익률로 줄 세운다.

    섹터는 개별 종목과 달리 팩터를 합성하지 않는다. 섹터 ETF 자체가 이미 지표이므로
    수익률로 직접 비교하는 편이 해석 가능하다.

    Args:
        sectors: `(ETF 심볼, 한글 이름)` 목록.
        benchmark: 비교 기준 심볼 (SPY).
    """
    prices = frames.prices
    returns = {
        key: trailing_return(prices, days)
        for key, days in (
            ("1w", TRADING_DAYS["1w"]),
            ("1m", TRADING_DAYS["1m"]),
            ("3m", TRADING_DAYS["3m"]),
        )
    }

    benchmark_1m = (
        float(returns["1m"].get(benchmark, np.nan)) if benchmark in prices.columns else float("nan")
    )

    rows = [
        SectorRow(
            etf=etf,
            name_ko=name_ko,
            return_1w=float(returns["1w"].get(etf, np.nan)),
            return_1m=float(returns["1m"].get(etf, np.nan)),
            return_3m=float(returns["3m"].get(etf, np.nan)),
            excess_1m=float(returns["1m"].get(etf, np.nan)) - benchmark_1m,
        )
        for etf, name_ko in sectors
        if etf in prices.columns
    ]
    # NaN 은 정렬에서 뒤로 보낸다. 계산 불가를 "가장 나쁨"으로 취급하지 않기 위함이다.
    rows.sort(
        key=lambda r: (np.isnan(r.excess_1m), -r.excess_1m if not np.isnan(r.excess_1m) else 0)
    )

    return SectorRanking(
        as_of=frames.as_of,
        benchmark=benchmark,
        benchmark_return_1m=benchmark_1m,
        rows=rows,
    )
