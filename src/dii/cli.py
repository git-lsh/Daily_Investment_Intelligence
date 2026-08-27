"""명령줄 진입점.

파이프라인의 각 단계는 여기에 하위 명령으로 붙는다.

로깅 설정은 **이 모듈에서만** 수행한다. (`logging_setup` 모듈 설명 참고)
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from collections.abc import Sequence
from datetime import date

from dii import __version__
from dii.collect import FilingCollector, NewsCollector, PriceCollector, RateLimitedClient
from dii.config import PROJECT_ROOT, Settings, get_settings
from dii.logging_setup import get_logger, setup_logging
from dii.processing import load_frames
from dii.processing.frames import InsufficientDataError
from dii.quant import FACTORS, rank_sectors, score_stocks
from dii.quant.scoring import ScoreTable, SectorRanking
from dii.storage import DocumentRepository, SecurityKind, SqliteStorage, connect
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


def _cmd_score(settings: Settings, args: argparse.Namespace) -> int:
    """기준일 시점의 섹터 랭킹과 종목 스코어를 출력한다."""
    try:
        universe = load_universe()
    except UniverseError as exc:
        logger.error("유니버스 설정을 읽을 수 없다: %s", exc)
        return 1

    if not settings.db_path.exists():
        print(f"DB 가 아직 없다: {settings.db_path}")
        print("`dii collect` 를 먼저 실행한다.")
        return 1

    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    sector_of = {t: s.etf for s in universe.sectors for t in s.tickers}

    with connect(settings.db_path) as conn:
        storage = SqliteStorage(conn)
        try:
            frames = load_frames(storage, universe.all_symbols, as_of)
        except InsufficientDataError as exc:
            logger.error("분석할 데이터가 없다: %s", exc)
            return 1

        ranking = rank_sectors(
            frames, [(s.etf, s.name_ko) for s in universe.sectors], universe.benchmark
        )
        table = score_stocks(frames, sector_of, universe=list(universe.stocks))

    _print_sector_ranking(ranking)
    print()
    _print_stock_scores(table, top=args.top)
    return 0


def _cmd_collect_docs(settings: Settings, args: argparse.Namespace) -> int:
    """SEC 공시와 뉴스를 수집해 저장한다."""
    try:
        universe = load_universe()
    except UniverseError as exc:
        logger.error("유니버스 설정을 읽을 수 없다: %s", exc)
        return 1

    explicit = list(args.symbols) if args.symbols else None
    want_sec = args.source in ("all", "sec")
    want_news = args.source in ("all", "news")

    # 뉴스는 섹터 ETF 와 벤치마크에도 붙는다 (시황 기사). 반면 SEC 공시는 개별 기업만 낸다 —
    # ETF 는 신탁 구조라 company_tickers.json 에 CIK 가 없고 8-K/10-Q 도 제출하지 않는다.
    # 전체를 대상으로 두면 매일 12건이 "CIK 없음"으로 실패 보고돼 알람 피로가 생긴다.
    news_symbols = explicit or list(universe.all_symbols)
    sec_symbols = explicit or list(universe.stocks)

    user_agent = ""
    if want_sec:
        try:
            user_agent = settings.require_sec_user_agent()
        except ValueError as exc:
            logger.error("%s", exc)
            return 1

    settings.ensure_directories()
    failures = 0
    total = 0

    with connect(settings.db_path) as conn:
        _register_securities(SqliteStorage(conn), universe)
        repository = DocumentRepository(conn)

        if want_sec:
            logger.info("SEC 공시 수집 시작 — 대상 %d 심볼 (개별 종목만)", len(sec_symbols))
            client = RateLimitedClient(
                user_agent=user_agent, min_interval=settings.sec_min_interval
            )
            collector = FilingCollector(repository, client, lookback_days=args.lookback_days)
            result = collector.collect(sec_symbols)
            total += result.total_documents
            failures += len(result.failed)
            print(
                f"공시: 성공 {len(result.succeeded)}/{len(sec_symbols)} 심볼, "
                f"{result.total_documents}건 저장"
            )
            for outcome in result.failed:
                print(f"  {outcome.symbol:<6} {outcome.error}")

        if want_news:
            logger.info("뉴스 수집 시작 — 대상 %d 심볼", len(news_symbols))
            news_result = NewsCollector(repository).collect(news_symbols)
            total += news_result.total_documents
            failures += len(news_result.failed)
            print(
                f"뉴스: 성공 {len(news_result.succeeded)}/{len(news_symbols)} 심볼, "
                f"{news_result.total_documents}건 저장, {news_result.total_skipped}건 건너뜀"
            )
            for news_outcome in news_result.failed:
                print(f"  {news_outcome.symbol:<6} {news_outcome.error}")

        stored = repository.count_documents()

    print(f"이번 실행 {total}건 처리, DB 총 문서 {stored}건")

    if total == 0 and failures:
        logger.error("한 건도 수집하지 못했다")
        return 1
    return EXIT_PARTIAL_FAILURE if failures else 0


def _cmd_docs(settings: Settings, args: argparse.Namespace) -> int:
    """저장된 문서를 조회한다."""
    if not settings.db_path.exists():
        print(f"DB 가 아직 없다: {settings.db_path}")
        return 1

    with connect(settings.db_path) as conn:
        repository = DocumentRepository(conn)
        if args.symbol:
            documents = repository.get_documents(symbol=args.symbol.upper(), limit=args.limit)
        else:
            documents = repository.get_documents(limit=args.limit)
        coverage = repository.document_coverage()

    if not coverage:
        print("저장된 문서가 없다. `dii collect-docs` 를 먼저 실행한다.")
        return 1

    print("■ 적재 현황")
    widths = (6, 22, 6, 12, 12)
    header = _row(("소스", "종류", "건수", "최초", "최종"), widths, "<<><<", sep="  ")
    print(header)
    print("-" * _display_width(header))
    for source, kind, count, first, last in coverage:
        print(_row((source, kind, str(count), first, last), widths, "<<><<", sep="  "))

    print()
    label = f"{args.symbol.upper()} " if args.symbol else ""
    print(f"■ 최근 {label}문서 {len(documents)}건")
    for doc in documents:
        stamp = doc.published_at.strftime("%Y-%m-%d %H:%M")
        kind = doc.doc_type or "-"
        print(f"  [{stamp}] ({doc.source.value}/{kind}) {doc.title}")
        print(f"      {', '.join(doc.symbols)} — {doc.url}")
    return 0


def _print_sector_ranking(ranking: SectorRanking) -> None:
    print(f"■ 섹터 랭킹 — {ranking.as_of.isoformat()} 기준")
    print(f"  ({ranking.benchmark} 1개월 {_pct(ranking.benchmark_return_1m)} 대비 초과 수익률 순)")
    print()

    widths = (3, 6, 20, 10, 10, 10, 11)
    header = _row(("", "ETF", "섹터", "1주", "1개월", "3개월", "초과(1M)"), widths, "<<<>>>>")
    print(header)
    print("-" * _display_width(header))
    for rank, row in enumerate(ranking.rows, start=1):
        print(
            _row(
                (
                    str(rank),
                    row.etf,
                    row.name_ko,
                    _pct(row.return_1w),
                    _pct(row.return_1m),
                    _pct(row.return_3m),
                    _pct(row.excess_1m),
                ),
                widths,
                "<<<>>>>",
            )
        )


def _print_stock_scores(table: ScoreTable, *, top: int) -> None:
    print(f"■ 종목 스코어 상위 {top} — {table.as_of.isoformat()} 기준")
    print("  (스크리닝 점수다. 매수 신호가 아니라 '무엇을 들여다볼지' 좁히는 용도)")
    print()

    widths = (3, 7, 7, 8, *(14 for _ in FACTORS))
    align = "<<<>" + ">" * len(FACTORS)
    header = _row(("", "심볼", "섹터", "점수", *(spec.name for spec in FACTORS)), widths, align)
    print(header)
    print("-" * _display_width(header))

    for rank, item in enumerate(table.top(top), start=1):
        cells = [f"{item.contributions.get(spec.key, float('nan')):+.2f}" for spec in FACTORS]
        line = _row(
            (str(rank), item.symbol, item.sector_etf or "", f"{item.score:+.2f}", *cells),
            widths,
            align,
        )
        flag = "" if item.coverage == 1.0 else f"  (근거 {item.coverage:.0%})"
        print(f"{line}{flag}")

    print()
    print("  표의 팩터 값은 정규화 후 가중치를 곱한 기여도다. 합이 점수와 같다.")

    if table.skipped:
        print()
        print(f"  점수 보류 {len(table.skipped)}건:")
        for symbol, reason in table.skipped:
            print(f"    {symbol:<6} {reason}")


def _pct(value: float) -> str:
    """수익률을 퍼센트 문자열로. NaN 은 계산 불가를 뜻하므로 하이픈으로 표시한다."""
    return "-" if value != value else f"{value * 100:+.2f}%"


def _display_width(text: str) -> int:
    """터미널에서 차지하는 칸 수.

    한글·한자·가나는 한 글자가 두 칸을 차지한다. `len()` 은 글자 수를 세므로
    한글이 섞인 표를 `f"{s:<10}"` 으로 맞추면 어긋난다. 이 프로젝트의 출력은
    한글 헤더가 기본이므로 폭 계산이 필요하다.
    """
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _cell(text: str, width: int, align: str) -> str:
    """표시 폭 기준으로 칸을 채운다. `align` 은 '<'(왼쪽) 또는 '>'(오른쪽).

    칸보다 긴 값은 말줄임표로 자른다. 자르지 않으면 그 줄만 뒤 컬럼이 통째로 밀려
    표 전체가 읽히지 않는다. 한 값의 온전함보다 표의 정렬이 우선이다.
    """
    if _display_width(text) > width:
        text = _truncate(text, width)
    padding = " " * max(0, width - _display_width(text))
    return text + padding if align == "<" else padding + text


def _truncate(text: str, width: int) -> str:
    """표시 폭이 `width` 를 넘지 않도록 자르고 말줄임표를 붙인다."""
    if width <= 1:
        return "…"[:width]
    limit = width - 1  # 말줄임표가 한 칸을 쓴다
    out: list[str] = []
    used = 0
    for char in text:
        step = 2 if unicodedata.east_asian_width(char) in "WF" else 1
        if used + step > limit:
            break
        out.append(char)
        used += step
    return "".join(out) + "…"


def _row(values: Sequence[str], widths: Sequence[int], aligns: str, *, sep: str = "") -> str:
    """표 한 줄. `sep` 은 컬럼 사이 구분자.

    우측정렬 컬럼은 값이 칸의 오른쪽 끝에 붙으므로, 구분자가 없으면 다음 컬럼과 맞닿는다.
    """
    return sep.join(_cell(v, w, a) for v, w, a in zip(values, widths, aligns, strict=True))


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

    docs_collect = subparsers.add_parser("collect-docs", help="SEC 공시와 뉴스를 수집해 저장한다")
    docs_collect.add_argument("symbols", nargs="*", help="수집할 심볼. 생략하면 유니버스 전체")
    docs_collect.add_argument(
        "--source",
        choices=("all", "sec", "news"),
        default="all",
        help="수집할 소스 (기본: all)",
    )
    docs_collect.add_argument(
        "--lookback-days",
        type=int,
        default=180,
        help="SEC 공시를 며칠 전까지 받을지 (기본: 180). 뉴스는 소스가 최근 것만 준다",
    )

    docs = subparsers.add_parser("docs", help="저장된 문서를 조회한다")
    docs.add_argument("--symbol", help="이 종목과 엮인 문서만")
    docs.add_argument("--limit", type=int, default=20, help="출력할 문서 수 (기본: 20)")

    subparsers.add_parser("status", help="저장소 적재 현황을 보여준다")

    score = subparsers.add_parser("score", help="섹터 랭킹과 종목 스코어를 계산해 출력한다")
    score.add_argument(
        "--as-of",
        metavar="YYYY-MM-DD",
        help="기준 날짜. 생략하면 저장된 마지막 거래일. "
        "거래일이 아니면 직전 거래일로 당겨진다. 그 시점 이후 데이터는 사용하지 않는다",
    )
    score.add_argument("--top", type=int, default=15, help="출력할 종목 수 (기본: 15)")

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
    if args.command == "score":
        return _cmd_score(settings, args)
    if args.command == "collect-docs":
        return _cmd_collect_docs(settings, args)
    if args.command == "docs":
        return _cmd_docs(settings, args)

    # argparse 가 required=True 로 막아 주므로 여기 도달하지 않는다.
    raise AssertionError(f"처리되지 않은 명령: {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
