"""스키마 정의와 마이그레이션.

스키마 버전은 SQLite 의 `PRAGMA user_version` 에 기록한다. DB 파일 자체가 자기 버전을
들고 있으므로 별도 관리 테이블이 필요 없다. 기동할 때마다 현재 버전을 보고 부족한 만큼만 적용한다.
"""

from __future__ import annotations

import sqlite3

from dii.logging_setup import get_logger

logger = get_logger(__name__)

#: 코드가 기대하는 스키마 버전. MIGRATIONS 를 추가할 때마다 함께 올린다.
SCHEMA_VERSION = 1

_V1 = """
-- 수집·분석 대상 종목. 가격 테이블이 참조한다.
CREATE TABLE IF NOT EXISTS security (
    symbol      TEXT    NOT NULL PRIMARY KEY,
    kind        TEXT    NOT NULL CHECK (kind IN ('benchmark', 'sector_etf', 'stock')),
    -- 개별 종목이면 소속 섹터 ETF, 벤치마크·ETF 자신이면 NULL
    sector_etf  TEXT    REFERENCES security(symbol),
    name        TEXT,
    updated_at  TEXT    NOT NULL
);

-- 일봉. 한 행 = 한 종목의 하루 (long 형식).
CREATE TABLE IF NOT EXISTS daily_price (
    symbol      TEXT    NOT NULL REFERENCES security(symbol),
    -- ISO 8601 'YYYY-MM-DD'. 사전순 정렬이 곧 시간순 정렬이라 범위 조회가 자연스럽다.
    trade_date  TEXT    NOT NULL,
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    -- 배당·분할 반영 종가. 원본(close)과 달리 과거 값이 소급해서 바뀔 수 있다.
    adj_close   REAL    NOT NULL,
    volume      INTEGER NOT NULL,
    ingested_at TEXT    NOT NULL,

    -- 멱등성의 핵심. 같은 종목·같은 날짜는 한 행뿐이고, 재실행 시 UPSERT 대상이 된다.
    PRIMARY KEY (symbol, trade_date),

    -- 마지막 방어선. 수집기가 먼저 거르지만, 버그가 있어도 쓰레기가 들어오지 않게 한다.
    CHECK (open > 0 AND high > 0 AND low > 0 AND close > 0 AND adj_close > 0),
    CHECK (volume >= 0),
    CHECK (high >= open AND high >= close AND high >= low),
    CHECK (low  <= open AND low  <= close)
);

-- 복합 기본키 (symbol, trade_date) 는 "한 종목의 기간 조회"를 커버한다.
-- 반대 방향인 "특정 날짜의 전 종목 조회"(M2 의 횡단면 랭킹)는 커버하지 못하므로 따로 둔다.
CREATE INDEX IF NOT EXISTS idx_daily_price_trade_date ON daily_price(trade_date);
"""

#: 버전 N 으로 올리는 DDL 을 순서대로 담는다. 인덱스 i 가 버전 i+1 에 대응한다.
MIGRATIONS: tuple[str, ...] = (_V1,)


def apply_migrations(conn: sqlite3.Connection) -> int:
    """DB 를 최신 스키마로 올린다. 이미 최신이면 아무 것도 하지 않는다.

    Returns:
        적용한 마이그레이션 수.
    """
    current: int = conn.execute("PRAGMA user_version").fetchone()[0]

    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"DB 스키마 버전({current})이 코드가 아는 버전({SCHEMA_VERSION})보다 높다. "
            "더 새로운 버전의 코드로 만들어진 DB 파일이다."
        )

    applied = 0
    for version in range(current + 1, SCHEMA_VERSION + 1):
        logger.info("스키마 마이그레이션 적용: v%d → v%d", version - 1, version)
        with conn:  # 트랜잭션 — DDL 과 버전 기록이 함께 반영되거나 함께 취소된다
            conn.executescript(MIGRATIONS[version - 1])
            # PRAGMA 는 파라미터 바인딩을 지원하지 않는다. version 은 코드가 만든 정수라 안전하다.
            conn.execute(f"PRAGMA user_version = {version}")
        applied += 1

    return applied
