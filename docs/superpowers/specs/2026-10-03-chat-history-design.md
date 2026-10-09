# 저장형 채팅과 후속 질문 맥락 설계안

작성일: 2026-10-03. 구현 승인: 2026-10-05. 아래 계약을 기준으로 구현하며 완료 여부는 구현 계획과 검증 기록에서 구분한다.

## 1. 확정된 요구사항

- 병합 전까지 `chat-history` worktree의 `feat/chat-history`에서 작업한다.
- 로그인 없이 익명 브라우저 식별로 같은 브라우저의 대화 목록과 메시지를 복원한다.
- 대화는 RAG 인덱스와 다른 SQLite DB에 저장한다.
- 기존 `/chat` 초기 화면을 유지한다. 첫 질문을 보내거나 저장된 대화를 열면 메시지 목록과 하단 입력창을 보여준다.
- 기존 색상·헤더·입력창·이미지를 참고해 확장한다. Home/Cards/My Page/화면 전환 애니메이션은 변경하지 않는다.
- 정적 사이드바를 실제 대화 목록으로 바꾸고, 모바일에서도 내역·새 채팅에 접근할 수 있게 한다.
- 후속 질문은 서버가 이전 대화를 조회하고 LLM에 필요한 맥락을 전달한다. LLM이 DB에 직접 접근하지 않는다.
- 기능 구현과 별도 로컬 SQLite 저장을 승인받았다. 의존성 설치, 유료 검증 호출, merge/push는 이번 승인에 포함하지 않는다.

## 2. 설계 작성 당시 기준과 격리 범위

- 코드 기준은 `f9064ac`, worktree 준비용 빈 커밋은 `7de3f44`다.
- 작업 위치: `/home/sms/openclaw_file/.worktree/PickCardU/chat-history`.
- `apps/main/app/chat/page.tsx`: 최신 질문·답변 하나만 React 상태로 표시한다. 내역은 정적이고 저장·복원이 없다.
- `apps/main/lib/rag-api.ts`: 브라우저에서 `POST /v1/answer`를 직접 호출한다.
- `packages/rag-core/src/pickcardu_rag/answering.py`: `completed_context()`와 `OpenAIService.rewrite()`가 이미 있지만 현재 API에는 연결되지 않았다.
- `.env`, 활성 release의 `index-release/`·`serving/`·`active-index.json`, BGE 모델, 프론트 `node_modules/`, 로컬 `HANDOFF.md`를 복사했다.
- OCR 캐시·청킹 중간 산출물·indexer state DB·다른 reranker는 채팅 실행에 필요하지 않아 복사하지 않았다. Python 환경은 복사하거나 설치하지 않았다.
- 복사본은 원본과 다른 파일이다. worktree의 소스·복사 DB·모델 수정은 원본 파일에 반영되지 않는다.
- Git 객체·브랜치 관리 영역과 Python 환경은 공유된다. Python 환경을 변경하지 않는다. 원본과 worktree 서버를 동일 포트로 동시에 실행하지 않는다.
- 로컬 자산과 새 대화 DB는 Git 제외 대상이다. 나중에 소스 코드를 병합해도 worktree의 대화 DB는 자동 이전되지 않는다.

## 3. 접근 방식 비교와 권장안

| 방식 | 장점 | 이번 요구에서의 제한 |
| --- | --- | --- |
| 브라우저만 저장 | 단순한 복원 | 서버 DB 저장이라는 합의와 다르고 서버 맥락 조회가 어려움 |
| 브라우저→FastAPI 직접 연결 | 기존 호출 방식 유지 | 쿠키를 위해 credential CORS, localhost/127.0.0.1/포트 포워딩 출처를 함께 맞춰야 함 |
| 브라우저→Next.js 같은 출처 경로→FastAPI→SQLite | 브라우저 쿠키·출처 처리 일원화 | 제한된 proxy 경로 하나가 추가됨 |

세 번째를 권장한다. 브라우저는 `/api/chat/...`를 호출하고 Next.js가 고정된 로컬 FastAPI 경로로 전달한다. 저장·소유권·검색·LLM 처리는 FastAPI에 모은다. 공개 배포용 인증이 아니므로 현재 production 실행 차단은 유지한다.

