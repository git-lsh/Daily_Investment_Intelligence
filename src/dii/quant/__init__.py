"""정량 분석 계층 — 팩터 스코어링과 랭킹.

여기서 나오는 점수는 **검증된 알파 모델이 아니라 스크리닝 점수**다.
"오늘 무엇을 들여다볼 만한가"를 좁히는 것이 목적이고, 매수 신호가 아니다.
(`docs/tech-notes/05-quant-factor-pipeline.md` 0절)
"""

from dii.quant.factors import FACTORS, FactorSpec, compute_factors
from dii.quant.scoring import (
    ScoreTable,
    SectorRanking,
    SectorRow,
    StockScore,
    rank_sectors,
    score_stocks,
)

__all__ = [
    "FACTORS",
    "FactorSpec",
    "ScoreTable",
    "SectorRanking",
    "SectorRow",
    "StockScore",
    "compute_factors",
    "rank_sectors",
    "score_stocks",
]
