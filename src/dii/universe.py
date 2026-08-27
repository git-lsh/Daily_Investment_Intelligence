"""분석 유니버스 로딩.

유니버스(무엇을 수집하고 분석할 것인가)는 코드가 아니라 `config/universe.toml` 에 있다.
대상을 바꾸는 일이 코드 변경이 되면 안 되기 때문이다.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from dii.config import PROJECT_ROOT

DEFAULT_UNIVERSE_PATH: Path = PROJECT_ROOT / "config" / "universe.toml"

#: 이 로더가 이해하는 설정 파일 형식 버전. 파일 쪽이 더 높으면 읽기를 거부한다.
SUPPORTED_SCHEMA_VERSION = 1


class UniverseError(Exception):
    """유니버스 설정을 읽지 못했을 때."""


@dataclass(frozen=True, slots=True)
class Sector:
    """하나의 섹터와 그 섹터를 대표하는 ETF, 소속 종목들."""

    etf: str
    name: str
    name_ko: str
    tickers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Universe:
    """수집·분석 대상 전체."""

    benchmark: str
    benchmark_name: str
    sectors: tuple[Sector, ...]

    @property
    def sector_etfs(self) -> tuple[str, ...]:
        return tuple(sector.etf for sector in self.sectors)

    @property
    def stocks(self) -> tuple[str, ...]:
        return tuple(ticker for sector in self.sectors for ticker in sector.tickers)

    @property
    def all_symbols(self) -> tuple[str, ...]:
        """수집해야 하는 전체 심볼. 벤치마크 → 섹터 ETF → 개별 종목 순."""
        return (self.benchmark, *self.sector_etfs, *self.stocks)

    def sector_of(self, ticker: str) -> str | None:
        """종목이 속한 섹터 ETF 를 돌려준다. 종목이 아니면 None."""
        for sector in self.sectors:
            if ticker in sector.tickers:
                return sector.etf
        return None


def load_universe(path: Path | None = None) -> Universe:
    """유니버스 설정 파일을 읽어 검증한다.

    설정이 잘못되어 있으면 수집을 시작하기 전에 실패시킨다. 절반쯤 수집한 뒤
    "그 종목은 오타였다"를 알게 되는 것이 가장 나쁜 시나리오이기 때문이다.
    """
    path = path or DEFAULT_UNIVERSE_PATH
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise UniverseError(f"유니버스 설정 파일이 없다: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise UniverseError(f"유니버스 설정 파일을 파싱할 수 없다: {path} — {exc}") from exc

    version = raw.get("schema_version")
    if version != SUPPORTED_SCHEMA_VERSION:
        raise UniverseError(
            f"지원하지 않는 schema_version {version!r} (지원: {SUPPORTED_SCHEMA_VERSION})"
        )

    try:
        benchmark_raw = raw["benchmark"]
        benchmark = str(benchmark_raw["ticker"]).strip().upper()
        benchmark_name = str(benchmark_raw.get("name", benchmark))
        sectors_raw = raw["sectors"]
    except KeyError as exc:
        raise UniverseError(f"유니버스 설정에 필수 항목이 없다: {exc}") from exc

    if not sectors_raw:
        raise UniverseError("섹터가 하나도 정의되어 있지 않다")

    sectors: list[Sector] = []
    for entry in sectors_raw:
        try:
            tickers = tuple(str(t).strip().upper() for t in entry["tickers"])
            sectors.append(
                Sector(
                    etf=str(entry["etf"]).strip().upper(),
                    name=str(entry["name"]),
                    name_ko=str(entry.get("name_ko", entry["name"])),
                    tickers=tickers,
                )
            )
        except KeyError as exc:
            raise UniverseError(f"섹터 정의에 필수 항목이 없다: {exc}") from exc
        if not tickers:
            raise UniverseError(f"섹터 {entry['etf']!r} 에 종목이 없다")

    universe = Universe(benchmark=benchmark, benchmark_name=benchmark_name, sectors=tuple(sectors))

    symbols = universe.all_symbols
    duplicates = sorted({s for s in symbols if symbols.count(s) > 1})
    if duplicates:
        raise UniverseError(f"심볼이 중복되어 있다: {', '.join(duplicates)}")

    return universe