## 4. 익명 브라우저 식별

- 서버가 32바이트 난수 토큰을 발급한다. 이름·이메일·fingerprint는 수집하지 않는다.
- 권장 쿠키: `pickcardu_browser`, `HttpOnly`, `SameSite=Lax`, `Path=/`, 유효기간 90일. 로컬 HTTP에서는 Secure=false이며 공개 배포에 그대로 사용하지 않는다.
- DB에는 원문 토큰 대신 SHA-256 해시를 저장한다. 토큰은 대화 접근 권한이므로 URL·JSON 응답·로그에 넣지 않는다.
- 모든 목록/메시지 조회·전송은 소유권을 확인한다. 남의 대화와 없는 대화는 같은 404로 응답한다.
- 쿠키 삭제·다른 브라우저·시크릿 모드·다른 PC에서는 자동 복원이 되지 않는다. 계정이나 컴퓨터 자체의 식별이 아니다.
- Next.js proxy는 GET/POST와 고정 경로만 허용한다. POST Origin이 접속한 Next.js 출처와 다르면 거절한다. 임의 목적지·Authorization·다른 쿠키는 전달하지 않는다.
- 검증된 Next.js 요청의 upstream Origin은 API 자체 출처로 정규화한다. FastAPI 신규 쓰기 경로도 API 자체 출처 또는 명시한 개발 출처만 허용한다. 이는 로컬 MVP의 방어이며 공개 인증을 대신하지 않는다.
- 모든 대화 응답에 `Cache-Control: no-store`를 적용한다. 기존 무상태 API의 CORS 동작은 유지한다.

## 5. 별도 SQLite와 저장 단위

기본 경로는 저장소 기준 `data/chat/runtime/chat.sqlite`, override는 `PICKCARDU_CHAT_DB_PATH`다. `data/chat/runtime/`는 Git에서 제외한다.

- `conversations`: id, owner_hash, client_conversation_id, title, created_at, updated_at. `(owner_hash, client_conversation_id)`는 unique다.
- `turns`: id, conversation_id, seq, client_request_id, request_json, query, state, attempt_id, standalone_query, answer_json, rewrite_usage_json, error_json, created_at, completed_at, lease_expires_at.
- turn 하나는 질문 하나와 답변/실패 상태 하나다. HTTP에서는 이를 user/assistant 메시지로 펼친다. 별도 messages 테이블을 중복 생성하지 않는다.
- 대화 안의 seq와 client_request_id는 각각 unique다. 한 대화에 pending turn 하나만 허용하는 partial unique index를 둔다.
- 연결은 저장 작업마다 열고 닫는다. foreign key, WAL, busy timeout=5000ms를 적용하고 예약은 `BEGIN IMMEDIATE`로 수행한다. DB transaction은 예약·완료·실패 기록에만 짧게 사용한다. embedding·검색·LLM 호출 동안 transaction을 유지하지 않는다.
- schema는 `PRAGMA user_version=1`로 식별한다. 모르는 schema version·손상·disk-full·잠금은 비파괴 오류로 처리한다. 기존 DB를 자동 삭제/재생성/덮어쓰기하지 않는다.
- ChatStore 생성과 앱 import는 DB를 만들지 않는다. 실제 채팅 API 사용 시 lazy 초기화한다. OpenAPI 생성도 DB를 만들지 않아야 한다.
- 첫 질문 앞 32자를 제목으로 사용한다. 제목 생성에 추가 LLM 호출을 쓰지 않는다.
- 새 채팅 버튼은 빈 화면을 준비하며 첫 전송 때 대화를 만든다. 생성에도 UUID `client_conversation_id`를 쓰고 동일 소유자/ID 재전송은 같은 대화를 반환한다. 응답 유실로 빈 대화가 중복 생성되지 않는다.
- 첫 질문 전 임시 제목은 '새 채팅'이고, turn이 없는 대화는 목록에서 숨긴다. 생성 후 전송 실패가 발생하면 대화 ID/생성 UUID를 유지한다. UUID는 token과 달리 권한 정보가 아니다.
- 자동 삭제는 하지 않는다. 삭제 UI·제목 편집·대화 내보내기는 이번 MVP 밖이다. 로컬 DB는 평문이며 로컬 접근 권한에 의존한다. 대화 저장과 일부 외부 LLM 전송을 공유 문서에 설명한다.
- 쿠키 유효기간은 사용 시 90일로 갱신하되 삭제/만료 뒤 기존 데이터 자동 복구는 보장하지 않는다. DB만 백업해도 브라우저 쿠키가 없으면 자동으로 소유권을 복원할 수 없다.
- worktree의 대화 DB는 임의 폐기하지 않는다. 병합/폴더 제거 전에 테스트 대화를 보존·이전할지 사용자에게 확인하고 필요하면 SQLite backup API로 일관된 백업을 만든다. WAL 사용 중 DB 파일 하나만 복사하는 절차를 안내하지 않는다. 실제 삭제는 경로·범위 확인과 명시적 승인 후에만 수행한다.

