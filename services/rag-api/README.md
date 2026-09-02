# RAG API

UI·인증·사용자 저장소와 분리된 FastAPI 서비스입니다. 현재 공개 계약은 아래 네 경로뿐입니다.

- `GET /v1/health/live`
- `GET /v1/health/ready`
- `POST /v1/search`
- `POST /v1/answer`

`/v1/search`는 질문만 입력받아 active release의 SQLite FTS5와 Chroma를 BM25:Vector `0.6:0.4` RRF로 결합하고 각 검색의 Top50 작업 목록에서 후보 20개를 만듭니다. `card_page_section_benefit`은 의미 질의에만 BGE를 적용합니다. `parent_child_bundle`은 후보를 카드별 같은-card 1-hop 근거 묶음으로 만든 뒤 모든 질의에 BGE를 적용합니다. active release는 두 프로필 중 정확히 하나만 사용하며 요청에서 다른 프로필을 섞을 수 없습니다. 사용자 출력은 Top3가 기본이고 Top1·Top5도 선택할 수 있습니다.

active pointer가 처음 로드되거나 다른 release로 바뀔 때만 SQLite·Chroma·임베딩 무결성을 전체 검증합니다. 같은 pointer를 사용하는 일반 요청은 검증이 끝난 `ReleaseHandle`을 재사용하므로 요청마다 전체 인덱스를 다시 읽지 않습니다.

`/v1/answer`도 클라이언트가 임의의 근거를 보내는 방식이 아니라 같은 검색을 서버 내부에서 먼저 수행한 뒤 검색 근거만 LLM에 전달합니다. benefit 프로필의 section은 연결된 benefit leaf로 풀고, parent-child 프로필은 BGE가 실제로 평가한 최대 5개의 직접 본문 근거만 전달합니다. 운영 요청마다 OCR을 다시 검증하거나 전체 평가를 수행하지는 않습니다. 대신 LLM 출력의 citation이 실제 검색 근거와 같은 카드에 속하는지는 동기식으로 가볍게 검사합니다.

현재 온라인 검증은 citation 소유권과 응답 schema만 확인하며, 생성된 문장의 의미가 근거와 완전히 같은지 판정하는 별도 LLM 채점기는 운영 경로에 넣지 않았습니다. 그 정확성은 holdout/LLM 평가 단계에서 검증해야 합니다. 또한 검색 점수의 calibration이 아직 끝나지 않아 “검색 단계에서 무조건 정답 없음”을 결정하는 score threshold도 두지 않았습니다. 검색 근거가 전혀 없거나 LLM이 `insufficient_evidence`를 반환하면 카드·추천·근거 목록을 비워 결과가 추천처럼 보이지 않게 합니다.

```bash
PYTHONPATH=services/rag-api/src:packages/rag-core/src \
  conda run -n skn25 python -m pickcardu_rag_api
```

현재 단계는 로컬 개발용입니다. production 설정은 시작 단계에서 차단되며, 외부 embedding/LLM 호출은 `OPENAI_API_KEY`가 명시된 실제 요청에서만 발생합니다. 로그인·프로필·대화 저장·Developer Lab은 이 서비스 범위에 없습니다.
