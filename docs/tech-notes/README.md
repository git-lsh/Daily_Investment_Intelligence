# Tech Notes — 기술 스택 학습 로그

이 디렉토리는 프로젝트에 새로운 기술 스택을 도입할 때마다 작성하는 학습 로그를 모아둔 곳이다.
운영 규칙의 원문은 [Project_Plan.md](../../Project_Plan.md) 3장을 따른다.

## 규칙 요약

- **기술 하나당 파일 하나**. 파일명은 `{단계번호}-{카테고리}-{기술명}.md`
  - 예: `01-data-collection-yfinance.md`, `03-storage-timescaledb.md`, `07-agent-langgraph.md`
- 작성 흐름은 **초안 → 구현 → 회고**
  1. 구현 전에 [_TEMPLATE.md](_TEMPLATE.md) 를 복사해 **1~3절**(사전 지식 / 개념 정리 / AI·ML 엔지니어 관점)을 먼저 채운다
  2. 코드를 작성하고 동작을 확인한다
  3. 구현하며 겪은 것을 반영해 **4~7절**(장점 / 한계·트레이드오프 / 선택 이유 / 참고 자료)을 채운다
- **구현과 학습 로그는 항상 세트로 커밋**한다. 커밋 메시지에 `docs(tech-notes): ...` 를 남긴다
- 이해도가 깊어지면 문서를 갱신하고, 필요하면 문서 하단에 "업데이트 로그" 섹션을 덧붙인다

## 단계 번호 규약

파일명 앞의 번호는 파이프라인 단계를 가리킨다.

| 번호 | 단계 | 카테고리 슬러그 |
|---|---|---|
| 01 | Data Collection | `data-collection` |
| 02 | Data Storage | `storage` |
| 03 | Data Processing | `processing` |
| 04 | Quantitative Analysis | `quant` |
| 05 | Information Retrieval | `retrieval` |
| 06 | AI Agent | `agent` |
| 07 | Report / API | `api` |
| 08 | Deployment | `deployment` |
| 09 | CI/CD | `cicd` |
| 10 | Monitoring | `monitoring` |

## 작성된 노트

_아직 없음._

<!-- 노트를 추가할 때마다 아래 형식으로 한 줄씩 등록한다
| 노트 | 단계 | 도입 시점 | 상태 |
|---|---|---|---|
| [01-data-collection-yfinance.md](01-data-collection-yfinance.md) | Data Collection | 2026-08-27 | 초안 / 구현완료 / 회고완료 |
-->
