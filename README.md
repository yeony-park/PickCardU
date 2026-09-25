# PickCardU

카드 상품안내서 OCR 벤치마크와 근거 기반 검색·생성 파이프라인입니다.

## 로컬 개발 실행

macOS 또는 Linux/WSL에서 Node.js 22.13 이상과 Python 3.11 이상을 준비합니다. 프로젝트를 처음 받은 뒤에는 사용할 Python 환경을 활성화하고 저장소 루트에서 다음 명령을 순서대로 실행합니다.

```bash
npm run setup
npm run dev
```

`npm run setup`은 잠금 파일 기준 프론트 의존성을 프로젝트 내부에 설치하고, 현재 Python 환경에 필요한 RAG/API 모듈이 있는지 변경 없이 확인한 뒤 고정 BGE reranker와 고정 RAG index release를 설치·검증합니다. Python·Conda 환경을 만들거나 `pip install`로 기존 패키지를 변경하지 않습니다. BGE는 약 2.3GB이고 RAG release 다운로드 파일은 약 64MB이므로 최초 실행에는 시간이 걸릴 수 있습니다. setup 자체는 OpenAI API를 호출하지 않습니다.

최초 설정이 끝난 뒤 평소에는 저장소 루트에서 다음 명령만 실행합니다.

```bash
npm run dev
```

프론트 잠금 파일이나 `config/dev-assets.json`의 release가 변경된 경우에는 `npm run setup`을 다시 실행합니다. Python 모듈 검사에 실패하면 사용할 환경의 관리 방식에 따라 필요한 패키지를 직접 준비한 뒤 setup을 다시 실행합니다. `PICKCARDU_PYTHON`을 지정하지 않으면 setup과 dev 모두 현재 `PATH`의 `python`을 사용합니다. 루트 `.env`와 `OPENAI_API_KEY`는 Git으로 배포하지 않으므로 팀원이 각자 준비해야 합니다.

`npm run dev`는 Next.js와 FastAPI를 함께 실행합니다. FastAPI 진입점은 루트 `.env`를 자동으로 읽고 기존 셸 환경변수를 우선합니다.

- 서비스 화면: `http://localhost:3000`
- FastAPI readiness: `http://127.0.0.1:8000/v1/health/ready`
- 종료: 실행한 터미널에서 `Ctrl+C`

상세한 자산 검증, release 활성화와 장애 확인 방법은 [RAG API 실행·운영 가이드](docs/API_RUNBOOK.md)를 참고합니다.

## 저장소 구조

- `apps/main`: 실제 서비스 UI. Next.js 기반이며 Vercel 배포 대상입니다.
- `apps/rag-lab`: 검색·생성·평가용 팀 내부 UI 예정 위치입니다.
- `services/rag-api`: 두 UI가 함께 사용하는 FastAPI 서비스입니다.
- `packages/rag-core`: 검색·재정렬·프롬프팅·평가 공통 로직입니다.
- `packages/contracts`: UI와 API 사이의 OpenAPI 계약과 생성 타입입니다.
- `jobs/rag-indexer`: OCR·청킹·임베딩·인덱스 생성 작업 예정 위치입니다.
- `data`: 원본 및 평가 데이터이며 앱 배포 산출물에는 포함하지 않습니다.
- `infra`: 배포 및 인프라 설정 예정 위치입니다.

현재 Python RAG 파이프라인은 동작 경로를 보존하기 위해 `scripts/rag_pipeline`에 유지하며, API 경계가 확정된 뒤 단계적으로 옮깁니다.

- [전수 파싱 → 검증 → parent-child 청킹 → 하이브리드 검색 → 생성 실행 가이드](docs/rag_pipeline.md)
- [기존 파서 비교와 106문서/617페이지 전수 실행 결과](data/rag/reports/pipeline_performance.md)
- [팀 공유용 단계별 RAG 실행 결과 HTML](data/rag/reports/rag_pipeline_dashboard.html)
- [로컬 자연어 검색·Luna 답변 테스트 HTML](data/rag/reports/rag_search_tester.html)

A RAG-based card recommendation service that prioritizes user-owned cards before suggesting new ones
