# 69 실행 통합의 읽기 전용 조사

2026-10-27. **실행 준비 메모이며 실행·선택 수락이 아니다.** 68 입력/지시문 선택은 아직 동결되지 않았다. 이번 조사에서는 새 24질문·정답·선택 설정·69 자료·API 요청을 만들지 않았다. 아래 호출은 노트북 구성 제안이며 실행하지 않았다.

## 공통 런타임과 세 방식

`68_69_retrieval_runtime.py`를 한 번 불러 `V`, `T=V.T`, `R=T.R`, `A=V.A`를 공유하고, 준비 자료를 동결한 뒤 `V.Runtime(69)` 하나를 사용한다. 별도 import로 만든 또 다른 런타임의 전역을 섞지 않는다. 생성자는 현재 `source_inventory.json`/manifest를 읽고 기존 31 청킹 색인·검색 프로필과 BGE를 재사용한다. **생성자가 DB4를 자동 선택하는 것은 아니다.** 구조화 DB와 matcher는 `T.K.route`에 명시적으로 전달한다.

| 방식 | 구조화 조회 | 후속 검색과 입력 | 답변 지시문 |
|---|---|---|---|
| U | 조회하지 않음 | 원 scope에서 U 1회, 원문 근거 그대로 | 기존 |
| 현재 SEQ65 | `R.M.DB_NEW` = `60_structured_retrieval_repair/db_v3`, `matcher='baseline'` | 양성 ID 범위에서 U 1회, 0건/추출실패는 **원 scope** U 1회; 추가 사실 없음 | 기존 |
| 수정 SEQ67+68 | `67_structured_retrieval_repair/db_v4`, `matcher='projected'`, 기존 parser | 같은 순차 규칙의 U 결과를 보존하고, 추후 수락될 68 A/B/C 형식만 적용 | 추후 수락될 68 old/direct |

근거: `68_69_retrieval_runtime.py:25,37`, `63_65_experiment_runtime.py:272,333`, `60_62_experiment_runtime.py:38`, `67_query_repair.py:58,75`, `68_fact_input.py:75`.

여기서 baseline은 DB56/SEQ_BASE가 아니다. `67_query_repair.query(..., matcher='baseline')`는 `60_query_repair.query(..., repaired=True)`를 호출하므로 **65의 기존 literal matcher**와 같다. projected도 같은 숫자/action/제외/동일카드 AND 술어를 사용하고 `진료비/회비` 뒤의 명시 명사 후보 투영만 추가한다. DB 파일과 `fact_map`은 반드시 같은 버전으로 묶는다. DB4의 추가 행이 모두 원문상 참이라는 뜻은 아니며, 69에서 실제 전송될 고유 추가 fact 전수의 값·단위·서비스 관계 원문 감사가 별도 gate다.

## 최소 노트북 호출안

69 선택 동결·새 질문 동결 뒤, 질문당/반복당 parser 한 행을 두 SEQ에서 함께 참조한다. 아래는 방식 구성을 보여 주는 호출안으로 질문/설정을 미리 작성하지 않는다.

```python
# V/T/R/A는 위의 단일 import 객체. q, vector, parser는 향후 동결 자료.
original = R.S.original_scope(q['question'], documents)
route_u = dict(route='U', scope=original['card_keys'],
               reason='independent_unstructured', db_attempted=False)
route65 = T.K.route(R, parser['plan'], original, db3, facts3,
                   matcher='baseline', parser_failed=parser['status'] != 'ok')
route67 = T.K.route(R, parser['plan'], original, db4, facts4,
                   matcher='projected', parser_failed=parser['status'] != 'ok')
# 각 route별로 V.retrieve(..., mode=서로_다른_방식ID,
#                          parser_binding=R.P.sha(parser))를 호출한다.
# 수정 SEQ만 F.assemble(..., route67, facts4)의 수락된 결과 형식을 택한다.
# 현재 SEQ/U에는 수정 SEQ의 matching_fact_ids나 추가 근거를 섞지 않는다.
```

