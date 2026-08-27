"""문서 저장소와 SEC·뉴스 파싱 테스트.

네트워크를 타지 않는다. SEC 응답과 yfinance 뉴스 항목은 실제 형태를 본뜬 고정값을 쓴다.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from dii.collect.filings import FilingCollector, _extract_filings
from dii.collect.http import HttpError
from dii.collect.news import _to_document
from dii.storage import Document, DocumentRepository, DocumentSource, SecurityKind, connect
from dii.storage.sqlite import SqliteStorage


@pytest.fixture
def repository(tmp_path: Path) -> Iterator[DocumentRepository]:
    with connect(tmp_path / "docs.sqlite3") as conn:
        SqliteStorage(conn).upsert_securities(
            [
                ("AAPL", SecurityKind.STOCK, None, "Apple"),
                ("MSFT", SecurityKind.STOCK, None, "Microsoft"),
            ]
        )
        yield DocumentRepository(conn)


def _doc(
    external_id: str = "x1", symbols: tuple[str, ...] = ("AAPL",), **overrides: object
) -> Document:
    base: dict[str, object] = {
        "source": DocumentSource.NEWS,
        "external_id": external_id,
        "doc_type": "Reuters",
        "title": "제목",
        "summary": "요약",
        "url": "https://example.com/a",
        "published_at": datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
        "symbols": symbols,
    }
    return Document(**{**base, **overrides})  # type: ignore[arg-type]


# ------------------------------------------------------------------ 저장소


def test_upsert_is_idempotent(repository: DocumentRepository) -> None:
    """(source, external_id) 가 유일 제약이라 재수집해도 늘지 않는다."""
    repository.upsert_documents([_doc()])
    repository.upsert_documents([_doc()])

    assert repository.count_documents() == 1


def test_upsert_updates_changed_content(repository: DocumentRepository) -> None:
    repository.upsert_documents([_doc(title="원래 제목")])
    repository.upsert_documents([_doc(title="고친 제목")])

    stored = repository.get_documents()
    assert len(stored) == 1
    assert stored[0].title == "고친 제목"


def test_same_article_links_to_multiple_symbols(repository: DocumentRepository) -> None:
    """기사 하나가 여러 종목에 엮인다. 본문을 복사하지 않고 연결만 늘어난다."""
    repository.upsert_documents([_doc(external_id="shared", symbols=("AAPL",))])
    repository.upsert_documents([_doc(external_id="shared", symbols=("MSFT",))])

    assert repository.count_documents() == 1, "문서는 하나뿐"
    assert repository.get_documents(symbol="AAPL")[0].symbols == ("AAPL", "MSFT")
    assert len(repository.get_documents(symbol="MSFT")) == 1


def test_get_documents_is_newest_first(repository: DocumentRepository) -> None:
    repository.upsert_documents(
        [
            _doc(external_id="old", published_at=datetime(2026, 8, 1, tzinfo=UTC)),
            _doc(external_id="new", published_at=datetime(2026, 8, 25, tzinfo=UTC)),
        ]
    )

    assert [d.external_id for d in repository.get_documents()] == ["new", "old"]


def test_get_documents_respects_as_of(repository: DocumentRepository) -> None:
    """가격과 마찬가지로 문서도 기준 시각 이후를 배제할 수 있어야 한다."""
    repository.upsert_documents(
        [
            _doc(external_id="before", published_at=datetime(2026, 8, 1, tzinfo=UTC)),
            _doc(external_id="after", published_at=datetime(2026, 8, 25, tzinfo=UTC)),
        ]
    )

    visible = repository.get_documents(as_of=datetime(2026, 8, 10, tzinfo=UTC))

    assert [d.external_id for d in visible] == ["before"]


def test_get_documents_filters_by_symbol(repository: DocumentRepository) -> None:
    repository.upsert_documents(
        [_doc(external_id="a", symbols=("AAPL",)), _doc(external_id="m", symbols=("MSFT",))]
    )

    assert [d.external_id for d in repository.get_documents(symbol="MSFT")] == ["m"]


def test_naive_datetime_is_rejected(repository: DocumentRepository) -> None:
    """타임존 없는 시각을 저장하면 문자열 비교가 시간 비교와 어긋난다."""
    with pytest.raises(ValueError, match="타임존"):
        repository.upsert_documents([_doc(published_at=datetime(2026, 8, 20, 12, 0))])


def test_empty_write_is_a_noop(repository: DocumentRepository) -> None:
    assert repository.upsert_documents([]) == (0, 0)


def test_validation_catches_missing_fields() -> None:
    assert _doc(title="  ").validation_error() == "제목이 비었다"
    assert _doc(symbols=()).validation_error() == "엮인 종목이 없다"
    assert _doc(published_at=datetime(2026, 1, 1)).validation_error() is not None


# ------------------------------------------------------------------ SEC 파싱

#: 실제 응답 형태를 본뜬 고정값. `filings.recent` 는 열 지향이다 — 레코드 배열이 아니라
#: 같은 길이의 배열 묶음이고, 같은 인덱스끼리 짝을 이룬다.
_SUBMISSIONS: dict[str, object] = {
    "name": "Apple Inc.",
    "filings": {
        "recent": {
            "form": ["8-K", "4", "10-Q", "8-K"],
            "filingDate": ["2026-08-20", "2026-08-19", "2026-07-31", "2025-01-15"],
            "accessionNumber": [
                "0000320193-26-000080",
                "0000320193-26-000079",
                "0000320193-26-000070",
                "0000320193-25-000005",
            ],
            "primaryDocument": ["a8k.htm", "f4.xml", "a10q.htm", "old8k.htm"],
            "primaryDocDescription": ["8-K", "FORM 4", "10-Q", "8-K"],
        }
    },
}


def _filings(cutoff: date = date(2026, 3, 1)) -> list[Document]:
    from dii.collect.filings import DEFAULT_FORM_TYPES

    return _extract_filings("AAPL", 320193, _SUBMISSIONS, DEFAULT_FORM_TYPES, cutoff)


def test_extract_filings_keeps_only_wanted_forms() -> None:
    """Form 4(내부자 거래)는 건수가 압도적이라 수집 단계에서 거른다."""
    forms = [d.doc_type for d in _filings()]

    assert "4" not in forms
    assert set(forms) == {"8-K", "10-Q"}


def test_extract_filings_respects_cutoff() -> None:
    ids = [d.external_id for d in _filings()]

    assert "0000320193-25-000005" not in ids, "2025-01-15 는 기준일 이전"
    assert len(ids) == 2


def test_extract_filings_builds_archive_url() -> None:
    """원문 링크는 accession number 의 하이픈을 뗀 경로로 만들어진다."""
    doc = next(d for d in _filings() if d.doc_type == "10-Q")

    assert doc.url == ("https://www.sec.gov/Archives/edgar/data/320193/000032019326000070/a10q.htm")


def test_extract_filings_uses_midnight_utc() -> None:
    """공시는 날짜만 준다. 뉴스의 타임스탬프와 한 테이블에 넣으려면 규약이 필요하다."""
    doc = _filings()[0]

    assert doc.published_at == datetime(2026, 8, 20, tzinfo=UTC)
    assert doc.published_at.tzinfo is UTC


def test_extract_filings_survives_malformed_payload() -> None:
    """응답 형태가 달라져도 예외로 배치를 죽이지 않는다."""
    from dii.collect.filings import DEFAULT_FORM_TYPES

    malformed: list[dict[str, object]] = [{}, {"filings": None}, {"filings": {"recent": []}}]
    for payload in malformed:
        assert _extract_filings("AAPL", 1, payload, DEFAULT_FORM_TYPES, date(2020, 1, 1)) == []


def test_collector_marks_all_symbols_failed_when_mapping_fails(
    repository: DocumentRepository,
) -> None:
    """티커-CIK 매핑을 못 받으면 개별 종목 문제가 아니므로 전부 실패로 기록한다."""

    class BrokenClient:
        def get_json(self, url: str) -> object:
            raise HttpError("503 Service Unavailable")

    collector = FilingCollector(repository, BrokenClient())  # type: ignore[arg-type]
    result = collector.collect(["AAPL", "MSFT"])

    assert len(result.failed) == 2
    assert result.total_documents == 0


def test_collector_reports_unknown_ticker(repository: DocumentRepository) -> None:
    class StubClient:
        def get_json(self, url: str) -> object:
            return {"0": {"ticker": "AAPL", "cik_str": 320193}}

    collector = FilingCollector(repository, StubClient())  # type: ignore[arg-type]
    result = collector.collect(["ZZZZ"])

    assert result.failed[0].error == "CIK 없음"


# ----------------------------------------------------------------- 뉴스 파싱

_NEWS_ITEM = {
    "id": "abc-123",
    "content": {
        "title": "Apple announces something",
        "summary": "요약 문장",
        "pubDate": "2026-08-27T03:52:08Z",
        "provider": {"displayName": "Benzinga"},
        "canonicalUrl": {"url": "https://example.com/article"},
    },
}


def test_news_item_is_parsed() -> None:
    doc = _to_document("AAPL", _NEWS_ITEM)

    assert doc is not None
    assert doc.external_id == "abc-123"
    assert doc.doc_type == "Benzinga"
    assert doc.published_at == datetime(2026, 8, 27, 3, 52, 8, tzinfo=UTC)
    assert doc.symbols == ("AAPL",)


def test_news_item_accepts_legacy_flat_shape() -> None:
    """yfinance 는 응답 형태를 바꿔 왔다. 옛 형태도 받아 수집이 통째로 멈추지 않게 한다."""
    legacy = {
        "uuid": "old-1",
        "title": "Legacy headline",
        "publisher": "Yahoo",
        "link": "https://example.com/legacy",
        "providerPublishTime": 1_756_000_000,
    }

    doc = _to_document("AAPL", legacy)

    assert doc is not None
    assert doc.external_id == "old-1"
    assert doc.doc_type == "Yahoo"
    assert doc.url == "https://example.com/legacy"


def test_news_item_without_title_is_dropped() -> None:
    assert _to_document("AAPL", {"id": "x", "content": {"title": ""}}) is None


def test_news_item_without_parsable_time_is_dropped() -> None:
    item = {"id": "x", "content": {"title": "T", "pubDate": "언제였더라"}}

    assert _to_document("AAPL", item) is None


# ------------------------------------------------------------------ 표 출력


def test_cell_truncates_overlong_values() -> None:
    """칸보다 긴 값은 잘린다. 자르지 않으면 그 줄만 뒤 컬럼이 밀려 표가 깨진다."""
    from dii.cli import _cell, _display_width

    cell = _cell("Investor's Business Daily", 12, "<")

    assert _display_width(cell) == 12
    assert cell.endswith("…") or cell.rstrip().endswith("…")


def test_cell_counts_east_asian_width() -> None:
    """한글은 터미널에서 두 칸을 차지한다. len() 으로 맞추면 어긋난다."""
    from dii.cli import _cell, _display_width

    assert _display_width("정보기술") == 8
    assert _display_width(_cell("정보기술", 12, "<")) == 12


def test_row_separator_keeps_columns_apart() -> None:
    """우측정렬 값은 칸의 오른쪽 끝에 붙으므로 구분자가 없으면 다음 컬럼과 맞닿는다."""
    from dii.cli import _row

    joined = _row(("가", "60"), (4, 2), "<>")
    separated = _row(("가", "60"), (4, 2), "<>", sep="  ")

    assert joined == "가  60"
    assert separated == "가    60"
