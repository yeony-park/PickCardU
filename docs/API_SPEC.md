# PickCardU RAG API 명세

## 1. 문서 목적과 기준

이 문서는 현재 구현된 PickCardU RAG FastAPI 서비스의 팀 공유용 HTTP 계약이다. 구현 예정 기능이 아니라 현재 FastAPI 경로, Pydantic schema와 생성된 OpenAPI 계약을 기준으로 작성했다.

- API 버전: `0.1.0`
- 기본 데이터 형식: `application/json`
- 기준 구현: `services/rag-api/src/pickcardu_rag_api/{main,chat,models,chat_models}.py`
- 기계 판독용 계약: `packages/contracts/openapi.yaml`
- 클라이언트 연동용 TypeScript 타입: `packages/contracts/generated/api.ts`
- 현재 경로: 7개 URL, 9개 method/path 조합

FastAPI 실행 중에는 `/docs`에서 Swagger UI, `/openapi.json`에서 런타임 OpenAPI 문서를 확인할 수 있다.

> 계정 로그인·사용자 프로필은 미구현이다. 대화 API만 익명 브라우저 쿠키로 소유자를 구분하며 공개 서비스 인증을 대신하지 않는다. 현재 계약을 외부 공개 API 계약으로 간주하면 안 된다.

실행, release 활성화, health 점검과 rollback 절차는 [`API_RUNBOOK.md`](API_RUNBOOK.md)를 참고한다. 검색·답변 내부 구조는 [`rag_pipeline.md`](rag_pipeline.md)를 참고한다.

## 2. 공통 HTTP 계약

### 2.1 서버 주소와 인증

| 항목 | 계약 |
|---|---|
| 개발 환경 예시 Base URL | `http://127.0.0.1:8000` |
| staging Base URL | 미정 |
| production Base URL | 미정 |
| 인증 | 계정 인증 없음; 대화 경로는 익명 소유자 쿠키 필요 |
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
| `POST` | `/v1/browser-session` | 익명 세션 쿠키 발급·유지 | 없음 |
| `GET` | `/v1/conversations` | 소유자의 저장 대화 목록 | 없음 |
| `POST` | `/v1/conversations` | 멱등 대화 생성 | 없음 |
| `GET` | `/v1/conversations/{conversation_id}/messages` | 질문·답변·실패 복원 | 없음 |
| `POST` | `/v1/conversations/{conversation_id}/messages` | 질문 저장·맥락 반영·검색·답변 저장 | 조건부 rewrite 최대1회 + embedding 최대1회 + 기존 answer 최대2회 |

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
- 답변 생성 API에는 질문과 검색된 근거의 요청 내부 ID(`e1`, `e2`, ...), 카드명, 카드사, 본문을 전송한다.
- 원본 `card_key`와 `chunk_id`는 답변 생성 API에 전송하지 않는다. 서버가 내부 ID를 원본 카드·청크에 다시 연결하고, 각 citation이 실제 검색 근거이며 하나의 카드에만 속하는지 검증한다.
- 질문과 근거 JSON의 답변 입력 payload는 기본 UTF-8 **64,000 bytes** 이하로 제한한다. 서버 설정 `PICKCARDU_ANSWER_PAYLOAD_BYTES`로 양의 정수 상한을 변경할 수 있으며, 검색 근거 구성과 provider 전송 직전 검사가 같은 값을 사용한다. 클라이언트 요청 필드가 아니며 무제한 모드는 제공하지 않는다. 이 바이트 예산은 시스템 지침·출력 공간까지 포함하는 모델의 토큰 한도와 다르다.

이 요청 내부 ID는 LLM 출력 검증에만 사용하며 HTTP 응답에는 노출하지 않는다. 클라이언트는 아래 응답 계약대로 원본 `card_key`와 `chunk_id` 기반 citation을 받는다.

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
| `answer` | string | 근거 기반 한국어 답변이다. 답변 생성 상한은 1,200자다. |
| `recommendations` | array | 최대 5개 추천과 근거 citation을 담는다. |
| `claims` | array | 최대 5개 원자 주장과 조건·수치·근거 citation을 담는다. |
| `cards` | array | 답변에 사용된 검색 카드 목록이다. |
| `evidence` | array | 답변 생성에 제공된 근거 목록이다. |
| `usage.embedding` | object | 질문 embedding 사용량과 지연 정보다. |
| `usage.answer` | object | 답변 모델, 시도 횟수, 입력 크기, 사용량과 지연 정보다. |