`67/selection.json`의 현재 확정값은 `parser_variant='old'`, `matcher='projected'`다(raw SHA `267488435fb070945391ef91ec35cc113dcf0b4d9030cfef89d4a35078e42c54`). 65 prompt도 `R.R.NEW_PROMPT`이고 `T.OLD_PARSER_PROMPT`가 바로 이를 가리킨다. 따라서 동일 새 질문 24×3=**72 논리 추출**을 stage690에서 한 번씩 생성해 U 이외 두 방식·두 프로필에 공유할 수 있다. parser_new stage691을 같은 old prompt로 복제 실행할 이유가 없다. 물리 재시도는 기존 최대2회이며 72와 물리 요청 수를 구분한다.

`R.prepare(69)`와 `R.search_matrix(69, ...)`를 그대로 호출하면 안 된다. prepare는 65의 48질문을 읽고, search_matrix는 DB3와 U/SEQ 두 방식이 고정되어 있다(`63_65_experiment_runtime.py:203,333`). 신규 runner 대신 68 노트북의 준비/검색/봉인 셀 구성을 69의 3방식 행렬로 명시하면 된다.

## scope·routing과 비교 보존

`T.K.load_runtime()`가 `65_scope_repair.install(R)`을 적용한다. `R.S.original_scope`를 사용해야 65의 short ASCII issuer 복구를 포함하며, `R.M.K.original_scope`(옛60) 또는 별도로 import한 scope 객체로 바꾸면 발급사/상품 경계가 달라질 수 있다. DB3/4 documents의 ID·identity·PDF SHA 동일성을 먼저 확인하고 같은 원 scope를 두 방식에 사용한다. 원질문은 검색과 생성 모두 그대로 둔다.

각 행에는 원 scope, parser row/raw/seal, DB3/4 세 파일 SHA, matcher, `route/scope/eligible_cards/matching_fact_ids/requirement_fact_ids/query_log/plan/reason`을 저장한다. typed SQL 재생과 저장 routing 비교에서 `db_wall_seconds`만 제외한다. 같은 parser의 두 route 차이와 0건 fallback을 구분하며 SQL0 자체를 오류로 세지 않는다.

`V.retrieve`는 현재 route scope와 원 scope **양쪽**에 `validate_scope`를 적용하고 별도 global U를 합치지 않는다. `runtime.reused_searches`는 동일 질문·vector·profile·scope·clarify의 검색만 재사용한다. 따라서 U와 fallback SEQ 또는 같은 양성 scope의 검색 재사용은 가능하나 방식별 row와 seal은 각각 남는다. 관련 테스트는 `68_test_retrieval_runtime.py`의 양성1회/0건원범위1회/범위유출/재개 사례다(이번 조사에서는 테스트를 실행하지 않음).

## 단계 등록·캐시·답변 결속

`67_69_execution_runtime.py:28,32`는 stage69/690/691을 `R.P.STAGES`, `R.X.STAGES` 및 실제 Cache 메서드 전역 `F.STAGES`에 등록한다. 단일 V import의 이 등록을 사용하고 경로를 임의 재매핑하지 않는다. `source_inventory.json=V.inventory()`는 기존31 색인·61/64/65의 score cache와 계약을 결속한다. 생성 전 manifest에 DB3/4, 67 선택, 추후 68 선택, 노트북 실행 phase hash와 운영 프로필을 고정한다. 프로필은 두 기존 명칭·벡터가중치0.4·depth50/candidate20/top3·bundle5·BGE route·64000bytes를 유지한다.

retrieval identity는 `qid:profile:mode:rN`이므로 세 방식의 mode는 반드시 달라야 한다. retrieval binding에는 parser/route/원scope/manifest/mode가 들어간다. search 재사용 key에는 DB/matcher가 없지만 검색은 최종 scope만 소비하므로 동일 scope/질문/vector/profile일 때만 재사용 가능하다. **서로 다른 route row를 덮어쓰거나 동일 mode로 저장하면 안 된다.**

