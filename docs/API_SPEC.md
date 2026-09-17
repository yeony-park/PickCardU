# PickCardU RAG API 명세

## 1. 문서 목적과 기준

이 문서는 현재 구현된 PickCardU RAG FastAPI 서비스의 팀 공유용 HTTP 계약이다. 구현 예정 기능이 아니라 현재 FastAPI 경로, Pydantic schema와 생성된 OpenAPI 계약을 기준으로 작성했다.

- API 버전: `0.1.0`
- 기본 데이터 형식: `application/json`
- 기준 구현: `services/rag-api/src/pickcardu_rag_api/main.py`
- 기계 판독용 계약: `packages/contracts/openapi.yaml`
- 클라이언트 연동용 TypeScript 타입: `packages/contracts/generated/api.ts`
- 현재 공개 경로: 4개

FastAPI 실행 중에는 `/docs`에서 Swagger UI, `/openapi.json`에서 런타임 OpenAPI 문서를 확인할 수 있다.

> 현재 인증·사용자 프로필·대화 저장 기능은 이 API에 구현되어 있지 않다. 인증이 없는 현재 계약을 외부 공개 API 계약으로 간주하면 안 된다.

실행, release 활성화, health 점검과 rollback 절차는 [`API_RUNBOOK.md`](API_RUNBOOK.md)를 참고한다. 검색·답변 내부 구조는 [`rag_pipeline.md`](rag_pipeline.md)를 참고한다.

## 2. 공통 HTTP 계약

### 2.1 서버 주소와 인증

| 항목 | 계약 |
|---|---|
| 개발 환경 예시 Base URL | `http://127.0.0.1:8000` |
| staging Base URL | 미정 |
| production Base URL | 미정 |
| 인증 | 현재 없음 |
| POST Content-Type | `application/json` |
| API 경로 버전 | `/v1` |

환경별 실제 Base URL과 허용 Origin은 배포 설정에서 확정한다. `production` 실행은 현재 구현에서 차단되어 있다.

### 2.2 요청 ID

클라이언트가 `x-request-id` 헤더를 보내면 서버가 그대로 사용한다. 없으면 서버가 새 ID를 생성한다. 모든 HTTP 응답에는 `x-request-id` 헤더가 포함된다.

### 2.3 Endpoint 요약

| Method | Path | 목적 | 외부 provider 호출 |
|---|---|---|---|
| `GET` | `/v1/health/live` | API 프로세스 생존 확인 | 없음 |
| `GET` | `/v1/health/ready` | active release 로딩·무결성 확인 | 없음 |
| `POST` | `/v1/search` | 카드와 근거 검색 | 질문 embedding 호출 |
| `POST` | `/v1/answer` | 검색 근거 기반 답변 생성 | 질문 embedding 및 조건부 LLM 호출 |

### 2.4 공통 질의 요청

`POST /v1/search`와 `POST /v1/answer`는 같은 요청 형식을 사용한다.

| 필드 | 타입 | 필수 | 제약 | 설명 |
|---|---|---:|---|---|
| `query` | string | 예 | 공백 제거 후 1~500자 | 검색하거나 답변할 카드 혜택 질문이다. |
| `profile` | string 또는 null | 아니요 | 아래 두 값 중 하나 | 생략하면 active release의 프로필을 사용한다. 다른 프로필을 지정하면 `409`다. |
| `top_k` | integer | 아니요 | `1`, `3`, `5`; 기본 `3` | 반환할 최대 카드 수다. |

허용 프로필:

- `card_page_section_benefit`
- `parent_child_bundle`

정의되지 않은 추가 필드는 허용하지 않는다.

```json
{
  "query": "카페 할인 혜택이 좋은 카드를 알려줘",
  "top_k": 3
}
```

### 2.5 공통 검색 결과

`query_type`은 서버가 분류한 검색 질의 유형이며 다음 중 하나다.

- `proper_noun`: 카드명·브랜드명 등 고유명사 중심
- `numeric_condition`: 금액·비율·조건 중심
- `semantic`: 의미 중심 일반 질문

`score`는 현재 검색 파이프라인 안에서 순위를 정하기 위한 값이다. 카드 혜택의 확률이나 절대 신뢰도로 해석하지 않는다.

## 3. Endpoint

### 3.1 `GET /v1/health/live`

프로세스가 요청을 받을 수 있는지만 확인한다. index 준비 상태와 외부 provider 상태는 확인하지 않는다.

성공 응답 `200`:

```json
{
  "status": "live"
}
```

### 3.2 `GET /v1/health/ready`

active pointer가 가리키는 release를 로드하고 manifest, SQLite, FTS5, Chroma, embedding identity를 검증한다. 첫 로드 또는 pointer 변경 시 전체 검증하고, 같은 pointer의 이후 요청은 검증된 handle을 재사용한다. 외부 API는 호출하지 않는다.

준비 완료 `200` 예시:

```json
{
  "status": "ready",
  "release_id": "release_example",
  "profile": "card_page_section_benefit",
  "document_count": 100,
  "chunk_count": 2000
}
```

`release_id`, `document_count`, `chunk_count`는 배포된 release에 따라 달라진다.

