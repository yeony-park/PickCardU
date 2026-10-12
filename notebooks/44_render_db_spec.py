"""Generate notebook 44's data dictionary from the read-only SQLite inventory.

Documentation generation only: fixed explanations plus inspected schema/value
profiles. No retrieval experiments, provider calls or production imports.
"""
import hashlib
import html
import json
from pathlib import Path
import re
import markdown

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'notebooks/data/44_structured_search_db_spec'
MD = ROOT / 'notebooks/44_structured_search_db_spec.md'
HTML = ROOT / 'docs/report/44_structured_search_db_spec.html'

# Meaning, builder rules / intended values, and actual search use.
DESCRIPTIONS = {
 'metadata': {
  'key': ('빌드 메타데이터 항목 이름', '현재 8개 key. 아래 key별 표 참조', '버전·출처·정책 추적'),
  'value_json': ('항목 값의 JSON 문자열', '문자열/객체/목록/숫자 구조를 JSON으로 직렬화', '검색 조건 자체가 아니라 빌드 설명'),
 },
 'documents': {
  'document_id': ('문서/카드 식별 키', '원본 document_id. 예: BC/BC_Green_v2. basename만으로 만들지 않음', 'card_keys 및 facts.document_id와 연결'),
  'provider': ('원본 OCR 제공자', '이 빌드는 upstage만 허용. SQL enum 제약은 없음', '제공자와 출처 구분'),
  'card_name': ('원본 identity.card_name', '원문 카드명; 누락 시 NULL 가능', 'LLM에 제공할 카드 목록 구성'),
  'issuer_name': ('원본 identity.issuer_name', '원문 카드사명; 누락 시 NULL 가능', '표시/카드 목록 정보. 별도 issuer 조건 없음'),
  'source_pdf_sha256': ('원본 PDF 내용 SHA-256', '원본 JSON에서 복사. DB는 길이·hex 형식을 강제하지 않음', '원문 동일성 추적'),
  'normalized_json_sha256': ('입력 normalized.json bytes SHA-256', '파일 실제 bytes로 계산. JSON 의미 동일성과는 다름', '입력 변경 추적'),
  'source_relative_path': ('normalized.json의 저장소 상대 경로', '현재 빌드 입력 glob의 문서 경로', '원문 출처 접근 및 정렬'),
  'source_path_sha256': ('위 상대 경로 문자열 SHA-256', 'UTF-8 경로를 해시. 파일 내용 해시가 아님', '경로 동일성 추적'),
  'page_count': ('저장 pages 목록 길이', '0 이상 정수로 생성. SQL CHECK 없음', '원문 규모 정보'),
  'fact_count': ('해당 문서 원본 facts 목록 길이', '일반 안내도 포함. 순수 혜택 수와 다름', '문서별 fact 수 확인'),
  'raw_json': ('문서 보존용 객체의 JSON 문자열', 'identity/pages/provenance/해시 등. 원본 normalized.json 전체 bytes 복제는 아님', 'load_facts의 원문 페이지·카드 정체성 복원'),
 },
 'facts': {
  'fact_id': ('문서 내 원본 fact 식별자', 'fact_ + SHA-256 앞 24hex. provider|PDF해시|document_id|ordinal로 계산', 'matching_fact_ids → 원문 근거 연결'),
  'document_id': ('소유 문서/카드 키', 'documents.document_id를 참조', '같은 카드 안의 조건 평가 및 근거 소유권'),
  'ordinal': ('원본 facts 목록의 0부터 시작하는 순서', 'enumerate로 생성. (document_id, ordinal) UNIQUE', '원본 fact 순서 복원. 목록 변경 시 fact_id가 달라질 수 있음'),
  'target_raw': ('원본 대상 문자열', '가맹점/업종/대상 목록/안내 대상 등 자유 문자열. 미기재는 빈 문자열 가능', 'derived.target_normalized에 대상/별칭 포함 여부 비교'),
  'action_raw': ('원본 행동 문자열', '할인/청구할인/적립/안내/부정 문구 등 자유 문자열', 'benefit_type_raw와 함께 action_kind/status 생성'),
  'benefit_type_raw': ('원본 혜택 이름/분류 설명', '자유 문자열. 고정 카테고리 enum 아님', '행동 정규화 보조. 별도 분류 필터 없음'),
  'value_raw': ('원본 수치/값 문구', '5, 3~5, 최대 10 등 문자열. 단일 숫자로 제한하지 않음', 'typed ratio와 일치할 때 percent 생성'),
  'unit_raw': ('원본 단위 문자열', '%, 원, 마일리지, 포인트, 빈 문자열 등. 전체 관측값은 inventory 참조', 'percent 정규화 보조. 단위 자유 비교 필터 없음'),
  'condition_raw': ('원본 적용 조건 문구', '전월 실적/결제 방식/복합 조건 등을 원문 형태로 보존', '명시적인 단일 전월 최소 실적만 파생 비교'),
  'cap_raw': ('원본 한도 문구', '혜택 지급 한도/이용금액 상한 등 원문 표현', '지급 한도만 보수적으로 cap_krw 생성'),
  'period_raw': ('원본 기간 문구', '월/일/지급일/행사 기간 등 자유 문자열', 'cap_raw와 합쳐 월/일 한도 기간 식별. 행사 날짜 검색 아님'),
  'frequency_raw': ('원본 횟수 조건', '월 1회 등 자유 문자열. 미기재는 빈 문자열', '보존만 함. 횟수 수치 필터 없음'),
  'exceptions_raw': ('원본 제외/예외 문구', '제외 업종/채널/기타 예외 등 자유 문자열', '요구 대상이 예외에 언급되면 unknown으로 보수 처리'),
  'action_kind': ('정규화한 행동 종류', 'discount(할인), earn(적립 계열), NULL(판단 불가)', 'Requirement.action과 비교'),
  'action_status': ('행동을 긍정 혜택으로 볼 수 있는지', 'true(긍정), false(부정/제외), unknown(혼합/모호)', 'false 제외; unknown은 확정 충족으로 선택하지 않음'),
  'percent': ('비율의 퍼센트값', '10% → 10.0, 0.1이 아님. known일 때 수치, 그 외 NULL', 'percent >= min_percent'),
  'percent_state': ('비율값의 사용 가능 상태', 'known / unknown / not_applicable. 아래 상태 정의 참조', '비율 요구 시 known만 확정 비교'),
  'spend_threshold_krw': ('필요 전월 최소 실적 원화 금액', '30만원 이상 → 300000. known일 때 수치, 그 외 NULL', '필요 실적 <= 질문의 max_spend_krw'),
  'spend_operator': ('원문 실적 부등호 판정', 'gte(이상), lte(이하), unknown, NULL을 코드가 만들 수 있음. 관측값은 별도 표', 'known은 gte만. 질문의 최대 허용 실적과 부등호 방향을 구분'),
  'spend_state': ('전월 실적값 사용 가능 상태', 'known / unknown / not_applicable', '실적 요구 시 known만 확정 비교'),
  'cap_krw': ('혜택 지급 한도 원화 금액', '월 할인 한도 5천원 → 5000. 이용금액 상한과 구분', 'cap_krw >= min_cap_krw'),
  'cap_state': ('혜택 한도값 사용 가능 상태', 'known / unknown / not_applicable', '한도 요구 시 known만 확정 비교'),
  'cap_period': ('한도와 연결된 기간', 'month(월), day(일), NULL(모호/미기재)', 'min_cap_krw 요구가 있을 때 cap_period_state와 함께 기간 비교'),
  'raw_json': ('원본 fact 객체의 JSON 문자열', '원문 10필드 + typed_normalization + field_evidence + relation_scope 등 보존', '검색 load_facts는 이 JSON에서 원문/근거를 복원'),
  'derived_json': ('보수적으로 계산한 파생 객체 JSON', 'SQL 파생 컬럼 + 대상 정규화/사유/기간 상태/예외 정규화 포함', '현재 선택기는 복원된 derived 객체로 비교'),
 },
 'target_aliases': {
  'alias_group': ('별칭 그룹 이름', '여행, 커피, 카페. LLM이 동적으로 생성하지 않음', '개발자가 고정한 대상 확장 규칙'),
  'alias': ('그룹에 속한 원문 별칭', '스타벅스, 항공, 호텔 등. (alias_group, alias) 복합 PK', '동일 그룹 내 대상 대안'),
  'alias_normalized': ('별칭의 정규화 문자열', 'NFKC → casefold → 모든 공백 제거', '현재 선택기는 코드의 TARGET_ALIASES를 사용; 이 테이블은 규칙 보존용'),
 },
}


