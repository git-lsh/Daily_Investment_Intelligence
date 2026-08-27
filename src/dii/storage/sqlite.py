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
from dii.storage.models import DailyBar, Document, DocumentSource, SecurityKind
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


class DocumentRepository:
    """뉴스·공시 문서를 다루는 리포지토리.

    가격과 분리한 이유: 조회 패턴도 수명주기도 다르다. M3 에서 임베딩과 검색이 붙는 곳은
    이쪽뿐이므로, 경계를 나눠 두면 PostgreSQL 이전 시 영향 범위가 좁아진다.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert_documents(self, documents: Iterable[Document]) -> tuple[int, int]:
        """문서를 저장하고 종목과 연결한다.

        `(source, external_id)` 가 이미 있으면 내용을 갱신한다. 재수집해도 중복되지 않는다.

        Returns:
            `(저장한 문서 수, 만든 연결 수)`
        """
        now = _utc_now_iso()
        documents = list(documents)
        if not documents:
            return 0, 0

        links = 0
        with self._conn:  # 문서와 연결이 함께 반영되거나 함께 취소된다
            for doc in documents:
                cursor = self._conn.execute(
                    """
                    INSERT INTO document
                        (source, external_id, doc_type, title, summary, url,
                         published_at, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source, external_id) DO UPDATE SET
                        doc_type     = excluded.doc_type,
                        title        = excluded.title,
                        summary      = excluded.summary,
                        url          = excluded.url,
                        published_at = excluded.published_at,
                        fetched_at   = excluded.fetched_at
                    RETURNING id
                    """,
                    (
                        doc.source.value,
                        doc.external_id,
                        doc.doc_type,
                        doc.title,
                        doc.summary,
                        doc.url,
                        _to_utc_iso(doc.published_at),
                        now,
                    ),
                )
                document_id = cursor.fetchone()["id"]

                # 연결은 INSERT OR IGNORE. 이미 있으면 그대로 두면 된다.
                self._conn.executemany(
                    "INSERT OR IGNORE INTO document_symbol (document_id, symbol) VALUES (?, ?)",
                    [(document_id, symbol) for symbol in doc.symbols],
                )
                links += len(doc.symbols)

        return len(documents), links

    def get_documents(
        self,
        *,
        symbol: str | None = None,
        since: datetime | None = None,
        as_of: datetime | None = None,
        limit: int = 100,
    ) -> list[Document]:
        """문서를 최신순으로 조회한다.

        Args:
            symbol: 이 종목과 엮인 문서만.
            since: 이 시각 이후 발행분만.
            as_of: **이 시각 이하 발행분만.** 가격과 마찬가지로 룩어헤드를 막는 지점이다.
            limit: 최대 건수.
        """
        sql = [
            "SELECT d.* FROM document d",
        ]
        params: list[object] = []
        where: list[str] = []

        if symbol is not None:
            sql.append("JOIN document_symbol ds ON ds.document_id = d.id")
            where.append("ds.symbol = ?")
            params.append(symbol)
        if since is not None:
            where.append("d.published_at >= ?")
            params.append(_to_utc_iso(since))
        if as_of is not None:
            where.append("d.published_at <= ?")
            params.append(_to_utc_iso(as_of))

        if where:
            sql.append("WHERE " + " AND ".join(where))
        sql.append("ORDER BY d.published_at DESC LIMIT ?")
        params.append(limit)

        rows = self._conn.execute("\n".join(sql), params).fetchall()
        return [self._row_to_document(row) for row in rows]

    def count_documents(self, source: DocumentSource | None = None) -> int:
        if source is None:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM document").fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM document WHERE source = ?", (source.value,)
            ).fetchone()
        return int(row["n"])

    def document_coverage(self) -> list[tuple[str, str, int, str, str]]:
        """`(소스, 서식/발행처, 건수, 최초 발행일, 최종 발행일)`. 적재 현황 확인용."""
        rows = self._conn.execute(
            """
            SELECT source, COALESCE(doc_type, '(없음)') AS kind, COUNT(*) AS n,
                   MIN(published_at) AS lo, MAX(published_at) AS hi
            FROM document
            GROUP BY source, kind
            ORDER BY source, n DESC
            """
        ).fetchall()
        return [(r["source"], r["kind"], int(r["n"]), r["lo"][:10], r["hi"][:10]) for r in rows]

    def _row_to_document(self, row: sqlite3.Row) -> Document:
        symbols = self._conn.execute(
            "SELECT symbol FROM document_symbol WHERE document_id = ? ORDER BY symbol",
            (row["id"],),
        ).fetchall()
        return Document(
            source=DocumentSource(row["source"]),
            external_id=row["external_id"],
            doc_type=row["doc_type"],
            title=row["title"],
            summary=row["summary"],
            url=row["url"],
            published_at=datetime.fromisoformat(row["published_at"]),
            symbols=tuple(s["symbol"] for s in symbols),
        )


def _to_utc_iso(moment: datetime) -> str:
    """UTC ISO 8601 문자열로 정규화한다.

    타임존을 섞어 저장하면 문자열 비교가 시간 비교와 어긋난다.
    저장 시점에 UTC 로 맞춰 두면 정렬과 범위 조회가 사전순으로 동작한다.
    """
    if moment.tzinfo is None:
        raise ValueError(f"타임존 없는 datetime 은 저장할 수 없다: {moment!r}")
    return moment.astimezone(UTC).isoformat(timespec="seconds")