### 실패·중복·재시작

- 질문을 먼저 저장하고 assistant를 pending으로 표시한다. 검증된 AnswerResponse 전체를 저장하며 `insufficient_evidence`도 정상 완료로 저장한다.
- 실패하면 질문과 안전한 ErrorResponse를 남긴다. exception 원문·API 키·로컬 경로는 노출하지 않는다.
- client_request_id는 UUID이며 한 번의 전송에 고정한다. 완료된 동일 ID·동일 요청 재전송은 기존 결과를 반환하고 API를 다시 호출하지 않는다.
- 동일 ID인데 query/profile/top_k가 다르면 409 `REQUEST_ID_CONFLICT`. 같은 turn이 pending이면 409 `TURN_IN_PROGRESS`, 다른 turn이 pending이면 409 `CONVERSATION_BUSY`다.
- failed 재전송은 `retry_failed=false`일 때 저장된 상태만 반환한다. 사용자의 명시적 재시도만 true를 보내 같은 turn을 재예약한다. 질문을 복제하지 않는다.
- pending lease 권장값은 10분이다. 만료된 turn은 다음 조회/쓰기에서 `TURN_INTERRUPTED` failed로 바꾸며 자동으로 LLM을 재호출하지 않는다.
- 완료/실패 기록은 attempt_id를 확인한다. 이전 시도의 늦은 응답이 새 재시도 결과를 덮지 못하게 한다.
- network 오류/502/504는 결과 불명으로 취급한다. 먼저 GET으로 저장 상태를 조회하고 pending/완료이면 새 유료 처리를 시작하지 않는다. failed로 확인된 경우에만 사용자가 명시적으로 재시도한다.
- 외부 provider 성공과 SQLite 완료 기록은 하나의 transaction이 아니므로 exactly-once 과금은 보장하지 않는다. 성공 후 DB 기록 전 프로세스가 중단되면 명시적 재시도 때 외부 호출이 중복될 수 있다.
- 승인된 방식: 쿠키 없는 최초 다중 탭 초기화만 browser Web Locks로 직렬화한다. client 초기화 promise도 한 탭 안에서 재사용한다. Web Locks를 사용할 수 없는 환경에서는 저장형 채팅 제한을 안내하고 중단한다. 일반 질문 전송이나 서로 다른 대화는 이 잠금으로 직렬화하지 않는다. 다른 토큰을 경쟁 발급하는 fallback은 넣지 않는다.

## 6. 신규 HTTP 계약 권장안

FastAPI 경로는 아래와 같다. 브라우저에서는 `/v1`을 `/api/chat`으로 바꾼 같은 suffix를 사용한다.

| Method / Path | 입력 | 결과 |
| --- | --- | --- |
| POST `/v1/browser-session` | `{}` | 200 `{status:"ready"}`, 최초 쿠키 발급/기존 유지 |
| GET `/v1/conversations` | limit=40, 선택 cursor | 최신 갱신순 conversations, next_cursor |
| POST `/v1/conversations` | `{client_conversation_id:"UUID"}` | 신규201/기존200 Conversation |
| GET `/v1/conversations/{id}/messages` | limit=50, 선택 before_seq | 시간순 messages, next_before_seq, has_pending |
| POST `/v1/conversations/{id}/messages` | 아래 JSON | 200 TurnResponse 또는 표준 ErrorResponse |

