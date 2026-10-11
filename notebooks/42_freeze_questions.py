"""Author expanded questions independently of evaluated retrieval and answers.

Labels are conservative source-relative OCR annotations, not PDF verification.
Public questions contain no gold plan/cards/witnesses and are the sole runner
input. Templates, partial source labels and shared corpus limit generalization.
"""
import importlib.util
from pathlib import Path
import sys
from collections import Counter, defaultdict

spec = importlib.util.spec_from_file_location('freeze42_runtime', Path(__file__).with_name('41_followup_runtime.py'))
R = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = R
spec.loader.exec_module(R)
B = R.load('freeze42_annotations', '39_freeze_new_questions.py')

def plan(groups=None, card=None, unresolved=None):
    return {'route': 'structured_then_unstructured' if groups else 'unstructured', 'groups': groups or [],
            'card_keys': [card] if card else [], 'unresolved': unresolved or []}

def specifications(catalog):
    req = B.req
    numeric = [
        ('GS25에서 10% 이상 할인받는 카드를 찾고 있어', [[req('GS25', 'discount', 10)]]),
        ('CU 결제액을 7% 이상 적립해주는 카드가 있을까', [[req('CU', 'earn', 7)]]),
        ('디즈니플러스 구독료에 5% 이상 할인이 가능한 카드', [[req('디즈니', 'discount', 5)]]),
        ('멜론 정기결제에 최소 7% 할인되는 카드 추천', [[req('멜론', 'discount', 7)]]),
        ('네이버쇼핑에서 5% 이상 할인받고 싶어', [[req('네이버쇼핑', 'discount', 5)]]),
        ('백화점 결제에 5% 이상 할인 혜택을 주는 카드', [[req('백화점', 'discount', 5)]]),
        ('병원비가 10% 이상 할인되는 카드 후보를 보여줘', [[req('병원', 'discount', 10)]]),
        ('약국에서 5% 이상 할인되는 카드가 필요해', [[req('약국', 'discount', 5)]]),
        ('다이소에서 결제하면 7% 이상 적립되는 카드', [[req('다이소', 'earn', 7)]]),
        ('SSG.COM 온라인 결제에 10% 이상 할인되는 카드', [[req('SSG.COM', 'discount', 10)]]),
        ('교보문고에서 책을 살 때 5% 이상 할인되는 카드', [[req('교보문고', 'discount', 5)]]),
        ('전월 실적 기준이 30만원 이하이고 커피를 10% 이상 할인해주는 카드', [[req('커피', 'discount', 10, spend=300000)]]),
        ('커피 10% 이상 할인과 월 할인한도 5천원 이상을 함께 만족하는 카드', [[req('커피', 'discount', 10, cap=5000, period='month')]]),
        ('GS25와 넷플릭스를 각각 10% 이상 할인해주는 카드 한 장', [[req('GS25', 'discount', 10), req('넷플릭스', 'discount', 10)]]),
        ('병원과 약국에서 모두 5% 이상 할인되는 카드를 찾아줘', [[req('병원', 'discount', 5), req('약국', 'discount', 5)]]),
        ('멜론 또는 네이버쇼핑 중 한 곳에서 7% 이상 할인되는 카드', [[req('멜론', 'discount', 7)], [req('네이버쇼핑', 'discount', 7)]])]
    details = [
        ('NH/NH_AllWonderful', '백화점', 'discount', 5),
        ('NH/NH_Namu_NH', '쿠팡', 'earn', 8),
        ('hana/Hana_Everyones_Shinsegae', '신세계', 'discount', 5),
        ('hana/Hana_JadeClassic', '국내외 가맹점', 'earn', 1),
        ('ibk/IBK_Bliss5', 'KTX', 'discount', 10),
        ('BC/BC_Shopping&', '다이소', 'discount', 7),
        ('kookmin/Kookmin_Coupang_Wow_20250702', '쿠팡', 'earn', 2),
        ('kookmin/Kookmin_Friend_20210917', '에버랜드', 'discount', 50),
        ('lotte/Lotte_Digiloca_LAS_VEGAS', '국내외 가맹점', 'discount', .5),
        ('samsung/Samsung_5_V4', '학원', 'discount', 5),
        ('samsung/Samsung_Toss_taptap_S', '국내외 가맹점', 'earn', 1),
        ('shinhan/Shinhan_Cheoeum_20250524', '음식점', 'earn', 5),
        ('shinhan/Shinhan_Deep_Oil_20231204', '주유소', 'discount', 10),
        ('woori/Woori_7CORE', '쿠팡', 'discount', 10),
        ('woori/Woori_Classic2', '국내외 가맹점', 'discount', 1.2),
        ('hyundai/Hyundai_D_250827', '음식점', 'discount', 10)]
    semantic = [
        ('커피를 자주 사 마셔. 관련 카드 혜택을 추천해줘', plan([[req('커피')]]), 'recommend'),
        ('카페에서 포인트가 쌓이는 카드를 알아보고 싶어', plan([[req('커피', 'earn')]]), 'recommend'),
        ('국내외 여행에 관련된 혜택을 가진 카드를 살펴보고 싶어', plan([[req('여행')]]), 'recommend'),
        ('여행 갈 때 호텔 결제를 할인받는 카드가 궁금해', plan([[req('호텔', 'discount')]]), 'recommend'),
        ('온라인 쇼핑할 때 쓸 수 있는 카드 혜택을 알려줘', plan([[req('온라인')]]), 'recommend'),
        ('배달 주문을 자주 해서 적립 혜택이 있는 카드가 필요해', plan([[req('배달', 'earn')]]), 'recommend'),
        ('영화 보러 갈 때 할인되는 카드를 찾아보고 싶어', plan([[req('영화', 'discount')]]), 'recommend'),
        ('차에 주유할 때 할인 혜택을 받을 수 있는 카드', plan([[req('주유', 'discount')]]), 'recommend'),
        ('통신요금을 내면서 적립받을 수 있는 카드가 궁금해', plan([[req('통신', 'earn')]]), 'recommend'),
        ('병원과 약국에서 할인 혜택을 주는 카드 한 장을 추천해줘', plan([[req('병원', 'discount'), req('약국', 'discount')]]), 'recommend'),
        ('해외에서 결제하면 적립 혜택이 있는 카드를 알아봐줘', plan([[req('해외', 'earn')]]), 'recommend'),
        ('대형마트에서 장볼 때 받을 수 있는 카드 혜택', plan([[req('대형마트')]]), 'recommend'),
        ('카드를 처음 만들 건데 할인과 적립 중 어떤 기준으로 비교해야 할까', plan(), 'advice'),
        ('한 달 생활비가 40만원이면 무조건 가장 이득인 카드 하나를 확정해줘', plan(unresolved=['소비 예산을 전월실적으로 변환하거나 최적 카드를 확정할 수 없음']), 'advice'),
        ('존재하지않는평가상점ABC에서 할인되는 카드가 있는지 확인해줘', plan([[req('존재하지않는평가상점ABC', 'discount')]]), 'no_source_positive'),
        ('호텔 결제에 499% 할인이라는 혜택을 받을 수 있는지 근거를 찾아줘', plan([[req('호텔', 'discount', 499)]]), 'no_source_positive')]
    rows = [(question, plan(groups), 'numeric', 'recommend') for question, groups in numeric]
    for card, target, action, rate in details:
        word = '할인' if action == 'discount' else '적립'
        rows.append((f"{catalog[card]}의 {target} {word} 혜택과 적용 조건을 근거로 설명해줘",
                     plan([[req(target, action, rate)]], card), 'proper_noun', 'detail'))
    rows.extend((question, value, 'semantic', task) for question, value, task in semantic)
    assert len(rows) == 48
    return rows

