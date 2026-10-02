# Chat History Implementation Plan

> **For agentic workers:** Execute according to the active `AGENTS.md`. The main Codex may implement directly or assign bounded work to registered custom agents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 `/chat`에 익명 브라우저 기반 대화 저장·복원, 누적 메시지 UI, 후속 질문 맥락을 연결한다.

**Architecture:** 브라우저는 같은 출처의 Next.js proxy로 접근한다. FastAPI가 익명 쿠키 소유권과 별도 SQLite turn 저장을 관리하고, 최근 완료 대화에서 독립형 질문을 만들어 기존 RAG에 전달한다. 무상태 API와 기존 근거 검증은 유지한다.

**Tech Stack:** 기존 Python 3.11+, sqlite3, FastAPI/Pydantic, Next.js 16.2.6/React 19.2.6, Node 22.13+, pytest/Node test/Playwright. 새 라이브러리 설치를 전제로 하지 않는다.

**Spec:** `docs/superpowers/specs/2026-10-03-chat-history-design.md`

이 계획은 저장소/API/화면/맥락의 검증 가능한 작업 단위로 나눈다. 사용자 체감 검증은 우선 저장·복원을 확인하고, 누적 UI를 확인한 뒤 후속 질문을 평가한다. Task 4는 UI에 앞서 backend 단위 테스트를 만들지만 실제 맥락 답변 평가는 Task 7에서 한다.

## Global Constraints

- 병합 전 위치는 `/home/sms/openclaw_file/.worktree/PickCardU/chat-history`, 브랜치는 `feat/chat-history`다.
- 현재는 계획만 작성한다. 기능 구현은 사용자 승인 이후 시작한다.
- Python/Conda/전역 라이브러리를 변경하지 않는다. 개인 환경 이름을 공유 명령에 넣지 않는다.
- `.env`, `node_modules/`, 모델·RAG DB·대화 DB는 커밋하지 않는다.
- 기존 초기 UI·이미지·색상·헤더·입력창을 재사용한다. Home/Cards/My Page/화면 전환은 건드리지 않는다.
- 질문 1~500자, top_k=1/3/5(기본 3), 최근 문맥 최대 2쌍·현재 질문 포함 6000자다.
- `pickcardu_browser` 쿠키는 HttpOnly/SameSite=Lax/Path=/, 권장 90일(사용 시 갱신)이다. 쿠키 손실 시 자동 복구를 보장하지 않는다.
- 별도 DB 기본값은 `data/chat/runtime/chat.sqlite`, override는 `PICKCARDU_CHAT_DB_PATH`다.
- 목록 기본 limit은 대화 40/turn 50, 최대 100이다. pending lease는 10분이고 자동 LLM 재시작은 하지 않는다.
- 신규 모델에서 계약을 재생성한다. 기존 search/answer의 무상태 계약을 깨지 않는다.
- 유료 호출·merge/push·worktree 삭제는 별도 승인 전 수행하지 않는다.
- 대화 생성과 turn 전송은 각각 UUID로 멱등성을 보장한다. 결과 불명 상태는 GET 재조회가 먼저다.
- 기존 answer 내부 최대 2회 시도는 유지한다. 후속 질문은 rewrite 최대 1회가 추가되며 외부 과금 exactly-once는 보장하지 않는다.
- Web Locks 직렬화/미지원 시 안전 중단은 사용자 확인을 기다리는 권장안이다. Task 1에서 지원 범위를 승인하기 전 구현 확정사항으로 취급하지 않는다.

---

## 파일별 책임

| 파일 | 변경과 책임 |
| --- | --- |
| `services/rag-api/src/pickcardu_rag_api/chat_store.py` | 신규 SQLite 초기화·소유권·turn 예약/완료/실패·페이지 조회 |
| `services/rag-api/src/pickcardu_rag_api/chat_models.py` | 신규 HTTP Pydantic 모델 |
| `services/rag-api/src/pickcardu_rag_api/chat.py` | 신규 쿠키/출처 검사·대화 router·맥락 연결 |
| `services/rag-api/src/pickcardu_rag_api/{config,main}.py` | DB 경로·store 주입·답변 처리 재사용 |
| `packages/rag-core/src/pickcardu_rag/answering.py` | 기존 context/rewrite 재사용, 필요한 공백 검증 |
| `apps/main/app/api/chat/[...path]/route.ts` | 신규 고정 경로 Next.js proxy |
| `apps/main/lib/{chat-proxy,chat-api,chat-state}.ts` | 신규 proxy 경계·생성 타입 client·race/중복 방지 |
| `apps/main/app/chat/page.tsx` | 초기/대화 상태·URL·목록·서버 연결 |
| `apps/main/app/chat/components/{chat-composer,conversation-list,message-list}.tsx` | 기존 표현 재사용, 입력/내역/메시지 책임 분리 |
| `apps/main/app/globals.css` | chat 전용 누적 대화/내역 패널 스타일 |
| `packages/contracts/{openapi.yaml,generated/api.ts}` | generator로 재생성 |
| `docs/{API_SPEC,API_RUNBOOK}.md`, `README.md`, `.gitignore` | 계약·저장 경로·복원 제약·실행 안내 |

