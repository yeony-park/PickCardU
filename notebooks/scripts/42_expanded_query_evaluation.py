"""Run public expanded questions sequentially; attach gold labels only afterward."""
import importlib.util
from pathlib import Path
import sys
import time

spec = importlib.util.spec_from_file_location('expanded_runtime', Path(__file__).with_name('41_followup_runtime.py'))
R = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = R
spec.loader.exec_module(R)
X = R.load('expanded_query_rules', '41_query_expansion.py')
I = R.load('expanded_input_binding', '42_input_binding.py')

def validate_digest(value):
    if R.digest({k: v for k, v in value.items() if k != 'sha256'}) != value['sha256']:
        raise RuntimeError('Frozen artifact digest mismatch')

def evaluate_saved(runtime, public, protocol):
    # Gold-plan path is never an input to parsing, expansion, retrieval or answer.
    bound = I.verify_inputs(runtime.path)
    dataset = R.read(runtime.path / 'dataset.json')
    validate_digest(dataset)
    if dataset['sha256'] != bound['dataset_sha256']:
        raise RuntimeError('Scoring dataset changed after frozen input validation')
    if dataset['questions_sha256'] != public['sha256'] or dataset['protocol_sha256'] != protocol['sha256']:
        raise RuntimeError('Gold/public/protocol binding mismatch')
    if protocol['scorer_sha256'] != R.digest((R.ROOT / 'notebooks/scripts/39_answer_rubric.py').read_bytes()):
        raise RuntimeError('Frozen scorer changed')
    previous = R.load('expanded_retrieval_metric', '40_new_query_comparison.py')
    runtime.F.initialize(42)
    facts = {f['fact_id']: f for f in runtime.x.facts}
    retrieved, answered = [], []
    for label in dataset['queries']:
        case = {**label, 'plan': label['gold_plan']}
        for profile in protocol['profiles']:
            for mode in protocol['modes']:
                name = f"{case['query_id']}_{profile}_{mode}.json"
                retrieval = R.read(runtime.path / 'retrieval' / name)
                answer = R.read(runtime.path / 'answers' / name)
                if answer['retrieval_sha256'] != R.digest(retrieval):
                    raise RuntimeError('Answer/retrieval binding mismatch')
                metrics = previous.retrieval_metrics(case, retrieval['result'], runtime.E, facts) if retrieval['status'] == 'ok' else {}
                scores = runtime.M.score(case, answer['answer'], retrieval['result']['evidence']) if answer['status'] == retrieval['status'] == 'ok' else {
                    'known_positive_complete': 0 if case['gold_cards'] else None, 'citation_ownership': 0,
                    'explicit_scope_preserved': 0, 'unsupported_percent_claims': 0}
                retrieved.append({**retrieval, 'metrics': metrics, 'subgroup': case['subgroup']})
                answered.append({**answer, 'scores': scores, 'subgroup': case['subgroup']})
    R.write(runtime.path / 'combined_results.json', {'retrievals': retrieved, 'answers': answered,
                                                   'dataset_sha256': dataset['sha256'], 'gold_attached_after_execution': True})
    return retrieved, answered