추천과 claim에는 `card_key`와 1~2개의 `citations`가 필요하다. 카드별 추천 이유 `reason`은 최대 400자, claim의 `text`는 최대 120자다. claim은 선택적으로 `value`, `unit`, 최대 2개의 `conditions`를 포함한다. 서버는 citation이 실제 검색 근거이며 동일 카드에 속하는지 검사한다. 글자 수는 상한이지 답변 길이의 목표가 아니며, 실제 설명은 질문과 확인된 근거에 따라 달라진다.

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

답변 프롬프트는 일부 카드·항목만 확인되어도 질문에 직접 답할 수 있는 사실이 있으면 `answered`로 부분 답변하도록 지시한다. 확인된 사실에만 claim·citation을 붙이고, 미확인 카드·항목은 본문에 따로 설명한다. 순위는 같은 혜택·이용 기준에서 비교 가능한 카드로 한정하며 전체 후보의 최저/최고로 확대하지 않는다. 이는 모델 지침이며 실제 출력의 의미 품질을 보장하지 않는다. 채팅 범위 검색에서 대상 카드의 최종 근거가 아예 없는 경우에는 §8.3의 기존 서버 사전 차단 규칙이 여전히 적용된다.

## 4. 오류 계약

질의·대화 API의 공통 오류 응답 형식:

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
| 503 | `EVIDENCE_PACKAGE_TOO_LARGE` | 아니요 | 최상위 근거 청크 하나만으로도 허용된 답변 입력 크기를 초과할 때 |
| 503 | `LLM_UNAVAILABLE` | 예 | 답변 생성 provider를 사용할 수 없을 때 |
| 503 | `LLM_UNGROUNDED` | 예 | 답변 schema 또는 citation 소유권 검증에 실패했을 때 |
| 401 | `BROWSER_SESSION_REQUIRED` | 아니요 | 익명 쿠키가 없거나 유효하지 않음 |
| 403 | `ORIGIN_NOT_ALLOWED` | 아니요 | 대화 POST 출처가 허용되지 않음 |
| 404 | `CONVERSATION_NOT_FOUND` | 아니요 | 대화 없음 또는 다른 소유자; 같은 응답으로 구분 불가 |
| 409 | `REQUEST_ID_CONFLICT` | 아니요 | 동일 요청 ID에 다른 query/profile/top_k/wallet_context |
| 409 | `CONVERSATION_CONTEXT_CONFLICT` | 아니요 | 동일 대화 생성 UUID에 다른 설문 내용 |
| 409 | `TURN_EXECUTION_CONFLICT` | 아니요 | 이미 확정한 내부 실행 조건의 변경 시도; 서버 진단 필요 |
| 409 | `TURN_IN_PROGRESS` / `CONVERSATION_BUSY` | 아니요 | 같은 질문 또는 해당 대화의 다른 질문 처리 중; 조회 우선 |
| 409 | `TURN_ATTEMPT_STALE` | 아니요 | 만료되거나 교체된 처리 시도의 저장 거절 |
| 503 | `CHAT_STORAGE_UNAVAILABLE` | 예 | 대화 SQLite 손상·지원하지 않는 schema·잠금·디스크 부족 등 |
| 502 / 504 | `API_UNREACHABLE` / `API_TIMEOUT` | 예 | Next proxy 연결 실패/540초 deadline; 전송 재시도 전 저장 상태 조회 |
| 413 | `REQUEST_TOO_LARGE` | 아니요 | Next proxy의 8KiB body 한도 초과 |

`GET /v1/health/ready`의 `503`은 위 공통 오류가 아니라 `{status, reason}` 형식이다.

## 5. 답변 안전 계약과 한계