준비되지 않음 `503`:

```json
{
  "status": "not_ready",
  "reason": "RuntimeError"
}
```

`reason`은 예외 타입만 반환하며 로컬 경로나 내부 index 상세는 노출하지 않는다.

### 3.3 `POST /v1/search`

질문 embedding을 생성한 뒤 active release의 SQLite FTS5와 Chroma를 검색한다. 검색 결과를 RRF로 결합하고 설정된 프로필에 따라 로컬 BGE reranker를 적용한다. 답변 생성 LLM은 호출하지 않는다.

외부 전송:

- 질문 문자열을 OpenAI embedding API로 전송한다.
- 문서 원문 전체를 embedding API로 다시 보내지는 않는다.

요청 예시:

```bash
curl -sS http://127.0.0.1:8000/v1/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"카페 할인 혜택이 좋은 카드를 알려줘","top_k":3}'
```

성공 응답 `200` 예시:

```json
{
  "status": "completed",
  "release_id": "release_example",
  "profile": "card_page_section_benefit",
  "query_type": "semantic",
  "cards": [
    {
      "card_key": "issuer/card-id",
      "card_name": "카드 이름",
      "issuer": "카드사",
      "score": 0.91,
      "rank": 1,
      "evidence_count": 1
    }
  ],
  "evidence": [
    {
      "rank": 1,
      "card_key": "issuer/card-id",
      "card_name": "카드 이름",
      "issuer": "카드사",
      "chunk_id": "chunk-id",
      "page_num": 3,
      "text": "검색된 원문 근거",
      "section": "카페 혜택",
      "level": "benefit",
      "score": 0.91
    }
  ],
  "usage": {
    "embedding": {
      "model": "text-embedding-3-small",
      "latency_ms": 120.0,
      "usage": {}
    }
  }
}
```

예시의 카드명, 점수, 지연 시간은 응답 형식을 설명하기 위한 값이며 실제 측정 결과가 아니다.

### 3.4 `POST /v1/answer`

서버 내부에서 `/v1/search`와 같은 검색을 먼저 수행한다. 검색 근거가 있으면 질문과 선정된 근거만 LLM에 전달해 답변을 생성한다. 클라이언트가 임의의 근거를 주입할 수 없다.

외부 전송:

- 질문 문자열을 OpenAI embedding API로 전송한다.
- 검색된 근거의 `card_key`, 카드명, 카드사, `chunk_id`, 본문을 질문과 함께 답변 생성 API로 전송한다.
- 답변 입력 payload는 보수적으로 UTF-8 12,000 bytes 이하로 제한한다.

요청 예시:

```bash
curl -sS http://127.0.0.1:8000/v1/answer \
  -H 'Content-Type: application/json' \
  -d '{"query":"카페 할인 혜택이 좋은 카드를 알려줘","top_k":3}'
```

성공 응답 `200`의 주요 필드:

| 필드 | 타입 | 설명 |
|---|---|---|
| `answer_status` | string | `answered` 또는 `insufficient_evidence`다. |
| `answer` | string | 근거 기반 한국어 답변이다. 최대 400자다. |
| `recommendations` | array | 최대 5개 추천과 근거 citation을 담는다. |
| `claims` | array | 최대 5개 원자 주장과 조건·수치·근거 citation을 담는다. |
| `cards` | array | 답변에 사용된 검색 카드 목록이다. |
| `evidence` | array | 답변 생성에 제공된 근거 목록이다. |
| `usage.embedding` | object | 질문 embedding 사용량과 지연 정보다. |
| `usage.answer` | object | 답변 모델, 시도 횟수, 입력 크기, 사용량과 지연 정보다. |

추천과 claim에는 `card_key`와 1~2개의 `citations`가 필요하다. claim은 선택적으로 `value`, `unit`, 최대 2개의 `conditions`를 포함한다. 서버는 citation이 실제 검색 근거이며 동일 카드에 속하는지 검사한다.

`answered` 응답 예시:

```json
{
  "status": "completed",
  "answer_status": "answered",
  "release_id": "release_example",
  "profile": "card_page_section_benefit",
  "query_type": "semantic",
  "cards": [
    {
      "card_key": "issuer/card-id",
      "card_name": "카드 이름",
      "issuer": "카드사",
      "score": 0.91,
      "rank": 1,
      "evidence_count": 1
    }
  ],
  "answer": "이 카드는 카페 이용 금액의 10%를 할인합니다. 신청 전 공식 상품설명서를 다시 확인하세요.",
  "recommendations": [
    {
      "card_key": "issuer/card-id",
      "reason": "카페 10% 할인 근거가 확인됨",
      "citations": ["chunk-id"]
    }
  ],
  "claims": [
    {
      "card_key": "issuer/card-id",
      "text": "카페 이용 금액의 10%가 할인됩니다.",
      "value": 10,
      "unit": "%",
      "conditions": [],
      "citations": ["chunk-id"]
    }
  ],
  "evidence": [
    {
      "rank": 1,
      "card_key": "issuer/card-id",
      "card_name": "카드 이름",
      "issuer": "카드사",
      "chunk_id": "chunk-id",
      "page_num": 3,
      "text": "카페 이용 금액 10% 할인",
      "section": "카페 혜택",
      "level": "benefit",
      "score": 0.91
    }
  ],
  "usage": {
    "embedding": {},
    "answer": {}
  }
}
```

