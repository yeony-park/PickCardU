# 구조화 검색 DB 명세서

## 1. 문서 범위와 기준

현재 구조화 검색 실험 DB의 데이터 사전이다. 스키마 관측 기준일은 2026-10-13, 문서 위치 정리일은 2026-10-20이다. 스키마 버전은 `structured-benefit-experiment-v3`다. 운영 DB/HTTP 계약을 정의하는 문서가 아니며 운영 반영·DB 변경은 하지 않았다. 이 문서는 DB 구성과 검색 계약을 설명하는 명세이며 비교 실험 보고서가 아니다. Markdown은 에이전트·AI가 참조하는 기준 문서이고 HTML은 사람이 읽기 위한 동일 내용의 표시본이다.

- DB: `notebooks/data/30_structured_benefit_db/structured.sqlite`.
- 입력: 기존 Upstage `normalized.json` 106개. 구조화 계약 `field-evidence-relation-v6`, 정규화 계약 `typed-facts-v2`. 이 입력의 추출 모델 기록은 `gpt-5.6-luna/max`이며 질문 조건 추출 실험의 `gpt-6-luna/medium`과 별개다.
- [DB 생성·정규화·선택기 구현](../notebooks/30_build_structured_db.py), [질문 스키마](../notebooks/32_search_experiment.py), [질문 해석 지시문](../notebooks/33_query_routing.py), [42번 실제 질문 조건 호출·순차 검색](../notebooks/42_expanded_query_evaluation.py).
- [실제 DB 스키마·저장값·검증 JSON](../notebooks/data/44_structured_search_db_spec/schema_inventory.json), [44번 노트북](../notebooks/44_structured_search_db_spec.ipynb), [30번 DB 보고서](report/30_structured_benefit_db_report.html), [43번 경계 검사](report/43_offline_validation_report.html).

같은 fact 안의 대상·행동·수치·조건 관계를 유지한다. 원문 필드는 그대로 보존하고 검색 가능한 파생값을 보수적으로 추가한다. OCR/원본 구조화 추출의 정확도는 이 명세 작성으로 검증하지 않았고 통과 가정을 유지한다. 문서에 제시한 코드 규칙과 실제 관측값은 구분한다.

**제약 해석:** TEXT/INTEGER/REAL은 SQLite 선언 타입이며 STRICT 테이블이 아니다. 모든 컬럼에 DEFAULT가 없다. `NOT NULL`은 빈 문자열 금지가 아니다. TEXT 단일 PK인 metadata.key/documents.document_id/facts.fact_id에는 별도 NOT NULL 선언이 없고 PRAGMA notnull=0이다. 일반 SQLite rowid 테이블의 이 선언만으로 NULL 금지를 주장하지 않는다. 현재 빌드는 비어 있지 않은 식별자를 만들고 실제 NULL 여부는 저장 프로필에서 확인한다. 복합 PK인 target_aliases의 두 컬럼은 명시적으로 NOT NULL이다. FK는 writer가 foreign_keys=ON으로 활성화했으며 현재 외래키 검사를 별도로 수행했다. UPDATE/DELETE CASCADE 선언은 없다.


## 2. 테이블 관계와 실제 행 수

```text
documents (1) ── facts (N)
metadata: 빌드 설명
target_aliases: 고정 별칭 사전 보존
```


| 테이블 | 행 수 | 컬럼 수 | 역할 |
| --- | --- | --- | --- |
| metadata | 8 | 2 | 빌드 계약 |
| documents | 106 | 11 | 문서·카드 정체성과 원문 페이지 |
| facts | 3403 | 25 | 원본 사실과 검색용 파생값 |
| target_aliases | 22 | 3 | 별칭 목록 |

행 수(Row count)는 읽은 DB의 COUNT(*), 컬럼 수는 PRAGMA table_info 기준이다. 품질 점수가 아니며 크다고 더 좋은 값은 아니다. facts에는 일반 안내·연회비·부정 문구도 들어 있다.


## 3. `metadata` 테이블 — 전체 컬럼


타입은 SQLite DDL의 선언 타입이다. 제약은 DB가 강제하는 선언이며, 값 규칙은 빌드/검색 코드의 의미다. enum과 단위·JSON 형식은 CHECK로 강제하지 않는다. 예시는 규칙 설명이며 실제 관측값과 구분한다.


| 컬럼 | 선언 타입 | DDL 제약 | 의미 | 값 규칙·예시 | 현재 검색 용도 |
| --- | --- | --- | --- | --- | --- |
| `key` | TEXT | NOT NULL 선언 없음, PK, DEFAULT 없음 | 빌드 메타데이터 항목 이름 | 현재 8개 key. 아래 key별 표 참조 | 버전·출처·정책 추적 |
| `value_json` | TEXT | NOT NULL, DEFAULT 없음 | 항목 값의 JSON 문자열 | 문자열/객체/목록/숫자 구조를 JSON으로 직렬화 | 검색 조건 자체가 아니라 빌드 설명 |

## 4. `documents` 테이블 — 전체 컬럼


타입은 SQLite DDL의 선언 타입이다. 제약은 DB가 강제하는 선언이며, 값 규칙은 빌드/검색 코드의 의미다. enum과 단위·JSON 형식은 CHECK로 강제하지 않는다. 예시는 규칙 설명이며 실제 관측값과 구분한다.


| 컬럼 | 선언 타입 | DDL 제약 | 의미 | 값 규칙·예시 | 현재 검색 용도 |
| --- | --- | --- | --- | --- | --- |
| `document_id` | TEXT | NOT NULL 선언 없음, PK, DEFAULT 없음 | 문서/카드 식별 키 | 원본 document_id. 예: BC/BC_Green_v2. basename만으로 만들지 않음 | card_keys 및 facts.document_id와 연결 |
| `provider` | TEXT | NOT NULL, DEFAULT 없음 | 원본 OCR 제공자 | 이 빌드는 upstage만 허용. SQL enum 제약은 없음 | 제공자와 출처 구분 |
| `card_name` | TEXT | NOT NULL 선언 없음, DEFAULT 없음 | 원본 identity.card_name | 원문 카드명; 누락 시 NULL 가능 | LLM에 제공할 카드 목록 구성 |
| `issuer_name` | TEXT | NOT NULL 선언 없음, DEFAULT 없음 | 원본 identity.issuer_name | 원문 카드사명; 누락 시 NULL 가능 | 표시/카드 목록 정보. 별도 issuer 조건 없음 |
| `source_pdf_sha256` | TEXT | NOT NULL, DEFAULT 없음 | 원본 PDF 내용 SHA-256 | 원본 JSON에서 복사. DB는 길이·hex 형식을 강제하지 않음 | 원문 동일성 추적 |
| `normalized_json_sha256` | TEXT | NOT NULL, DEFAULT 없음 | 입력 normalized.json bytes SHA-256 | 파일 실제 bytes로 계산. JSON 의미 동일성과는 다름 | 입력 변경 추적 |
| `source_relative_path` | TEXT | NOT NULL, DEFAULT 없음 | normalized.json의 저장소 상대 경로 | 현재 빌드 입력 glob의 문서 경로 | 원문 출처 접근 및 정렬 |
| `source_path_sha256` | TEXT | NOT NULL, DEFAULT 없음 | 위 상대 경로 문자열 SHA-256 | UTF-8 경로를 해시. 파일 내용 해시가 아님 | 경로 동일성 추적 |
| `page_count` | INTEGER | NOT NULL, DEFAULT 없음 | 저장 pages 목록 길이 | 0 이상 정수로 생성. SQL CHECK 없음 | 원문 규모 정보 |
| `fact_count` | INTEGER | NOT NULL, DEFAULT 없음 | 해당 문서 원본 facts 목록 길이 | 일반 안내도 포함. 순수 혜택 수와 다름 | 문서별 fact 수 확인 |
| `raw_json` | TEXT | NOT NULL, DEFAULT 없음 | 문서 보존용 객체의 JSON 문자열 | identity/pages/provenance/해시 등. 원본 normalized.json 전체 bytes 복제는 아님 | load_facts의 원문 페이지·카드 정체성 복원 |