- LLM에는 서버가 검색한 근거만 전달한다.
- 답변 입력 한도 안에 들어오는 순위 상위의 완전한 근거 청크만 전달한다. 다음 청크가 한도를 넘으면 거기서 멈추며 청크 본문을 중간에서 자르지 않는다.
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
| `services/rag-api/src/pickcardu_rag_api/{main,chat}.py` | FastAPI 경로와 처리 원본 |
| `services/rag-api/src/pickcardu_rag_api/{models,chat_models}.py` | Pydantic request/response 계약 원본 |
| `packages/contracts/openapi.yaml` | 코드에서 생성한 기계 판독용 OpenAPI snapshot |
| `packages/contracts/generated/api.ts` | 같은 OpenAPI에서 생성한 클라이언트 연동용 TypeScript 타입 |
| `packages/contracts/README.md` | 계약 생성과 drift 검사 방법 |

API 계약 변경은 구현과 Pydantic model을 먼저 수정하고, OpenAPI와 TypeScript 타입을 재생성한 뒤 drift 검사를 통과시키는 순서로 진행한다.

## 8. 저장형 채팅 HTTP 계약

### 8.1 세션·출처·보존

브라우저는 Next.js의 같은 출처 `/api/chat/...`를 사용한다. suffix는 `/v1/...`와 같다. 예: `/api/chat/conversations` → `/v1/conversations`. 서버 측 `PICKCARDU_RAG_API_BASE_URL`로 FastAPI 주소를 지정하며 브라우저가 목적지를 정하지 않는다. Next POST·DELETE는 접속한 출처와 정확히 일치하는 Origin만 허용하며 검증 후 API 자체 출처로 정규화한다. FastAPI는 API 자체 출처 또는 명시된 개발 Origin만 허용한다. 허용 Origin 정책은 유지하며 CORS 허용 메서드는 GET·POST·DELETE다.

`POST /v1/browser-session`은 `{}`를 받고 `200 {"status":"ready"}`를 반환한다. 쿠키가 없거나 유효하지 않으면 32바이트 난수 토큰을 발급하며 유효한 기존 쿠키는 유지한다. 쿠키 이름은 `pickcardu_browser`, HttpOnly, SameSite=Lax, Path=/, 90일이며 사용 시 갱신한다. 로컬 HTTP이므로 Secure=false다. 원문 토큰은 JSON·URL·브라우저 저장소에 넣지 않는다. DB에는 SHA-256 해시만 저장한다.

최초 다중 탭 초기화만 Web Locks로 순서를 정하며 일반 질문 전송은 잠그지 않는다. 기능 미지원 환경에서는 클라이언트 `BROWSER_SESSION_UNSUPPORTED` 안내 후 저장형 채팅을 중단한다. 브라우저 브랜드만으로 지원 여부를 판단하지 않는다.

대화는 RAG와 별도 평문 로컬 SQLite에 자동 삭제 없이 저장한다. 사용자가 명시적으로 삭제한 대화는 해당 메시지와 함께 제거한다(8.5). 쿠키 삭제/만료·다른 브라우저/프로필/PC에서는 DB가 남아 있어도 자동 복원되지 않는다. 모든 대화 응답(오류 포함)은 `Cache-Control: no-store`다.

### 8.2 생성·목록

`POST /v1/conversations`:

```json
{"client_conversation_id":"00000000-0000-4000-8000-000000000001"}
```

UUID는 한 번의 새 대화 생성에 고정한다. 신규 `201`, 같은 소유자/UUID와 동일한 설문 재전송은 `200`이며 동일 Conversation을 반환한다. 선택 필드 `survey_context`는 첫 생성 때 확정하며 이후 덮어쓰지 않는다. 동일 UUID로 다른 설문을 보내면 `409 CONVERSATION_CONTEXT_CONFLICT`다. 필드 생략·null·빈 설문은 같은 의미다. 필드 상세는 8.6절을 참고한다:

```json
{"id":"00000000-0000-4000-8000-000000000002","title":"새 채팅","created_at":"2026-01-01T00:00:00+00:00","updated_at":"2026-01-01T00:00:00+00:00"}
```

예시 UUID와 시간은 설명용이다. 첫 질문 앞 32자가 제목이 되고 제목용 LLM 호출은 없다. 질문 없는 대화는 목록에 나오지 않는다.

`GET /v1/conversations?limit=40&cursor=<opaque>`는 `{conversations: Conversation[], next_cursor: string|null}`을 최신 갱신순으로 반환한다. limit은 기본40, 범위1~100. 다음 페이지에 같은 cursor를 사용하며 값 자체는 접근 권한이 아니다.

### 8.3 질문 전송·응답