```json
{"query":"첫 번째 카드 연회비는?","client_request_id":"UUID","top_k":3,"profile":null,"retry_failed":false}
```

- query는 trim 후 1~500자, top_k는 1/3/5다. 목록 limit은 1~100으로 검증한다.
- Conversation: `id`, `title`, `created_at`, `updated_at`.
- ChatMessage: `id`, `turn_id`, `client_request_id`, `seq`, `role(user|assistant)`, `content`, `status(completed|pending|failed)`, `answer(AnswerResponse|null)`, `rewrite_usage(ChatRewriteUsage|null)`, `error(ErrorResponse|null)`, `created_at`.
- ChatRewriteUsage는 chat 전용 모델이다: `provider_called(bool)`, `model(str|null)`, `latency_ms(float|null)`, `usage(object|null)`. 기존 AnswerUsage/AnswerResponse에 rewrite 필드를 넣지 않는다. assistant 메시지에서만 문맥 재작성 사용량을 반환한다.
- TurnResponse: `turn_id`, `messages`(질문·답변 두 항목). 저장된 failed 상태의 재조회도 이 형태다. 현재 요청에서 새로 실패한 경우는 기존 ErrorResponse와 503/409 등을 반환하고 GET으로 저장 상태를 확인할 수 있다.
- user seq는 turn.seq*2-1, assistant seq는 turn.seq*2다. before_seq와 messages limit은 **turn 단위**다. 한 페이지에 항상 질문·답변 쌍을 보존한다.
- ConversationPage: `conversations`, `next_cursor`. MessagesPage: `messages`, `next_before_seq`, `has_pending`. cursor는 opaque 값이며 권한 정보가 아니다.
- 쿠키 없이 대화 API 호출 시 401 `BROWSER_SESSION_REQUIRED`; 출처 위반 403 `ORIGIN_NOT_ALLOWED`; 소유권/존재 오류 404 `CONVERSATION_NOT_FOUND`.
- 추가 오류는 409 위 충돌 코드, 422 `INVALID_REQUEST`, 503 `CHAT_STORAGE_UNAVAILABLE`/기존 RAG 오류다. proxy 연결 실패는 502 `API_UNREACHABLE`, deadline은 504 `API_TIMEOUT`으로 통일한다.
- 신규 모델은 FastAPI에서 정의하고 OpenAPI/TypeScript를 generator로 재생성한다. 기존 `/v1/search`·`/v1/answer`와 AnswerResponse는 유지한다.

## 7. 맥락 연결과 비용

```text
새 질문 + conversation ID
 → 소유권 확인·turn 예약·질문 저장
 → 같은 대화의 최근 completed/answered turn 조회
 → 이전 대화로 독립형 질문 rewrite
 → embedding → 기존 하이브리드 검색·BGE·근거 조립
 → 기존 답변 생성·citation 검증
 → 결과/실패 저장 → 브라우저 표시
```

- 첫 질문 또는 이전 answered turn이 없으면 rewrite 0회다. 이후에는 요청당 rewrite 최대 1회가 추가된다. 독립적인 질문을 이어 입력한 경우에도 이 권장안에서는 rewrite한다.
- 최근 최대 2개 완료 질문·답변 쌍만 사용한다. failed/pending/근거 부족 turn은 제외한다. 장기 기억이나 전체 대화 요약은 이번 범위 밖이다.
- `completed_context(..., max_pairs=2, max_chars=5500)`를 재사용한다. 현재 질문 500자를 더해 총 6000자 이내다. 문자 수는 token 수나 비용과 동일하지 않다.
- assistant 문맥에는 답변과 실제 recommendations 순서의 카드명·카드사·이유를 포함한다. cards 검색 배열 순서로 추천 순번을 재구성하지 않는다.
- 예: `편의점 카드 추천 → A/B/C 추천 → 첫 번째 카드 연회비는?`를 `A 카드의 연회비는?`로 바꿔 검색한다. 이는 목표 동작이며 실제 모델 정확도는 별도 검증한다.
- rewrite LLM에는 제한된 과거 대화와 현재 질문을 준다. 기존 answer LLM에는 재작성된 독립형 질문과 **새 검색 근거만** 준다. 과거 답변을 새 혜택 근거나 citation으로 사용하지 않는다.
- 과거 대화는 명령이 아닌 참고 데이터로 취급한다. rewrite의 공백·500자 초과·불완전 응답을 검증한다. 실패 시 모호한 원문으로 조용히 검색하지 않고 실패를 저장한다.
- release가 바뀌어도 과거 화면은 저장된 답변 JSON으로 복원한다. 새 질문은 현재 active release에서 검색한다. 과거 citation이 현재 release에도 존재한다고 가장하지 않는다.
- chat 전용 rewrite_usage에 호출 여부·시간·provider usage를 기록한다. 첫 질문/후속 질문 latency를 구분하며 미제공 사용량은 미측정이다. 실제 추가 비용/속도는 아직 측정하지 않았다.

