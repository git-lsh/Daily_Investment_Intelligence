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
| [Project_Plan.md](Project_Plan.md) | 프로젝트 기획서 — 목표, 문제 정의, 학습 로그 운영 규칙 |
| [docs/tech-notes/](docs/tech-notes/) | 기술 스택별 학습 로그 (기술 1개 = 파일 1개) |

## 개발 원칙

새로운 기술 스택을 도입하는 작업은 항상 아래 순서를 따른다.

1. **작업 정의** — 구현할 기능과 여기에 필요한 신규 기술 스택을 명시
2. **학습 로그 초안** — `docs/tech-notes/` 에 템플릿 기반으로 1~3절 작성
3. **구현** — 코드 작성 및 동작 확인
4. **회고** — 구현 중 겪은 이슈를 반영해 4~7절 작성
5. **커밋** — 코드와 학습 로그를 함께 커밋 (`docs(tech-notes): ...` 명시)

즉 **"기능 구현"과 "학습 로그"는 항상 세트**다.

## 상태

구현 착수 전. 저장소 뼈대와 문서 규약만 세워진 상태.
