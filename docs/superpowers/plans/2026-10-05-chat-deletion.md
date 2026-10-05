# Chat Deletion Implementation Plan

> **For agentic workers:** Execute according to the active `AGENTS.md`. The main Codex may implement directly or assign bounded work to registered custom agents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 채팅 목록 오른쪽의 ×에서 삭제를 확인하면 해당 대화와 메시지가 삭제되고 새로고침 후에도 목록에서 사라진다.

**Architecture:** 기존 쿠키 소유권과 별도 chat SQLite를 재사용한다. 소유권 확인·pending 검사·대화 삭제를 한 쓰기 transaction에서 수행하고 기존 FK cascade로 메시지를 삭제한다. Next proxy는 정확한 단일 대화 DELETE만 허용하며 UI는 성공 확인 후에만 목록을 변경한다.

**Tech Stack:** SQLite, FastAPI/Pydantic, Next.js/React, 기존 unittest·Node test·Playwright.

**Spec:** 현재 사용자 승인: 기존 디자인 유지, 목록 오른쪽 ×, 확인 후 실제 대화 삭제, 다른 기능은 변경하지 않는다. 배경 구조는 `docs/superpowers/specs/2026-10-03-chat-history-design.md`이며 이 승인으로 대화 삭제만 추가한다.

## Global Constraints

- 작업은 chat-history worktree에 한정한다. 원본·RAG index·LLM·검색·프롬프트·환경·패키지는 변경하지 않는다.
- 본인 소유 단일 대화만 삭제한다. 생성 중(pending)에는 409로 거부하며 전체 삭제·숨김·휴지통·자동 재전송은 추가하지 않는다.
- 실제 사용자 DB로 삭제를 테스트하지 않는다. 임시 SQLite와 fake provider를 사용하며 유료 호출은 없다.
- 기존 색상·글꼴·목록 선택 및 모바일 구조를 유지한다. 확인은 브라우저 기본 confirm을 사용한다.
- API 계약 변경은 생성기와 API_SPEC에 동기화한다. Git push/merge는 하지 않는다.

---

### Task 1: 소유권 있는 단일 대화 삭제

**Files:** `services/rag-api/src/pickcardu_rag_api/{chat_store,chat,main}.py`, `services/rag-api/tests/test_chat_{store,api}.py`.

**Interfaces:** `ChatStore.delete_conversation(owner_hash: str, conversation_id: str) -> None`; `DELETE /v1/conversations/{conversation_id}` → 204, 401/403/404/409/422/503 오류.

- [x] 테스트 먼저 추가하고 RED 확인: 삭제 후 대화·turn 모두 없음, 다른 대화 유지, foreign owner 404, pending 409와 rows 유지, Origin 검사 및 provider 호출 0.

```python
store.delete_conversation('a', cid)
assert store.list_conversations('a')['conversations'] == []
with sqlite3.connect(store.path) as db:
    assert db.execute('SELECT count(*) FROM turns WHERE conversation_id=?', (cid,)).fetchone()[0] == 0
```

- [x] 기존 `_connection(write=True)` 안에서 `_owned`, pending 검사, `DELETE FROM conversations WHERE id=? AND owner_hash=?`를 수행한다. schema migration은 없다.
- [x] DELETE도 POST와 동일 Origin 규칙을 적용하고 route는 `Response(status_code=204)`를 반환한다. CORS 허용 메서드에도 DELETE를 추가한다.
- [x] 임시 SQLite/API 테스트 GREEN과 전체 service 회귀를 확인한다.

### Task 2: Next proxy·client·목록 상태 연결

**Files:** `apps/main/lib/{chat-api,chat-proxy,use-chat}.ts`, `apps/main/app/api/chat/[...path]/route.ts`, `apps/main/tests/chat-{api,proxy}.test.ts`.

**Interfaces:** client `deleteConversation(id: string): Promise<void>`; hook `removeConversation(id: string): Promise<boolean>`(현재 선택 대화를 비웠을 때만 true), `deletingId: string | null`.

- [x] DELETE 204를 JSON parsing 없이 받는 client test, DELETE 단일 UUID 경로만 통과하고 잘못된 Origin·다른 메서드/경로는 차단되는 proxy test를 RED로 확인한다.

```ts
await api.deleteConversation(cid);
assert.deepEqual(methods, ['DELETE']);
assert.equal(response.status, 204);
assert.equal(await response.text(), '');
```