## 5. `facts` 테이블 — 전체 컬럼


타입은 SQLite DDL의 선언 타입이다. 제약은 DB가 강제하는 선언이며, 값 규칙은 빌드/검색 코드의 의미다. enum과 단위·JSON 형식은 CHECK로 강제하지 않는다. 예시는 규칙 설명이며 실제 관측값과 구분한다.


| 컬럼 | 선언 타입 | DDL 제약 | 의미 | 값 규칙·예시 | 현재 검색 용도 |
| --- | --- | --- | --- | --- | --- |
| `fact_id` | TEXT | NOT NULL 선언 없음, PK, DEFAULT 없음 | 문서 내 원본 fact 식별자 | fact_ + SHA-256 앞 24hex. provider&#124;PDF해시&#124;document_id&#124;ordinal로 계산 | matching_fact_ids → 원문 근거 연결 |
| `document_id` | TEXT | NOT NULL, FK → documents.document_id, 두 컬럼 조합 UNIQUE, DEFAULT 없음 | 소유 문서/카드 키 | documents.document_id를 참조 | 같은 카드 안의 조건 평가 및 근거 소유권 |
| `ordinal` | INTEGER | NOT NULL, 두 컬럼 조합 UNIQUE, DEFAULT 없음 | 원본 facts 목록의 0부터 시작하는 순서 | enumerate로 생성. (document_id, ordinal) UNIQUE | 원본 fact 순서 복원. 목록 변경 시 fact_id가 달라질 수 있음 |
| `target_raw` | TEXT | NOT NULL, DEFAULT 없음 | 원본 대상 문자열 | 가맹점/업종/대상 목록/안내 대상 등 자유 문자열. 미기재는 빈 문자열 가능 | derived.target_normalized에 대상/별칭 포함 여부 비교 |
| `action_raw` | TEXT | NOT NULL, DEFAULT 없음 | 원본 행동 문자열 | 할인/청구할인/적립/안내/부정 문구 등 자유 문자열 | benefit_type_raw와 함께 action_kind/status 생성 |
| `benefit_type_raw` | TEXT | NOT NULL, DEFAULT 없음 | 원본 혜택 이름/분류 설명 | 자유 문자열. 고정 카테고리 enum 아님 | 행동 정규화 보조. 별도 분류 필터 없음 |
| `value_raw` | TEXT | NOT NULL, DEFAULT 없음 | 원본 수치/값 문구 | 5, 3~5, 최대 10 등 문자열. 단일 숫자로 제한하지 않음 | typed ratio와 일치할 때 percent 생성 |
| `unit_raw` | TEXT | NOT NULL, DEFAULT 없음 | 원본 단위 문자열 | %, 원, 마일리지, 포인트, 빈 문자열 등. 전체 관측값은 inventory 참조 | percent 정규화 보조. 단위 자유 비교 필터 없음 |
| `condition_raw` | TEXT | NOT NULL, DEFAULT 없음 | 원본 적용 조건 문구 | 전월 실적/결제 방식/복합 조건 등을 원문 형태로 보존 | 명시적인 단일 전월 최소 실적만 파생 비교 |
| `cap_raw` | TEXT | NOT NULL, DEFAULT 없음 | 원본 한도 문구 | 혜택 지급 한도/이용금액 상한 등 원문 표현 | 지급 한도만 보수적으로 cap_krw 생성 |
| `period_raw` | TEXT | NOT NULL, DEFAULT 없음 | 원본 기간 문구 | 월/일/지급일/행사 기간 등 자유 문자열 | cap_raw와 합쳐 월/일 한도 기간 식별. 행사 날짜 검색 아님 |
| `frequency_raw` | TEXT | NOT NULL, DEFAULT 없음 | 원본 횟수 조건 | 월 1회 등 자유 문자열. 미기재는 빈 문자열 | 보존만 함. 횟수 수치 필터 없음 |
| `exceptions_raw` | TEXT | NOT NULL, DEFAULT 없음 | 원본 제외/예외 문구 | 제외 업종/채널/기타 예외 등 자유 문자열 | 요구 대상이 예외에 언급되면 unknown으로 보수 처리 |
| `action_kind` | TEXT | NOT NULL 선언 없음, DEFAULT 없음 | 정규화한 행동 종류 | discount(할인), earn(적립 계열), NULL(판단 불가) | Requirement.action과 비교 |
| `action_status` | TEXT | NOT NULL, DEFAULT 없음 | 행동을 긍정 혜택으로 볼 수 있는지 | true(긍정), false(부정/제외), unknown(혼합/모호) | false 제외; unknown은 확정 충족으로 선택하지 않음 |
| `percent` | REAL | NOT NULL 선언 없음, DEFAULT 없음 | 비율의 퍼센트값 | 10% → 10.0, 0.1이 아님. known일 때 수치, 그 외 NULL | percent >= min_percent |
| `percent_state` | TEXT | NOT NULL, DEFAULT 없음 | 비율값의 사용 가능 상태 | known / unknown / not_applicable. 아래 상태 정의 참조 | 비율 요구 시 known만 확정 비교 |
| `spend_threshold_krw` | INTEGER | NOT NULL 선언 없음, DEFAULT 없음 | 필요 전월 최소 실적 원화 금액 | 30만원 이상 → 300000. known일 때 수치, 그 외 NULL | 필요 실적 <= 질문의 max_spend_krw |
| `spend_operator` | TEXT | NOT NULL 선언 없음, DEFAULT 없음 | 원문 실적 부등호 판정 | gte(이상), lte(이하), unknown, NULL을 코드가 만들 수 있음. 관측값은 별도 표 | known은 gte만. 질문의 최대 허용 실적과 부등호 방향을 구분 |
| `spend_state` | TEXT | NOT NULL, DEFAULT 없음 | 전월 실적값 사용 가능 상태 | known / unknown / not_applicable | 실적 요구 시 known만 확정 비교 |
| `cap_krw` | INTEGER | NOT NULL 선언 없음, DEFAULT 없음 | 혜택 지급 한도 원화 금액 | 월 할인 한도 5천원 → 5000. 이용금액 상한과 구분 | cap_krw >= min_cap_krw |
| `cap_state` | TEXT | NOT NULL, DEFAULT 없음 | 혜택 한도값 사용 가능 상태 | known / unknown / not_applicable | 한도 요구 시 known만 확정 비교 |
| `cap_period` | TEXT | NOT NULL 선언 없음, DEFAULT 없음 | 한도와 연결된 기간 | month(월), day(일), NULL(모호/미기재) | min_cap_krw 요구가 있을 때 cap_period_state와 함께 기간 비교 |
| `raw_json` | TEXT | NOT NULL, DEFAULT 없음 | 원본 fact 객체의 JSON 문자열 | 원문 10필드 + typed_normalization + field_evidence + relation_scope 등 보존 | 검색 load_facts는 이 JSON에서 원문/근거를 복원 |
| `derived_json` | TEXT | NOT NULL, DEFAULT 없음 | 보수적으로 계산한 파생 객체 JSON | SQL 파생 컬럼 + 대상 정규화/사유/기간 상태/예외 정규화 포함 | 현재 선택기는 복원된 derived 객체로 비교 |

