"""Offline, source-bound regrading and paired summaries; never calls an API.

The notebook owns interpretation. This small reusable scorer validates saved
manual judgments and supports resumable review without changing sealed52/62.
"""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'notebooks/data/63_core_evaluation_alignment'


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def path_supported(path, *, answer=False):
    requirements = path['requirements']
    if not requirements:
        return False
    return all(r['source_status'] == 'verified' and r['input_status'] == 'supported'
               and (not answer or r['answer_status'] == 'supported') for r in requirements)


def score(row):
    """All requirements within a permitted path; never mix different cards."""
    if row['group'] not in ('explicit', 'general', 'uncertain') or any(
            type(row[k]) is not bool for k in ('core_grounded_success', 'legacy_pass', 'false_certainty', 'scope_violation')):
        raise ValueError('Unreviewed group or nonboolean judgment')
    paths = row['core_paths']
    if row['group'] == 'uncertain':
        evidence = None
        core = row.get('appropriate_abstention') is True and not row['false_certainty']
    else:
        evidence = any(path_supported(p) for p in paths)
        core = any(path_supported(p, answer=True) for p in paths)
    direct = bool(core)
    guards = row.get('core_assertion_guard_conditions', [])
    guard = all(r['source_status'] == 'verified' and r['input_status'] == 'supported'
                and r['answer_status'] == 'supported' for r in guards)
    if row.get('core_assertion_guard_complete', True) != guard:
        raise ValueError('Assertion guard disagrees with its conditions')
    core = core and guard and not row['scope_violation'] and not row['critical_error_ids']
    if row['core_evidence_complete'] != evidence or row['core_grounded_success'] != core:
        raise ValueError('Manual verdict disagrees with its condition judgments: ' + row['identity'])
    extended = row['extended_conditions']
    keys = [(c['card_key'], c['condition_id']) for c in extended]
    if len(set(keys)) != len(keys):
        raise ValueError('Duplicate extended condition')
    verified = [c for c in extended if c['source_status'] == 'verified']
    reflected = sum(c['input_status'] == 'supported' and c['answer_status'] == 'supported'
                    for c in verified)
    return {k: row[k] for k in ('dataset', 'identity', 'query_id', 'profile', 'mode', 'repeat', 'group')} | dict(
        core_evidence_complete=evidence, core_direct_supported=direct,
        core_assertion_guard_complete=guard, core_grounded_success=bool(core),
        legacy_pass=row['legacy_pass'], extended_reflected=reflected,
        extended_total=len(verified), extended_uncertain=len(extended)-len(verified),
        extended_completeness=reflected/len(verified) if verified else None,
        critical_errors=len(row['critical_error_ids']), false_certainty=row['false_certainty'],
        scope_violation=row['scope_violation'])


def aggregate(rows):
    groups = defaultdict(list)
    for row in rows:
        for group in (row['group'], 'all'):
            groups[row['dataset'], row['profile'], row['mode'], group].append(row)
    result = []
    for (dataset, profile, mode, group), values in sorted(groups.items()):
        qrows = defaultdict(list)
        for row in values:
            qrows[row['query_id']].append(row)
        item = dict(dataset=dataset, profile=profile, mode=mode, group=group,
                    questions=len(qrows), logical_rows=len(values))
        for metric in ('core_evidence_complete', 'core_direct_supported', 'core_assertion_guard_complete',
                       'core_grounded_success', 'legacy_pass', 'extended_completeness'):
            per_question = [statistics.mean(float(r[metric]) for r in items if r[metric] is not None)
                            for items in qrows.values() if any(r[metric] is not None for r in items)]
            item[metric] = statistics.mean(per_question) if per_question else None
            item[metric+'_question_denominator'] = len(per_question)
        item['extended_applicable_rows'] = sum(r['extended_total'] > 0 for r in values)
        item['critical_error_rows'] = sum(r['critical_errors'] > 0 for r in values)
        item['false_certainty_rows'] = sum(r['false_certainty'] for r in values)
        item['scope_violation_rows'] = sum(r['scope_violation'] for r in values)
        result.append(item)
    return result


def validate_groups(rows, expected):
    for row in rows:
        if row['group'] != expected.get(row['query_id']):
            raise ValueError('Changed frozen question group: '+row['query_id'])


