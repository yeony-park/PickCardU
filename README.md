# PickCardU

카드 상품안내서 OCR 벤치마크와 근거 기반 검색·생성 파이프라인입니다.

## 저장소 구조

- `apps/main`: 실제 서비스 UI. Next.js 기반이며 Vercel 배포 대상입니다.
- `apps/rag-lab`: 검색·생성·평가용 팀 내부 UI 예정 위치입니다.
- `services/rag-api`: 두 UI가 함께 사용할 Python API 예정 위치입니다.
- `packages/rag-core`: 검색·재정렬·프롬프팅·평가 공통 로직 예정 위치입니다.
- `packages/contracts`: UI와 API 사이의 OpenAPI·JSON Schema 계약 예정 위치입니다.
- `jobs/rag-indexer`: OCR·청킹·임베딩·인덱스 생성 작업 예정 위치입니다.
- `data`: 원본 및 평가 데이터이며 앱 배포 산출물에는 포함하지 않습니다.
- `infra`: 배포 및 인프라 설정 예정 위치입니다.

현재 Python RAG 파이프라인은 동작 경로를 보존하기 위해 `scripts/rag_pipeline`에 유지하며, API 경계가 확정된 뒤 단계적으로 옮깁니다.

- [전수 파싱 → 검증 → parent-child 청킹 → 하이브리드 검색 → 생성 실행 가이드](docs/rag_pipeline.md)
- [기존 파서 비교와 106문서/617페이지 전수 실행 결과](data/rag/reports/pipeline_performance.md)
- [팀 공유용 단계별 RAG 실행 결과 HTML](data/rag/reports/rag_pipeline_dashboard.html)
- [로컬 자연어 검색·Luna 답변 테스트 HTML](data/rag/reports/rag_search_tester.html)

A RAG-based card recommendation service that prioritizes user-owned cards before suggesting new ones