`POST /v1/conversations/{conversation_id}/messages`:

```json
{"query":"첫 번째 카드 연회비는?","client_request_id":"00000000-0000-4000-8000-000000000003","top_k":3,"profile":null,"retry_failed":false}
```

- conversation_id/client_request_id는 UUID. query/profile/top_k는 공통 질의 규칙과 같으며 기본 top_k=3, profile=null이다.
- 사용자 웹 `/chat`은 `top_k=5`를 명시해서 최대5개 카드 후보를 요청한다. API에서 필드를 생략했을 때의 기본값3은 유지한다. 실제 추천 개수는 관련 근거와 답변 판단에 따라5개보다 적을 수 있다.
- 선택 필드 `wallet_context`는 이번 질문 시점의 My Page 보유 목록 snapshot이다. 일반 질문에도 요청/재시도 보존용으로 저장될 수 있지만, 개인화·내 카드 의도로 판별한 경우에만 검색/답변에 적용한다. 적용 의도는 클라이언트가 지정하지 않는다.
- 질문 예약·저장을 먼저 하고 맥락·RAG·LLM 처리 중에는 DB transaction을 유지하지 않는다.
- 최근 완료된 대화 최대2쌍, 과거 최대5500자와 현재 질문 최대500자를 사용한다. 실제 추천 순서를 유지한다. failed/pending은 제외한다. 근거 부족·확인 질문은 **미확인 정보**로 표시해 질의 재작성에 전달하며, 혜택의 긍정 근거로 취급하지 않는다.
- 첫 질문/유효 맥락 없음은 설문이 없고 wallet이 null/empty이면 rewrite0회다. 설문이 있거나 wallet이 ready/needs_review이면 의도 판별 rewrite1회가 추가된다. 후속 질문은 기존 rewrite1회에 개인화 판별을 통합하며 추가 분류 호출을 하지 않는다. 설정이 없는 첫 질문의 명백한 내 카드/내 소비 패턴 요청은 제한적인 서버 규칙으로 설정 확인을 안내한다. 폭넓은 무설정 표현 판별은 보장하지 않는다. 재작성 실패는 저장된 실패가 되며 원문으로 조용히 fallback하지 않는다.
- LLM이 DB를 직접 읽지 않는다. 서버가 제한된 이전 대화와 설정의 존재 여부/상태/보유 수를 rewrite provider에 전달한다. 실제 설문값·보유 상세는 판별 입력에 넣지 않는다. 답변 provider에는 독립 질의·새 검색 근거와 적용 결정된 개인화 context만 주며 과거 답변의 혜택·citation을 새 근거로 재사용하지 않는다.
- 기존 rewrite 호출 하나에서 `global`(전체 검색), `previous`(이전 카드 범위), `clarification`(대상 확인)을 함께 판단한다. 특정 문장 일치 방식이 아니다. 예: “저 카드 중”, “저것들 중”, “두 번째 것”은 문맥과 추천 순서로 해석하고, “그러면 주유 혜택 카드 추천”은 새 전체 검색으로 판단하도록 지시한다. 실제 모델의 모든 표현 인식률을 보장하는 것은 아니다.
- 같은 rewrite 출력의 `operation=retrieve|recall`로 새 상품 사실 검색과 이전 목록 확인을 구분한다. `previous+recall`은 “위 카드 3개가 뭐지?”, “두 번째 카드 이름?”, “아까 추천한 발급사는?”처럼 저장된 카드명·발급사·목록 순서만 안내한다. “이름하고 연회비”, 실적·혜택·종류·조건·추천 이유·비교는 `retrieve`로 새 근거 검색을 유지한다. `global+recall`이나 잘못된 ref 조합은 확인 질문으로 닫는다.
- 목록 확인은 소유 대화의 검증된 ref 메타데이터를 원래 순서/번호로 조합하며 검색·embedding·답변 LLM·활성 index loader를 호출하지 않는다. 문맥 판별 rewrite는 여전히 최대1회 호출한다. 예전 혜택 설명·추천 이유·citation을 복사하거나 새 금융 사실로 검증했다고 표시하지 않는다.
- chat의 `usage.answer.reason=conversation_card_recall` 응답은 기록 안내 예외다. `answer_status=answered`, `query_type=proper_noun`이며 `cards/recommendations/claims/evidence`는 빈 배열이다. `release_id/profile`은 저장된 참조 메타데이터이며 현재 release로 재검색했다는 뜻이 아니다. 새 snapshot은 카드별 출처를 보존한다. 카드별 출처가 없는 과거 snapshot은 해당 snapshot을 기록한 turn의 값을 사용하므로 원래 추천 시점의 출처는 확실하지 않다. 일반 `/v1/answer`와 상품 혜택 답변은 기존 grounded claim 규칙을 유지한다.
- rewrite에는 이전 목록의 카드명·발급사·서버 부여 짧은 ref만 전달한다. 모델이 선택한 ref를 소유 대화의 실제 card_key로 서버가 해석한다. 정상 파싱 후 중복 ref·서로 다른 목록의 혼합·불명확한 대상은 전체 검색으로 fallback하지 않고 확인 질문으로 응답한다. 허용되지 않은 ref(enum 위반)·응답 형식 위반으로 provider 파싱이 실패하거나 provider 호출 자체가 실패하면 기존 `503 LLM_UNAVAILABLE`로 처리한다. 이 경우에도 전체 검색으로 범위를 확대하지 않는다.
- scope snapshot이 없는 기존 저장 대화는 최근 정상 완료 응답 최대2개에서 추천 목록을 읽어 참조를 복원한다. 과거 답변 전문을 추가로 보내거나 유료 호출을 늘리는 방식이 아니다. 참조 가능한 목록은 최대2개로 제한된다.
- `previous`는 키워드와 벡터 양쪽에서 카드별 범위를 **후보 LIMIT 이전**에 적용한다. 전역 검색 뒤에 필터링하지 않는다. 기존의 검증된 임베딩을 활용하며 재임베딩·release 변경은 없다. 전역 경로는 기존 Chroma 검색을 유지하고, 범위 벡터 검색은 대상 행의 squared-L2를 사용한다.
- 비교 카드별 후보와 최종 근거를 순서대로 번갈아 배치한다. 비교 대상이 요청 top_k보다 많으면 **검색 근거용 카드 상한**은 대상 수(최대5)까지 확보한다. 이는 새 카드 추천 범위를 넓히거나 추천 개수를 반드시 채운다는 의미가 아니다.
- 대상 카드 중 하나라도 최종 근거가 없으면 답변 LLM을 호출하지 않고 `insufficient_evidence`로 반환한다. 현재 release에 대상 ID가 없으면 embedding도 호출하지 않는다. 청크가 있다는 것만으로 질문한 조건이 확인되었다고 판단하지 않으며, 답변 모델은 카드별 조건 미확인을 밝히도록 지시한다. 현재 서버 검증이 의미적 사실 일치를 보장하는 것은 아니다.
- 확인 질문은 UI 변경 없이 기존 `200` TurnResponse의 completed assistant 메시지로 저장된다. `answer_status=insufficient_evidence`, 카드·추천·주장·근거는 빈 배열이며 embedding/답변 LLM은 호출하지 않는다. 기존 UI에서 “근거 부족” 표시와 함께 확인 질문이 보일 수 있다.

