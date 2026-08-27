from __future__ import annotations

from pathlib import Path

import pytest

from dii.universe import UniverseError, load_universe

_MINIMAL = """
schema_version = 1

[benchmark]
ticker = "spy"
name = "S&P 500"

[[sectors]]
etf = "xlk"
name = "Information Technology"
name_ko = "정보기술"
tickers = ["aapl", "msft"]
"""


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "universe.toml"
    path.write_text(content, encoding="utf-8")
    return path


def test_symbols_are_normalized_to_upper_case(tmp_path: Path) -> None:
    """설정 파일에 소문자로 적혀 있어도 심볼은 대문자로 통일된다."""
    universe = load_universe(_write(tmp_path, _MINIMAL))

    assert universe.benchmark == "SPY"
    assert universe.sector_etfs == ("XLK",)
    assert universe.stocks == ("AAPL", "MSFT")


def test_all_symbols_ordering(tmp_path: Path) -> None:
    universe = load_universe(_write(tmp_path, _MINIMAL))
    assert universe.all_symbols == ("SPY", "XLK", "AAPL", "MSFT")


def test_sector_of(tmp_path: Path) -> None:
    universe = load_universe(_write(tmp_path, _MINIMAL))

    assert universe.sector_of("AAPL") == "XLK"
    assert universe.sector_of("SPY") is None, "벤치마크는 어느 섹터에도 속하지 않는다"


def test_real_universe_file_is_valid() -> None:
    """저장소에 커밋된 실제 유니버스 파일이 로더를 통과하는지.

    설정 파일을 손으로 고치다 깨뜨렸을 때 테스트가 잡아 준다.
    """
    universe = load_universe()

    assert len(universe.sectors) == 11
    assert len(universe.stocks) == 44
    assert len(universe.all_symbols) == 56
    counts = {len(sector.tickers) for sector in universe.sectors}
    assert counts == {4}, "섹터당 종목 수가 같아야 횡단면 비교가 성립한다"


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(UniverseError, match="없다"):
        load_universe(tmp_path / "nope.toml")


def test_unsupported_schema_version_raises(tmp_path: Path) -> None:
    """형식이 바뀐 파일을 조용히 잘못 읽는 것보다 실패하는 편이 낫다."""
    with pytest.raises(UniverseError, match="schema_version"):
        load_universe(
            _write(tmp_path, _MINIMAL.replace("schema_version = 1", "schema_version = 99"))
        )


def test_duplicate_symbol_raises(tmp_path: Path) -> None:
    """같은 종목이 두 섹터에 들어가면 소속이 모호해지므로 막는다."""
    content = (
        _MINIMAL
        + """
[[sectors]]
etf = "XLF"
name = "Financials"
name_ko = "금융"
tickers = ["AAPL"]
"""
    )
    with pytest.raises(UniverseError, match="중복"):
        load_universe(_write(tmp_path, content))


def test_sector_without_tickers_raises(tmp_path: Path) -> None:
    with pytest.raises(UniverseError, match="종목이 없다"):
        load_universe(_write(tmp_path, _MINIMAL.replace('["aapl", "msft"]', "[]")))