def paired(rows, baseline, candidate, *, metric, samples=10000, seed=65):
    """Query-block paired bootstrap: all repeats stay inside each query."""
    import numpy as np
    grouped = defaultdict(dict)
    for row in rows:
        if row['mode'] in (baseline, candidate) and row[metric] is not None:
            grouped[row['query_id']].setdefault(row['mode'], []).append(float(row[metric]))
    if any(set(v) != {baseline, candidate} or len(v[baseline]) != len(v[candidate]) for v in grouped.values()):
        raise ValueError('Unpaired question or repetitions')
    differences = np.asarray([statistics.mean(v[candidate])-statistics.mean(v[baseline])
                              for _, v in sorted(grouped.items())], dtype=float)
    if not len(differences):
        return dict(questions=0, mean_difference=None, ci95=None, wins=0, losses=0, ties=0)
    rng = np.random.default_rng(seed)
    means = differences[rng.integers(0, len(differences), size=(samples, len(differences)))].mean(axis=1)
    return dict(questions=len(differences), mean_difference=float(differences.mean()),
                ci95=[float(v) for v in np.quantile(means, [.025, .975])],
                wins=int((differences > 0).sum()), losses=int((differences < 0).sum()),
                ties=int((differences == 0).sum()), samples=samples, seed=seed,
                unit='paired questions; all repetitions retained in each block')


def validate_judgments(document, *, root=ROOT, validate_source=None):
    identities = set()
    for relative, expected in document['source_files_sha256'].items():
        p = (root/relative).resolve()
        if not p.is_relative_to(root.resolve()) or sha(p.read_bytes()) != expected:
            raise ValueError('Changed review source: '+relative)
    for row in document['records']:
        key = row['dataset'], row['identity']
        if key in identities:
            raise ValueError('Duplicate manual identity')
        identities.add(key)
        if not row.get('reason') or not row.get('source_review_path'):
            raise ValueError('Unreviewed manual verdict')
        review_path = (root/row['source_review_path']).resolve()
        if not review_path.is_relative_to(root.resolve()) or not review_path.is_file():
            raise ValueError('Missing per-question source review')
        for path in row['core_paths']:
            if not path['card_keys'] or len(path['card_keys']) != len(set(path['card_keys'])):
                raise ValueError('Invalid core path card ownership')
            for condition in path['requirements']:
                validate_condition(condition, validate_source)
        for condition in row['extended_conditions']:
            validate_condition(condition, validate_source)
        for condition in row.get('core_assertion_guard_conditions', []):
            validate_condition(condition, validate_source)
        score(row)
    counts = {dataset: sum(d == dataset for d, _ in identities) for dataset in ('52', '62')}
    if counts != {'52': 160, '62': 720}:
        raise ValueError('Approved regrading matrix differs: '+str(counts))
    return counts


def validate_condition(condition, validate_source):
    if condition['source_status'] not in ('verified', 'uncertain'):
        raise ValueError('Unknown source status')
    states = ('supported', 'absent', 'ambiguous', 'contradicted', 'unknown', 'not_applicable')
    if condition['input_status'] not in states or condition['answer_status'] not in states:
        raise ValueError('Unknown condition status')
    if not condition.get('reason') or not condition.get('condition_id'):
        raise ValueError('Unreviewed condition')
    if condition['source_status'] == 'verified':
        if not condition['source_support']:
            raise ValueError('Verified fact lacks source support')
        if validate_source:
            for proof in condition['source_support']:
                validate_source(proof)
    for phase in ('input', 'answer'):
        if condition[phase+'_status'] == 'supported' and not condition[phase+'_quotes']:
            raise ValueError('Supported condition lacks exact quotations')