예시의 카드 정보, 점수와 혜택 내용은 응답 구조를 설명하기 위한 값이며 실제 상품 정보가 아니다.

근거가 없거나 LLM이 근거 부족으로 판단한 응답 예시:

```json
{
  "status": "completed",
  "answer_status": "insufficient_evidence",
  "release_id": "release_example",
  "profile": "card_page_section_benefit",
  "query_type": "semantic",
  "cards": [],
  "answer": "현재 등록된 카드 문서에서는 질문을 뒷받침할 근거를 확인하기 어렵습니다.",
  "recommendations": [],
  "claims": [],
  "evidence": [],
  "usage": {
    "embedding": {},
    "answer": {
      "provider_called": false
    }
  }
}
```

검색 근거가 전혀 없으면 답변 LLM을 호출하지 않는다. 검색 근거는 있었지만 LLM이 `insufficient_evidence`를 반환한 경우에도 카드·추천·근거 목록을 비워 추천처럼 보이지 않게 한다.

## 4. 오류 계약

`POST /v1/search`와 `POST /v1/answer`의 오류 응답 형식:

```json
{
  "code": "INDEX_UNAVAILABLE",
  "message": "활성 검색 인덱스를 사용할 수 없습니다.",
  "retryable": true,
  "request_id": "요청 추적 ID"
}
```

| HTTP | `code` | 재시도 | 발생 조건 |
|---:|---|---:|---|
| 409 | `CONTRACT_MISMATCH` | 아니요 | 요청 프로필이나 embedding 모델이 active release 계약과 다를 때 |
| 422 | `INVALID_REQUEST` | 아니요 | JSON body, 필드, 길이 또는 허용값이 잘못됐을 때 |
| 503 | `INDEX_UNAVAILABLE` | 예 | active pointer 또는 index를 로드·검증할 수 없을 때 |
| 503 | `EMBEDDING_UNAVAILABLE` | 예 | 질문 embedding 호출이 실패했을 때 |
| 503 | `RERANKER_UNAVAILABLE` | 아니요 | 로컬 reranker를 사용할 수 없을 때 |
| 503 | `EVIDENCE_PACKAGE_TOO_LARGE` | 아니요 | 선정 근거가 허용된 답변 입력 크기를 초과할 때 |
| 503 | `LLM_UNAVAILABLE` | 예 | 답변 생성 provider를 사용할 수 없을 때 |
| 503 | `LLM_UNGROUNDED` | 예 | 답변 schema 또는 citation 소유권 검증에 실패했을 때 |

`GET /v1/health/ready`의 `503`은 위 공통 오류가 아니라 `{status, reason}` 형식이다.

## 5. 답변 안전 계약과 한계

- LLM에는 서버가 검색한 근거만 전달한다.
- recommendation과 atomic claim의 citation이 실제 검색 근거에 속하는지 검사한다.
- citation이 같은 카드의 근거인지 검사한다.
- 근거가 부족하면 `insufficient_evidence`로 응답한다.
- 현재 온라인 검증은 응답 schema와 citation 소유권을 확인한다.
- 생성 문장의 의미가 근거와 완전히 동일한지는 별도 LLM 심사로 실시간 검증하지 않는다.
- 검색 점수 calibration이 완료되지 않아 score threshold만으로 정답 없음 판정을 하지 않는다.
- release의 OCR·원천 데이터 검증 상태는 현재 API 응답에 포함되지 않는다. 배포 담당자는 활성화 전에 manifest의 `source_validation`과 관련 검증 기록을 별도로 확인한다.

## 6. 미확정 운영 계약

다음 항목은 현재 구현 또는 팀 정책으로 확정되지 않았다. 클라이언트는 임의의 기본값을 가정하면 안 된다.

- staging·production Base URL
- 인증·권한 방식
- rate limit
- 외부 클라이언트 timeout·재시도 기준
- API 호환성·폐기 정책

## 7. 계약 원본과 생성물

HTTP 계약의 기준은 FastAPI 경로 선언과 Pydantic request/response model이다. 저장소의 OpenAPI와 TypeScript 타입은 여기서 생성되는 동기화 산출물이며 직접 수정하지 않는다.

| 경로 | 역할 |
|---|---|
| `services/rag-api/src/pickcardu_rag_api/main.py` | FastAPI 경로와 HTTP 응답 계약의 구현 원본 |
| `packages/contracts/openapi.yaml` | 코드에서 생성한 기계 판독용 OpenAPI snapshot |
| `packages/contracts/generated/api.ts` | 같은 OpenAPI에서 생성한 클라이언트 연동용 TypeScript 타입 |
| `packages/contracts/README.md` | 계약 생성과 drift 검사 방법 |

API 계약 변경은 구현과 Pydantic model을 먼저 수정하고, OpenAPI와 TypeScript 타입을 재생성한 뒤 drift 검사를 통과시키는 순서로 진행한다.