## Task 1: 요구·작업 경계·UI 레퍼런스

**Files:** spec/plan, 기존 chat/page.tsx와 globals.css 읽기 전용.

**Interfaces:** 승인 범위·API 모델·DB 경로·UI 레퍼런스를 이후 작업에 전달한다.

- [ ] 권장 계약과 후속 질문의 추가 LLM 호출을 설명하고 사용자 구현 승인을 확인한다.
- [x] proposal-reviewer 1차 독립 검토를 반영했다. 생성 멱등성·chat 전용 사용량·결과 불명 재조회·비파괴 DB 처리·비용과 보존 한계를 보강했다. 사용자 범위를 바꾸는 결정은 사용자에게 확인한다.
- [x] 보강 후 2차 검토에서 큰 내부 모순이 없다는 판단을 받았다. 지원 브라우저/후속 rewrite 비용/평문 로컬 보존 정책은 사용자 확인이 필요하다.
- [ ] spec 12절의 세 정책을 확인한다. Web Locks 미지원 브라우저 제한·후속 추가 호출·보존 정책 승인 전 해당 동작을 구현하지 않는다.
- [ ] worktree와 원본이 분리됐는지 확인하고, 실행 포트 충돌을 점검한다. 기존 프로세스를 임의 종료하지 않는다.

```bash
git status --short --branch
git diff f9064ac HEAD --stat
git check-ignore .env data/rag/runtime/active-index.json .cache/reranker/bge-reranker-v2-m3/model.safetensors apps/main/node_modules/next/package.json
```

- [ ] desktop 1440×900/mobile 390×844의 기존 초기 `/chat`을 실제 캡처한다. ui-designer가 이미지를 보고 빈 상태 유지·대화 상태 확장 계약을 제출한다.
- [ ] 보호 대상·화면 비교 기준·파일 소유권을 기록한다. 이 단계에서는 새 DB/API를 만들지 않는다.

## Task 2: 별도 SQLite 저장소

**Files:** Create chat_store.py, `services/rag-api/tests/test_chat_store.py`; Modify config.py, `services/rag-api/tests/test_config.py`, .gitignore.

**Owner:** database-manager가 schema/실제 임시 SQLite 변경과 테스트를 맡고 main Codex가 설정을 통합한다. 원본 RAG DB는 대상이 아니다.

**Interfaces:**

```python
ChatStore(path: Path)  # 생성 시 DB 파일을 만들지 않는다.
create_conversation(owner_hash: str, client_conversation_id: str) -> dict
get_conversation(owner_hash: str, conversation_id: str) -> dict
list_conversations(owner_hash: str, *, limit: int=40, cursor: str|None=None) -> dict
reserve_turn(owner_hash: str, conversation_id: str, client_request_id: str,
             request: dict, *, retry_failed: bool=False) -> dict
complete_turn(turn_id: str, attempt_id: str, answer: dict,
              *, standalone_query: str, rewrite_usage: dict|None=None) -> dict
fail_turn(turn_id: str, attempt_id: str, error: dict,
          *, rewrite_usage: dict|None=None) -> dict
list_turns(owner_hash: str, conversation_id: str, *, limit: int=50,
           before_seq: int|None=None) -> dict
completed_turns(owner_hash: str, conversation_id: str, *, before_seq: int,
                limit: int=2) -> list[dict]
```

reserve_turn 결과는 `{turn, attempt_id, should_run}`이다. 신규/명시적 failed 재시도만 true, 완료/failed 재조회는 false다. pending/경합은 `ChatStoreError(status_code, code, message, retryable=False)`로 전달한다. list_turns는 `{turns,next_before_seq,has_pending}`, completed_turns는 answered 완료만 시간순으로 반환한다.

- [ ] 먼저 아래 테스트를 작성하고 미구현으로 실패하는지 확인한다.