def build_dataset(prepare=False):
    facts = [__import__('json').loads(line) for line in (R.DATA / '30_structured_benefit_db/facts.jsonl').read_text().splitlines()]
    documents = {f['document_id']: None for f in facts}
    for fact in facts:
        if documents[fact['document_id']] is None:
            documents[fact['document_id']] = R.read(R.ROOT / fact['source_relative_path'])
    catalog = {card: document['identity']['card_name'] for card, document in documents.items()}
    old_questions = {q['question'] for q in R.read(R.DATA / '32_structured_search_comparison/dataset_freeze.json')['queries']}
    old_questions.update(q['question'] for q in R.read(R.DATA / '39_answer_rubric/dataset.json')['queries'])
    by_card = defaultdict(list)
    for fact in facts:
        by_card[fact['document_id']].append(fact)
    audits, exclusions = {}, []
    def verified_match(fact, requirement):
        if not B.matches(fact, requirement):
            return False
        fid = fact['fact_id']
        if fid not in audits:
            audits[fid] = B.raw_audit(fact, documents)
        passed = B.required_fragments_pass(requirement, audits[fid])
        if not passed:
            exclusions.append({'fact_id': fid, 'requirement': requirement, 'reason': 'Required source fragments unavailable or ambiguous'})
        return passed
    queries = []
    for i, (question, gold_plan, group, task) in enumerate(specifications(catalog), 1):
        assert question not in old_questions
        gold, witnesses = [], {}
        for card, rows in by_card.items():
            if gold_plan['card_keys'] and card not in gold_plan['card_keys']:
                continue
            matched = []
            for requirements in gold_plan['groups']:
                choices = [[f['fact_id'] for f in rows if verified_match(f, requirement)] for requirement in requirements]
                if all(choices):
                    matched.append(choices)
            if matched:
                gold.append(card)
                witnesses[card] = matched
        # Freeze a small, auditable partial anchor set before answer execution.
        # This is known-positive anchor coverage, not exhaustive card relevance.
        selected = []
        issuers = set()
        for card in sorted(gold):
            issuer = card.split('/')[0]
            if issuer not in issuers:
                selected.append(card)
                issuers.add(issuer)
            if len(selected) == 3:
                break
        for card in sorted(gold):
            if len(selected) == 3:
                break
            if card not in selected:
                selected.append(card)
        witnesses = {card: [[[ids[0]] for ids in choices] for choices in witnesses[card][:1]] for card in selected}
        category_shared = any(t in ['여행', '커피', '카페'] for requirements in gold_plan['groups'] for r in requirements for t in r['targets'])
        behaviors = {45: 'general_advice_without_unsupported_benefit_claims',
                     46: 'acknowledge_insufficient_personal_information_no_unconditional_optimal_card',
                     47: 'source_unconfirmed_merchant_no_positive_benefit_assertion',
                     48: 'reject_or_state_no_evidence_for_499_percent_no_positive_499_claim'}
        queries.append({'query_id': f'E{i:02}', 'question': question, 'gold_plan': gold_plan, 'subgroup': group,
                        'task': task, 'gold_cards': sorted(selected), 'witness_groups': witnesses,
                        'source_match_candidate_cards': len(gold), 'shared_category_taxonomy': category_shared,
                        'expected_behavior': behaviors.get(i),
                        'answer_checks': [], 'calculations': [], 'split': 'expanded_source_relative_confirmatory',
                        'negative_control': False})
    missing = [q['query_id'] for q in queries[:44] if not q['gold_cards']]
    if missing:
        raise RuntimeError('Pre-answer source annotations lack positive evidence: ' + repr(missing))
    assert all(not q['gold_cards'] for q in queries[44:])
    path = R.STAGES[42]
    detail_distribution = dict(Counter(q['gold_plan']['card_keys'][0].split('/')[0] for q in queries if q['task'] == 'detail'))
    assert len(detail_distribution) == 10
    queued_ids = {fid for q in queries for groups in q['witness_groups'].values() for group in groups for ids in group for fid in ids}
    queue = []
    for fid in sorted(queued_ids):
        fact = next(f for f in facts if f['fact_id'] == fid)
        pages = {p['page']: p['text'] for p in documents[fact['document_id']]['pages']}
        page_ids = sorted({span['page'] for span in audits[fid]['spans']})
        queue.append({**audits[fid], 'question_ids': [q['query_id'] for q in queries if fid in
            {f for groups in q['witness_groups'].values() for group in groups for ids in group for f in ids}],
            'source_pages': {str(page): pages[page] for page in page_ids}})
    draft = {'queries': queries, 'catalog': catalog, 'gold_positive_queries': 44,
             'selection': 'first up-to3 source-matched cards across distinct issuers, then remaining sorted cards; first verified fact per requirement; partial anchors, not exhaustive relevance',
             'detail_issuer_counts': detail_distribution}
    if prepare:
        R.freeze(path / 'prepared_dataset.json', draft)
        R.freeze(path / 'source_review_queue.json', {'records': queue, 'no_answers_used': True})
        print('Prepared48 questions for source relation review;unique anchor facts', len(queue), flush=True)
        return draft
    review = R.read(path / 'source_relation_review.json')
    if review['queue_sha256'] != R.read(path / 'source_review_queue.json')['sha256']:
        raise RuntimeError('Source review queue binding mismatch')
    accepted = {row['fact_id'] for row in review['records'] if row['decision'] == 'accepted_same_benefit_context'}
    if {row['fact_id'] for row in review['records']} != queued_ids:
        raise RuntimeError('Every proposed anchor needs an explicit source relation review')
    rejected = sorted(queued_ids - accepted)
    for q in queries:
        reviewed = {}
        for card, groups in q['witness_groups'].items():
            keep = []
            for group in groups:
                choices = [[fid for fid in ids if fid in accepted] for ids in group]
                if all(choices):
                    keep.append(choices)
            if keep:
                reviewed[card] = keep
        q['witness_groups'] = reviewed
        q['gold_cards'] = sorted(reviewed)
    missing = [q['query_id'] for q in queries[:44] if not q['gold_cards']]
    if missing:
        raise RuntimeError('Source review leaves no positive anchor before answers: ' + repr(missing))
    R.freeze(path / 'source_review_exclusions.json', {'rejected_or_uncertain_fact_ids': rejected,
            'review_sha256': R.digest((path / 'source_relation_review.json').read_bytes()),
            'prepared_anchors_retained': len(accepted), 'prepared_anchors_excluded': len(rejected)})
    contract = R.freeze(path / 'protocol.json', {'stage': 42, 'questions': 48, 'positive_questions': 44,
        'profiles': ['card_page_section_benefit', 'parent_child_bundle'], 'modes': ['U', 'S_THEN_U', 'S_PLUS_U', 'Q_EXPAND_U'],
        'expected_rows': 384, 'same_query_repeated_generations': 0, 'independent_holdout': False,
        'primary': 'known_positive_complete; positive44 denominator; failures0',
        'selection': 'Q_EXPAND_U vs same-profile U wins>losses plus guards; candidate change vs benefit S_THEN_U net>=2/44; net0~1 retain baseline; subtype loss>=2 or confirmed guard regression blocks selection',
        'statistics': 'Wilson95% individual interval; exact two-sided paired McNemar descriptive; multiple comparisons/shared cards are correlated',
        'sampling': '16 numeric,16 named-card detail across10 issuers,12 semantic positive,4 advice/ambiguous/source-unconfirmed diagnostics; source-reviewed partial anchors authored before evaluated retrieval/answers',
        'shared_category_taxonomy': 'coffee/travel5 questions reported separately; shared lexical taxonomy is not independent validation of expansion meaning',
        'diagnostic_behavior': 'all4 expected_behavior rules source-reviewed for every configuration; no ungrounded definite benefit/optimality claims; no guard regression allowed',
        'source_relation_review_sha256': R.digest((path / 'source_relation_review.json').read_bytes()),
        'scorer_sha256': R.digest((R.ROOT / 'notebooks/39_answer_rubric.py').read_bytes()),
        'sources': {str(p.relative_to(R.ROOT)): R.digest(p.read_bytes()) for p in [Path(__file__),
            R.ROOT / 'notebooks/41_query_expansion.py', R.DATA / '30_structured_benefit_db/facts.jsonl']},
        'numeric_normalization_and_alias_rubric_changes': 0, 'production_adopted': False})
    runtime_catalog = R.freeze(path / 'catalog.json', {'catalog': catalog, 'protocol_sha256': contract['sha256']})
    public = R.freeze(path / 'questions.json', {'queries': [{'query_id': q['query_id'], 'question': q['question']} for q in queries],
                        'catalog_sha256': runtime_catalog['sha256'], 'protocol_sha256': contract['sha256']})
    dataset = R.freeze(path / 'dataset.json', {'queries': queries, 'catalog': catalog, 'questions_sha256': public['sha256'],
        'protocol_sha256': contract['sha256'], 'gold_positive_queries': 44,
        'source_sha256': {name: R.digest((R.DATA / '30_structured_benefit_db' / (name + '.jsonl')).read_bytes()) for name in ['facts', 'documents']},
        'subgroup_counts': dict(Counter(q['subgroup'] for q in queries)),
        'detail_issuer_counts': detail_distribution,
        'partial_anchor_selection': draft['selection'],
        'limits': ['Shared source corpus, partial reviewed OCR-source anchor labels, repeated templates and overlapping cards; valid alternative cards outside anchors may be scored as misses; no population-generalization or repeated-call variance claim']})
    R.freeze(path / 'source_audit.json', {'records': list(audits.values()), 'excluded_requirements': exclusions,
                                        'dataset_sha256': dataset['sha256']})
    R.freeze(path / 'freeze_receipt.json', {'dataset_sha256': dataset['sha256'], 'questions_sha256': public['sha256'],
        'protocol_sha256': contract['sha256'], 'paid_api_files_at_freeze': len(list((path / 'api_cache').glob('*.json')))})
    print('48 questions frozen;44 positive;4 diagnostics;source audits', len(audits), flush=True)
    return dataset

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare', action='store_true')
    args = parser.parse_args()
    build_dataset(prepare=args.prepare)