## 6. `target_aliases` 테이블 — 전체 컬럼


타입은 SQLite DDL의 선언 타입이다. 제약은 DB가 강제하는 선언이며, 값 규칙은 빌드/검색 코드의 의미다. enum과 단위·JSON 형식은 CHECK로 강제하지 않는다. 예시는 규칙 설명이며 실제 관측값과 구분한다.


| 컬럼 | 선언 타입 | DDL 제약 | 의미 | 값 규칙·예시 | 현재 검색 용도 |
| --- | --- | --- | --- | --- | --- |
| `alias_group` | TEXT | NOT NULL, PK(1), DEFAULT 없음 | 별칭 그룹 이름 | 여행, 커피, 카페. LLM이 동적으로 생성하지 않음 | 개발자가 고정한 대상 확장 규칙 |
| `alias` | TEXT | NOT NULL, PK(2), DEFAULT 없음 | 그룹에 속한 원문 별칭 | 스타벅스, 항공, 호텔 등. (alias_group, alias) 복합 PK | 동일 그룹 내 대상 대안 |
| `alias_normalized` | TEXT | NOT NULL, DEFAULT 없음 | 별칭의 정규화 문자열 | NFKC → casefold → 모든 공백 제거 | 현재 선택기는 코드의 TARGET_ALIASES를 사용; 이 테이블은 규칙 보존용 |

## 7. JSON 내부 속성과 출처 연결

아래 속성은 별도 SQL 컬럼이 아니다. DB의 TEXT JSON을 파싱해 접근한다. JSON 유효성/필드 필수 여부는 SQL CHECK로 강제되지 않는다. `_raw` SQL 컬럼을 수정하는 것으로 JSON이 자동 갱신되지 않으며 동기화 trigger도 없다. 이번 작성은 양쪽 3,403행의 일치를 확인했다.

### metadata.value_json: key별 속성값

