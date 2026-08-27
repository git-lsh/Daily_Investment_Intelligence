"""SQLite 리포지토리 구현.

바깥 코드가 보는 것은 `SqliteStorage` 의 메서드 이름(도메인 언어)뿐이고, SQL 은 이 파일 안에만 있다.
M3 에서 PostgreSQL 구현체로 갈아탈 때 바뀌는 범위를 이 파일로 묶어 두기 위함이다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

from dii.logging_setup import get_logger
from dii.storage.models import DailyBar, SecurityKind
from dii.storage.schema import apply_migrations

logger = get_logger(__name__)

_DATE_FMT = "%Y-%m-%d"


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """DB 커넥션을 열고 스키마를 최신으로 맞춘 뒤 넘긴다.

    설정하는 PRAGMA:
        - `foreign_keys`: SQLite 는 외래키 강제가 기본 꺼짐이다. 커넥션마다 켜 줘야 한다
        - `journal_mode=WAL`: 읽기와 쓰기가 서로를 막지 않게 한다
        - `synchronous=NORMAL`: WAL 에서 권장되는 절충. 수집 데이터는 언제든 다시 받을 수 있으므로
          최고 내구성보다 쓰기 속도를 택한다
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level="DEFERRED")
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        apply_migrations(conn)
        yield conn
    finally:
        conn.close()