- [x] 요청 함수에 DELETE를 추가하고 204를 빈 성공으로 처리한다. proxy는 단일 UUID 경로에서 DELETE만 허용하고 204 응답 body는 null을 사용한다.
- [x] 삭제 중 중복 요청·같은 대화 전송을 막는다. 실패 시 목록을 유지한다. 이미 삭제된 404(CONVERSATION_NOT_FOUND)는 없어진 상태로 정리한다.
- [x] 성공한 ID를 ref Set에 기록해 진행 중이던 목록 GET의 늦은 응답이 삭제 항목을 되살리지 못하게 한다. 현재 선택 ID가 삭제 대상이면 generation을 갱신해 새 빈 화면으로 이동한다. 다른 대화·작성 초안은 유지한다.
- [x] client/proxy 회귀와 TypeScript build를 확인한다.

### Task 3: × 버튼과 실제 브라우저 검증

**Files:** `apps/main/app/chat/components/conversation-list.tsx`, `apps/main/app/chat/page.tsx`, `apps/main/app/globals.css`, `docs/API_SPEC.md`, generated contracts.

**Interfaces:** `ConversationList`의 `onDelete(id)`, `deletingId`, `blockedDeleteId`. 기존 선택 버튼과 ×는 sibling이며 nested button을 만들지 않는다.

- [x] 실제 컴포넌트 test로 삭제 버튼이 대상 ID를 전달하며 선택을 실행하지 않는지, busy/deleting 버튼이 disabled인지 확인한다.

```ts
assert.equal(deleteButton.props.type, 'button');
deleteButton.props.onClick();
assert.deepEqual(deletedIds, [cid]);
assert.deepEqual(selectedIds, []);
```

- [x] 목록 항목에 wrapper와 오른쪽 ×만 추가한다. 기존 목록 버튼 스타일을 유지하고 ×에 필요한 위치·크기만 override한다.
- [x] `window.confirm` 취소는 요청 0회. 확인 후 hook를 호출하고 현재 대화 삭제 시에만 입력 초안·모바일 drawer를 정리한다.
- [x] 임시 backend fixture에서 desktop/mobile 확인: 취소, 현재/다른 대화 삭제, 새로고침, pending 409, 잘못된 소유자, 실패 시 목록 유지, 늦은 다른 화면 응답, overflow 없음. 기존 디자인과 비교한다.
- [x] API_SPEC에서 삭제 규칙과 irreversible deletion을 기록하고 계약 생성기·drift check를 실행한다. 기존 DB data 자체를 변경하지 않는다.
- [x] OpenClaw 읽기 전용 review 결과(시간 초과)를 회수하고 final diff·테스트·원본 소스 불변을 확인해 로컬 checkpoint commit으로 보존한다.

## Completion Criteria

× → 취소 시 변화 없음 / 확인 성공 시 해당 대화·turn만 사라짐 / 현재 화면은 빈 채팅으로 전환 / 다른 대화와 초안 유지 / pending·외부 소유·잘못된 Origin은 차단 / reload 후 유지 / 기존 디자인과 환경 유지. 실제 LLM 호출은 검증 범위가 아니다.

## Verification Record

- 삭제 테스트를 먼저 실행해 미구현 상태의 실패를 확인한 뒤 구현했다. backend는 `PYTHONPATH=services/rag-api/src:packages/rag-core/src python -m unittest discover -s services/rag-api/tests -v`로 50개 통과했다.
- frontend는 `npm --prefix apps/main test`, `npm --prefix apps/main run lint`, `npm --prefix apps/main run build`로 7개 테스트 파일·lint·production build 통과했다. 계약 생성 및 `generate.py --check`도 통과했다.
- RAG core 회귀는 `PYTHONPATH=packages/rag-core/src python -m unittest discover -s packages/rag-core/tests -v`로 21개 통과했다. 이 작업에서 RAG core 코드는 변경하지 않았다.
- 실제 Chromium에서 임시 SQLite·fake provider로 desktop 1440×900, mobile 390×600을 검증했다. 확인창 취소, 현재/다른 대화 삭제와 초안, reload, pending 409, foreign owner 404, 삭제 실패 시 보존, 늦은 목록 GET 및 삭제 중 화면 전환을 확인했다. 모바일 root 너비 390/390, drawer 너비 319/319로 가로 overflow가 없었다. 확인창은 실제 dialog와 자동화용 confirm 반환값 양쪽으로 확인했다.
- OpenClaw `openclaw_1791199910_f782eee8` 결과는 timeout이었다. 독립 리뷰 통과로 간주하지 않으며 main Codex가 핵심 변경을 직접 검토했다.
- 실제 대화 DB·RAG index·환경·의존성·원본 프로젝트는 변경하지 않았다. 삭제 검증에 사용한 임시 DB는 `/tmp`에 남겼고 검증용 서버만 종료했다. 유료 호출·push·merge는 없다.