def table(headers, rows):
    def cell(value):
        return str(value).replace('|', '&#124;').replace('\n', '<br>')
    return '\n'.join(['| ' + ' | '.join(map(cell, headers)) + ' |',
                       '| ' + ' | '.join(['---'] * len(headers)) + ' |'] +
                      ['| ' + ' | '.join(map(cell, row)) + ' |' for row in rows])


def constraints(col, name):
    values = ['NOT NULL' if col['notnull'] else 'NOT NULL 선언 없음']
    if col['pk']:
        values.append('PK' + (f"({col['pk']})" if name == 'target_aliases' else ''))
    if name == 'facts' and col['name'] == 'document_id':
        values.append('FK → documents.document_id')
    if name == 'facts' and col['name'] in ['document_id', 'ordinal']:
        values.append('두 컬럼 조합 UNIQUE')
    values.append('DEFAULT 없음')
    return ', '.join(values)


def render():
    r = json.loads((DATA / 'schema_inventory.json').read_text())
    for name, identity in r['input_receipt'].items():
        p = ROOT / name
        assert hashlib.sha256(p.read_bytes()).hexdigest() == identity['sha256']
        assert p.stat().st_mtime_ns == identity['mtime_ns']
    parts = [INTRO]
    parts.append('## 2. 테이블 관계와 실제 행 수\n\n```text\ndocuments (1) ── facts (N)\nmetadata: 빌드 설명\ntarget_aliases: 고정 별칭 사전 보존\n```\n')
    parts.append(table(['테이블', '행 수', '컬럼 수', '역할'], [[name, r['tables'][name]['row_count'], len(r['tables'][name]['columns']),
                        {'metadata':'빌드 계약', 'documents':'문서·카드 정체성과 원문 페이지', 'facts':'원본 사실과 검색용 파생값', 'target_aliases':'별칭 목록'}[name]] for name in r['tables']]))
    parts.append('행 수(Row count)는 읽은 DB의 COUNT(*), 컬럼 수는 PRAGMA table_info 기준이다. 품질 점수가 아니며 크다고 더 좋은 값은 아니다. facts에는 일반 안내·연회비·부정 문구도 들어 있다.\n')
    for number, name in enumerate(['metadata', 'documents', 'facts', 'target_aliases'], 3):
        t = r['tables'][name]
        assert {c['name'] for c in t['columns']} == set(DESCRIPTIONS[name])
        parts.append(f'## {number}. `{name}` 테이블 — 전체 컬럼\n')
        parts.append('타입은 SQLite DDL의 선언 타입이다. 제약은 DB가 강제하는 선언이며, 값 규칙은 빌드/검색 코드의 의미다. enum과 단위·JSON 형식은 CHECK로 강제하지 않는다. 예시는 규칙 설명이며 실제 관측값과 구분한다.\n')
        parts.append(table(['컬럼', '선언 타입', 'DDL 제약', '의미', '값 규칙·예시', '현재 검색 용도'],
            [[f"`{c['name']}`", c['type'], constraints(c, name), *DESCRIPTIONS[name][c['name']]] for c in t['columns']]))
    parts.append(NESTED)
    parts.append('### JSON 키 누락과 명시적 null의 실제 관측\n\nMissing(키 누락), Null(명시적 null), Empty object(빈 객체 {})를 구분한다. facts 원문/복원값은 각 3,403행, documents는 106행이 분모다. 품질 점수가 아니며 원문 누락값을 복원한 것이 신규 정보 추출을 뜻하지 않는다.\n')
    parts.append(table(['위치', '속성', '실제 타입: 행 수', 'Missing', 'Null', 'Empty object'],
        [[location, key, '; '.join(f'{kind}: {count}' for kind, count in profile['type_counts'].items()),
          profile['missing_count'], profile['explicit_null_count'], profile['empty_object_count']]
         for location, fields in r['json_shape_profiles'].items() for key, profile in fields.items()]))
    parts.append('typed_normalization은 원본 raw_json에서 객체 3,378행·키 누락 25행·명시적 null 0행이다. 현재 reader는 누락된 키를 {}로 복원하므로 읽은 facts에는 객체 3,403행이 있다. 원본 저장 형태와 reader 결과를 혼동하지 않는다.\n')
    parts.append(QUERY)
    parts.append('## 9. 실제 저장값·분포와 빈 값\n\n아래는 2026-10-13 DB 관측값이다. SQL 허용값 전체나 사용자 질문의 빈도를 뜻하지 않는다. NULL count는 미저장값 수, Empty count는 정확한 빈 문자열 수, Distinct는 NULL을 제외한 서로 다른 값 수다. 각각 해당 테이블의 전체 행이 분모이며 품질 통과 기준은 없다. 원문 누락의 이유와 추출 정확도를 이 수치만으로 판정하지 않는다. 전체 자유 문자열과 빈도는 [schema_inventory.json](data/44_structured_search_db_spec/schema_inventory.json)의 column_profiles.value_counts에서 확인한다.\n')
    for name, t in r['tables'].items():
        parts.append(f'### {name}: 전체 컬럼의 저장 프로필\n')
        parts.append(table(['컬럼', 'NULL count', 'Empty count', 'Distinct', '실제 예시(최대 3개, 각 120자 절단)'],
            [[f"`{col}`", p['null_count'], p['empty_string_count'], p['distinct_nonnull'],
              'JSON 본문은 다음 예시/기계 판독 자료 참조' if col in {'raw_json','derived_json','value_json'} else '<br>'.join(f'`{html.escape(v)}`' for v in p['examples']) or '없음']
              for col, p in t['column_profiles'].items()]))
    domains = ['action_kind','action_status','percent_state','spend_state','spend_operator','cap_state','cap_period']
    parts.append('### 코드 규칙과 별도로 확인한 상태·행동 관측값\n\n각 빈도 분모는 facts 3,403행이다. NULL은 별도 값으로 표시한다. 서로 다른 상태와 행동 컬럼의 집계는 중복되므로 합쳐서 혜택 수로 계산하지 않는다.\n')
    parts.append(table(['컬럼', '관측값: 행 수'], [[col, '; '.join(f"{x['value'] if x['value'] is not None else 'NULL'}: {x['count']}" for x in r['tables']['facts']['column_profiles'][col]['value_counts'])] for col in domains]))
    parts.append('### 수치 파생값 지원 현황\n\nKnown(확정 비교 가능)은 코드의 보수적 정규화 규칙을 통과한 상태다. 분모는 전체 facts 3,403개이며 일반 안내도 포함한다. 높을수록 사용 가능한 수치가 많지만 OCR 정확도·검색 정확도 점수는 아니다. 예: 단일 10%는 원문/typed 값이 같아야 known, 최대 10%는 unknown이다.\n')
    counts = r['build_summary']['counts']
    parts.append(table(['항목', '행 수', '전체 fact 대비 비율', '해석'], [[key, counts[key], f"{counts[key]/counts['facts']:.2%}", explanation] for key, explanation in [
        ('known_percent','비율값 확정'),('known_spend_threshold','단일 전월 최소 실적 확정'),('known_cap','지급 한도 금액 확정'),('known_cap_period','한도 금액과 월/일 기간 모두 확정'),('positive_supported_action','긍정 할인/적립 행동 확정')]]))
    parts.append('cap_period만 식별된 행은 364개(월 358, 일 6)지만 금액과 기간이 모두 known인 행은 66개다. 기간을 알 수 있다고 지급 한도 금액도 알 수 있는 것은 아니다.\n')
    parts.append('## 10. 실제 커피 혜택 행과 근거 연결 예시\n\n다음은 가상값이 아니라 DB JSONL과 일치 확인한 실제 fact의 일부다. 이 행에서 조건·한도 문구가 비어 있다는 사실은 해당 카드 전체가 무실적·무제한이라는 뜻이 아니다. 카드의 다른 근거와 적용 조건은 별도 확인해야 한다.\n')
    example = r['actual_coffee_example']
    parts.append('```json\n' + json.dumps({k:example[k] for k in ['fact_id','document_id','fields','derived','field_evidence','relation_scope','typed_normalization']}, ensure_ascii=False, indent=2) + '\n```\n')
    parts.append('## 11. 별칭과 인덱스\n\n별칭 확장은 고정 사전으로 대상 부분 문자열을 비교한다. 형태소 분석/온톨로지/완전한 업종 분류가 아니다. 문자열이 예외에도 등장하면 unknown으로 처리한다. 커피·카페 그룹은 같은 7개 별칭이며 여행은 8개, 전체 22행이다.\n')
    parts.append(table(['그룹', '별칭'], [[key, ', '.join(values)] for key, values in r['build_summary']['target_aliases'].items()]))
    parts.append('인덱스는 SQLite 선언의 조회 보조 장치다. 현재 선택기의 대상·비율 비교가 SQL 인덱스로 실행된다는 뜻은 아니다. 자동 PK/UNIQUE 인덱스는 sqlite_autoindex_*로 생성되고 SQL 본문이 NULL이다.\n')
    parts.append(table(['명시적 인덱스', '정의'], [[idx['name'], f"`{idx['sql']}`"] for idx in r['indexes'] if idx['sql']]))
    parts.append('### 실제 테이블 DDL\n\nPK/FK/UNIQUE와 null 선언을 감사할 때의 기준이다. DEFAULT, CHECK, JSON 유효성 검사, ENUM, STRICT 선언은 이 스키마에 없다.\n')
    for name, t in r['tables'].items():
        parts.append(f"```sql\n{t['ddl']};\n```\n")
    parts.append(LIMITS)
    MD.write_text('\n\n'.join(parts).rstrip() + '\n')
    body = markdown.markdown(MD.read_text(), extensions=['tables','fenced_code','toc'])
    def link(match):
        href = match.group(1)
        if href.startswith('#') or '://' in href:
            return match.group(0)
        absolute = (MD.parent / href).resolve()
        import os
        return 'href="' + html.escape(os.path.relpath(absolute, HTML.parent)) + '"'
    body = re.sub(r'href="([^"]+)"', link, body)
    css = 'body{font:16px/1.7 system-ui,sans-serif;color:#20304a;background:#f5f7fb;margin:0}main{max-width:1500px;margin:24px auto;padding:28px;background:white}table{border-collapse:collapse;width:100%;font-size:14px;display:block;overflow-x:auto;margin:20px 0}th,td{border:1px solid #cbd5e1;padding:9px;vertical-align:top;min-width:100px}th{background:#edf3fb}pre{white-space:pre-wrap;overflow-wrap:anywhere;padding:16px;background:#f1f5f9}code{overflow-wrap:anywhere}a{color:#1458aa}h2{border-top:1px solid #dbe3ee;padding-top:20px}'
    HTML.write_text('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>44. 구조화 검색 DB 명세서</title><style>' + css + '</style></head><body><main>' + body + '</main></body></html>\n')
    return {'markdown': str(MD.relative_to(ROOT)), 'html': str(HTML.relative_to(ROOT)), 'tables': len(r['tables'])}