```python
from pickcardu_rag_api.chat_store import ChatStore

def test_reopen_preserves_question(tmp_path):
    path = tmp_path / 'chat.sqlite'
    store = ChatStore(path)
    chat = store.create_conversation('owner-a', 'draft-1')
    request = {'query': '편의점 카드 추천', 'top_k': 3, 'profile': None}
    reserved = store.reserve_turn('owner-a', chat['id'], 'request-1', request)
    reopened = ChatStore(path)
    page = reopened.list_turns('owner-a', chat['id'])
    assert len(page['turns']) == 1
    assert page['turns'][0]['id'] == reserved['turn']['id']
    assert page['turns'][0]['query'] == request['query']
```

- [ ] lazy 초기화, 작업별 connection, foreign key, WAL, busy timeout 5000ms, BEGIN IMMEDIATE 예약, user_version=1, unique/partial index와 SQL 파라미터를 구현한다.
- [ ] 소유권 404, 생성 UUID 재전송, 빈 turn 대화 목록 제외, 동일 ID 완료 재조회, payload 충돌 409, pending 경합, 같은 turn의 failed 재시도, lease 만료, 오래된 attempt 완료 거절, cursor/시간순을 테스트한다.
- [ ] lock/disk-full/손상/미지원 schema version에서 원본을 삭제·재생성하지 않는 테스트를 만든다. SQLite backup API로 일관된 백업 경계를 확인한다.
- [ ] 아래 테스트 후 DB 경로/RAG 비변경/환경 비변경을 확인하고 checkpoint를 기록한다.

```bash
PYTHONPATH=services/rag-api/src:packages/rag-core/src python -m pytest services/rag-api/tests/test_chat_store.py services/rag-api/tests/test_config.py -q
```

## Task 3: 익명 쿠키·대화 API·기존 답변 연결

**Files:** Create chat_models.py, chat.py, `services/rag-api/tests/test_chat_api.py`; Modify main.py, `services/rag-api/tests/test_api.py`, API_SPEC.md; Generate contracts.

**Interfaces:** `create_app(..., chat_store=None)`로 store를 주입한다. 기존 답변 처리를 `generate_answer(payload: QueryRequest) -> AnswerResponse`로 공유한다. spec의 5개 endpoint와 Conversation/ChatMessage/TurnResponse/페이지 모델을 제공한다. ChatMessage에는 복원용 client_request_id와 chat 전용 ChatRewriteUsage가 들어가며 기존 AnswerUsage는 변경하지 않는다.

- [ ] 실제 SQLite와 기존 support의 FakeProvider/FakeReranker를 사용하는 HTTP 테스트를 먼저 작성한다.

```python
from fastapi.testclient import TestClient
from pickcardu_rag_api.chat_store import ChatStore
from pickcardu_rag_api.main import create_app
from support import FakeProvider, FakeReranker, build_release, settings

def test_completed_retry_does_not_call_provider_twice(tmp_path):
    build_release(tmp_path / 'runtime')
    provider = FakeProvider()
    app = create_app(settings(tmp_path), provider=provider,
                     reranker=FakeReranker(), chat_store=ChatStore(tmp_path / 'chat.sqlite'))
    with TestClient(app) as browser:
        headers = {'Origin': 'http://testserver'}
        assert browser.post('/v1/browser-session', json={}, headers=headers).status_code == 200
        created = browser.post('/v1/conversations', json={
            'client_conversation_id': '00000000-0000-4000-8000-000000000002',
        }, headers=headers)
        assert created.status_code == 201
        cid = created.json()['id']
        payload = {'query': '카페 카드', 'client_request_id': '00000000-0000-4000-8000-000000000001'}
        first = browser.post(f'/v1/conversations/{cid}/messages', json=payload, headers=headers)
        again = browser.post(f'/v1/conversations/{cid}/messages', json=payload, headers=headers)
        assert first.status_code == again.status_code == 200
        assert first.json()['turn_id'] == again.json()['turn_id']
        assert len(provider.embedding_queries) == 1
```

