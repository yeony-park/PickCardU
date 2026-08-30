# PickCardU

카드 상품안내서 OCR 벤치마크와 근거 기반 검색·생성 파이프라인입니다.

- [전수 파싱 → 검증 → parent-child 청킹 → 하이브리드 검색 → 생성 실행 가이드](docs/rag_pipeline.md)
- [기존 파서 비교와 106문서/617페이지 전수 실행 결과](data/rag/reports/pipeline_performance.md)
- [팀 공유용 단계별 RAG 실행 결과 HTML](data/rag/reports/rag_pipeline_dashboard.html)
- [로컬 자연어 검색·Luna 답변 테스트 HTML](data/rag/reports/rag_search_tester.html)

A RAG-based card recommendation service that prioritizes user-owned cards before suggesting new ones