class SqliteStorage:
    """일봉과 종목 메타데이터를 다루는 리포지토리."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------ 종목

    def upsert_securities(
        self, rows: Iterable[tuple[str, SecurityKind, str | None, str | None]]
    ) -> int:
        """종목 메타데이터를 등록하거나 갱신한다.

        Args:
            rows: `(symbol, kind, sector_etf, name)` 튜플들.
                  가격이 종목을 외래키로 참조하므로 **가격보다 먼저** 들어가야 한다.
        """
        now = _utc_now_iso()
        payload = [(sym, kind.value, sector, name, now) for sym, kind, sector, name in rows]
        with self._conn:
            self._conn.executemany(
                """
                INSERT INTO security (symbol, kind, sector_etf, name, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    kind       = excluded.kind,
                    sector_etf = excluded.sector_etf,
                    name       = excluded.name,
                    updated_at = excluded.updated_at
                """,
                payload,
            )
        return len(payload)

    # -------------------------------------------------------------- 일봉 쓰기

    def upsert_daily_bars(self, bars: Iterable[DailyBar]) -> int:
        """일봉을 저장한다. 이미 있는 `(종목, 날짜)` 는 덮어쓴다.

        덮어쓰는 것이 핵심이다. 재실행해도 중복 행이 생기지 않고(멱등성),
        배당으로 수정 종가가 소급 변경되어도 최신 값으로 갱신된다.

        전체를 **한 트랜잭션**으로 묶는다. 행마다 커밋하면 매번 디스크 동기화가 일어나 훨씬 느리다.
        """
        now = _utc_now_iso()
        payload = [
            (
                bar.symbol,
                bar.trade_date.strftime(_DATE_FMT),
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.adj_close,
                bar.volume,
                now,
            )
            for bar in bars
        ]
        if not payload:
            return 0

        with self._conn:
            self._conn.executemany(
                """
                INSERT INTO daily_price
                    (symbol, trade_date, open, high, low, close, adj_close, volume, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, trade_date) DO UPDATE SET
                    open        = excluded.open,
                    high        = excluded.high,
                    low         = excluded.low,
                    close       = excluded.close,
                    adj_close   = excluded.adj_close,
                    volume      = excluded.volume,
                    ingested_at = excluded.ingested_at
                """,
                payload,
            )
        return len(payload)

    # -------------------------------------------------------------- 일봉 읽기

    def latest_trade_date(self, symbol: str) -> date | None:
        """해당 종목의 가장 최근 저장 날짜. 없으면 None.

        증분 수집이 "어디서부터 다시 받을지" 정하는 근거다.
        """
        row = self._conn.execute(
            "SELECT MAX(trade_date) AS d FROM daily_price WHERE symbol = ?", (symbol,)
        ).fetchone()
        return _parse_date(row["d"]) if row and row["d"] else None

    def latest_trade_dates(self) -> dict[str, date]:
        """전 종목의 마지막 저장 날짜를 한 번에 가져온다.

        종목마다 따로 물으면 56번 왕복한다(N+1). 한 번에 집계해 온다.
        """
        rows = self._conn.execute(
            "SELECT symbol, MAX(trade_date) AS d FROM daily_price GROUP BY symbol"
        ).fetchall()
        return {row["symbol"]: _parse_date(row["d"]) for row in rows if row["d"]}

    def get_bars(
        self, symbol: str, start: date | None = None, end: date | None = None
    ) -> list[DailyBar]:
        """한 종목의 기간 시세를 날짜 오름차순으로 돌려준다.

        복합 기본키 `(symbol, trade_date)` 가 그대로 쓰이는 조회 패턴이다.
        """
        sql = "SELECT * FROM daily_price WHERE symbol = ?"
        params: list[object] = [symbol]
        if start is not None:
            sql += " AND trade_date >= ?"
            params.append(start.strftime(_DATE_FMT))
        if end is not None:
            sql += " AND trade_date <= ?"
            params.append(end.strftime(_DATE_FMT))
        sql += " ORDER BY trade_date"

        return [_row_to_bar(row) for row in self._conn.execute(sql, params)]

    def get_bars_asof(self, symbols: Sequence[str], as_of: date) -> list[DailyBar]:
        """여러 종목의 **`as_of` 이하** 시세를 한 번의 조회로 가져온다.

        이 메서드가 룩어헤드를 막는 지점이다. 여기서 미래를 잘라 내면, 이후 계산 코드는
        미래를 볼 방법 자체가 없다. 계산 단계에서 조심하는 방식은 언젠가 깨진다.
        (`docs/tech-notes/05-quant-factor-pipeline.md` 3절)

        종목마다 따로 물으면 56번 왕복한다(N+1). 한 번에 가져온다.
        """
        if not symbols:
            return []

        placeholders = ", ".join("?" for _ in symbols)
        sql = (
            f"SELECT * FROM daily_price WHERE symbol IN ({placeholders}) AND trade_date <= ? "
            "ORDER BY symbol, trade_date"
        )
        params: list[object] = [*symbols, as_of.strftime(_DATE_FMT)]
        return [_row_to_bar(row) for row in self._conn.execute(sql, params)]

    def last_trade_date_on_or_before(self, as_of: date) -> date | None:
        """`as_of` 이하의 가장 최근 거래일. 없으면 None.

        휴장일이나 주말을 기준 날짜로 넘겨도 직전 거래일로 맞춰 주기 위한 조회다.
        """
        row = self._conn.execute(
            "SELECT MAX(trade_date) AS d FROM daily_price WHERE trade_date <= ?",
            (as_of.strftime(_DATE_FMT),),
        ).fetchone()
        return _parse_date(row["d"]) if row and row["d"] else None

    def count_bars(self, symbol: str | None = None) -> int:
        """저장된 일봉 수. 종목을 주면 그 종목만."""
        if symbol is None:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM daily_price").fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM daily_price WHERE symbol = ?", (symbol,)
            ).fetchone()
        return int(row["n"])

    def coverage(self) -> list[tuple[str, date, date, int]]:
        """종목별 `(심볼, 최초일, 최종일, 행 수)`. 적재 상태를 눈으로 확인하는 용도."""
        rows = self._conn.execute(
            """
            SELECT symbol, MIN(trade_date) AS lo, MAX(trade_date) AS hi, COUNT(*) AS n
            FROM daily_price
            GROUP BY symbol
            ORDER BY symbol
            """
        ).fetchall()
        return [
            (r["symbol"], _parse_date(r["lo"]), _parse_date(r["hi"]), int(r["n"])) for r in rows
        ]


def _row_to_bar(row: sqlite3.Row) -> DailyBar:
    return DailyBar(
        symbol=row["symbol"],
        trade_date=_parse_date(row["trade_date"]),
        open=row["open"],
        high=row["high"],
        low=row["low"],
        close=row["close"],
        adj_close=row["adj_close"],
        volume=row["volume"],
    )


def _parse_date(value: str) -> date:
    return datetime.strptime(value, _DATE_FMT).date()


def _utc_now_iso() -> str:
    """적재 시각은 UTC 로 기록한다. 로컬 타임존에 의존하면 환경마다 값이 달라진다."""
    return datetime.now(UTC).isoformat(timespec="seconds")