- [ ] 쿠키/hash/Origin/no-store를 구현하고 모든 경로의 owner 검사를 적용한다. store 오류를 ErrorResponse로 변환한다.
- [ ] 질문 예약→기존 RAG→성공/실패 저장을 연결한다. 이 단계는 단발 query로 동작하며 다음 Task에서 맥락을 추가한다.
- [ ] 기존 'conversation endpoint 없음' 테스트만 새로운 기대에 맞춘다. 기존 search/answer 결과 검증은 유지한다.
- [ ] 다른 browser 404, 쿠키 없음 401, 외부 Origin 403, 빈 질문 422, 오류/근거 부족 저장, restore 시 provider 0회, import/OpenAPI 시 DB 생성 0회를 검증한다.
- [ ] 동일 생성 UUID의 200/신규 201, 생성 응답 유실 후 재조회, 메시지 복원의 client_request_id, provider 성공 후 DB 완료 기록 실패를 검증한다. 마지막 사례는 과금 exactly-once 보장이 없음을 보고한다.
- [ ] generator 실행·drift 검사 후 API_SPEC에 쿠키/페이지/중복/재시도 JSON과 오류 예제를 추가한다.

```bash
PYTHONPATH=services/rag-api/src:packages/rag-core/src python -m pytest services/rag-api/tests/test_chat_api.py services/rag-api/tests/test_api.py -q
python packages/contracts/generate.py
python packages/contracts/generate.py --check
```

- [ ] main Codex가 신구 계약·비밀 비노출·회귀 결과를 확인하고 checkpoint를 기록한다.

## Task 4: 후속 질문 문맥 재사용

**Files:** Modify chat.py, answering.py의 필요한 검증; Create `services/rag-api/tests/test_chat_context.py`; Modify `packages/rag-core/tests/test_answering.py`.

**Interfaces:** chat.py에 `build_chat_context(turns: list[dict], question: str) -> list[dict[str,str]]`를 둔다. store의 완료 turn을 추천 순서가 담긴 assistant text로 바꿔 completed_context에 전달한다. 기존 rewrite signature를 유지하고 독립형 query를 generate_answer에 전달한다. ChatMessage.rewrite_usage(ChatRewriteUsage)로 호출/시간/사용량을 반환하며 기존 AnswerResponse.usage는 변경하지 않는다.

- [ ] 검색순서가 아니라 실제 추천순서가 문맥에 들어가는 테스트를 먼저 작성한다.

```python
from pickcardu_rag_api.chat import build_chat_context

def test_context_preserves_recommendation_order():
    turns = [{'query': '카드 추천', 'state': 'completed', 'answer': {
        'answer_status': 'answered', 'answer': '두 카드를 추천합니다.',
        'cards': [{'card_key': 'a', 'card_name': 'A', 'issuer': '카드사'},
                  {'card_key': 'b', 'card_name': 'B', 'issuer': '카드사'}],
        'recommendations': [{'card_key': 'b', 'reason': '혜택 B', 'citations': ['kb']},
                            {'card_key': 'a', 'reason': '혜택 A', 'citations': ['ka']}],
    }}]
    context = build_chat_context(turns, '첫 번째 카드 연회비는?')
    assert '1. B' in context[1]['content']
    assert context[-1]['content'] == '첫 번째 카드 연회비는?'
    assert len(context) == 3
```

- [ ] 기존 FakeProvider.rewrite는 현재 질문을 그대로 돌려준다. 테스트용 spy로만 바꿔 첫 질문 0회/후속 1회, rewrite query가 embedding/answer에 전달되는지 검증한다.
- [ ] 최근 2쌍/6000자, failed/pending/근거 부족/다른 대화 제외, 현재 질문 중복 제외, 공백/500자 초과 rewrite 거절을 테스트한다.
- [ ] rewrite 실패 시 검색을 하지 않고 failed로 저장한다. 과거 citation을 새 evidence로 쓰지 않는다. 추가 rewrite 재시도는 넣지 않는다.

```bash
PYTHONPATH=services/rag-api/src:packages/rag-core/src python -m pytest services/rag-api/tests/test_chat_context.py packages/rag-core/tests/test_answering.py -q
```

- [ ] main Codex가 문맥·근거 경계와 실 GPT 미검증 한계를 확인하고 checkpoint를 기록한다.

## Task 5: 같은 출처 proxy와 프론트 client

**Files:** Create chat-proxy.ts, app/api/chat/[...path]/route.ts, chat-api.ts, `apps/main/tests/{chat-proxy,chat-api}.test.ts`. 기존 rag-api.ts는 유지한다.

**Interfaces:** `proxyChatRequest(request: Request, path: string[], options?: {baseUrl?:string; fetchImpl?:typeof fetch}) -> Promise<Response>`. upstream은 서버 측 `PICKCARDU_RAG_API_BASE_URL` 또는 `http://127.0.0.1:8000`이다. client는 `initializeBrowser()`, `listConversations(cursor?)`, `createConversation(clientConversationId)`, `getMessages(id,beforeSeq?)`, `sendMessage(id,request)`를 생성 타입으로 제공한다.

