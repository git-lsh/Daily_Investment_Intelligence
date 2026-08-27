# Tech Notes — 기술 스택 학습 로그

이 디렉토리는 프로젝트에 새로운 기술 스택을 도입할 때마다 작성하는 학습 로그를 모아둔 곳이다.
운영 규칙의 원문은 [Project_Plan.md](../../Project_Plan.md) 3장, 작성 순서와 계획은 5장(로드맵)을 따른다.

## 규칙 요약

- **기술 하나당 파일 하나**. 파일명은 `{순번}-{카테고리}-{기술명}.md`
  - 순번은 **파이프라인 단계 번호가 아니라 프로젝트에 도입한 순서**다. 한 번 붙인 번호는 바꾸지 않는다
  - 예: `01-data-collection-yfinance.md`, `06-storage-postgresql.md`, `10-agent-orchestration.md`
- 작성 흐름은 **초안 → 구현 → 회고**
  1. 구현 전에 [_TEMPLATE.md](_TEMPLATE.md) 를 복사해 **1~3절**(사전 지식 / 개념 정리 / AI·ML 엔지니어 관점)을 먼저 채운다
  2. 코드를 작성하고 동작을 확인한다
  3. 구현하며 겪은 것을 반영해 **4~7절**(장점 / 한계·트레이드오프 / 선택 이유 / 참고 자료)을 채운다
- **구현과 학습 로그는 항상 세트로 커밋**한다. 커밋 메시지에 `docs(tech-notes): ...` 를 남긴다
- 이해도가 깊어지면 문서를 갱신하고, 필요하면 문서 하단에 "업데이트 로그" 섹션을 덧붙인다

## 카테고리 슬러그

파일명의 카테고리 부분에는 아래 값만 쓴다. (파이프라인 단계와 대응)

| 슬러그 | 파이프라인 단계 |
|---|---|
| `foundation` | 프로젝트 기반 (환경/설정/의존성) |
| `data-collection` | Data Collection |
| `storage` | Data Storage |
| `processing` | Data Processing |
| `quant` | Quantitative Analysis |
| `retrieval` | Information Retrieval |
| `agent` | AI Agent |
| `api` | Report / API |
| `deployment` | Deployment |
| `cicd` | CI/CD |
| `monitoring` | Monitoring |

## 작성된 노트

노트를 추가할 때마다 아래 표에 한 줄씩 등록한다.
상태는 `초안`(1~3절 작성) → `구현완료` → `회고완료`(4~7절 작성) 순으로 갱신한다.

| 노트 | 카테고리 | 도입 시점 | 상태 |
|---|---|---|---|
| [01-foundation-uv.md](01-foundation-uv.md) | Foundation | 2026-08-27 | 회고완료 |
| [02-foundation-pydantic-settings.md](02-foundation-pydantic-settings.md) | Foundation | 2026-08-27 | 회고완료 |
| [03-data-collection-yfinance.md](03-data-collection-yfinance.md) | Data Collection | 2026-08-27 | 회고완료 |
| [04-storage-sqlite.md](04-storage-sqlite.md) | Storage | 2026-08-27 | 회고완료 |
| [05-quant-factor-pipeline.md](05-quant-factor-pipeline.md) | Processing + Quant | 2026-08-27 | 회고완료 |

작성 예정 목록은 [Project_Plan.md 5.7절](../../Project_Plan.md)을 참고한다.