성공 `200`은 `{turn_id:string, messages:[user,assistant]}`다. ChatMessage 필드:

| 필드 | 타입 / 의미 |
|---|---|
| id / turn_id / client_request_id | 메시지 ID / 질문·답변 쌍 ID / 재시도에도 유지하는 요청 UUID |
| seq / role / content | 메시지 순번 / user 또는 assistant / 표시 텍스트 |
| status | completed, pending, failed. 저장된 사용자 질문은 completed |
| answer | assistant 완료의 기존 AnswerResponse 전체, 그 외 생략 또는 null |
| rewrite_usage | assistant의 `{provider_called,model,latency_ms,usage}` 또는 null; 미제공 필드는 생략 가능 |
| error | assistant 실패의 공통 ErrorResponse, 그 외 생략 또는 null |
| created_at | turn 생성 시각 |
| input_snapshot | failed assistant에만 반환하는 원래 `{query,profile,top_k,wallet_context}`. user/completed/pending 메시지에서는 생략하며 새 질문의 값으로 바꾸지 않는다. |

`insufficient_evidence`도 completed로 저장한다. 새 실패는 `503` 또는 `409`의 ErrorResponse이며 GET으로 저장된 질문·실패를 조회할 수 있다. 실패 결과의 동일 ID 재조회는 `200` TurnResponse로 반환한다. 기존 AnswerResponse/usage에는 rewrite 필드를 추가하지 않는다.