def validate_bound_condition(condition, card_key, input_by_card, answer_by_card, pdf_sha_by_card):
    """Hash/exact-quote ownership is evidence binding, not a semantic judge."""
    compact = lambda text: ''.join(text.casefold().split())
    if card_key not in pdf_sha_by_card or any(p['sha256'] != pdf_sha_by_card[card_key]
                                             for p in condition['source_support']):
        raise ValueError('Condition source ownership mismatch: '+card_key)
    for phase, texts in [('input', input_by_card), ('answer', answer_by_card)]:
        quotes = condition[phase+'_quotes']
        if condition[phase+'_status'] == 'supported' and not quotes:
            raise ValueError('Missing '+phase+' quotation')
        if any(not q.strip() or compact(q) not in compact(texts.get(card_key, '')) for q in quotes):
            raise ValueError('Wrong-card or absent '+phase+' quotation: '+card_key)


def audit_structured_funnel():
    """Reuse semantic audits; zero results alone never diagnose a DB defect."""
    base = ROOT/'notebooks/data/60_structured_retrieval_repair'
    read = lambda name: json.loads((base/name).read_text())
    plans = read('parser_results.json')['records']
    diagnostics = {(r['query_id'], r['repeat']): r for r in read('manual_plan_diagnostic_results.json')['records']}
    actual = {(r['query_id'], r['repeat']): r['routing'] for r in read('factorial_results.json')['records']
              if (r['db_version'], r['predicate'], r['scope_version']) == ('new', 'new', 'new')}
    semantic = {q['query_id']: {r['repeat']: r for r in json.loads((base/'query_review'/
                (q['query_id']+'.json')).read_text())['records']} for q in read('questions.json')['queries']}
    rows = []
    for item in plans:
        key = item['query_id'], item['repeat']
        parser, judgment, route = item['parser'], semantic[key[0]][key[1]], actual[key]
        plan = parser.get('plan') or {}
        flags = []
        if parser['status'] != 'ok':
            flags.append('parser_technical_failure')
        if judgment['verdict'] != 'PASS':
            flags.append('extraction_semantic_issue')
        if plan.get('unsupported_filters'):
            flags.append('constraint_not_representable_by_fixed_schema')
        if not plan.get('groups'):
            flags.append('no_structured_conditions')
        if item['scopes']['new']['clarify']:
            flags.append('original_scope_needs_confirmation')
        gold_route = diagnostics[key]['routing']
        if not route['eligible_cards'] and gold_route['eligible_cards']:
            flags.append('manual_diagnostic_plan_finds_candidates')
        if not route['eligible_cards'] and not flags:
            flags.append('zero_result_cause_requires_source_review')
        rows.append(dict(query_id=key[0], repeat=key[1], semantic_verdict=judgment['verdict'],
            semantic_reason=judgment['reason'], semantic_issues=judgment['issues'],
            eligible_cards=route['eligible_cards'], route=route['route'], reason=route['reason'],
            unsupported_filters=plan.get('unsupported_filters', []), flags=flags,
            diagnostic_eligible_cards=gold_route['eligible_cards']))
    from collections import Counter
    result = dict(records=rows, semantic_verdicts=dict(Counter(r['semantic_verdict'] for r in rows)),
        flags=dict(Counter(f for r in rows for f in r['flags'])),
        positive_routes=sum(bool(r['eligible_cards']) for r in rows), logical_plans=len(rows), paid_api_requests=0,
        limitations=['Flags overlap; diagnostic manual plans never drive production/model input.',
                     'A zero result does not prove an extraction, normalization or DB error.',
                     'Source-confirmed normalization71 is not an accuracy audit of all3403 facts.'])
    write(BASE/'structured_funnel.json', result)
    return result