- [ ] 외부 Origin 거절을 upstream 호출 전에 검증한다.

```typescript
import assert from 'node:assert/strict';
import test from 'node:test';
import { proxyChatRequest } from '../lib/chat-proxy.ts';

test('reject cross-origin POST before upstream fetch', async () => {
  let called = false;
  const response = await proxyChatRequest(new Request('http://localhost:3000/api/chat/conversations', {
    method: 'POST', headers: { Origin: 'http://untrusted.example' }, body: '{}',
  }), ['conversations'], { fetchImpl: async () => { called = true; return Response.json({}); } });
  assert.equal(response.status, 403);
  assert.equal(called, false);
});
```

- [ ] 고정 session/conversations/messages 패턴만 허용하고 traversal/encoded slash/임의 destination을 거절한다.
- [ ] 해당 쿠키만 forward하고 HttpOnly Set-Cookie/request ID/no-store를 전달한다. body 최대 8KiB, deadline 540초, GET body 없음, 502/504 표준 오류를 구현한다.
- [ ] client는 `credentials: same-origin`, `cache: no-store`를 사용한다. 원문 token을 localStorage에 넣지 않는다. 비 JSON 응답과 network 오류를 안전하게 처리한다.
- [ ] 같은 전송의 request ID를 유지하고 network 재시도에서 새 UUID를 자동 발급하지 않는다.
- [ ] initializeBrowser를 Web Locks와 탭 내 promise로 직렬화하고 browser API가 없으면 `BROWSER_SESSION_UNSUPPORTED`로 중단한다. 다른 토큰을 경쟁 발급하는 fallback은 하지 않는다.
- [ ] network 오류/502/504는 먼저 getMessages로 조회한다. completed/pending에는 유료 재시도를 보내지 않고 확인된 failed만 명시적 재시도 대상으로 제공한다.

```bash
npm --prefix apps/main test
```

- [ ] main Codex가 Origin/owner 경계와 기존 client 회귀를 확인하고 checkpoint를 기록한다.

## Task 6: 기존 UI 확장과 복원 연결

**Files:** Modify chat/page.tsx, globals.css; Create chat components, chat-state.ts, `apps/main/tests/chat-state.test.ts`.

**Owner:** ui-designer가 승인된 레퍼런스의 chat 표현/CSS를 맡고 main Codex가 client/상태/API를 통합한다. 같은 파일을 동시에 수정하지 않는다.

**Interfaces:** `mergeMessages(current: ChatMessage[], incoming: ChatMessage[]) -> ChatMessage[]`는 id로 중복 제거하고 seq 순으로 반환한다. `isCurrentConversation(requestConversationId: string, selectedConversationId: string|null) -> boolean`은 다른 대화의 늦은 응답을 막는다.

- [ ] 중복/정렬/늦은 응답 회귀 테스트를 먼저 작성한다.

```typescript
import assert from 'node:assert/strict';
import test from 'node:test';
import { isCurrentConversation } from '../lib/chat-state.ts';

test('late response never replaces another conversation', () => {
  assert.equal(isCurrentConversation('old', 'new'), false);
  assert.equal(isCurrentConversation('old', null), false);
  assert.equal(isCurrentConversation('new', 'new'), true);
});
```

- [ ] session 초기화→목록→URL 선택 대화 복원을 연결한다. 첫 전송 대화 생성, 새 채팅 빈 화면, create 성공 뒤 send 실패에서 ID 보존을 구현한다.
- [ ] 생성 UUID만 확인될 때까지 sessionStorage에 보존한다. 원문 token/대화를 브라우저 저장소에 중복 저장하지 않는다. 생성 응답 유실 뒤 같은 UUID를 사용한다.
- [ ] user/assistant 목록과 하단 composer, pending/실패/근거 부족/추천을 기존 스타일로 확장한다. 실패 질문 보존과 명시적 동일 ID 재시도를 구현한다.
- [ ] 목록 활성 스타일을 first-child에서 실제 선택으로 바꾼다. 목록 cursor와 위 스크롤의 older turn 추가에 scroll anchor를 유지한다.
- [ ] mobile 내역/새 채팅/닫기/Escape/focus 복귀를 연결한다. 초기 예시 질문과 IME/Enter/Shift+Enter를 유지한다.
- [ ] 선택 대화의 pending만 2초 polling하고 완료/실패/이동 시 cleanup한다. 자동 스크롤은 하단 근처에서만 동작한다.