| key | JSON 값 종류 | 내용 |
| --- | --- | --- |
| schema_version | string | structured-benefit-experiment-v3 |
| source_glob | string | data/rag/runtime/working/run_f2d29820c6dca642/documents/*/upstage/normalized.json |
| source_policy | string | Upstage JSON 보존, OCR/검증 통과 가정 |
| source_version | object | provider, structure_schema_version, normalization_contract, normalization_config_hash, normalization_model_recorded_in_source, normalization_reasoning_recorded_in_source |
| counts | object | documents, pages, facts, known_percent, known_spend_threshold, known_cap, known_cap_period, positive_supported_action |
| support_rates | object | known_*와 positive_supported_action의 전체 facts 대비 비율 |
| target_aliases | object | 여행/커피/카페 → 별칭 목록 |
| limitations | array[string] | OCR 미재검증, 복잡한 수치/기간의 unknown 처리 등 |

외부 db_build_summary.json의 verification/artifact_sha256는 DB metadata에 삽입한 후에 추가한다. 따라서 그 두 key가 metadata에 없다는 것은 누락 오류가 아니다.

### documents.raw_json

| 속성 | 값 구조·역할 |
| --- | --- |
| document_id, provider, *_sha256, source_relative_path, page_count, fact_count | 문서 SQL 값에 대응하는 출처 정보 |
| identity | card_name, issuer_name, card_evidence.line_ids, issuer_evidence.line_ids |
| structure_schema_version, normalization_contract | 원본 구조화/정규화 계약 버전 |
| provenance | 구조화 추출의 model, reasoning, config_hash, prompt_sha256, schema_sha256, endpoint, max_output_tokens 등 원본 기록 |
| ocr_provenance, ocr_parse_provenance | OCR 제공자 설정과 정규화 정책의 원본 기록 |
| pages[] | 원본 정규화 페이지 객체. blocks/text/표 등 원문 연결에 필요한 자료를 보존. 페이지별 속성은 원본 제공 형태에 따름 |

이 객체는 builder가 선택한 문서 정보를 저장한다. 원본 facts는 facts.raw_json에 따로 저장하며 documents.raw_json에 facts 목록을 중복 저장하지 않는다.

### facts.raw_json

| 속성 | 값 구조·예시·용도 |
| --- | --- |
| target/action/benefit_type/value/unit/condition/cap/period/frequency/exceptions | SQL의 원문 10개 *_raw 컬럼에 대응 |
| typed_normalization | 원본의 선택적 정규화 객체. 필드명 → [{kind, decimal}, …], condition_operators → 비교 연산자 목록 등. 현재 raw_json에서 키 누락 25행, 객체 3,378행, 명시적 null 0행. builder/reader의 get(...) or {}는 키 누락 및 null을 {}로 취급. 실제 관측 kind는 ratio, KRW, number |
| typed_normalization.*[].kind | ratio=비율, KRW=원화, number=일반 숫자. 현재 수치 파생기는 value의 ratio, condition/cap의 KRW를 사용 |
| typed_normalization.*[].decimal | 정밀 수치 문자열. 10%의 ratio는 "0.1", 30만원 KRW는 "300000". percent 컬럼의 10.0과 표현 단위가 다름 |
| field_evidence | 원문 필드명 → 근거 fragment 목록. 미기재/근거 없음은 빈 목록 가능 |
| field_evidence.*[].line_id | 원본 줄 레지스트리 참조. 예: P0003-L0010. 검색 chunk_id가 아님 |
| field_evidence.*[].fragment | 해당 줄에서 발췌한 원문. 예: 청구할인 |
| field_evidence.*[].char_start / char_end | 줄 문자열의 0부터 시작하는 시작/끝 위치. Python 슬라이스 [start:end], 끝 제외. [원문 위치 처리 코드](../jobs/rag-indexer/src/pickcardu_indexer/pipeline.py) 참고 |
| relation_scope.scope_type | 실제 sentence(문장), table_row(표 행), bounded_block(제한된 블록) |
| relation_scope.line_ids | 같은 fact를 설명하는 원문 줄 목록 |
| relation_scope.row_line_ids / header_line_ids | 표 행/헤더의 연결 줄 목록. 해당하지 않으면 빈 목록 |

raw_json의 typed_normalization 키는 필수가 아니다. SQL/JSON 보존본과 달리 load_facts 및 facts.jsonl 최상위의 typed_normalization/field_evidence/relation_scope는 값이 누락되거나 null이면 {}로 복원한다. 현재 field_evidence/relation_scope는 원문에서도 모두 객체다. 객체임을 가정한 소비자는 먼저 키 존재와 값을 처리해야 한다.

원문 근거 참조를 보존했다는 사실은 참조/의미의 정확성이 이번에 검증됐다는 뜻이 아니다. 하나의 fact에 여러 줄·필드 fragment가 있을 수 있어 fact_id를 검색 chunk_id 하나로 곧바로 바꿀 수 있다고 가정하지 않는다.

### facts.derived_json: 전체 파생 속성

| 속성 | 값·의미·검색 사용 |
| --- | --- |
| target_normalized | 대상 원문에 NFKC/casefold/공백 제거 적용. 별도 SQL 컬럼 없음 |
| action_kind, action_status | SQL 동명 컬럼과 동일 |
| percent, percent_state | SQL 동명 컬럼과 동일 |
| spend_threshold_krw, spend_operator, spend_state | SQL 동명 컬럼과 동일 |
| cap_krw, cap_state, cap_period | SQL 동명 컬럼과 동일 |
| cap_period_state | known/unknown/not_applicable. **별도 SQL 컬럼 없음**. 한도 기간 필터에서 사용 |
| action_reason / percent_reason / spend_reason / cap_reason / cap_period_reason | 각 값/상태를 만든 이유 문자열. 실제 전체 사유와 빈도는 inventory.nested_value_counts 참조. 판정 확률이나 LLM confidence가 아님 |
| period_raw | 원본 period 문자열의 파생 객체 내 보존본 |
| has_exceptions | 원본 예외 문구가 공백 제외 비어 있지 않으면 true. 현재 선택기가 이 bool을 직접 필터하지 않음 |
| exceptions_normalized | 예외 문구 정규화 문자열. 요구 대상이 포함되면 unknown 처리 |


### JSON 키 누락과 명시적 null의 실제 관측

Missing(키 누락), Null(명시적 null), Empty object(빈 객체 {})를 구분한다. facts 원문/복원값은 각 3,403행, documents는 106행이 분모다. 품질 점수가 아니며 원문 누락값을 복원한 것이 신규 정보 추출을 뜻하지 않는다.


| 위치 | 속성 | 실제 타입: 행 수 | Missing | Null | Empty object |
| --- | --- | --- | --- | --- | --- |
| facts.raw_json | typed_normalization | object: 3378; missing: 25 | 25 | 0 | 0 |
| facts.raw_json | field_evidence | object: 3403 | 0 | 0 | 0 |
| facts.raw_json | relation_scope | object: 3403 | 0 | 0 | 0 |
| reader_restored_facts | typed_normalization | object: 3403 | 0 | 0 | 25 |
| reader_restored_facts | field_evidence | object: 3403 | 0 | 0 | 0 |
| reader_restored_facts | relation_scope | object: 3403 | 0 | 0 | 0 |
| documents.raw_json | identity | object: 106 | 0 | 0 | 0 |
| documents.raw_json | pages | array: 106 | 0 | 0 | 0 |
| documents.raw_json | provenance | object: 106 | 0 | 0 | 0 |
| documents.raw_json | ocr_provenance | object: 106 | 0 | 0 | 0 |
| documents.raw_json | ocr_parse_provenance | object: 106 | 0 | 0 | 0 |

typed_normalization은 원본 raw_json에서 객체 3,378행·키 누락 25행·명시적 null 0행이다. 현재 reader는 누락된 키를 {}로 복원하므로 읽은 facts에는 객체 3,403행이 있다. 원본 저장 형태와 reader 결과를 혼동하지 않는다.


## 8. 정규화 규칙과 질문 스키마의 연결

### 상태값의 의미

| 상태 | 의미 | 예시 | 잘못 해석하면 안 되는 점 |
| --- | --- | --- | --- |
| known | 코드 규칙상 단일 값으로 비교 가능 | 원문 10%와 typed ratio 0.1이 일치 | PDF 사실 정확도 검증 성공을 뜻하지 않음 |
| unknown | 원문에 값/문구가 있으나 모호·복합·불일치·지원 밖 | 최대 10%, 3~5%, 두 실적 구간 | 0/false로 바꾸지 않음 |
| not_applicable | 해당 파생 종류의 값/표현을 확인하지 못한 분기 | value/ratio 없음, cap 없음 등 | 카드에 혜택/실적/한도가 없다는 전역 결론 아님 |

- 비율: 원본 typed ratio 하나 ×100과 원문 퍼센트값 하나가 일치해야 known. 범위/상한/하한/원화 혼합은 unknown. 질문의 최소 비율과 원문의 최대 비율을 혼동하지 않는다.
- 실적: 명시 전월 실적 문구에 연결된 단일 금액·이상(gte)이 typed KRW와 맞아야 known. 코드가 이하(lte)를 식별할 수 있지만 확정 실적값으로 지원하지 않는다. 복수 구간은 unknown.
- 지급 한도: 원문 금액 하나와 typed KRW 하나가 맞아야 known. 이용/결제/승인 금액 상한은 지급 혜택 한도로 사용하지 않는다.
- 한도 기간: cap/period 문구의 월/일을 확인한다. 둘 다 등장하거나 불명확하면 unknown. 일반 period는 지급일·행사 기간일 수도 있다.
- 행동: action + benefit_type에서 할인/적립 계열을 식별한다. 제외/제공되지 않음/적용되지 않음/불가이면 false, 할인과 적립 혼합은 unknown. **질문 지시문은 earn에 캐시백을 포함한다고 설명하지만 현재 정규화 함수의 적립 토큰에는 캐시백이 없다.** 캐시백만 있는 문구가 earn으로 정규화된다고 보장하지 않는다.

### LLM이 출력하는 QueryPlan

LLM은 스키마를 새로 만들거나 SQL을 생성하지 않는다. 고정 Pydantic QueryPlan에 맞춘 JSON 값을 출력한다. 추가 속성은 extra=forbid지만 질문 의미의 정확성은 별도 검사 대상이다. 다음 필드는 기본값이 없어 출력에 필요하며 nullable은 null 허용을 뜻한다.

| 속성 | 타입·허용값 | 처리 |
| --- | --- | --- |
| route | unstructured / structured_then_unstructured | 검색 경로 선택 |
| groups | array[array[Requirement]] | 그룹 사이 OR, 그룹 내부 AND. 각 AND를 같은 카드가 충족 |
| card_keys | array[string] | 공급한 catalog의 정확한 키. []는 전체 범위. 스키마 enum이 아니라 후속 목록 검사 |
| unresolved | array[string] | 미지원/불명확 조건. 비어 있지 않으면 현재 실험에서 복귀 |
| Requirement.targets | array[string] | 동일 혜택의 OR 대상. []는 대상 제한 없음 |
| Requirement.action | discount / earn / any | 할인 / 적립 계열 / 종류 제한 없음 |
| Requirement.min_percent | float 또는 null | percent >= 요구값. 정확히 같은 비율/최대 비율 필드 없음 |
| Requirement.max_spend_krw | int 또는 null | 필요 전월 최소 실적 <= 사용자가 허용한 상한. 개인 소비 예산을 자동 대입하지 않음 |
| Requirement.min_cap_krw | int 또는 null | 지급 한도 >= 요구 금액 |
| Requirement.cap_period | month / day / any | min_cap_krw가 있을 때만 기간 검사. 기간만 지정한 조건은 현재 별도 필터가 안 됨 |
| Requirement.exclude_targets | array[string] | 동일 fact 대상 문자열에 제외 대상이 있으면 해당 fact 미충족 |

스키마에 리스트 최소 길이·숫자 최소/최대 범위 제약이 없다. 예를 들어 groups=[[]]도 현재 형식 검사에 통과한다. 숫자 문자열 "10"은 Pydantic에서 수치로 정규화되지만 "10%"는 실패한다. 음수/과도한 수치의 의미적 유효성이 이 스키마로 보장되는 것은 아니다.

### 같은 혜택 관계와 실행 방식

```text
하나의 Requirement: 커피 + 할인 + 최소 10%
→ 같은 fact에서 대상·행동·비율·요구 실적·요구 한도 확인

AND 그룹: [커피 10% 할인, 넷플릭스 10% 할인]
→ 같은 카드의 각 조건을 충족하는 fact가 필요
→ 서로 다른 혜택 fact로 각 조건을 충족해도 됨

OR 그룹: [[커피 10% 할인], [넷플릭스 10% 할인]]
→ 어느 한 그룹을 충족하면 후보
```

현재 DB 읽기는 documents와 facts를 JOIN하여 raw_json/derived_json을 복원하고 Python 선택기가 비교한다. SQL 컬럼 인덱스로 조건별 WHERE를 생성하는 방식이 아니다. 상태가 true인 그룹의 fact_id를 카드별 matching_fact_ids로 반환한다. unknown은 true가 없고 필요한 값이 불확실한 카드로 분리되며, 하나라도 true 그룹이 있으면 후보가 된다.

```json
{
  "route": "structured_then_unstructured",
  "groups": [[{
    "targets": ["커피"], "action": "discount", "min_percent": 10,
    "max_spend_krw": null, "min_cap_krw": null, "cap_period": "any",
    "exclude_targets": []
  }]],
  "card_keys": [], "unresolved": []
}
```

위 조건과 일치한 카드 및 fact를 선택한다. 현재 S_THEN_U는 카드 목록만 검색 범위로 넘겨 원래 질문으로 벡터+BM25 검색한다. matching_fact_ids가 가리키는 원문 근거는 아직 최종 검색 후보에 자동 추가하지 않는다.


## 9. 실제 저장값·분포와 빈 값

아래는 2026-10-13 DB 관측값이다. SQL 허용값 전체나 사용자 질문의 빈도를 뜻하지 않는다. NULL count는 미저장값 수, Empty count는 정확한 빈 문자열 수, Distinct는 NULL을 제외한 서로 다른 값 수다. 각각 해당 테이블의 전체 행이 분모이며 품질 통과 기준은 없다. 원문 누락의 이유와 추출 정확도를 이 수치만으로 판정하지 않는다. 전체 자유 문자열과 빈도는 [schema_inventory.json](../notebooks/data/44_structured_search_db_spec/schema_inventory.json)의 column_profiles.value_counts에서 확인한다.


### metadata: 전체 컬럼의 저장 프로필


| 컬럼 | NULL count | Empty count | Distinct | 실제 예시(최대 3개, 각 120자 절단) |
| --- | --- | --- | --- | --- |
| `key` | 0 | 0 | 8 | `counts`<br>`limitations`<br>`schema_version` |
| `value_json` | 0 | 0 | 8 | JSON 본문은 다음 예시/기계 판독 자료 참조 |

### documents: 전체 컬럼의 저장 프로필


| 컬럼 | NULL count | Empty count | Distinct | 실제 예시(최대 3개, 각 120자 절단) |
| --- | --- | --- | --- | --- |
| `document_id` | 0 | 0 | 106 | `BC/BC_Baro_Clear_Plus`<br>`BC/BC_Baro_KaPick`<br>`BC/BC_BizCorporate` |
| `provider` | 0 | 0 | 1 | `upstage` |
| `card_name` | 0 | 0 | 106 | `7CORE`<br>`ALL 우리카드 Infinite`<br>`Air One` |
| `issuer_name` | 0 | 0 | 22 | `롯데카드`<br>`우리카드`<br>`IBK기업은행` |
| `source_pdf_sha256` | 0 | 0 | 106 | `0de3582377f749a1c3db2b9e6f4cf02ec708a7f243d6a466f1f8cee8bdf40277`<br>`0e7ec487f3133f8400dd1e3fb26244c7947ebcbc65f7384e64f44501fc319aec`<br>`0f06d13bbb81e589092da814616552c1eafb91c104b1a70e95bb58b028e20f58` |
| `normalized_json_sha256` | 0 | 0 | 106 | `02dbd7714f06309dbba216263a809ce352063232d84cfe5b4edf7f2f0c949243`<br>`02fa1abd7d8c8daba33aac9f2ea255f74588d8cc6421bc6c9c756cc48d4e425d`<br>`067803c539cd715e7a1efcb02118ee8c0b7e52c5b66f69e440364108ff3c3ce8` |
| `source_relative_path` | 0 | 0 | 106 | `data/rag/runtime/working/run_f2d29820c6dca642/documents/BC__BC_Baro_Clear_Plus/upstage/normalized.json`<br>`data/rag/runtime/working/run_f2d29820c6dca642/documents/BC__BC_Baro_KaPick/upstage/normalized.json`<br>`data/rag/runtime/working/run_f2d29820c6dca642/documents/BC__BC_BizCorporate/upstage/normalized.json` |
| `source_path_sha256` | 0 | 0 | 106 | `063e807d7fc7410e06f9e749ab4878488d1f151a16ebfc130285174b3e010e2b`<br>`074f424b3ab84c0c024e0364ea719c851c2931a93ada061085d50a6d215032d5`<br>`09f84057c52a26c183febe38c3dc5d9f61eec95ba6db18bb2c93cb7534cda30f` |
| `page_count` | 0 | 0 | 15 | `2`<br>`8`<br>`6` |
| `fact_count` | 0 | 0 | 54 | `15`<br>`35`<br>`16` |
| `raw_json` | 0 | 0 | 106 | JSON 본문은 다음 예시/기계 판독 자료 참조 |

### facts: 전체 컬럼의 저장 프로필


| 컬럼 | NULL count | Empty count | Distinct | 실제 예시(최대 3개, 각 120자 절단) |
| --- | --- | --- | --- | --- |
| `fact_id` | 0 | 0 | 3403 | `fact_007a1f0c8920646865cd86a6`<br>`fact_007edcb323879064762c9af8`<br>`fact_0086038247fc9234f433e59f` |
| `document_id` | 0 | 0 | 106 | `hyundai/Hyundai_BlueMembers_F_M2_20260323`<br>`shinhan/Shinhan_Discount_Plan+_20250509`<br>`woori/Woori_ALL_Woori_Infinite` |
| `ordinal` | 0 | 0 | 87 | `0`<br>`1`<br>`2` |
| `target_raw` | 0 | 0 | 1964 | `가족카드`<br>`국내전용`<br>`국내외 가맹점` |
| `action_raw` | 0 | 0 | 616 | `적립`<br>`할인`<br>`청구할인` |
| `benefit_type_raw` | 0 | 0 | 990 | `연회비`<br>`M포인트`<br>`일상 생활비 적립 서비스` |
| `value_raw` | 0 | 1533 | 387 | ``<br>`10`<br>`5` |
| `unit_raw` | 0 | 1871 | 55 | ``<br>`%`<br>`원` |
| `condition_raw` | 0 | 437 | 1754 | ``<br>`국내전용`<br>`100만원 이상` |
| `cap_raw` | 0 | 2307 | 378 | ``<br>`5천원`<br>`1만원` |
| `period_raw` | 0 | 2253 | 403 | ``<br>`월`<br>`당월 1일~말일까지` |
| `frequency_raw` | 0 | 2887 | 165 | ``<br>`월`<br>`월 1회 변경 가능` |
| `exceptions_raw` | 0 | 1808 | 1070 | ``<br>`① 생활소비, ② 건강/자기관리, ③ 교통, ④ 여가 영역별 월 할인한도는 개별 적용 최초 카드사용등록일로부터 다음 달 말일까지는 전월실적 미달 시에도 최소한도 기준으로 서비스가 제공되며, 전월실적 조건 달성 시 해`<br>`멤버십 구독료에 대해서는 혜택이 제공되지 않습니다.` |
| `action_kind` | 1432 | 0 | 2 | `discount`<br>`earn` |
| `action_status` | 0 | 0 | 3 | `true`<br>`unknown`<br>`false` |
| `percent` | 2370 | 0 | 42 | `10.0`<br>`5.0`<br>`1.0` |
| `percent_state` | 0 | 0 | 3 | `not_applicable`<br>`known`<br>`unknown` |
| `spend_threshold_krw` | 3216 | 0 | 10 | `400000`<br>`500000`<br>`800000` |
| `spend_operator` | 3208 | 0 | 2 | `gte`<br>`unknown` |
| `spend_state` | 0 | 0 | 3 | `not_applicable`<br>`unknown`<br>`known` |
| `cap_krw` | 3022 | 0 | 31 | `5000`<br>`10000`<br>`20000` |
| `cap_state` | 0 | 0 | 3 | `not_applicable`<br>`unknown`<br>`known` |
| `cap_period` | 3039 | 0 | 2 | `month`<br>`day` |
| `raw_json` | 0 | 0 | 3403 | JSON 본문은 다음 예시/기계 판독 자료 참조 |
| `derived_json` | 0 | 0 | 2869 | JSON 본문은 다음 예시/기계 판독 자료 참조 |

### target_aliases: 전체 컬럼의 저장 프로필


| 컬럼 | NULL count | Empty count | Distinct | 실제 예시(최대 3개, 각 120자 절단) |
| --- | --- | --- | --- | --- |
| `alias_group` | 0 | 0 | 3 | `여행`<br>`카페`<br>`커피` |
| `alias` | 0 | 0 | 15 | `스타벅스`<br>`이디야`<br>`카페` |
| `alias_normalized` | 0 | 0 | 15 | `스타벅스`<br>`이디야`<br>`카페` |

### 코드 규칙과 별도로 확인한 상태·행동 관측값

각 빈도 분모는 facts 3,403행이다. NULL은 별도 값으로 표시한다. 서로 다른 상태와 행동 컬럼의 집계는 중복되므로 합쳐서 혜택 수로 계산하지 않는다.


| 컬럼 | 관측값: 행 수 |
| --- | --- |
| action_kind | NULL: 1432; discount: 1151; earn: 820 |
| action_status | true: 1818; unknown: 1249; false: 336 |
| percent_state | not_applicable: 1533; known: 1033; unknown: 837 |
| spend_state | not_applicable: 2097; unknown: 1119; known: 187 |
| spend_operator | NULL: 3208; gte: 187; unknown: 8 |
| cap_state | not_applicable: 2307; unknown: 715; known: 381 |
| cap_period | NULL: 3039; month: 358; day: 6 |

### 수치 파생값 지원 현황

Known(확정 비교 가능)은 코드의 보수적 정규화 규칙을 통과한 상태다. 분모는 전체 facts 3,403개이며 일반 안내도 포함한다. 높을수록 사용 가능한 수치가 많지만 OCR 정확도·검색 정확도 점수는 아니다. 예: 단일 10%는 원문/typed 값이 같아야 known, 최대 10%는 unknown이다.


| 항목 | 행 수 | 전체 fact 대비 비율 | 해석 |
| --- | --- | --- | --- |
| known_percent | 1033 | 30.36% | 비율값 확정 |
| known_spend_threshold | 187 | 5.50% | 단일 전월 최소 실적 확정 |
| known_cap | 381 | 11.20% | 지급 한도 금액 확정 |
| known_cap_period | 66 | 1.94% | 한도 금액과 월/일 기간 모두 확정 |
| positive_supported_action | 1818 | 53.42% | 긍정 할인/적립 행동 확정 |

cap_period만 식별된 행은 364개(월 358, 일 6)지만 금액과 기간이 모두 known인 행은 66개다. 기간을 알 수 있다고 지급 한도 금액도 알 수 있는 것은 아니다.


## 10. 실제 커피 혜택 행과 근거 연결 예시

다음은 가상값이 아니라 DB JSONL과 일치 확인한 실제 fact의 일부다. 이 행에서 조건·한도 문구가 비어 있다는 사실은 해당 카드 전체가 무실적·무제한이라는 뜻이 아니다. 카드의 다른 근거와 적용 조건은 별도 확인해야 한다.


```json
{
  "fact_id": "fact_fb3604346e3261cc2007a3c4",
  "document_id": "BC/BC_Green_v2",
  "fields": {
    "action": "청구할인",
    "benefit_type": "커피",
    "cap": "",
    "condition": "",
    "exceptions": "",
    "frequency": "",
    "period": "",
    "target": "스타벅스, 투썸플레이스, 카페베네, 할리스커피",
    "unit": "%",
    "value": "10"
  },
  "derived": {
    "action_kind": "discount",
    "action_reason": "single supported positive action",
    "action_status": "true",
    "cap_krw": null,
    "cap_period": null,
    "cap_period_reason": "cap and period are absent",
    "cap_period_state": "not_applicable",
    "cap_reason": "typed cap count is not one",
    "cap_state": "not_applicable",
    "exceptions_normalized": "",
    "has_exceptions": false,
    "percent": 10.0,
    "percent_reason": "single typed ratio agrees with raw percent",
    "percent_state": "known",
    "period_raw": "",
    "spend_operator": null,
    "spend_reason": "no explicit previous-month spend phrase",
    "spend_state": "not_applicable",
    "spend_threshold_krw": null,
    "target_normalized": "스타벅스,투썸플레이스,카페베네,할리스커피"
  },
  "field_evidence": {
    "action": [
      {
        "char_end": 15,
        "char_start": 11,
        "fragment": "청구할인",
        "line_id": "P0003-L0010"
      }
    ],
    "benefit_type": [
      {
        "char_end": 6,
        "char_start": 4,
        "fragment": "커피",
        "line_id": "P0003-L0010"
      }
    ],
    "cap": [],
    "condition": [],
    "exceptions": [],
    "frequency": [],
    "period": [],
    "target": [
      {
        "char_end": 29,
        "char_start": 4,
        "fragment": "스타벅스, 투썸플레이스, 카페베네, 할리스커피",
        "line_id": "P0003-L0011"
      }
    ],
    "unit": [
      {
        "char_end": 10,
        "char_start": 9,
        "fragment": "%",
        "line_id": "P0003-L0010"
      }
    ],
    "value": [
      {
        "char_end": 9,
        "char_start": 7,
        "fragment": "10",
        "line_id": "P0003-L0010"
      }
    ]
  },
  "relation_scope": {
    "header_line_ids": [],
    "line_ids": [
      "P0003-L0010",
      "P0003-L0011"
    ],
    "row_line_ids": [],
    "scope_type": "bounded_block"
  },
  "typed_normalization": {
    "condition_operators": [],
    "value": [
      {
        "decimal": "0.1",
        "kind": "ratio"
      }
    ]
  }
}
```


## 11. 별칭과 인덱스

별칭 확장은 고정 사전으로 대상 부분 문자열을 비교한다. 형태소 분석/온톨로지/완전한 업종 분류가 아니다. 문자열이 예외에도 등장하면 unknown으로 처리한다. 커피·카페 그룹은 같은 7개 별칭이며 여행은 8개, 전체 22행이다.


| 그룹 | 별칭 |
| --- | --- |
| 여행 | 여행, 항공, 항공사, 호텔, 숙박, 공항, 라운지, 면세점 |
| 카페 | 커피, 카페, 스타벅스, 투썸, 이디야, 폴바셋, 커피빈 |
| 커피 | 커피, 카페, 스타벅스, 투썸, 이디야, 폴바셋, 커피빈 |

인덱스는 SQLite 선언의 조회 보조 장치다. 현재 선택기의 대상·비율 비교가 SQL 인덱스로 실행된다는 뜻은 아니다. 자동 PK/UNIQUE 인덱스는 sqlite_autoindex_*로 생성되고 SQL 본문이 NULL이다.


| 명시적 인덱스 | 정의 |
| --- | --- |
| idx_facts_action | `CREATE INDEX idx_facts_action ON facts(action_kind, action_status)` |
| idx_facts_cap | `CREATE INDEX idx_facts_cap ON facts(cap_krw) WHERE cap_state = 'known'` |
| idx_facts_document | `CREATE INDEX idx_facts_document ON facts(document_id)` |
| idx_facts_percent | `CREATE INDEX idx_facts_percent ON facts(percent) WHERE percent_state = 'known'` |
| idx_facts_spend | `CREATE INDEX idx_facts_spend ON facts(spend_threshold_krw) WHERE spend_state = 'known'` |

### 실제 테이블 DDL

PK/FK/UNIQUE와 null 선언을 감사할 때의 기준이다. DEFAULT, CHECK, JSON 유효성 검사, ENUM, STRICT 선언은 이 스키마에 없다.


```sql
CREATE TABLE metadata (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
```


```sql
CREATE TABLE documents (
            document_id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            card_name TEXT,
            issuer_name TEXT,
            source_pdf_sha256 TEXT NOT NULL,
            normalized_json_sha256 TEXT NOT NULL,
            source_relative_path TEXT NOT NULL,
            source_path_sha256 TEXT NOT NULL,
            page_count INTEGER NOT NULL,
            fact_count INTEGER NOT NULL,
            raw_json TEXT NOT NULL
        );
```


```sql
CREATE TABLE facts (
            fact_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL REFERENCES documents(document_id),
            ordinal INTEGER NOT NULL,
            target_raw TEXT NOT NULL,
            action_raw TEXT NOT NULL,
            benefit_type_raw TEXT NOT NULL,
            value_raw TEXT NOT NULL,
            unit_raw TEXT NOT NULL,
            condition_raw TEXT NOT NULL,
            cap_raw TEXT NOT NULL,
            period_raw TEXT NOT NULL,
            frequency_raw TEXT NOT NULL,
            exceptions_raw TEXT NOT NULL,
            action_kind TEXT,
            action_status TEXT NOT NULL,
            percent REAL,
            percent_state TEXT NOT NULL,
            spend_threshold_krw INTEGER,
            spend_operator TEXT,
            spend_state TEXT NOT NULL,
            cap_krw INTEGER,
            cap_state TEXT NOT NULL,
            cap_period TEXT,
            raw_json TEXT NOT NULL,
            derived_json TEXT NOT NULL,
            UNIQUE(document_id, ordinal)
        );
```


```sql
CREATE TABLE target_aliases (
            alias_group TEXT NOT NULL,
            alias TEXT NOT NULL,
            alias_normalized TEXT NOT NULL,
            PRIMARY KEY(alias_group, alias)
        );
```


## 12. 지원 범위·빈 곳·다음 비교에 필요한 확인

| 요구/정보 | 현재 상태 | 후속 확인 |
| --- | --- | --- |
| 가맹점/업종·할인/적립·최소 비율 | 단일 fact 조건 지원 | 고정 별칭/문자열 포함 검사의 오탐·미탐 |
| 전월 최소 실적·지급 한도·월/일 | known인 단일 값만 지원 | 복수 실적 구간·공유 한도·혼합 기간 처리 |
| 원화 정액 할인·마일리지·포인트 환산 | 원문/typed로 보존하지만 QueryPlan 비교 필드 없음 | 필요 질문을 먼저 정하고 단위별 필터 설계 |
| 횟수/행사 날짜/결제 채널/연회비/개인 예산 최적화 | 일부 원문 정보는 보존. 전용 필터 없음 | 비구조화 보완 필요. raw 저장과 검색 지원을 구분 |
| 전체 카드 기준 제외 혜택 | exclude_targets는 개별 fact 대상에 적용 | 카드의 다른 혜택에 제외 대상이 있으면 카드 전체를 제외하는 정책과는 다름 |
| 긍정 혜택과 적용 제외 관계 | action 부정/예외 대상은 보수 처리 | 전체 관계 의미 검증 및 OCR 검증은 미실행 |
| fact → 원문 → 검색 chunk | 원문 fragment/줄/범위 있음, chunk_id SQL 컬럼 및 매핑 테이블 없음 | 다음 비교 전에 원문 연결·같은 카드 소유권·복수 청크 연결 검증 |
| 구조화 후보가 남는 의미 오류 | 형식 검사만으로 검출되지 않음 | 질문-조건 의미 검증/재검색 실험 필요 |
| 추출 실패의 지정 카드 범위 | 현재 예외 처리에서 card_keys 초기화 | 신뢰 가능한 지정 범위 보존 방식을 실험할 필요 |
| 빈 내부 AND 그룹 | 현재 모든 범위 카드를 통과시킬 수 있음 | 합의 방향은 빈 조건을 사용하지 않고 비구조화 복귀. 코드에는 아직 미반영 |

fact_id는 내용 전체의 해시가 아니다. 같은 문서/PDF/순서에서 fact 문구가 바뀌어도 fact_id만으로 변경을 알아낼 수 없으므로 normalized_json_sha256와 artifact 해시를 함께 확인한다. 구조화 후보 선택이 정답 라벨을 대신하지 않으며 OCR 통과 가정과 제목 계층 미완성도 유지한다.

## 13. 검증·자원·문서 관리

실제 SQLite를 mode=ro&immutable=1 및 query_only=ON으로 읽었다. 입력 DB/JSONL/요약/참고 코드의 해시·크기·mtime 보존, summary artifact SHA 일치, integrity_check=ok, foreign_key_check 오류 없음, SQLite와 JSONL facts 3,403행 및 documents 106행의 일치를 확인했다. 불변 모드는 WAL을 무시하므로 현재 정적 실험 DB에 적용했으며 활성 writer가 있는 운영 DB의 최신 조회 방법으로 일반화하지 않는다.

새 실험 API 요청 0, 추가 실험 API 사용량 비용 0달러, 새 임베딩/OCR/모델 검색 추론 0. Codex 문서 작성의 모델 사용료·청구액, 경과/CPU/GPU/최대 메모리는 미측정이다. 다음 방식 비교의 성능·비용·지연은 아직 측정하지 않았다. 정적 HTML과 로컬 링크는 확인하며 브라우저 시각 검증 여부는 별도 완료 기록에 적는다.

```bash
python -B notebooks/44_inspect_structured_db.py
```

명세는 docs/STRUCTURED_SEARCH_DB_SPEC.md와 docs/STRUCTURED_SEARCH_DB_SPEC.html에서 관리한다. DB 구성이나 값 규칙이 바뀌면 실제 DB·생성 코드와 대조해 두 문서를 함께 갱신한다. 전용 문서 생성 코드는 제거했다. 기존 44번 inspector와 노트북은 DB 스키마·저장값 확인용으로만 유지하며, inspector는 원본 DB를 재생성하지 않고 JSON inventory만 작성한다. 노트북은 저장 inventory를 읽기 전용으로 재검사하고 이 명세를 표시한다. 전체 유료 실험을 재실행하지 않는다.

## 최종 정리

현재 4테이블 41컬럼의 선언·값 규칙·관측 분포·JSON 내부 근거·질문 조건 연결을 문서화했다. 원문과 파생값의 관계는 유지되지만 원화 정액·복합 구간 등 검색 미지원 정보가 남아 있다. 이 명세를 작성·정리하면서 운영/DB/이전 비교 실험의 검색 설정은 변경하지 않았다. 추가 실험 API 비용 0, 시간·메모리 미측정이다. 다음 단계는 fact와 원문 청크를 연결해 현재 카드 범위 검색과 근거 보완 검색을 같은 조건에서 비교하는 것이다. 성능 우열은 아직 미확정이다.

## 63~65번 실행 계약과 기존 스냅샷의 차이

위 테이블·분포·기능 설명은44번 작성 시점의 스냅샷이다. 아래는60번 이후 실험 코드의 실행 계약을 구분한 기록이며 원본 DB나 스키마를 변경하지 않는다.

- 카드/문서1개에 혜택 fact 여러 행이 연결된다. 고정 `QueryPlan`에 LLM이 조건값을 채우며 임의 SQL이나 새 컬럼을 만들지 않는다.
- 현재 조건에는 `min_frequency`와 `frequency_period`가 있다. `frequency_raw`는 원문 문자열을 유지하고 실행 조회에서 횟수·기간을 보수적으로 판정한다. 위의 ‘횟수 필터 없음’은44번 기능 설명이다.
- 발급사·상품 범위는 LLM `card_keys`가 아니라 독립된 질문–카탈로그 처리에서 결정한다. 64번은 명확한 발급사 문맥과 유일한 상품명을 사용하고, 가맹점·결제수단의 브랜드만으로 발급사를 제한하지 않는다.
- 같은 카드 AND는 서로 다른 fact 행도 허용하는 SQL `EXISTS`와 바인딩된 파라미터/UDF로 조회한다. 양성 결과 카드 안에서 원질문 U 한 번,0건/추출 실패면 신뢰 가능한 원범위 U 한 번이다. DB 예외는0건으로 숨기지 않는다.
- 빈 바깥 조건은 비구조화로 복귀한다. 빈 내부 AND는 `QueryPlan` 검증에서 거절되고, 추출 실패 경로에서는 원범위를 보존한다. 상품이 모호한 경우의 확인 요청은 별도 경로다.
- 65번이 사용하는 DB는60번 복사 `db_v3`다. 명시 무실적23행·무료48행의 파생값71행을 원문 확인했으나 전체3403행의 추출 정확도를 인증한 것은 아니다. unknown을 일괄0으로 합치지 않는다.

실행 근거: `notebooks/scripts/50_query_contract.py`, `60_query_repair.py`, `64_scope_repair.py`. 운영 반영 여부와 새 실험 채택 여부는 각 결과 보고서에서 따로 확인한다.

## worktree 운영 코드의 DB3 순차 검색 계약

이번 운영 이식은 73번의 **기존 순차**(DB3 정규화와 기존 조회 판정, 후보 카드 목록만 전달)를 사용한다. 73번 수정 순차의 DB5·대상어 보정·구조화 근거 전달은 포함하지 않는다. 운영 모듈은 노트북 스크립트를 import하지 않는다.

- 생성: `jobs/rag-indexer/src/pickcardu_indexer/structured.py`, `structured_normalization.py`. 기본 파생 규칙(30) → 보수적 정규화(56) → 명시 무실적·무료 이용 보정(60)을 적용한다. Luna와 Upstage 입력에 같은 함수가 적용되며 원문 fact·필드 근거·relation_scope·pages·provenance는 보존한다.
- 조회: `packages/rag-core/src/pickcardu_rag/structured.py`. 고정 `QueryPlan` 값만 사용하고 SQL 파라미터를 분리한다. 같은 카드의 서로 다른 fact가 AND를 만족할 수 있으며 다른 카드의 부분 일치를 합치지 않는다. 기존 target aliases만 유지한다.
- 계약 식별자: `db3-normalization30-56-60-v1`, `db3-query60-v1`. 기존 네 테이블과 scalar/raw/derived 컬럼을 유지하며 `metadata`의 `contract` 값에 공급자·문서 ID·PDF SHA256·입력 JSON SHA256·원천 경로·입력 정규화 계약을 기록한다.
- 불변 릴리스의 `manifest.structured`는 DB SHA256와 같은 contract 전체를 결속한다. 빌드 시 실제 PDF/JSON/OCR·청크 원천과 raw/derived를 대조하고, 로딩/설치 시 DB 해시·SQLite integrity/FK·문서/카탈로그·aliases·scalar/derived 일치를 검사한다. DB가 있는 릴리스의 오류는 준비 검사에서 차단한다. 요청 중에는 DB 쓰기가 없다.
- 구조화 후보는 `allowed_card_keys`로 전달한다. 모든 대상이 필수인 `target_card_keys`(1~5개)와 분리되어 후보 5개 제한이 없다. BM25·벡터의 깊이 제한 전에 범위를 적용하고, parent-child 관련 근거도 같은 카드 안에 머무른다.
- 0건·빈 조건·미지원·추출 실패·요청 중 조회 오류는 원래 허용 집합으로 복귀한다. 보유·신규는 각 집합을 별도 처리한다. 명시 발급사·상품 집합을 독립 인식해 허용 집합과 교차하고, 모순되면 확대하지 않는다. 긴 상품명에 포함된 짧은 다른 상품명은 제외하되 별도 언급은 보존한다.
- 실적 상한·혜택 한도 하한 검색 제한(무실적 0 포함)은 후보 축소를 생략한다. 이는 **DB3 재현에 추가한 운영 보호 정책**이며 정규화 보정이나 DB5 이식이 아니다. 실적/한도 설명 요청은 검색 제한과 구분한다.
- 기본 `hybrid`는 DB 없는 구버전도 지원한다. 선택 설정 `structured_then_hybrid`는 결속된 DB가 있어야 ready다. 구조화 결과가 있는 요청은 해당 범위의 기존 본문 검색 한 번만 사용하며, 전체 카드 검색 결과나 구조화 fact를 답변 근거에 추가하지 않는다.

실제 검증 DB·릴리스별 수치, 무료 대조·소규모 API 확인과 한계는 `data/rag/validation/structured_search_integration/`의 기록과 별도 통합 보고서를 따른다. 기존 표의 관측 분포는 이전 실험 시점의 값이며 이 절의 운영 계약과 구분한다. 생성·설치·rollback 명령은 `docs/API_RUNBOOK.md`를 참고한다.
