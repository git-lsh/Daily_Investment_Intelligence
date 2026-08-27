"""뉴스 수집.

yfinance 의 내장 뉴스 기능을 쓴다. API 키가 없고 이미 의존성에 있다.
종목당 10건 내외의 **최근** 기사만 주므로, 과거를 소급해 채울 수는 없다.
매일 돌려 이력을 쌓는 것으로 메운다.
(`docs/tech-notes/06-data-collection-sec-edgar.md` 2절)
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import yfinance as yf

from dii.logging_setup import get_logger
from dii.storage.models import Document, DocumentSource
from dii.storage.sqlite import DocumentRepository

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class NewsOutcome:
    symbol: str
    documents: int = 0
    skipped: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(slots=True)
class NewsResult:
    outcomes: list[NewsOutcome] = field(default_factory=list)

    @property
    def succeeded(self) -> list[NewsOutcome]:
        return [o for o in self.outcomes if o.ok]

    @property
    def failed(self) -> list[NewsOutcome]:
        return [o for o in self.outcomes if not o.ok]

    @property
    def total_documents(self) -> int:
        return sum(o.documents for o in self.outcomes)

    @property
    def total_skipped(self) -> int:
        return sum(o.skipped for o in self.outcomes)


class NewsCollector:
    """유니버스 종목의 최근 뉴스를 받아 저장한다."""

    def __init__(self, repository: DocumentRepository) -> None:
        self._repository = repository

    def collect(self, symbols: Sequence[str]) -> NewsResult:
        result = NewsResult()
        for symbol in symbols:
            result.outcomes.append(self._collect_symbol(symbol))
        return result

    def _collect_symbol(self, symbol: str) -> NewsOutcome:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                items = yf.Ticker(symbol).news
        # 외부 경계다. 무엇이 오든 배치 전체를 죽이지 않는다.
        except Exception as exc:
            logger.warning("[%s] 뉴스 조회 실패: %s", symbol, exc)
            return NewsOutcome(symbol=symbol, error=f"{type(exc).__name__}: {exc}")

        if not items:
            logger.debug("[%s] 뉴스 없음", symbol)
            return NewsOutcome(symbol=symbol)

        documents: list[Document] = []
        skipped = 0
        for item in items:
            document = _to_document(symbol, item)
            if document is None:
                skipped += 1
                continue
            problem = document.validation_error()
            if problem is not None:
                logger.debug("[%s] 기사 건너뜀 — %s", symbol, problem)
                skipped += 1
                continue
            documents.append(document)

        if not documents:
            return NewsOutcome(symbol=symbol, skipped=skipped)

        stored, _ = self._repository.upsert_documents(documents)
        logger.debug("[%s] 뉴스 %d건 저장 (%d건 건너뜀)", symbol, stored, skipped)
        return NewsOutcome(symbol=symbol, documents=stored, skipped=skipped)


def _to_document(symbol: str, item: dict[str, Any]) -> Document | None:
    """yfinance 뉴스 항목을 `Document` 로 바꾼다. 형태가 다르면 None.

    응답 형태가 버전에 따라 바뀌어 왔다. 현재는 `{"id": ..., "content": {...}}` 이지만
    과거에는 평평한 딕셔너리였다. **둘 다 받아 준다** — 라이브러리가 형태를 바꿔도
    수집이 통째로 멈추지 않게 하기 위함이다.
    """
    nested = item.get("content")
    content: dict[str, Any] = nested if isinstance(nested, dict) else item

    external_id = str(item.get("id") or content.get("uuid") or "").strip()
    title = str(content.get("title") or "").strip()
    if not external_id or not title:
        return None

    published = _parse_published(content)
    if published is None:
        return None

    return Document(
        source=DocumentSource.NEWS,
        external_id=external_id,
        doc_type=_publisher(content),
        title=title,
        summary=(str(content.get("summary")).strip() or None) if content.get("summary") else None,
        url=_url(content),
        published_at=published,
        symbols=(symbol,),
    )


def _parse_published(content: dict[str, Any]) -> datetime | None:
    """발행 시각을 UTC datetime 으로. ISO 문자열과 유닉스 초 둘 다 처리한다."""
    raw = content.get("pubDate") or content.get("displayTime")
    if isinstance(raw, str) and raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            return None

    epoch = content.get("providerPublishTime")
    if isinstance(epoch, int | float):
        return datetime.fromtimestamp(float(epoch), tz=UTC)
    return None


def _publisher(content: dict[str, Any]) -> str | None:
    provider = content.get("provider")
    if isinstance(provider, dict):
        name = provider.get("displayName")
        return str(name) if name else None
    return str(content["publisher"]) if content.get("publisher") else None


def _url(content: dict[str, Any]) -> str:
    for key in ("canonicalUrl", "clickThroughUrl"):
        value = content.get(key)
        if isinstance(value, dict) and value.get("url"):
            return str(value["url"])
        if isinstance(value, str) and value:
            return value
    return str(content.get("link") or "")