| 1회 요청/시도 | embedding 요청 | rewrite Responses | 기존 answer Responses |
| --- | --- | --- | --- |
| 첫 질문 | 최대1회 | 0회 | 최대2회 |
| 완료 맥락이 있는 후속 질문 | 최대1회 | 최대1회 | 최대2회 |
| 완료 결과 재조회/동일 ID 재전송 | 0회 | 0회 | 0회 |
| failed의 명시적 재시도 | 해당 흐름 반복 | 맥락 있으면 최대1회 | 최대2회 |

검색 근거가 없으면 answer 호출은 0회다. 기존 answer의 2회 상한은 첫 생성과 검증 실패 재시도를 합친 값이다. 채팅 계층은 turn을 자동 재실행하지 않지만 기존 생성기 내부 재시도는 유지한다. 재시도 실패의 usage가 provider에서 제공되지 않으면 정확한 총 비용은 미측정이다.

## 8. UI 계약

- 사용자 승인 방향은 기존 `/chat`이다. 기존 desktop/mobile 초기 화면 캡처를 레퍼런스로 삼아 구현한다. 현재 구현·검증 상태는 계획 문서 마지막 기록을 따른다.
- 빈 화면의 orb·제목·설명·입력창·예시 질문은 유지한다. 사이드바는 실제 DB 목록으로 연결한다.
- 대화가 시작되면 같은 `/chat`의 본문을 스크롤 가능한 메시지 영역으로 바꾸고 기존 composer를 아래 둔다. 추천 카드명·이유·근거 부족 표시를 유지한다.
- 새 질문은 임시 사용자 메시지/로딩으로 표시하고 서버 결과와 request ID로 합쳐 중복을 막는다. 실패 시 질문·오류·재시도 버튼을 유지한다. 복원된 메시지에도 client_request_id가 있으므로 새로고침 뒤 같은 turn을 재시도할 수 있다.
- 최초 생성 UUID는 대화 ID를 확인할 때까지 sessionStorage에 유지한다. owner token과 원문 대화는 여기에 저장하지 않는다. 결과 불명 상태는 서버 조회를 우선하고 자동으로 새 request ID를 만들지 않는다.
- 하단 근처에서만 자동 스크롤한다. 이전 대화를 읽는 중에는 강제로 내리지 않는다. 위쪽의 `이전 대화 보기` 버튼으로 이전 페이지를 추가하며 현재 화면 위치를 유지한다.
- `/chat?conversation=<id>`로 선택 대화를 나타낸다. 쿠키가 없는 다른 브라우저는 URL만으로 접근할 수 없다. 새 채팅은 `/chat`다.
- mobile <=760px에서는 기존 숨김 사이드바 대신 채팅 영역에 내역/새 채팅 버튼을 추가한다. 내역 패널의 닫기·Escape·focus 복귀·선택 후 닫기를 처리한다.
- 다른 대화 선택/새 채팅 후에도 기존 turn은 서버에서 처리된다. 늦은 응답은 다른 대화 화면에 붙이지 않는다. 선택된 대화의 pending만 2초 간격으로 조회하고 완료·실패·이동 시 중단한다.
- 본문은 React 텍스트로 렌더링한다. HTML 삽입·이미지 변경은 하지 않는다. Enter/Shift+Enter/한글 IME 보호·예시 질문 입력을 유지한다.