INTRO = '''# 44. 구조화 검색 DB 명세서

## 1. 문서 범위와 기준

현재 실험 DB의 데이터 사전이다. 검토일은 2026-10-13, 스키마 버전은 `structured-benefit-experiment-v3`다. 운영 DB/HTTP 계약을 정의하는 문서가 아니며 운영 반영·DB 변경은 하지 않았다. 현재 방식과 근거 보완 방식의 비교는 다음 실험이며 이 문서는 그 전에 작성한 읽기 전용 명세다.

- DB: `notebooks/data/30_structured_benefit_db/structured.sqlite`.
- 입력: 기존 Upstage `normalized.json` 106개. 구조화 계약 `field-evidence-relation-v6`, 정규화 계약 `typed-facts-v2`. 이 입력의 추출 모델 기록은 `gpt-5.6-luna/max`이며 질문 조건 추출 실험의 `gpt-6-luna/medium`과 별개다.
- [DB 생성·정규화·선택기 구현](30_build_structured_db.py), [질문 스키마](32_search_experiment.py), [질문 해석 지시문](33_query_routing.py), [42번 실제 질문 조건 호출·순차 검색](42_expanded_query_evaluation.py).
- [실제 DB 스키마·저장값·검증 JSON](data/44_structured_search_db_spec/schema_inventory.json), [44번 노트북](44_structured_search_db_spec.ipynb), [30번 DB 보고서](../docs/report/30_structured_benefit_db_report.html), [43번 경계 검사](../docs/report/43_offline_validation_report.html).

같은 fact 안의 대상·행동·수치·조건 관계를 유지한다. 원문 필드는 그대로 보존하고 검색 가능한 파생값을 보수적으로 추가한다. OCR/원본 구조화 추출의 정확도는 이 명세 작성으로 검증하지 않았고 통과 가정을 유지한다. 문서에 제시한 코드 규칙과 실제 관측값은 구분한다.

**제약 해석:** TEXT/INTEGER/REAL은 SQLite 선언 타입이며 STRICT 테이블이 아니다. 모든 컬럼에 DEFAULT가 없다. `NOT NULL`은 빈 문자열 금지가 아니다. TEXT 단일 PK인 metadata.key/documents.document_id/facts.fact_id에는 별도 NOT NULL 선언이 없고 PRAGMA notnull=0이다. 일반 SQLite rowid 테이블의 이 선언만으로 NULL 금지를 주장하지 않는다. 현재 빌드는 비어 있지 않은 식별자를 만들고 실제 NULL 여부는 저장 프로필에서 확인한다. 복합 PK인 target_aliases의 두 컬럼은 명시적으로 NOT NULL이다. FK는 writer가 foreign_keys=ON으로 활성화했으며 현재 외래키 검사를 별도로 수행했다. UPDATE/DELETE CASCADE 선언은 없다.
'''

