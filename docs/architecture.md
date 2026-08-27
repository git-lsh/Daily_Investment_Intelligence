# 패키지 레이아웃과 계층 경계

## 디렉토리 구조

```
.
├── src/dii/              # 애플리케이션 패키지 (import 이름은 dii)
├── tests/                # 테스트
├── config/               # 유니버스 등 사람이 고치는 설정 (커밋함)
├── docs/
│   ├── architecture.md   # 이 문서
│   └── tech-notes/       # 기술 스택 학습 로그
├── data/                 # 수집 데이터 · 로컬 DB (커밋하지 않음)
├── pyproject.toml        # 프로젝트 메타데이터 · 의존성 · 도구 설정
├── uv.lock               # 의존성 잠금 (커밋함)
└── .env / .env.example   # 환경별 설정 (.env 는 커밋하지 않음)
```

`src` 레이아웃을 쓴다. 저장소 루트가 `sys.path` 에 자동으로 들어가지 않으므로,
**설치된 패키지를 import 하게 강제**된다. 덕분에 "내 PC 에서는 되는데" 부류의
경로 사고가 테스트 단계에서 미리 드러난다.

배포명은 `daily-investment-intelligence`, import 이름은 `dii` 로 다르게 두었다
(`pyproject.toml` 의 `[tool.uv.build-backend] module-name`).

## 현재 모듈

| 모듈 | 책임 |
|---|---|
| `dii.config` | 환경에서 설정을 읽어 검증한다. 다른 모듈은 여기서만 설정을 가져온다 |
| `dii.logging_setup` | 로깅 설정. **핸들러를 붙이는 것은 진입점에서 한 번뿐** |
| `dii.cli` | 명령줄 진입점. 파이프라인 각 단계가 하위 명령으로 붙는다 |
| `dii.universe` | `config/universe.toml` 을 읽어 검증한다. 수집 대상의 단일 출처 |
| `dii.storage.models` | 저장 계층의 공용 자료형. 바깥이 pandas 나 sqlite3 에 의존하지 않게 한다 |
| `dii.storage.schema` | 테이블 정의와 마이그레이션 (`PRAGMA user_version` 기반) |
| `dii.storage.sqlite` | 리포지토리 구현(가격·문서). **SQL 은 이 파일 밖으로 나가지 않는다** |
| `dii.collect.prices` | yfinance 로 일봉을 받아 검증하고 저장소에 넘긴다 |
| `dii.collect.http` | rate limit·재시도·타임아웃을 지키는 공용 HTTP 클라이언트 |
| `dii.collect.filings` | SEC EDGAR 공시 목록 수집 |
| `dii.collect.news` | yfinance 내장 뉴스 수집 |
| `dii.processing.frames` | 저장 형식(long) → 분석 형식(wide) 변환. **룩어헤드를 막는 관문** |
| `dii.processing.indicators` | 수익률·변동성·거래량 지표. 전부 순수 함수 |
| `dii.quant.factors` | 팩터 정의와 가중치. 미리 고정한다 |
| `dii.quant.scoring` | 횡단면 정규화, 가중 합산, 섹터 랭킹 |

## 계층 경계 원칙

[Project_Plan.md](../Project_Plan.md) 4.1절에서 정한 대로, **M1 의 SQLite 는 M3 에서
PostgreSQL 로 갈아탄다.** 그 이전을 감당 가능한 크기로 만들기 위해 아래를 지킨다.

1. **저장소 접근은 리포지토리 계층 뒤에 숨긴다.**
   분석·리포트 코드가 SQL 이나 DB 커넥션을 직접 만지지 않는다.
   `PriceRepository.get_prices(ticker, start, end)` 처럼 **도메인 언어로 된 인터페이스**를 통해서만
   데이터에 접근한다. 그래야 M3 에서 구현체만 바꾸면 된다

2. **분석 로직은 SQL 방언에 의존하지 않는다.**
   집계나 계산을 DB 쪽 특수 문법으로 밀어 넣지 않는다. 성능상 꼭 필요해질 때
   그 판단을 학습 로그에 남기고 예외를 둔다

3. **설정은 `dii.config` 를 통해서만 읽는다.**
   `os.environ` 을 코드 곳곳에서 직접 부르지 않는다. 설정 스키마가 한 곳에 모여 있어야
   무엇이 환경에 의존하는지 파악할 수 있다

4. **핸들러 설정은 진입점에서만 한다.**
   라이브러리 성격의 모듈은 `get_logger(__name__)` 로 로거를 얻어 쓰기만 하고,
   포매터·핸들러·레벨을 건드리지 않는다

5. **미래 데이터는 조회 지점에서 잘라낸다.**
   기준일 이후 데이터를 배제하는 책임은 `load_frames()` **한 곳**에 있다.
   계산 코드가 "미래를 쓰지 말자"고 조심하는 방식은 언젠가 깨진다.
   분석 함수는 기준 날짜를 인자로 받고, 내부에서 `date.today()` 를 부르지 않는다

## 앞으로 추가될 모듈 (예정)

마일스톤 진행에 따라 아래가 추가된다. **미리 빈 껍데기를 만들어 두지 않는다** —
실제로 필요해지는 시점에 만든다.

| 예정 모듈 | 마일스톤 | 책임 |
|---|---|---|
| `dii.retrieval.*` | M3 | 임베딩, 검색 |
| `dii.agent.*` | M4 | Agent 루프와 도구 정의 |
| `dii.report.*` | M4 | 리포트 렌더링 |
| `dii.api.*` | M4 | HTTP API |
