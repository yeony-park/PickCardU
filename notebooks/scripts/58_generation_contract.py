"""Shared prompt contract for58/59; capture operating instructions without API.

The notebook owns the comparison. This helper prevents retry instructions or
same-input reuse from silently changing between resumable experiments.
"""
from types import SimpleNamespace

CONDITION_GUIDE = '''

[질문별 필수 정보와 적용 조건 점검]
- 답변 전에 질문이 직접 요구한 항목과 해당 혜택의 적용 여부를 바꾸는 중요한 조건을 카드별로 확인하세요.
- 입력 근거에 있는 요구 항목은 답변에 반영하세요. 이미 있는 내용을 미확인이라고 하거나 일부 항목만 답하지 마세요.
- 질문과 관련된 전월실적, 할인·적립 한도, 횟수, 서비스 선택·공유 조건, 제외 대상과 결제 경로를 확인하세요. 질문과 무관한 조건을 모두 나열할 필요는 없습니다.
- 각 조건이 어느 카드의 어느 혜택에 적용되는지 보존하세요. 같은 카드라도 다른 혜택의 수치나 조건을 가져오지 마세요.
- 결제 대상금액 한도와 실제 할인액 한도, 금액과 비율, 회당·일·월·연 기준을 구분하세요. 계산에는 해당 기준과 적용 조건을 함께 사용하세요.
- 원문에 횟수나 조건이 적혀 있지 않은 것과 제한이 없는 것은 다릅니다. 미기재를 무제한이나 조건 없음으로 단정하지 마세요.
- 질문에 포함된 수치나 예상 결론도 근거와 대조하세요. 질문의 전제가 틀리면 확인된 조건에 따라 설명하세요.
- 출력 길이가 부족하면 질문에 직접 답하는 사실과 중요한 적용 조건을 우선하고, 반복과 부가 설명을 줄이세요. 기존 JSON 스키마와 길이 상한을 유지하세요.
'''


def operating_prompts():
    """Exercise the injected client twice to capture the real retry branch."""
    from pickcardu_rag.answering import OpenAIService

    class Capture:
        def __init__(self):
            self.responses = self
            self.instructions = []

        def parse(self, **kwargs):
            self.instructions.append(kwargs['instructions'])
            if len(self.instructions) == 1:
                raise ValueError('offline deliberate validation failure')
            return SimpleNamespace(output_parsed=kwargs['text_format'](
                answer_status='insufficient_evidence', answer_text='근거 확인 진단',
                recommendations=[], claims=[]), usage={})

    capture = Capture()
    evidence = [dict(chunk_id='offline-only', card_key='offline/card',
                     card_name='형식 확인 카드', issuer='형식 확인', text='형식 확인 근거')]
    OpenAIService(api_key=None, client=capture).answer('형식 확인', evidence)
    if len(capture.instructions) != 2:
        raise RuntimeError('Operating service did not expose exactly two validation attempts')
    return tuple(capture.instructions)


def transform_prompt(original, variant):
    if variant not in ['old', 'new']:
        raise ValueError('Unknown prompt variant')
    if variant == 'old' or original.endswith(CONDITION_GUIDE):
        return original
    return original + CONDITION_GUIDE


def reuse_identity(identity):
    parts = identity.split(':')
    if len(parts) != 5 or parts[2] not in ['A', 'B'] or parts[3] not in ['old', 'new'] or parts[4] not in ['r1', 'r2', 'r3']:
        raise ValueError('Unexpected answer identity')
    if parts[2] == 'A':
        return None
    parts[2] = 'A'
    return ':'.join(parts)


def validate_diagnostic(data,*,questions,retrieval_sha,source_facts_sha,required_ids,receipts,reference_contract,sha,validate_source):
    """Bind manually checked original excerpts; hashes do not prove semantics."""
    if (data.get('status')!='source_excerpts_manually_verified'
            or data.get('retrieval_results_sha256')!=retrieval_sha
            or data.get('source_facts_sha256')!=source_facts_sha
            or data.get('reference_contract_sha256')!=sha(reference_contract)
            or data.get('review_receipts_sha256')!=sha(receipts)
            or set(data.get('by_query',{}))!=set(questions)):
        raise ValueError('Diagnostic input or review seal changed')
    for qid,question in questions.items():
        row=data['by_query'][qid];result=row['result'];review=receipts[qid]
        reference=reference_contract['by_query'][qid]
        expected=dict(question_sha256=sha(question),result_sha256=sha(result),required_fact_ids=sorted(required_ids[qid]))
        if (any(review.get(k)!=v for k,v in expected.items()) or row.get('question_sha256')!=sha(question)
                or review.get('reviewed_by')!='main Codex' or not review.get('observation')
                or review.get('verified_required_fact_coverage_complete') is not True):
            raise ValueError('Diagnostic source review is incomplete or stale: '+qid)
        if (row.get('reference_identity')!=reference['source_identity']
                or row.get('reference_adjudication_sha256')!=reference['source_record_sha256']
                or review.get('reference_contract_sha256')!=sha(reference_contract)):
            raise ValueError('Diagnostic input differs from its fixed SEQ source reference')
        evidence=result['evidence'];proofs=row['source_proofs']
        ids=[e['chunk_id'] for e in evidence]
        if len(ids)!=len(set(ids)) or set(ids)!=set(proofs) or not ids:
            raise ValueError('Diagnostic evidence lacks exact excerpt proof')
        cards={c['card_key'] for c in result['cards']}
        if cards!={e['card_key'] for e in evidence} or cards!=set(reference['card_keys']):
            raise ValueError('Diagnostic card ownership differs from evidence')
        for item in evidence:
            proof=proofs[item['chunk_id']]
            if proof.get('card_key')!=item['card_key'] or proof.get('excerpt_sha256')!=sha(item['text']):
                raise ValueError('Diagnostic excerpt ownership/content changed')
            spans=proof['source_spans']
            if not spans or item['text']!='\n\n'.join(s['quote'] for s in spans):
                raise ValueError('Diagnostic text includes non-source annotations')
            for source in spans:
                if source['path']!='data/raw/'+item['card_key']+'.pdf':
                    raise ValueError('Diagnostic excerpt belongs to another card')
                validate_source(source)


def diagnostic_reference(rows,facts,sha):
    """Oracle-source diagnostic only: prefer source-verifiable SEQ references."""
    candidates=[]
    for rank,config in enumerate(['card_page_section_benefit:SEQ','parent_child_bundle:SEQ']):
        matches=[r for r in rows if r['config_id']==config]
        if len(matches)!=1:raise ValueError('Missing/duplicate historical SEQ source reference')
        row=matches[0];required=row['required_fact_ids']
        uncertain=[fid for fid in required if facts[fid]['source_status']!='verified']
        candidates.append((len(uncertain),rank,dict(source_config_id=config,source_record_sha256=sha(row),
            source_identity=row['identity'],card_keys=sorted({facts[fid]['card_key'] for fid in required}),
            required_fact_ids=required,uncertain_fact_ids=uncertain)))
    return min(candidates,key=lambda x:x[:2])[2]
