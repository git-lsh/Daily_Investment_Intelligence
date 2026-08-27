"""SEC EDGAR 공시 수집.

공시 원문은 저장하지 않는다. 어떤 서식이 언제 나왔는지와 원문 링크만 보관한다.
(`docs/tech-notes/06-data-collection-sec-edgar.md`)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from dii.collect.http import HttpError, RateLimitedClient
from dii.logging_setup import get_logger
from dii.storage.models import Document, DocumentSource
from dii.storage.sqlite import DocumentRepository

logger = get_logger(__name__)

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
FILING_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"

#: 수집할 서식. Form 4(내부자 거래)는 건수가 압도적인데 개별 임원의 소량 매매까지 잡혀
#: 신호 대 잡음비가 낮다. 수집 단계에서 걸러야 뒷단의 검색과 Agent 가 쉬워진다.
DEFAULT_FORM_TYPES: frozenset[str] = frozenset(
    {
        "8-K",  # 수시공시 — 이 프로젝트가 가장 관심 있는 종류
        "10-Q",  # 분기 보고서
        "10-K",  # 연간 보고서
        "8-K/A",
        "10-Q/A",
        "10-K/A",
    }
)

#: 기본 수집 기간. 처음 실행 시 이만큼 거슬러 올라간다.
DEFAULT_LOOKBACK_DAYS = 180


@dataclass(frozen=True, slots=True)
class FilingOutcome:
    symbol: str
    documents: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(slots=True)
class FilingResult:
    outcomes: list[FilingOutcome] = field(default_factory=list)

    @property
    def succeeded(self) -> list[FilingOutcome]:
        return [o for o in self.outcomes if o.ok]

    @property
    def failed(self) -> list[FilingOutcome]:
        return [o for o in self.outcomes if not o.ok]

    @property
    def total_documents(self) -> int:
        return sum(o.documents for o in self.outcomes)


class FilingCollector:
    """SEC EDGAR 에서 공시 목록을 받아 저장한다."""

    def __init__(
        self,
        repository: DocumentRepository,
        client: RateLimitedClient,
        *,
        form_types: frozenset[str] = DEFAULT_FORM_TYPES,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    ) -> None:
        self._repository = repository
        self._client = client
        self._form_types = form_types
        self._lookback_days = lookback_days
        self._ticker_to_cik: dict[str, int] | None = None

    def collect(self, symbols: Sequence[str], *, today: date | None = None) -> FilingResult:
        """주어진 종목들의 최근 공시를 받아 저장한다.

        Args:
            symbols: 대상 티커.
            today: 기준 날짜. 테스트에서 주입한다.
        """
        cutoff = (today or datetime.now(UTC).date()) - timedelta(days=self._lookback_days)
        result = FilingResult()

        try:
            mapping = self._load_ticker_map()
        except HttpError as exc:
            logger.error("티커-CIK 매핑을 받지 못했다: %s", exc)
            reason = f"매핑 조회 실패: {exc}"
            result.outcomes = [FilingOutcome(symbol=s, error=reason) for s in symbols]
            return result

        for symbol in symbols:
            result.outcomes.append(self._collect_symbol(symbol, mapping, cutoff))

        return result

    # ------------------------------------------------------------------ 내부

    def _load_ticker_map(self) -> dict[str, int]:
        """티커 -> CIK. 프로세스 단위로 한 번만 받는다 (1만여 건의 정적 파일)."""
        if self._ticker_to_cik is None:
            payload = self._client.get_json(TICKER_MAP_URL)
            self._ticker_to_cik = {
                str(entry["ticker"]).upper(): int(entry["cik_str"]) for entry in payload.values()
            }
            logger.info("티커-CIK 매핑 %d건 적재", len(self._ticker_to_cik))
        return self._ticker_to_cik

    def _collect_symbol(self, symbol: str, mapping: dict[str, int], cutoff: date) -> FilingOutcome:
        cik = mapping.get(symbol.upper())
        if cik is None:
            logger.warning("[%s] SEC 에 등록된 CIK 를 찾을 수 없다", symbol)
            return FilingOutcome(symbol=symbol, error="CIK 없음")

        try:
            payload = self._client.get_json(SUBMISSIONS_URL.format(cik=cik))
        except HttpError as exc:
            logger.warning("[%s] 제출 이력 조회 실패: %s", symbol, exc)
            return FilingOutcome(symbol=symbol, error=str(exc))

        documents = list(_extract_filings(symbol, cik, payload, self._form_types, cutoff))
        if not documents:
            logger.debug("[%s] %s 이후 대상 공시 없음", symbol, cutoff.isoformat())
            return FilingOutcome(symbol=symbol)

        stored, _ = self._repository.upsert_documents(documents)
        logger.debug("[%s] 공시 %d건 저장", symbol, stored)
        return FilingOutcome(symbol=symbol, documents=stored)


def _extract_filings(
    symbol: str,
    cik: int,
    payload: dict[str, object],
    form_types: frozenset[str],
    cutoff: date,
) -> list[Document]:
    """제출 이력 응답에서 대상 공시를 뽑아낸다.

    응답의 `filings.recent` 는 **열 지향**이다. `form`, `filingDate`, `accessionNumber` 가
    각각 같은 길이의 배열이고 같은 인덱스끼리 짝을 이룬다. 레코드 배열이 아니다.
    """
    filings = payload.get("filings")
    if not isinstance(filings, dict):
        return []
    recent = filings.get("recent")
    if not isinstance(recent, dict):
        return []

    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    descriptions = recent.get("primaryDocDescription", [])

    # 열 길이가 어긋나면 짝이 틀어진다. 가장 짧은 것에 맞춰 조용히 잘라낸다.
    count = min(len(forms), len(dates), len(accessions))
    documents: list[Document] = []

    for index in range(count):
        form = str(forms[index])
        if form not in form_types:
            continue

        try:
            filed_on = date.fromisoformat(str(dates[index]))
        except ValueError:
            continue
        if filed_on < cutoff:
            # 응답은 최신순이므로 여기서 멈춰도 되지만, 정렬을 가정하지 않고 계속 훑는다.
            continue

        accession = str(accessions[index])
        primary = str(primary_docs[index]) if index < len(primary_docs) else ""
        description = str(descriptions[index]) if index < len(descriptions) else ""

        documents.append(
            Document(
                source=DocumentSource.SEC,
                external_id=accession,
                doc_type=form,
                title=f"{symbol} {form}" + (f" — {description}" if description else ""),
                summary=None,
                url=FILING_URL.format(
                    cik=cik, accession=accession.replace("-", ""), document=primary
                ),
                # 공시는 날짜만 준다. 그날 자정(UTC)으로 통일한다.
                published_at=datetime.combine(filed_on, time.min, tzinfo=UTC),
                symbols=(symbol,),
            )
        )

    return documents
