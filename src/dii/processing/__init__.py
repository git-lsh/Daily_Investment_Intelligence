"""파생 지표 계산 계층.

저장 형식(long: 한 행 = 한 종목의 하루)을 분석 형식(wide: 행=날짜, 열=종목)으로 바꾸고,
그 위에서 수익률·변동성·거래량 지표를 계산한다.
"""

from dii.processing.frames import MarketFrames, load_frames
from dii.processing.indicators import (
    TRADING_DAYS,
    realized_volatility,
    trailing_return,
    volume_surge,
    window_return,
)

__all__ = [
    "TRADING_DAYS",
    "MarketFrames",
    "load_frames",
    "realized_volatility",
    "trailing_return",
    "volume_surge",
    "window_return",
]
