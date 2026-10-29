"""Resume bounded 73 card-only answers; reports and analysis stay in notebook."""
import argparse
import copy
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
import resource
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'notebooks/data/73_repaired_card_scope'
OLD = ROOT / 'notebooks/data/70_cap_context_repair'
EVIDENCE = ROOT / 'notebooks/data/71_pre_generation_evidence_audit'
QUERIES = ('C01', 'C02', 'C03', 'C05', 'C09', 'C10', 'C11', 'C12', 'C13', 'C14', 'C16', 'C18')
PROFILES = ('card_page_section_benefit', 'parent_child_bundle')
REQUIRED_GATE_FILES = frozenset({
    'notebooks/data/73_repaired_card_scope/jobs.json',
    'notebooks/data/73_repaired_card_scope/answer_payload_contract.json',
    'notebooks/data/73_repaired_card_scope/cost_reservation.json',
    'notebooks/data/73_repaired_card_scope/baseline_validation.json',
    'notebooks/data/73_repaired_card_scope/execution_manifest.json',
    'notebooks/data/73_repaired_card_scope/pre_paid_preflight.json',
    'notebooks/data/73_repaired_card_scope/additional_source_judgments.json',
    'notebooks/data/73_repaired_card_scope/query_gold.json',
    'notebooks/data/73_repaired_card_scope/ranking_results.json',
    'notebooks/data/73_repaired_card_scope/ranking_summary.json',
    'notebooks/data/73_repaired_card_scope/source_verification.json',
    'notebooks/data/73_repaired_card_scope/db_difference.json',
    'notebooks/data/73_repaired_card_scope/routing_diagnostics.json',
    'notebooks/73_repaired_card_scope.ipynb',
    'notebooks/notes/73_repaired_card_scope_plan.md',
    'notebooks/scripts/73_answer_runtime.py',
    'notebooks/scripts/73_card_ranking.py',
    'notebooks/scripts/73_test_runtime.py',
    'notebooks/scripts/73_test_card_ranking.py',
})


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


A = load('answer73_runtime', ROOT / 'notebooks/scripts/68_69_answer_runtime.py')
T = A.T
for stage, leaf in [(70, '70_cap_context_repair'), (72, '72_input_presentation'), (73, '73_repaired_card_scope')]:
    T.LEAVES[stage] = leaf
    T.R.P.STAGES[stage] = T.R.X.STAGES[stage] = leaf
    for registry in T.stage_registries():
        registry.STAGES[stage] = ROOT / 'notebooks/data' / leaf
    T.E.STAGE_FOLDERS[stage] = leaf


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_card_only(result):
    cards = [card['card_key'] for card in result['cards']]
    if len(cards) > 3 or len(cards) != len(set(cards)):
        raise ValueError('Card ranking must contain at most three unique cards')
    for evidence in result['evidence']:
        if evidence.get('origin') == 'structured' or str(evidence.get('chunk_id', '')).startswith('s68:'):
            raise ValueError('Structured suffix cannot enter the card-only answer')
        if evidence['card_key'] not in cards:
            raise ValueError('Evidence owner is outside selected cards')
    return True


def check_total_budget(ledger, maximum):
    if ledger['unknown_cost_records']:
        raise RuntimeError('A new unknown usage record prevents further calls')
    if Decimal(ledger['budget_debit_usd']) + Decimal(maximum) > Decimal('8'):
        raise RuntimeError('Maximum possible new requests exceed the cumulative $8 cap')