검색 진단은 기존 확장 가능 dict인 `answer.usage.answer`에 저장된다. 부족 응답에서 외부 `cards/evidence`가 비어도 다음 값으로 검색과 대상의 실제 상태를 확인할 수 있다. API 비밀키·문서 본문·전체 대화 전문은 진단에 넣지 않는다.

| 항목 | 의미 |
|---|---|
| `conversation_scope.scope` | global / previous / clarification |
| `conversation_scope.operation` | retrieve(새 상품 사실 검색) / recall(이전 목록의 식별정보 안내) |
| `conversation_scope.target_card_keys` | 서버가 선택한 최대5개 정밀 비교 대상 ID. 일반 global/clarification은 null; 보유 카드 전체 정밀 조회·비교는 global에서도 목록을 가질 수 있다. |
| `conversation_scope.use_survey` / `use_wallet` / `wallet_operation` | 이번 실행의 설문·보유 정보 적용 여부와 none/lookup/compare. 일반 질문은 적용하지 않는다. |
| `conversation_scope.reference_groups` | 최대2개 목록, 목록당 최대5개의 카드 ID·이름·발급사와 release/profile snapshot. 새 기록은 카드별 출처를 보존하며, 카드별 값이 없는 과거 기록은 snapshot 기록 turn의 값으로 보충한다(원래 추천 출처 불확실). 부족/확인/목록 안내 응답 및 일부 카드만 안내한 뒤에도 참조 대상을 유지한다. 혜택·citation은 보존 근거로 재사용하지 않는다. |
| `retrieval.target_card_keys` | 검색에 적용한 범위; global은 null |
| `retrieval.retrieved_card_keys` / `evidence_card_keys` | 실제 후보에서 발견된 카드 / 최종 답변 입력 근거의 카드 |
| `retrieval.evidence_counts` / `missing_card_keys` | 비교 대상별 최종 청크 수 / 근거가 없는 대상. 조건 확인 여부나 품질 점수가 아니다. |
| `retrieval.unavailable_card_keys` | 현재 활성 release에 존재하지 않는 이전 대상 ID |
| `retrieval.missing_required_groups` | 보유/신규 비교의 최종 온전한 근거가 없는 쪽(owned/new). 한쪽이라도 누락되면 답변 LLM을 호출하지 않는다. |
| `retrieval.stages` | bm25/vector/rrf/leaf/rerank 단계별 chunk ID. scoped component 순위는 카드 안에서 계산한다. |
| `retrieval.evidence_budget` | payload 크기·한도·예산 제외 ID·truncation. scoped는 큰 청크를 건너뛰고 다른 카드의 온전한 청크가 들어갈 여지를 남긴다. |

`conversation_scope`는 대화 응답에만 존재한다. 공개 `/v1/search`, `/v1/answer` 요청에는 ref나 target_card_keys를 추가하지 않았다. 클라이언트가 범위를 직접 신뢰 입력으로 전달하지 않는다. 유료 실제 호출 없이 실행하는 테스트는 서버의 분기·범위 격리·저장·호출 수를 검증하며, 자연어 판별과 최종 답변 품질은 별도 실호출 검증 대상이다.

### 8.4 복원·페이지·재시도

`GET /v1/conversations/{conversation_id}/messages?limit=50&before_seq=<turn-seq>`는 `{messages:ChatMessage[],next_before_seq:number|null,has_pending:boolean,survey_context:SurveyContext|null}`이다. limit 기본50, 범위1~100이며 **turn(질문·답변 쌍) 단위**다. 메시지는 시간순이며 user seq=turn.seq*2-1, assistant seq=turn.seq*2. 다음 과거 페이지는 next_before_seq를 그대로 사용한다. 이 값은 메시지 seq가 아니다. survey_context는 대화에 처음 저장한 값이며 구버전 대화는 null이다.