def run():
    runtime = R.Runtime(42)
    runtime.memory(starting=True)
    runtime.verify()
    prior = R.read(R.STAGES[41] / 'run_summary.json')
    if prior['answer_rows'] != 36 or prior['retrieval_rows'] != 36:
        raise RuntimeError('Complete stage41 before stage42')
    bound = I.verify_inputs(runtime.path)
    public, protocol, catalog = bound['questions'], bound['protocol'], bound['catalog']
    validate_digest(public)
    validate_digest(protocol)
    validate_digest(catalog)
    if public['catalog_sha256'] != catalog['sha256'] or catalog['protocol_sha256'] != protocol['sha256']:
        raise RuntimeError('Catalog/public binding mismatch')
    if public['protocol_sha256'] != protocol['sha256']:
        raise RuntimeError('Public input protocol mismatch')
    if any(set(q) != {'query_id', 'question'} for q in public['queries']):
        raise RuntimeError('Public questions must not expose gold labels')
    P = R.load('expanded_parser_prompt', '33_query_routing.py')
    runtime.F.initialize(42)
    instructions = P.INSTRUCTIONS + '\n카드 catalog:\n' + runtime.F.canonical(catalog['catalog'])
    execution = R.freeze(runtime.path / 'execution_protocol.json', {
        'public_questions_sha256': public['sha256'], 'evaluation_protocol_sha256': protocol['sha256'],
        'dataset_sha256': bound['dataset_sha256'], 'freeze_receipt_sha256': bound['receipt_sha256'],
        'parser_instructions': instructions, 'parser_schema': runtime.E.QueryPlan.model_json_schema(),
        'sources': {str(p.relative_to(R.ROOT)): R.digest(p.read_bytes()) for p in [Path(__file__),
            R.ROOT / 'notebooks/scripts/41_followup_runtime.py', R.ROOT / 'notebooks/scripts/41_query_expansion.py',
            R.ROOT / 'notebooks/scripts/42_input_binding.py']},
        'condition_depth': 50, 'candidate_depth': 20, 'top_k': 3, 'bundle': 'budget5',
        'answer_output': 2400, 'parser_output': 3000, 'model': 'gpt-6-luna', 'reasoning': 'medium'})
    plans, expansions = {}, {}
    for q in public['queries']:
        path = runtime.path / 'plans' / (q['query_id'] + '.json')
        if path.exists():
            row = R.read(path)
        else:
            api = runtime.cache('parser')
            try:
                response = api.parse(model='gpt-6-luna', instructions=instructions, input=[{'role': 'user', 'content': q['question']}],
                                     text_format=runtime.E.QueryPlan, tools=[], store=False, max_output_tokens=3000)
                value = response.output_parsed.model_dump(mode='json')
                if not set(value['card_keys']) <= set(catalog['catalog']):
                    raise ValueError('Unknown parser card scope')
                row = {'status': 'ok', 'plan': value, 'events': api.events}
            except Exception as error:
                row = {'status': 'failed', 'error': f'{type(error).__name__}: {error}', 'events': api.events,
                       'plan': {'route': 'unstructured', 'groups': [], 'card_keys': [], 'unresolved': ['parser failure']}}
            row.update(query_id=q['query_id'], execution_sha256=execution['sha256'])
            R.write(path, row)
        if row['execution_sha256'] != execution['sha256']:
            raise RuntimeError('Parser execution binding changed')
        plans[q['query_id']] = row
        expansions[q['query_id']] = X.expand_query(q['question'], row['plan'])
        print('42 parser', q['query_id'], row['status'], flush=True)
    R.write(runtime.path / 'parser_results.json', {'records': list(plans.values())})
    R.write(runtime.path / 'expansions.json', {'records': expansions})
    texts = [q['question'] for q in public['queries']] + [expansions[q['query_id']]['search_question'] for q in public['queries']]
    vectors = runtime.embeddings(texts)
    count = len(public['queries'])
    runtime.experiment()
    done = 0
    for i, q in enumerate(public['queries']):
        value = plans[q['query_id']]['plan']
        selection = runtime.E.DB.select_cards(runtime.x.facts, value)
        fallback = not selection['eligible_cards'] or bool(value['unresolved']) or value['route'] == 'unstructured'
        expansion = expansions[q['query_id']]
        for profile in protocol['profiles']:
            cache = {}
            for mode in protocol['modes']:
                name = f"{q['query_id']}_{profile}_{mode}.json"
                path = runtime.path / 'retrieval' / name
                if path.exists():
                    row = R.read(path)
                else:
                    tick = time.perf_counter()
                    try:
                        if mode == 'S_PLUS_U':
                            result = cache['U'] if fallback else runtime.E.merge_results(cache['U'], cache['S_THEN_U'], q['question'])
                        elif mode == 'S_THEN_U' and fallback:
                            result = cache['U']
                        elif mode == 'Q_EXPAND_U' and expansion['search_question'] == q['question']:
                            result = cache['U']
                        else:
                            query = expansion['search_question'] if mode == 'Q_EXPAND_U' else q['question']
                            vector = vectors[count + i] if mode == 'Q_EXPAND_U' else vectors[i]
                            scope = selection['eligible_cards'] if mode == 'S_THEN_U' else value['card_keys'] or None
                            result = runtime.search(query, vector, profile, scope)
                        if result is None:
                            raise RuntimeError('Required source search failed')
                        row = {'status': 'ok', 'result': result}
                    except Exception as error:
                        row = {'status': 'failed', 'error': f'{type(error).__name__}: {error}'}
                    row.update(query_id=q['query_id'], profile=profile, mode=mode, execution_sha256=execution['sha256'],
                               fallback=fallback if mode in ['S_THEN_U', 'S_PLUS_U'] else bool(expansion['fallback_reason']) if mode == 'Q_EXPAND_U' else False,
                               selection=selection, expansion=expansion if mode == 'Q_EXPAND_U' else None,
                               wall_seconds=time.perf_counter() - tick)
                    R.write(path, row)
                if row['execution_sha256'] != execution['sha256']:
                    raise RuntimeError('Retrieval execution binding changed')
                cache[mode] = row.get('result')
                path = runtime.path / 'answers' / name
                if path.exists():
                    answer = R.read(path)
                else:
                    tick = time.perf_counter()
                    try:
                        if row['status'] != 'ok':
                            raise RuntimeError('Retrieval failed')
                        result, usage, events = runtime.answer(q['question'], row['result']['evidence'])
                        answer = {'status': 'ok', 'answer': result, 'usage': usage, 'events': events}
                    except Exception as error:
                        answer = {'status': 'failed', 'error': f'{type(error).__name__}: {error}'}
                    answer.update(query_id=q['query_id'], profile=profile, mode=mode, execution_sha256=execution['sha256'],
                                  retrieval_sha256=R.digest(row), original_answer_question=q['question'], wall_seconds=time.perf_counter() - tick)
                    R.write(path, answer)
                if answer['execution_sha256'] != execution['sha256'] or answer['retrieval_sha256'] != R.digest(row):
                    raise RuntimeError('Answer execution binding changed')
                done += 1
                R.write(runtime.path / 'progress.json', {'answers': done, 'expected': 384})
                runtime.cost()
                print('42', name, row['status'], answer['status'], flush=True)
    retrieved, answered = evaluate_saved(runtime, public, protocol)
    result = {'expected': 384, 'retrieval_rows': len(retrieved), 'answer_rows': len(answered),
              'retrieval_success_rows': sum(r['status'] == 'ok' for r in retrieved),
              'answer_success_rows': sum(r['status'] == 'ok' for r in answered), 'cost': runtime.cost()['summary'],
              'resources': runtime.resources(), 'production_adopted': False}
    R.write(runtime.path / 'run_summary.json', result)
    runtime.verify()
    return result

if __name__ == '__main__':
    try:
        run()
    except R.ExperimentStop as error:
        R.write(R.STAGES[42] / 'interrupted.json', {'status': 'resource_stop', 'reason': str(error)})
        raise
