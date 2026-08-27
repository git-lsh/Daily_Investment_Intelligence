# Daily Investment Intelligence

개인 맞춤형 AI 투자 리서치 시스템.

매일 정해진 시간에 시장 데이터·뉴스·공시·리서치 자료를 수집하고, 관심 섹터와 종목을 정량적으로
분석한 뒤, AI Agent가 근거 기반의 Daily Investment Report를 생성한다.

> **이 프로젝트의 진짜 목표**
> "오늘 어떤 주식이 좋아?"를 LLM에게 묻는 시스템을 만드는 것이 아니다.
> 데이터 수집부터 배포·모니터링까지의 End-to-End AI/ML 시스템을 직접 설계하고 운영하면서,
> 보유한 **Python / ML / Data Analysis** 역량을 **Backend / DB / Network / Infra / CI-CD** 로 확장하는 것이 목적이다.

---

## 파이프라인

```
Data Collection → Data Storage → Data Processing → Quantitative Analysis
      → Information Retrieval → AI Agent → Investment Research
            → Report / API → Deployment → Monitoring
```

각 단계에서 다룰 기술 후보와 그에 대응하는 CS 학습 포인트는
[Project_Plan.md](Project_Plan.md) 3.4절 표에 정리되어 있다.

## 문서

| 문서 | 내용 |
|---|---|
| [Project_Plan.md](Project_Plan.md) | 프로젝트 기획서 — 목표, 확정 결정, 마일스톤 로드맵, 학습 로그 운영 규칙 |
| [docs/architecture.md](docs/architecture.md) | 패키지 레이아웃과 계층 경계 원칙 |
| [config/universe.toml](config/universe.toml) | 분석 유니버스 — 무엇을 수집하고 분석하는가 |
| [docs/tech-notes/](docs/tech-notes/) | 기술 스택별 학습 로그 (기술 1개 = 파일 1개) |

## 개발 환경 준비

[uv](https://docs.astral.sh/uv/) 하나만 있으면 된다. Python 설치나 가상환경 생성은 uv 가 처리한다.

```bash
git clone https://github.com/git-lsh/Daily_Investment_Intelligence.git
cd Daily_Investment_Intelligence

uv sync                 # 가상환경 생성 + uv.lock 대로 의존성 설치
cp .env.example .env    # 설정 파일 준비 (기본값 그대로도 동작한다)

uv run dii config       # 해석된 설정 확인 — 여기까지 되면 환경 준비 완료
```

`uv run` 이 실행 직전에 환경을 `uv.lock` 과 맞춰 주므로 가상환경을 따로 activate 하지 않는다.

### 자주 쓰는 명령

```bash
uv run dii collect      # 유니버스 전체의 일봉을 수집해 저장 (재실행 안전)
uv run dii collect AAPL MSFT   # 특정 심볼만
uv run dii status       # 저장소 적재 현황
uv run dii config       # 해석된 설정값

uv run pytest           # 테스트
uv run ruff check .     # 린트
uv run ruff format .    # 포매팅
uv run mypy             # 타입 체크
```

`dii collect` 는 **몇 번을 실행해도 안전하다.** 이미 받은 날짜는 덮어쓰고 중복 행을 만들지 않으며,
중간에 실패해도 다시 실행하면 빠진 곳을 채운다. 종료 코드는 성공 `0`, 부분 실패 `2`, 전체 실패 `1` 이다.

### 분석 유니버스

수집·분석 대상은 [config/universe.toml](config/universe.toml) 에 있다.
GICS 11개 섹터 × 4종목 + 섹터 ETF 11개 + 벤치마크(SPY) = **56 심볼**.
종목을 바꾸려면 이 파일만 고치면 되고 코드는 건드리지 않는다.

### 설정

모든 설정은 `DII_` 접두사를 붙인 환경변수 또는 `.env` 파일로 주입한다.
사용 가능한 항목은 [.env.example](.env.example) 을 참고한다. `.env` 는 커밋하지 않는다.

## 개발 원칙

새로운 기술 스택을 도입하는 작업은 항상 아래 순서를 따른다.

1. **작업 정의** — 구현할 기능과 여기에 필요한 신규 기술 스택을 명시
2. **학습 로그 초안** — `docs/tech-notes/` 에 템플릿 기반으로 1~3절 작성
3. **구현** — 코드 작성 및 동작 확인
4. **회고** — 구현 중 겪은 이슈를 반영해 4~7절 작성
5. **커밋** — 코드와 학습 로그를 함께 커밋 (`docs(tech-notes): ...` 명시)

즉 **"기능 구현"과 "학습 로그"는 항상 세트**다.

## 상태

**M1 — 데이터 수집 + 저장 완료.** 56심볼 5년치 약 7만 행이 SQLite 에 적재된다.
다음은 M2(파생 지표 계산 + 팩터 스코어링).

마일스톤 M0~M5와 각 단계의 완료 조건은 [Project_Plan.md 5장](Project_Plan.md)을 참고.