NESTED = '''## 7. JSON 내부 속성과 출처 연결

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
'''

QUERY = '''## 8. 정규화 규칙과 질문 스키마의 연결

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
'''

LIMITS = '''## 12. 지원 범위·빈 곳·다음 비교에 필요한 확인

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

## 13. 검증·자원·문서 재생성

실제 SQLite를 mode=ro&immutable=1 및 query_only=ON으로 읽었다. 입력 DB/JSONL/요약/참고 코드의 해시·크기·mtime 보존, summary artifact SHA 일치, integrity_check=ok, foreign_key_check 오류 없음, SQLite와 JSONL facts 3,403행 및 documents 106행의 일치를 확인했다. 불변 모드는 WAL을 무시하므로 현재 정적 실험 DB에 적용했으며 활성 writer가 있는 운영 DB의 최신 조회 방법으로 일반화하지 않는다.

새 실험 API 요청 0, 추가 실험 API 사용량 비용 0달러, 새 임베딩/OCR/모델 검색 추론 0. Codex 문서 작성의 모델 사용료·청구액, 경과/CPU/GPU/최대 메모리는 미측정이다. 다음 방식 비교의 성능·비용·지연은 아직 측정하지 않았다. 정적 HTML과 로컬 링크는 확인하며 브라우저 시각 검증 여부는 별도 완료 기록에 적는다.

```bash
python -B notebooks/44_inspect_structured_db.py
python -B notebooks/44_render_db_spec.py
```

기존 승인 환경의 markdown/nbformat/nbclient를 사용하고 새 패키지를 설치하지 않았다. inspector는 원본 DB를 재생성하지 않고 44번 JSON inventory만 작성한다. renderer는 44번 Markdown과 HTML만 작성한다. 별도 helper는 다수 테이블/컬럼의 반복 검사와 재생성 책임을 담당하며, 노트북은 이를 읽기 전용으로 재검사하고 결과를 표시한다. 전체 유료 실험을 노트북에서 재실행하는 구조가 아니다.

## 최종 정리

현재 4테이블 41컬럼의 선언·값 규칙·관측 분포·JSON 내부 근거·질문 조건 연결을 문서화했다. 원문과 파생값의 관계는 유지되지만 원화 정액·복합 구간 등 검색 미지원 정보가 남아 있다. 운영/DB/이전 실험은 변경하지 않았고 이번 단계에서 채택·거절한 검색 설정은 없다. 추가 실험 API 비용 0, 시간·메모리 미측정이다. 다음 단계는 fact와 원문 청크를 연결해 현재 카드 범위 검색과 근거 보완 검색을 같은 조건에서 비교하는 것이다. 성능 우열은 아직 미확정이다.
'''


if __name__ == '__main__':
    print(render())