def validate_matrix(jobs):
    expected = {(q, p, r) for q in QUERIES for p in PROFILES for r in (1, 2)}
    coordinates = [(j['query_id'], j['profile'], j['repeat']) for j in jobs]
    if len(coordinates) != len(expected) or len(set(coordinates)) != len(expected) or set(coordinates) != expected:
        raise ValueError('Exact 12-question, two-profile, two-repeat matrix changed')
    questions = {q['query_id']: q['question'] for q in read(OLD / 'questions.json')['queries']}
    for job in jobs:
        q, p, r = job['query_id'], job['profile'], job['repeat']
        if (job['identity'] != f'{q}:{p}:SEQ_CARDS:r{r}' or job['mode'] != 'SEQ_CARDS'
                or job['question'] != questions[q]
                or job['prior_retrieval_identity'] != f'{q}:{p}:SEQ_REPAIRED:r{r}'):
            raise ValueError('Job metadata does not match the fixed matrix')
        validate_card_only(job['result'])
    return True


def require_gate(base=BASE):
    def no_duplicates(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Gate contains a duplicate JSON key')
            result[key] = value
        return result
    gate = json.loads((Path(base) / 'pre_paid_acceptance.json').read_text(), object_pairs_hook=no_duplicates)
    if gate.get('status') != 'accepted' or not gate.get('independent_review_accepted'):
        raise RuntimeError('73 pre-paid review gate not accepted')
    if not REQUIRED_GATE_FILES.issubset(gate.get('files_sha256', {})):
        raise ValueError('Gate omits one or more required input files')
    T.E.verify_file_map(gate['files_sha256'], ROOT, fresh=True)
    return gate


def prepare():
    """Reconstruct fixed card-only jobs and freeze bounded request contracts."""
    from pickcardu_rag.answering import _generated_answer_model
    BASE.mkdir(parents=True, exist_ok=True)
    if (BASE / 'cost_reservation.json').exists():
        previous = read(BASE / 'cost_reservation.json')
        if previous['logical_answers'] == 48:
            T.guard_manifest(read(BASE / 'execution_manifest.json'))
            validate_matrix(read(BASE / 'jobs.json')['records'])
            return previous
        if previous['logical_answers'] != 144 or not (BASE / 'preflight_v1/receipt.json').exists():
            raise RuntimeError('Previous preparation requires a verified preserved version before replacement')
    prior_manifest = read(OLD / 'execution_manifest.json')
    T.guard_manifest(prior_manifest)
    retrieval = read(OLD / 'retrieval_results.json')['records']
    old_jobs = {r['identity']: r for r in read(OLD / 'jobs.json')['records']}
    old_answers = {r['identity']: r for r in read(OLD / 'execution_results.json')['records']}
    old_contract = read(OLD / 'answer_payload_contract.json')
    prior_inputs = read(EVIDENCE / 'inputs.json')
    inputs = {r['input_id']: r for r in prior_inputs['records']}
    filters = {r['identity']: r for r in prior_inputs['bindings'] if r['arm'] == 'FILTER'}
    if len(retrieval) != 432 or len(filters) != 144:
        raise ValueError('Prior retrieval or independent FILTER bindings are incomplete')
    base_rows, repaired, seen = [], [], set()
    for row in retrieval:
        if row['query_id'] not in QUERIES or row['repeat'] not in (1, 2):
            continue
        identity = row['identity']
        if identity in seen:
            raise ValueError('Duplicate previous retrieval identity')
        seen.add(identity)
        if read(OLD / 'retrievals' / (identity + '.seal.json'))['row_sha256'] != T.R.P.sha(row):
            raise ValueError('Previous retrieval seal differs')
        validate_card_only(row['result'])
        T.R.G.T.S.validate_scope(row['result'], row['routing']['scope'])
        T.R.G.T.S.validate_scope(row['result'], row['original_scope']['card_keys'])
        if row['mode'] in ('U', 'SEQ65'):
            job, answer = old_jobs[identity], old_answers[identity]
            A.validate_answer(answer, job, prior_manifest, old_contract, OLD)
            base_rows.append(dict(identity=identity, job_sha256=T.R.P.sha(job), answer_sha256=T.R.P.sha(answer)))
        elif row['mode'] == 'SEQ_REPAIRED':
            binding = filters[identity]
            packet = inputs[binding['input_id']]
            if packet['cards'] != row['result']['cards'] or packet['evidence'] != row['result']['evidence']:
                raise ValueError('Prior FILTER does not match actual repaired retrieval')
            identity73 = identity.replace(':SEQ_REPAIRED:', ':SEQ_CARDS:')
            repaired.append(dict(identity=identity73, query_id=row['query_id'], profile=row['profile'],
                mode='SEQ_CARDS', repeat=row['repeat'], question=row['question'], result=copy.deepcopy(row['result']),
                prior_retrieval_identity=identity, prior_retrieval_sha256=T.R.P.sha(row),
                filter_input_id=binding['input_id'], filter_input_sha256=T.R.P.sha(packet)))
        else:
            raise ValueError('Unexpected prior search mode')
    if len(repaired) != 48 or len(base_rows) != 96:
        raise ValueError('12-question, two-profile, two-repeat matrix is incomplete')
    repaired.sort(key=lambda x: (x['repeat'], x['query_id'], x['profile']))
    validate_matrix(repaired)
    sample = next(x for x in old_answers.values() if ':U:' in x['identity'] and x['events'])
    prompts = []
    for event in sample['events']:
        prompts.append(read(OLD / 'api_cache' / (event['key'] + '.json'))['request']['instructions'])
    # The prior manifest binds the two operating prompts even when this sample used one request.
    hashes = prior_manifest['answer_instructions_sha256_by_identity'][sample['identity']]
    if len(prompts) == 1:
        for answer in old_answers.values():
            if ':U:' in answer['identity'] and len(answer['events']) == 2:
                candidate = [read(OLD / 'api_cache' / (e['key'] + '.json'))['request']['instructions'] for e in answer['events']]
                if [T.R.P.sha(p) for p in candidate] == hashes:
                    prompts = candidate
                    break
    if len(prompts) != 2 or [T.R.P.sha(p) for p in prompts] != hashes:
        raise ValueError('Could not bind both previous operating prompts')
    identities = [j['identity'] for j in repaired]
    source_paths = [
        'notebooks/scripts/73_answer_runtime.py', 'notebooks/scripts/73_card_ranking.py',
        'notebooks/notes/73_repaired_card_scope_plan.md',
        'notebooks/data/73_repaired_card_scope/additional_source_judgments.json',
    ]
    critical = [
        'notebooks/data/70_cap_context_repair/execution_manifest.json',
        'notebooks/data/70_cap_context_repair/retrieval_results.json',
        'notebooks/data/70_cap_context_repair/jobs.json',
        'notebooks/data/70_cap_context_repair/execution_results.json',
        'notebooks/data/70_cap_context_repair/answer_payload_contract.json',
        'notebooks/data/71_pre_generation_evidence_audit/inputs.json',
        'notebooks/data/71_pre_generation_evidence_audit/input_reviews.json',
        'notebooks/data/71_pre_generation_evidence_audit/question_contract.json',
        'notebooks/data/71_pre_generation_evidence_audit/input_review_seal.json',
    ]
    manifest = dict(stage=73, logical_answers=48, maximum_answer_requests=96,
        maximum_physical_requests=96, maximum_embedding_requests=0, maximum_parser_requests=0,
        model='gpt-6-luna', reasoning='medium', parameters=prior_manifest['parameters'],
        stage_cost_cap_usd='8', allowed_identities={'answer': identities},
        prompt_variant_by_identity={i: 'old' for i in identities},
        answer_instructions_sha256_by_identity={i: hashes for i in identities},
        source_sha256={p: digest(ROOT / p) for p in source_paths},
        critical_data_sha256={p: digest(ROOT / p) for p in critical},
        baseline='70 actual U and SEQ65 answer outputs, identical old operating prompt',
        candidate='70 actual SEQ_REPAIRED retrieval only, no structured evidence suffix',
        source_note='C18 Monthly and C19 UniMile known PDF/OCR conflicts retained; no original changes')
    contract = A.payload_contract(repaired, manifest)
    ledger = T.budget_ledger()
    reservations = []
    for job in repaired:
        schema = _generated_answer_model(len(job['result']['evidence'])).model_json_schema()
        payload = T.R.P.F.serialize_answer_payload(job['question'], job['result']['evidence']) if hasattr(T.R.P.F, 'serialize_answer_payload') else None
        if payload is None:
            from pickcardu_rag.answering import serialize_answer_payload
            payload = serialize_answer_payload(job['question'], job['result']['evidence'])
        wire = dict(model='gpt-6-luna', reasoning={'effort': 'medium'}, input=[dict(role='user', content=payload)],
                    instructions=max(prompts, key=len), schema=schema, max_output_tokens=2400, tools=[], store=False)
        upper_bytes = len(json.dumps(wire, ensure_ascii=False, separators=(',', ':')).encode()) + 8192
        upper = (Decimal(upper_bytes) * Decimal('.25' if upper_bytes > 272000 else '.125')
                 + Decimal(2400) * Decimal('.75' if upper_bytes > 272000 else '.50')) / Decimal(1000000)
        reservations.append(dict(identity=job['identity'], per_request_upper_usd=str(upper)))
    maximum = sum((Decimal(r['per_request_upper_usd']) * 2 for r in reservations), Decimal(0))
    check_total_budget(ledger, maximum)
    outputs = [('jobs.json', {'records': repaired}), ('baseline_validation.json', {'records': base_rows}),
               ('execution_manifest.json', manifest), ('answer_payload_contract.json', contract),
               ('cost_reservation.json', dict(starting_budget_debit_usd=ledger['budget_debit_usd'],
                   maximum_new_usd=str(maximum), maximum_cumulative_usd=str(Decimal(ledger['budget_debit_usd']) + maximum),
                   logical_answers=48, maximum_physical_requests=96, records=reservations,
                   method='Full serialized request UTF8 bytes + 8192 overhead, maximum output, two attempts; frozen historical rates'))]
    for name, value in outputs:
        if (BASE / name).exists():
            T.write(BASE / name, value)
        else:
            T.R.X.frozen(BASE / name, value)
    return outputs[-1][1]


def run(allow_network=False):
    gate = require_gate()
    manifest = read(BASE / 'execution_manifest.json')
    T.guard_manifest(manifest)
    jobs = read(BASE / 'jobs.json')['records']
    validate_matrix(jobs)
    if A.payload_contract(jobs, manifest) != read(BASE / 'answer_payload_contract.json'):
        raise ValueError('Answer contract differs from the frozen jobs and manifest')
    reservation = read(BASE / 'cost_reservation.json')
    if (reservation['logical_answers'] != 48 or reservation['maximum_physical_requests'] != 96
            or {r['identity'] for r in reservation['records']} != {j['identity'] for j in jobs}):
        raise ValueError('Reservation does not cover the exact answer matrix')
    start, cpu = time.perf_counter(), time.process_time()
    rows = []
    for job in jobs:
        require_gate()
        rows.append(A.generate(73, job, allow_network=allow_network))
        T.write(BASE / 'execution_results.json', dict(records=rows, expected=48, complete=len(rows) == 48))
        print(f"{len(rows)}/48 {job['identity']} {rows[-1]['status']}", flush=True)
    stamp = time.time_ns()
    (BASE / 'execution_segments').mkdir(exist_ok=True)
    T.write(BASE / 'execution_segments' / f'{stamp}.json', dict(jobs_seen=len(rows),
        wall_seconds=time.perf_counter() - start, cpu_seconds=time.process_time() - cpu,
        peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        note='Segment includes checkpoint replay and validation; not end-to-end production time'))
    return rows


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['prepare', 'run'])
    parser.add_argument('--allow-network', action='store_true')
    args = parser.parse_args()
    if args.command == 'prepare':
        print(json.dumps(prepare(), ensure_ascii=False, indent=2))
    else:
        run(allow_network=args.allow_network)