```bash
npm --prefix apps/main test
npm --prefix apps/main run lint
npm --prefix apps/main run build
```

- [ ] ui-designer가 desktop/mobile 초기/긴 대화/pending/오류를 실제 렌더링한다. ui-reviewer가 레퍼런스와 독립 비교하고 main Codex가 필수 수정과 범위 준수를 판단한다.

## Task 7: 통합 검증·공유 문서·인계

**Files:** Create `services/rag-api/tests/chat_browser_fixture.py`; Update API_SPEC/API_RUNBOOK/README, worktree의 로컬 HANDOFF.

**Interfaces:** 테스트 전용 runner는 `--port`, `--database`를 받아 실제 SQLite/create_app/FakeProvider를 연결한다. production 코드에 fake provider나 테스트 전환 endpoint를 추가하지 않는다.

- [ ] 실제 Next.js proxy+FastAPI+SQLite를 연결하고 Playwright의 별도 browser context로 소유권 격리를 검증한다. 유료 API는 사용하지 않는다.
- [ ] 대화 A/B 각 2왕복, refresh, 전환, mobile 내역, 과거 페이지, 실패/동일 ID 재시도/pending 복원을 조작한다. HTTP 결과와 화면을 기록한다.
- [ ] 최초 다중 탭 session 경쟁, 생성 응답 유실, 답변 저장 전 프로세스 중단, 쿠키 삭제/만료 후 자동 복원 불가를 테스트한다.
- [ ] 프로세스 재시작 후 복원과 GET의 provider 0회를 확인한다. 첫/후속 요청 경로와 시간을 구분하되 mock 시간을 실 GPT 시간으로 표현하지 않는다.
- [ ] 아래 전체 관련 검증을 실행한다. 테스트가 sandbox 제약으로 정지하면 기존 진단 근거를 확인하고 승인된 실행 권한 안에서 검증한다.

```bash
PYTHONPATH=services/rag-api/src:packages/rag-core/src python -m pytest services/rag-api/tests packages/rag-core/tests -q
python packages/contracts/generate.py --check
npm --prefix apps/main test
npm --prefix apps/main run lint
npm --prefix apps/main run build
npm run test:dev
npm run test:setup
git diff --check
git status --short
```

- [ ] 코드 핵심 diff를 OpenClaw review_code에 읽기 전용으로 전달하고 결과를 회수한다. 중요 지적을 근거/테스트로 판단하고 수정 후 관련 검증을 다시 실행한다.
- [ ] API_SPEC에 method/path/JSON/error/cookie/pagination/retry, RUNBOOK/README에 DB 위치/backup/쿠키 소실/worktree별 DB/포트/production 제약을 반영한다.
- [ ] 실 GPT 검증을 원할 때만 질문·전송 범위·최대 호출수를 설명하고 승인받는다. 승인 없이는 mock 통합 검증과 실 GPT 미검증을 구분해 보고한다.
- [ ] 실제 문맥 평가에서는 지시어 후속/독립 질문/주제 전환/근거 부족 이후 질문을 나눠 대상 카드·혜택 의미 보존과 허위 이전 맥락 배제를 확인한다. 첫/후속 지연과 사용량을 기록하되 승인 없이 API를 호출하지 않는다.
- [ ] main Codex가 diff/테스트/비밀 및 runtime 제외를 확인한 뒤 승인된 구현만 feature commit에 저장한다. merge/push/worktree 삭제는 하지 않는다.
- [ ] 사용자가 worktree 화면과 동작을 확인한 뒤 별도로 병합을 승인하면 원본 브랜치에 통합한다. 대화 DB 이전은 소스 병합과 별개다.
- [ ] 병합/폴더 제거 전에 worktree 대화 DB 보존·이전을 확인한다. 기본은 삭제하지 않으며 쿠키 손실 뒤 DB 백업만으로 자동 복원할 수 없다는 한계를 설명한다.

## 현재 상태

계획 작성과 2차 독립 검토를 완료했고 사용자에게 계획대로 진행하라는 지시를 받았다. 다만 spec 12절의 비용·보존·지원 브라우저 정책은 별도 확인 대상으로 남아 있어 해당 확인 전 기능 구현은 시작하지 않는다. 구현 Task 2~7과 실제 UI 캡처는 미착수이며 원본 폴더는 변경하지 않는다.
