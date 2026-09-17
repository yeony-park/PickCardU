# PickCardU

카드 상품안내서 OCR 벤치마크와 근거 기반 검색·생성 파이프라인입니다.

## 로컬 개발 실행

Python 3.11 이상 환경에 RAG 패키지 의존성을 설치하고 `apps/main`의 Node 의존성을 설치한 뒤, 저장소 루트에서 실행합니다.

```bash
python -m pip install -e "packages/rag-core[reranker]" -e services/rag-api
npm --prefix apps/main install
npm run dev
```

`npm run dev`는 Next.js와 FastAPI를 함께 실행합니다. FastAPI 진입점은 루트 `.env`를 자동으로 읽고 기존 셸 환경변수를 우선합니다. 특정 Conda 환경 이름은 가정하지 않으므로 팀원이 준비한 Python 환경을 활성화한 상태에서 실행해야 합니다.

- 서비스 화면: `http://localhost:3000`
- FastAPI readiness: `http://127.0.0.1:8000/v1/health/ready`
- 종료: 실행한 터미널에서 `Ctrl+C`

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