def main():
    import importlib.util
    import time
    import resource
    started, cpu = time.perf_counter(), time.process_time()
    p = Path(__file__).with_name('55_fact_evaluation.py')
    spec = importlib.util.spec_from_file_location('source63', p)
    source = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source)
    document = json.loads((BASE/'manual_judgments.json').read_text())
    funnel = audit_structured_funnel()
    validated = set()
    def proof(value):
        identity = sha(value)
        if identity not in validated:
            source.validate_source_support(value, ROOT)
            validated.add(identity)
    counts = validate_judgments(document, validate_source=proof)
    fixed_groups = json.loads((ROOT/'notebooks/data/61_retrieval_evidence_comparison/source_judgments.json').read_text())['group_by_query']
    validate_groups([r for r in document['records'] if r['dataset'] == '62'], fixed_groups)
    files = {}
    def saved(relative):
        path = (ROOT/relative).resolve()
        if not path.is_relative_to(ROOT):
            raise ValueError('Review path escapes repository')
        if relative not in files:
            raw = path.read_bytes()
            files[relative] = (sha(raw), json.loads(raw))
        return files[relative]
    def find(container, identity, names):
        if container.get('identity') == identity:
            return container
        for name in names:
            found = [r for r in container.get(name, []) if r.get('identity') == identity]
            if found:
                if len(found) != 1:
                    raise ValueError('Duplicate bound row')
                return found[0]
        raise ValueError('Missing bound identity: '+identity)
    old52 = saved('notebooks/data/52_corrected_four_retrieval_modes/question_source_judgments.json')[1]['records']
    old62 = saved('notebooks/data/62_fixed_prompt_answer_check/evaluation.json')[1]['records']
    legacy = {('52', r['identity']): r['question_verdict'] == 'PASS'
              for r in old52 if r['mode'] in ('U', 'SEQ')}
    legacy.update({('62', r['identity']): r['passed'] for r in old62})
    if set(legacy) != {(r['dataset'], r['identity']) for r in document['records']}:
        raise ValueError('Regrading identities differ from accepted52/62 matrix')
    docs = [json.loads(line) for line in (ROOT/'notebooks/data/60_structured_retrieval_repair/db_v3/documents.jsonl').read_text().splitlines()]
    pdf_hashes = {r['document_id']: r['source_pdf_sha256'] for r in docs}
    for row in document['records']:
        actual_sha, container = saved(row['answer_path'])
        if actual_sha != row['answer_raw_sha256']:
            raise ValueError('Changed raw answer file')
        answer = find(container, row['identity'], ('records', 'answers'))
        if row.get('answer_row_sha256') != sha(answer) or row['legacy_pass'] != legacy[row['dataset'], row['identity']]:
            raise ValueError('Changed answer row or historical PASS')
        job = find(saved(row['job_path'])[1], row['identity'], ('records', 'retrievals'))
        if any(row[k] != job[k] for k in ('identity', 'query_id', 'profile', 'mode', 'repeat')):
            raise ValueError('Changed query/profile/mode/repeat')
        if answer.get('status') != 'ok' and row['core_grounded_success']:
            raise ValueError('Technical failure cannot be a core success')
        input_by_card = defaultdict(str)
        for item in job['result']['evidence']:
            input_by_card[item['card_key']] += '\n'+item['text']
        answer_texts = source.answer_by_card(answer.get('answer') or {},
            {c['card_key'] for c in job['result']['cards']}, row.get('answer_prose_spans', []))
        for path in row['core_paths']:
            for c in path['requirements']:
                owner = c.get('card_key') or (path['card_keys'][0] if len(path['card_keys']) == 1 else None)
                if owner not in path['card_keys']:
                    raise ValueError('Comparison condition lacks owning card')
                validate_bound_condition(c, owner, input_by_card, answer_texts, pdf_hashes)
        for c in row['extended_conditions']:
            validate_bound_condition(c, c['card_key'], input_by_card, answer_texts, pdf_hashes)
        for c in row.get('core_assertion_guard_conditions', []):
            validate_bound_condition(c, c['card_key'], input_by_card, answer_texts, pdf_hashes)
    rows = [score(r) for r in document['records']]
    output = dict(status='source_bound_retrospective_regrading', records=rows, summary=aggregate(rows),
                  manual_judgments_raw_sha256=sha((BASE/'manual_judgments.json').read_bytes()),
                  source_counts=counts, source_proofs=len(validated), paid_api_requests=0,
                  structured_funnel_counts={k: v for k, v in funnel.items() if k != 'records'},
                  bound_files_sha256={p: value[0] for p, value in files.items()},
                  offline_resources=dict(wall_seconds=time.perf_counter()-started,
                      cpu_seconds=time.process_time()-cpu,
                      peak_process_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                      scope='offline scorer including source/row validation; notebook startup excluded'),
                  limitations=document['limitations'])
    write(BASE/'evaluation.json', output)
    print(json.dumps(dict(rows=len(rows), counts=counts, source_proofs=len(validated), paid_api_requests=0)))


if __name__ == '__main__':
    main()
