"""명령줄 진입점.

파이프라인의 각 단계는 여기에 하위 명령으로 붙는다.

로깅 설정은 **이 모듈에서만** 수행한다. (`logging_setup` 모듈 설명 참고)
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from dii import __version__
from dii.collect import PriceCollector
from dii.config import PROJECT_ROOT, Settings, get_settings
from dii.logging_setup import get_logger, setup_logging
from dii.storage import SecurityKind, SqliteStorage, connect
from dii.universe import Universe, UniverseError, load_universe

logger = get_logger(__name__)

# 수집이 부분적으로 실패했을 때의 종료 코드. 성공(0)과도, 완전 실패(1)와도 구분한다.
# 스케줄러나 CI 가 "일부만 실패"를 다르게 다룰 수 있게 하기 위함이다.
EXIT_PARTIAL_FAILURE = 2


def _configure_console_encoding() -> None:
    """콘솔 출력 스트림을 UTF-8 로 고정한다.

    Windows 의 기본 콘솔 인코딩은 시스템 로캘(한국어 환경이면 cp949)이다. 이 프로젝트의
    로그와 리포트에는 한글이 들어가므로, 그대로 두면 터미널·파일 리다이렉트·CI 로그에서
    글자가 깨진다. 진입점에서 한 번 UTF-8 로 맞춰 두어 실행 환경에 관계없이 같게 보이게 한다.

    `errors="backslashreplace"` 로 두어, 인코딩 불가 문자가 있어도 예외로 파이프라인을
    멈추지 않고 이스케이프 표기로 흘려보낸다. 로깅이 작업을 죽이면 안 된다.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")


def _cmd_config(settings: Settings) -> int:
    """해석된 설정값을 출력한다. 환경변수 주입이 의도대로 됐는지 확인하는 용도."""
    settings.ensure_directories()

    rows: list[tuple[str, str]] = [
        ("version", __version__),
        ("project_root", str(PROJECT_ROOT)),
        ("app_env", settings.app_env.value),
        ("log_level", settings.log_level),
        ("data_dir", str(settings.data_dir)),
        ("db_path", str(settings.db_path)),
    ]
    width = max(len(key) for key, _ in rows)
    for key, value in rows:
        print(f"{key:<{width}}  {value}")

    logger.debug("설정 출력 완료")
    return 0


def _register_securities(storage: SqliteStorage, universe: Universe) -> None:
    """유니버스의 종목 메타데이터를 저장소에 반영한다.

    가격 테이블이 종목을 외래키로 참조하므로 수집보다 먼저 실행해야 한다.
    """
    rows: list[tuple[str, SecurityKind, str | None, str | None]] = [
        (universe.benchmark, SecurityKind.BENCHMARK, None, universe.benchmark_name)
    ]
    for sector in universe.sectors:
        rows.append((sector.etf, SecurityKind.SECTOR_ETF, None, sector.name))
    for sector in universe.sectors:
        rows.extend((ticker, SecurityKind.STOCK, sector.etf, None) for ticker in sector.tickers)

    count = storage.upsert_securities(rows)
    logger.info("종목 메타데이터 %d건 반영", count)


def _cmd_collect(settings: Settings, args: argparse.Namespace) -> int:
    """유니버스의 일봉을 수집해 저장한다."""
    try:
        universe = load_universe()
    except UniverseError as exc:
        logger.error("유니버스 설정을 읽을 수 없다: %s", exc)
        return 1

    symbols = list(args.symbols) if args.symbols else list(universe.all_symbols)
    logger.info("수집 시작 — 대상 %d 심볼", len(symbols))

    settings.ensure_directories()
    with connect(settings.db_path) as conn:
        storage = SqliteStorage(conn)
        _register_securities(storage, universe)

        collector = PriceCollector(storage, overlap_days=args.overlap_days)
        result = collector.collect(symbols)

        total_rows = storage.count_bars()

    print(
        f"수집 완료: 성공 {len(result.succeeded)}/{len(symbols)} 심볼, "
        f"{result.rows_written}행 기록, {result.rows_rejected}행 거부, "
        f"DB 총 {total_rows}행"
    )

    if result.failed:
        print(f"실패 {len(result.failed)}건:")
        for outcome in result.failed:
            print(f"  {outcome.symbol:<6} {outcome.error}")

    if result.is_complete_failure:
        logger.error("전 종목 수집 실패 — 네트워크나 데이터 소스 문제일 가능성이 높다")
        return 1
    if result.failed:
        return EXIT_PARTIAL_FAILURE
    return 0


def _cmd_status(settings: Settings) -> int:
    """저장소에 무엇이 얼마나 들어 있는지 보여준다."""
    if not settings.db_path.exists():
        print(f"DB 가 아직 없다: {settings.db_path}")
        print("`dii collect` 를 먼저 실행한다.")
        return 1

    with connect(settings.db_path) as conn:
        rows = SqliteStorage(conn).coverage()

    if not rows:
        print("저장된 일봉이 없다.")
        return 1

    print(f"{'심볼':<8} {'최초':<12} {'최종':<12} {'행수':>7}")
    print("-" * 42)
    for symbol, first, last, count in rows:
        print(f"{symbol:<8} {first.isoformat():<12} {last.isoformat():<12} {count:>7}")
    print("-" * 42)
    print(f"{len(rows)} 심볼, 총 {sum(r[3] for r in rows)}행")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dii",
        description="Daily Investment Intelligence — 투자 리서치 파이프라인",
    )
    parser.add_argument("--version", action="version", version=f"dii {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("config", help="해석된 설정값을 출력한다")

    collect = subparsers.add_parser("collect", help="유니버스의 일봉을 수집해 저장한다")
    collect.add_argument(
        "symbols",
        nargs="*",
        help="수집할 심볼. 생략하면 유니버스 전체를 받는다",
    )
    collect.add_argument(
        "--overlap-days",
        type=int,
        default=7,
        help="증분 수집 시 마지막 저장일보다 며칠 앞에서부터 다시 받을지 (기본: 7). "
        "배당·분할로 수정 종가가 소급 변경되는 것을 흡수한다",
    )

    subparsers.add_parser("status", help="저장소 적재 현황을 보여준다")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 진입점. 종료 코드를 반환한다."""
    _configure_console_encoding()
    args = build_parser().parse_args(argv)

    settings = get_settings()
    setup_logging(settings)

    if args.command == "config":
        return _cmd_config(settings)
    if args.command == "collect":
        return _cmd_collect(settings, args)
    if args.command == "status":
        return _cmd_status(settings)

    # argparse 가 required=True 로 막아 주므로 여기 도달하지 않는다.
    raise AssertionError(f"처리되지 않은 명령: {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