API cache key는 request+logical identity+kind이며 stage 자체는 key에 없고 stage별 디렉터리로 분리된다(`46_preproduction_common.py:163,176`). 서로 다른 profile/mode/repeat는 반드시 다른 identity를 가진다. `A.generate(69, job)`는 과거 답변을 복사하지 않고 `reuse_source_sha256=None`이며 정확히 같은 identity의 봉인된 행만 재개한다. 총 **432 논리 답변**을 각각 보존한다. 검색/입력이 같아도 답변 공유는 금지한다. 단 빈 근거/clarify/검색 기술실패는 helper의 무호출 처리와 실패/정보부족 행을 그대로 보존하므로 물리 API432회를 무조건 보장한다는 뜻은 아니다.

`A.payload_contract(jobs, manifest)` → 동결 → `A.generate` → `A.validate_answer` 순서로 실제 serialized evidence·동적 인용 schema·prompt order·raw usage·복원 출력·row seal을 확인한다. old/direct는 `prompt_variant_by_identity`와 두 운영 시도 지시문 SHA에 모두 고정한다. 68 B/C 64000bytes 초과 시 양쪽 A복귀 정책도 수정 SEQ에 수락된 형식대로 유지한다.

## 24벡터 배치와 8달러 원장

기존 `R.embedding_batch(69, queries, manifest, allow_network=...)`를 재사용할 수 있다(`63_65_experiment_runtime.py:284`). 한 요청에24원질문, `text-embedding-3-small`/1536차원, 요청→raw/usage→질문순서/vector SHA→npy 계약을 만든다. 실패/중단 raw를 자동 재요청하지 않는다. 여러 모듈의 legacy `Runtime.embeddings()`로 우회하지 않는다.

공유 신원장은 `T.budget_ledger()`다. `67/shared_budget_baseline.json`의 역사적 보수차감 **1.658813910달러**(예약0.006234500 포함)를 고정하고 LEAVES의 67·68·69 새 API raw 전부를 더한다. `T.Cache.reserve`는 이 원장을 기존 reserve에 한시 주입하여 요청 전8달러 상한/최대비용 예약/신규 unknown중단을 지킨다(`67_69_execution_runtime.py:107,128,169`).

**임베딩은 별도 확인 지점:** 위 batch 함수는 `R.X.budget_upper_ledger()`를 직접 호출한다. 현재 parser/answer의 `T.budget_ledger()`와 호출 경로가 다르다. 노트북에서 배치 직전 원장 합계/unknown을 대조하고, 같은 canonical 원장으로 강제하려면 호출 동안만 `R.X.budget_upper_ledger=T.budget_ledger`로 두고 `finally`로 복구하는 최소 in-memory bridge를 검토한다. 동시에 다른 API를 돌리지 않는다. 이번 조사에서 bridge를 적용하거나 원장을 실행·가격 측정하지 않았다. 새69 manifest에는 embedding 물리 상한(최대2)도 answer의 최대864와 별도로 포함해 합계 cap을 빠뜨리지 않는다. 사용량 미측정 값을 추정 비용으로 쓰지 않는다.

## 미결과 확인 범위

68 입력/지시문 선택과69 새 질문·정답·manifest는 이번에 만들지 않았다. 현재 SEQ65 재현은 DB3+기존 literal+scope65+단일 U 규칙이며, 원65의 과거 답변 재사용 최적화를69로 복사하지 않는다. 68/H11의 실제 기술실패1건도 최종 선택 분모에서 보존할 사항이지 이 조사에서 재호출할 대상이 아니다. 이번 근거는 위 함수와 원65/68 노트북 셀의 정적 읽기이며69 실행/전체회귀 통과를 주장하지 않는다. 변경은 이 메모 한 파일뿐이다.