같은 요청 ID·같은 query/profile/top_k/wallet_context의 완료 재전송은 저장 결과만 반환하며 provider를 호출하지 않는다. pending은409, failed는 기본 재조회만 한다. 사용자의 명시적 실패 재시도만 `retry_failed:true`로 같은 ID를 다시 보낸다. My Page가 변경됐어도 failed input_snapshot으로 원래 body를 재구성해야 한다. 실제 body가 다르면409다. 구버전 request_json의 wallet 필드 생략과 새 null은 동일하게 비교하며 DB 원문을 덮어쓰지 않는다. 대화당 pending은1개이며 서로 다른 대화는 별개다.

확정된 의도·질의·검색 대상·적용 context·목록 순서는 내부 execution snapshot으로 보존한다. 실패 재시도는 이를 재사용해 rewrite0회이며, 확정 전 판별 실패는 다시 rewrite가 필요할 수 있다. 새 상품 근거는 매번 검색하고 적용 대상의 현재 활성 ID 및 citation 소유권을 다시 검증한다. 과거 실행 snapshot을 금융 사실의 근거로 재사용하지 않는다. 원래 wallet을 적용하지 않은 일반 질문은 누락된 wallet 키 때문에 차단하지 않는다.

pending lease10분이 지나면 다음 조회/쓰기에서 `TURN_INTERRUPTED` failed로 기록한다. 자동 LLM 재실행은 하지 않는다. 네트워크 오류/502/504/비JSON 응답은 결과 불명일 수 있으므로 **먼저 GET**한다. pending/완료에 대해 새 ID로 자동 전송하지 않으며 확인된 failed만 명시적으로 재시도한다. 클라이언트 `RESULT_UNKNOWN`은 자동 재전송 금지 안내다.

provider 성공 후 SQLite 기록 전 프로세스 중단은 하나의 transaction으로 묶을 수 없다. 이 경우 과금 exactly-once는 보장하지 않으며 사용자가 명시적으로 재시도하면 호출이 중복될 수 있다. GET 복원·완료 재조회는 외부 API를 호출하지 않는다.

### 8.5 단일 대화 삭제

`DELETE /v1/conversations/{conversation_id}`는 본인 브라우저 쿠키 소유의 대화와 해당 질문·답변을 삭제한다. 요청 본문은 없고 성공은 **본문 없는 `204 No Content`**다. Next 경로는 `DELETE /api/chat/conversations/{conversation_id}`다. 전체 대화 삭제나 RAG 인덱스 삭제는 지원하지 않는다.

- 소유권 확인·pending 검사·삭제는 동일 SQLite 쓰기 transaction에서 수행하며, 메시지는 기존 FK cascade로 함께 제거한다.
- 세션 없음은 `401 BROWSER_SESSION_REQUIRED`, 허용되지 않은 Origin은 `403 ORIGIN_NOT_ALLOWED`, 다른 소유자 또는 이미 삭제된 대화는 `404 CONVERSATION_NOT_FOUND`다.
- 답변 생성 중인 pending 대화는 `409 CONVERSATION_BUSY`로 거부한다. 만료된 pending은 기존 메시지 조회의 lease 처리를 거친 후 삭제할 수 있다.
- 형식이 잘못된 UUID는 `422`, 저장소 오류는 `503 CHAT_STORAGE_UNAVAILABLE`다. 모든 오류는 공통 ErrorResponse다.
- 삭제는 휴지통·실행 취소 없이 영구 삭제한다. 클라이언트는 삭제 전 확인하고 성공 후에만 목록을 제거한다. 현재 대화를 삭제하면 빈 채팅 화면으로 돌아간다.
- 삭제 자체에는 embedding·LLM 호출이 없다. 실패 또는 결과 불명 시 자동 재전송하지 않는다. 이미 삭제된 404는 클라이언트에서 목록 정리에 사용할 수 있다.

### 8.6 대화별 설문과 My Page 개인화

설문 생성 요청 예시:

```json
{
  "client_conversation_id": "00000000-0000-4000-8000-000000000001",
  "survey_context": {
    "monthly_spending": "50-100",
    "spending_categories": ["카페", "여행"],
    "preferred_benefits": ["할인"]
  }
}
```

| SurveyContext 필드 | 허용값 / 기본값 |
|---|---|
| monthly_spending | under-30 / 30-50 / 50-100 / 100-200 / over-200 또는 null. 단위는 월평균 카드 사용액 만 원 구간이며 기본 null |
| spending_categories | 쇼핑 / 배달·외식 / 카페 / 교통 / 주유 / 여행 중 최대6개, 기본 [] |
| preferred_benefits | 할인 / 포인트 적립 / 항공 마일리지 중 최대3개, 기본 [] |