## 9. 완료 기준

1. 같은 브라우저의 대화 2개 생성·선택·새로고침 복원과 별도 브라우저 접근 차단.
2. 질문·답변 누적, 위 스크롤과 이전 페이지 조회, DB 재연결/프로세스 재시작 후 유지.
3. 성공적으로 예약된 질문의 상태 보존, 생성/turn의 멱등성, 결과 불명 상태의 조회 우선, 채팅 계층의 자동 turn 재실행 없음. 기존 answer 내부 재시도와 외부 과금의 exactly-once 한계는 분리해 보고.
4. 후속 질문 문맥에 실제 추천 순서가 반영되고 rewrite query가 embedding/검색/답변에 전달됨.
5. 기존 evidence ID 복원·단일 카드 citation 검증·재시도·무상태 API 회귀 유지.
6. desktop/mobile 빈 화면·대화·오류 상태의 실제 렌더링 검토와 다른 페이지 비변경 확인.
7. store/API/core/client 테스트, OpenAPI drift, lint/build, 실제 SQLite+테스트 provider를 연결한 브라우저 통합 검증.
8. 최초 다중 탭 세션, 생성 응답 유실, provider 성공 후 DB 기록 전 중단, 손상/잠금/disk-full의 비파괴 실패를 검증.
9. 실 GPT 검증은 별도 전송 승인 후 진행. 최소 사례는 지시어 후속/독립 질문/주제 전환/근거 부족 이후 질문이다. 각 query의 대상 카드·혜택 의미가 유지되고 이전 대화가 새 사실을 만들지 않아야 한다. 실패 시 맥락 품질 미검증/실패로 보고하며 mock 성공과 구분한다. 지연·token·실제 비용은 측정값을 기록하되 임의의 성능 상한은 아직 확정하지 않는다.

## 10. 검토와 범위

API·DB 필드·쿠키·상한은 계획 검토 후 사용자 승인을 받은 구현 계약이다. proposal-reviewer 독립 검토, 실제 레퍼런스를 확인한 ui-designer 구현, 안정된 렌더링의 ui-reviewer 검토를 거친다. 실제 DB 변경은 database-manager에게 범위를 명시해 배정한다. 핵심 API/맥락 통합·Git 작업·최종 판단은 main Codex가 담당한다. 병렬 실행은 별도 승인 없이 시작하지 않는다.

코드 변경 후 OpenClaw 기본 리뷰와 테스트를 수행한다. 로그인·Cards·My Page·배포·검색 품질 고도화·streaming·대화 삭제는 이번 기능 범위가 아니다.

## 11. 계획 독립 검토 반영

proposal-reviewer의 1차 판정은 조건부 진행이었다. 코드와 비교해 대화 생성 멱등성, chat 전용 rewrite_usage, 기존 answer 내부 재시도와 과금 한계, 결과 불명 상태 재조회, 다중 탭 세션, DB 비파괴 오류·보존 정책을 보강했다. 실 GPT 품질/추가 지연은 미검증이며 사용자 구현 승인과 별도 전송 승인으로 구분한다.

보강 후 2차 검토에서는 큰 내부 모순이 없고 주요 계약 공백이 해소됐다는 판단을 받았다. 아래 제품 정책은 사용자 확인을 완료했다.

## 12. 승인된 제품 정책

1. 후속 질문: 완료 맥락이 있으면 독립 질문을 포함해 rewrite 최대 1회 추가를 승인했다. 실 GPT 검증 전송은 별도 승인한다.
2. 보존: 별도 평문 로컬 SQLite, 자동 삭제 없음, 쿠키 손실 시 자동 복원 불가를 승인했다. 삭제 UI/내보내기는 제외하며 worktree DB를 임의 폐기하지 않는다.
3. 지원: 최초 세션 초기화에 Web Locks를 사용하고 미지원 시 안내 후 저장형 채팅을 중단하는 흐름을 승인했다. 브라우저 브랜드로 차단하지 않으며 실제 기능 지원과 실기 검증 여부를 구분한다.
