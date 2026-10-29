"""Stage 41: original frozen questions, new search text, original answer query."""
import importlib.util
from pathlib import Path
import sys
import time

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

R = load('expansion_runtime', '41_followup_runtime.py')
X = load('expansion_rules', '41_query_expansion.py')

def run():
    runtime = R.Runtime(41)
    runtime.memory(starting=True)
    runtime.verify()
    old = R.DATA / '40_new_query_comparison'
    summary = R.read(old / 'run_summary.json')
    if summary['retrieval_records'] != 108 or summary['answer_success_rows'] != 108:
        raise RuntimeError('Stage 40 is incomplete')
    dataset = R.read(R.DATA / '39_answer_rubric/dataset.json')
    if R.digest({k: v for k, v in dataset.items() if k != 'sha256'}) != dataset['sha256']:
        raise RuntimeError('Prior dataset digest changed')
    contract = R.read(R.DATA / '39_answer_rubric/scorer_contract.json')
    if contract['scorer_sha256'] != R.digest((R.ROOT / 'notebooks/scripts/39_answer_rubric.py').read_bytes()):
        raise RuntimeError('Frozen scorer changed')
    plans = {r['query_id']: r for r in R.read(old / 'parser_results.json')['records']}
    baseline_r = R.read(old / 'retrieval_results.json')['records']
    baseline_a = R.read(old / 'answer_results.json')['records']
    lookup_r = {(r['query_id'], r['profile'], r['mode']): r for r in baseline_r}
    expansions = {q['query_id']: X.expand_query(q['question'], plans[q['query_id']]['plan']) for q in dataset['queries']}
    protocol = R.freeze(runtime.path / 'protocol.json', {
        'stage': 41, 'split': 'development_existing18', 'dataset_sha256': dataset['sha256'],
        'profiles': runtime.E.PROFILES, 'modes': runtime.E.MODES + ['Q_EXPAND_U'], 'expected_new_rows': 36,
        'baseline_source': '40_new_query_comparison', 'primary_positive_queries': 15,
        'sources': {str(p.relative_to(R.ROOT)): R.digest(p.read_bytes()) for p in [
            old / 'parser_results.json', old / 'retrieval_results.json', old / 'answer_results.json',
            Path(__file__), R.ROOT / 'notebooks/scripts/41_query_expansion.py', R.ROOT / 'notebooks/scripts/41_followup_runtime.py']},
        'expansions': expansions, 'answer_question': 'original user question',
        'conditions': {'component': 50, 'candidate': 20, 'top_k': 3, 'parent_bundle': 'budget5', 'answer_output': 2400},
        'repeated_generations': 0, 'production_adopted': False})
    embeddings = runtime.embeddings([expansions[q['query_id']]['search_question'] for q in dataset['queries']])
    runtime.experiment()
    previous = R.load('expansion_prior_metrics', '40_new_query_comparison.py')
    runtime.F.initialize(41)
    facts = {f['fact_id']: f for f in runtime.x.facts}
    retrievals, answers = [], []
    for q, embedding in zip(dataset['queries'], embeddings, strict=True):
        expansion = expansions[q['query_id']]
        for profile in runtime.E.PROFILES:
            name = f"{q['query_id']}_{profile}_Q_EXPAND_U.json"
            path = runtime.path / 'retrieval' / name
            if path.exists():
                row = R.read(path)
            else:
                tick = time.perf_counter()
                try:
                    if expansion['search_question'] == q['question']:
                        prior = lookup_r[q['query_id'], profile, 'U']
                        if prior['status'] != 'ok':
                            raise RuntimeError('Prior U retrieval failed')
                        result = prior['result']
                        provenance = 'exact saved40 U retrieval; original query unchanged'
                    else:
                        scope = plans[q['query_id']]['plan']['card_keys'] or None
                        result = runtime.search(expansion['search_question'], embedding, profile, scope)
                        provenance = 'new operational search with expanded query'
                    row = {'status': 'ok', 'result': result,
                           'metrics': previous.retrieval_metrics(q, result, runtime.E, facts), 'provenance': provenance}
                except Exception as error:
                    row = {'status': 'failed', 'error': f'{type(error).__name__}: {error}'}
                row.update(query_id=q['query_id'], profile=profile, mode='Q_EXPAND_U', subgroup=q['subgroup'],
                           protocol_sha256=protocol['sha256'], expansion=expansion, wall_seconds=time.perf_counter() - tick)
                R.write(path, row)
            if row['protocol_sha256'] != protocol['sha256']:
                raise RuntimeError('Retrieval protocol binding changed')
            retrievals.append(row)
            path = runtime.path / 'answers' / name
            if path.exists():
                answer = R.read(path)
            else:
                tick = time.perf_counter()
                try:
                    if row['status'] != 'ok':
                        raise RuntimeError('Retrieval failed')
                    evidence = row['result']['evidence']
                    result, usage, events = runtime.answer(q['question'], evidence)
                    answer = {'status': 'ok', 'answer': result, 'usage': usage, 'events': events,
                              'scores': runtime.M.score(q, result, evidence)}
                except Exception as error:
                    answer = {'status': 'failed', 'error': f'{type(error).__name__}: {error}',
                              'scores': {'known_positive_complete': 0 if q['gold_cards'] else None,
                                         'citation_ownership': 0, 'explicit_scope_preserved': 0, 'unsupported_percent_claims': 0}}
                answer.update(query_id=q['query_id'], profile=profile, mode='Q_EXPAND_U', subgroup=q['subgroup'],
                              protocol_sha256=protocol['sha256'], retrieval_sha256=R.digest(row),
                              original_answer_question=q['question'], wall_seconds=time.perf_counter() - tick)
                R.write(path, answer)
            if answer['protocol_sha256'] != protocol['sha256'] or answer['retrieval_sha256'] != R.digest(row):
                raise RuntimeError('Answer binding changed')
            answers.append(answer)
            R.write(runtime.path / 'progress.json', {'expected': 36, 'retrieval': len(retrievals), 'answers': len(answers)})
            runtime.cost()
            print('41', name, row['status'], answer['status'], flush=True)
    R.write(runtime.path / 'retrieval_results.json', {'records': retrievals})
    R.write(runtime.path / 'answer_results.json', {'records': answers})
    R.write(runtime.path / 'combined_results.json', {'retrievals': baseline_r + retrievals, 'answers': baseline_a + answers,
            'baseline_source_sha256': protocol['sources'], 'same_frozen_dataset': True})
    result = {'expected': 36, 'retrieval_rows': len(retrievals), 'answer_rows': len(answers),
              'retrieval_success_rows': sum(r['status'] == 'ok' for r in retrievals),
              'answer_success_rows': sum(r['status'] == 'ok' for r in answers),
              'cost': runtime.cost()['summary'], 'resources': runtime.resources(), 'production_adopted': False}
    R.write(runtime.path / 'run_summary.json', result)
    runtime.verify()
    return result

if __name__ == '__main__':
    try:
        run()
    except R.ExperimentStop as error:
        R.write(R.STAGES[41] / 'interrupted.json', {'reason': str(error), 'status': 'resource_stop'})
        raise