중복 선택·알 수 없는 값/필드는422다. 선택 배열은 화면 정의 순서로 정규화한다. 모두 비어 있으면 null이다. 저장은 첫 대화 생성 때 한 번이며 같은 대화 후속·새로고침·복원에 유지한다. 새 채팅은 초기화하고 질문 텍스트로 저장값을 자동 변경하지 않는다. 설문 편집 endpoint는 없다.

질문 body의 선택 필드 wallet_context 예시: `{"status":"ready","card_keys":["issuer/card_document"]}`. 예시 키는 실제 활성 카탈로그의 정확한 document_id로 교체해야 한다.

| WalletContext 필드 | 허용값 / 규칙 |
|---|---|
| status | ready / empty / needs_review, 필수 |
| card_keys | 최대106개의 중복 없는 정확한 ID, 기본 []. ready는1~106개, empty/needs_review는[]만 허용. 각 키는1~64자이며 앞뒤 공백은 허용하지 않음 |

카드 등록 원본은 브라우저 localStorage의 기존 이름 목록이다. 클라이언트는 카탈로그 sourcePath의 `data/raw/`·`.pdf`를 제거해 ID를 정확히 매핑한다. 이름이 모호하거나 저장값이 손상되면 needs_review이며 추측하지 않는다. 전송한 목록은 turn request snapshot으로 채팅 DB에도 저장될 수 있지만, 공통 사용자 wallet DB나 자동 동기화 기능은 아니다. 대화 삭제는 관련 snapshot도 제거한다.

| 질문 | 적용·검색 정책 |
|---|---|
| 편의점/여행 등 일반 혜택 추천 | 저장 설문·보유 정보를 적용하지 않고 현재 원문 질문으로 전체 검색 |
| 내 소비 패턴에 맞는 추천 | 필요한 설문 조건을 적용하고 전체 카드에서 후보 검색. 보유 카드 정보 사용이 필요하면 보유/신규 후보를 함께 비교하며 보유 목록만으로 추천을 제한하지 않음 |
| 내 카드 혜택 | 보유 허용 집합을 키워드·벡터 후보 제한 전에 적용 |
| 보유 카드와 새 카드 비교 | 신규 집합에서 보유 카드를 후보 제한 전에 제외. 질문 embedding은1회이며 최종 답변 입력에 양쪽 온전한 근거를 최소1개씩 확보 |
| 보유 카드 각각의 정밀 조건 비교 | 최대5개. 6개 이상은 대상을 좁히도록 확인하며 앞5개로 조용히 자르지 않음 |
| 이전 목록 이름·발급사 확인 | 저장된 목록 순서만 안내하고 개인화/검색/embedding/답변 LLM/index loader를 사용하지 않음 |

보유 카드를 모두 포함한 활성 카탈로그에는 신규 후보가 없으므로 판별 후 embedding/답변 호출 없이 비교 불가를 안내한다. 비교에 필요한 근거용 카드 수는 요청 top_k보다 클 수 있다(최대5개 정밀 대상 또는 보유/신규 최소2개). 추천 출력은 최대5개이며 반드시 개수를 채우지 않는다.

현재 질문이 명시한 금액·소비 영역·혜택은 같은 차원의 저장 설문보다 우선한다. 적용할 값만 내부 context와 검색 힌트에 사용하며, 월 사용액 구간을 전월실적 충족·정확한 소비금액·예상 혜택액으로 단정하지 않는다. 검색 query는 최대1,024자, 공개 질문/답변용 독립 query는 기존 최대500자로 구분한다. 실제 answer query+개인화 context+근거 전체의 UTF-8 JSON bytes를 같은 serializer로 측정해 설정된 한도(기본64,000)를 지킨다. HTTP proxy의8,192-byte 한도와는 별개다.

실제 모델의 다양한 표현 분류 정확도와 추가 비용·지연은 자동 가짜 응답 테스트만으로 검증되지 않는다. 별도 실호출 평가가 필요하며, 설정이 있는 첫 일반 질문도 판별 비용은 발생한다.
