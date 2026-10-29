"""Experiment-only whole-chunk bundle policies over unchanged operational search."""
from __future__ import annotations

from dataclasses import replace
import importlib.util
from pathlib import Path
import sys
from types import FunctionType, SimpleNamespace
import time

spec = importlib.util.spec_from_file_location('followup_common', Path(__file__).with_name('34_followup_common.py'))
F = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = F
spec.loader.exec_module(F)

POLICIES = ['fixed5', 'fixed4', 'budget5', 'budget8']


def build_budgeted(rows, chunks, *, max_chunks, fits):
    """Keep seed and whole chunks, using operational priority AND formatter."""
    from pickcardu_rag.retrieval import parent_child_bundles
    from pickcardu_rag.errors import RerankerUnavailable
    if max_chunks < 1:
        raise ValueError('Bundle size must be positive')
    # Full original ordered pool removes neither ownership nor level checks.
    pools = parent_child_bundles(rows, chunks, max_chunks=len(chunks) or 1)
    results = []
    for pool in pools:
        seed_row = pool['seed']
        seed = chunks[seed_row.chunk_id]
        selected = [seed.chunk_id]
        def formatted(ids):
            # A private relation view feeds the original formatter; text and
            # all other chunk metadata remain unchanged in the source mapping.
            view = dict(chunks)
            view[seed.chunk_id] = replace(seed, related_chunk_ids=tuple(ids[1:]))
            return parent_child_bundles([seed_row], view, max_chunks=len(ids))[0]
        bundle = formatted(selected)
        if not fits(bundle['text']):
            raise RerankerUnavailable(f'parent-child seed {seed.chunk_id} exceeds token limit')
        for chunk_id in pool['selected_chunk_ids'][1:]:
            if len(selected) == max_chunks:
                break
            candidate = formatted(selected + [chunk_id])
            if fits(candidate['text']):
                selected.append(chunk_id)
                bundle = candidate
        results.append(bundle)
    return results


def isolated_search(builder):
    """Reuse exact operational bytecode without changing its shared globals."""
    from pickcardu_rag.retrieval import RagPipeline
    original = RagPipeline.search
    namespace = dict(original.__globals__)
    namespace['parent_child_bundles'] = builder
    function = FunctionType(original.__code__, namespace, original.__name__, original.__defaults__, original.__closure__)
    function.__kwdefaults__ = original.__kwdefaults__
    return function


class TokenGuard:
    def __init__(self):
        from transformers import AutoConfig, AutoTokenizer
        from pickcardu_rag.retrieval import LocalReranker, PARENT_CHILD_DOCUMENT_TOKEN_LIMIT
        path = str(F.ROOT / '.cache/reranker/bge-reranker-v2-m3')
        self.tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True)
        config = AutoConfig.from_pretrained(path, local_files_only=True)
        self.max_length = LocalReranker._effective_max_length(self.tokenizer, SimpleNamespace(config=config))
        self.document_limit = PARENT_CHILD_DOCUMENT_TOKEN_LIMIT

    def lengths(self, query, text):
        return {'document_tokens': len(self.tokenizer(text, truncation=False, add_special_tokens=False)['input_ids']),
                'pair_tokens': len(self.tokenizer([[query, text]], truncation=False, add_special_tokens=True)['input_ids'][0])}

    def fits(self, query, text):
        length = self.lengths(query, text)
        return length['document_tokens'] <= self.document_limit and length['pair_tokens'] <= self.max_length


class ReadThroughScorer:
    def __init__(self, guard):
        import torch
        from pickcardu_rag.retrieval import LocalReranker
        torch.set_num_threads(4)
        self.delegate = LocalReranker(str(F.ROOT / '.cache/reranker/bge-reranker-v2-m3'))
        self.contract = self.delegate.artifact_contract('bge')
        self.guard = guard
        self.calls = []
        self.cache = F.STAGES[34] / 'bge_cache'
        self.cache.mkdir(exist_ok=True)
        self.old_cache = F.OLD[32] / 'bge_cache'

    def score(self, mode, query, documents, **kwargs):
        from pickcardu_rag.errors import RerankerUnavailable
        # Validate ALL inputs, including cached scores, against unchanged limits.
        lengths = [self.guard.lengths(query, text) for text in documents]
        if any(r['pair_tokens'] > self.guard.max_length for r in lengths):
            raise RerankerUnavailable('reranker input exceeds token limit; truncation forbidden')
        limit = kwargs.get('document_token_limit')
        if limit is not None and any(r['document_tokens'] > limit for r in lengths):
            raise RerankerUnavailable('parent-child document exceeds bundle token limit')
        started = time.perf_counter()
        scores, missing, paths = [], [], []
        for i, document in enumerate(documents):
            key = F.digest({'mode': mode, 'q': query, 'doc': document, 'kwargs': kwargs, 'contract': self.contract})
            path = self.cache / (key + '.json')
            paths.append(path)
            old = self.old_cache / path.name
            found = path if path.exists() else old
            if found.exists():
                scores.append(F.read_json(found)['score'])
            else:
                scores.append(None)
                missing.append(i)
        trace = {'cache_hits': len(documents) - len(missing), 'new_pairs': len(missing),
                 'device': 'cpu', 'dtype': 'float32', 'contract': self.contract,
                 'document_tokens': sum(r['document_tokens'] for r in lengths),
                 'pair_tokens': sum(r['pair_tokens'] for r in lengths)}
        if missing:
            new, measured = self.delegate.score(mode, query, [documents[i] for i in missing], **kwargs)
            trace['measured_scorer'] = measured
            for i, value in zip(missing, new, strict=True):
                scores[i] = value
                F.write_json(paths[i], {'score': value, 'query_sha256': F.digest(query),
                                       'document_sha256': F.digest(documents[i])})
        trace['wall_seconds'] = time.perf_counter() - started
        self.calls.append(trace)
        return scores, trace


class ReadOnlyEmbeddingAPI:
    """Never writes an old cache or silently creates new paid embeddings."""
    def embeddings(self, texts):
        import numpy as np
        vectors = []
        for text in texts:
            key = F.digest({'text': text, 'model': 'text-embedding-3-small', 'dimension': 1536})
            path = F.OLD[30] / 'api_cache/embeddings' / (key + '.npy')
            if not path.exists():
                raise RuntimeError('Frozen question embedding is missing')
            vectors.append(np.load(path, allow_pickle=False))
        return np.asarray(vectors, dtype=np.float32)


def query_identity(value):
    import numpy as np
    return F.digest(value if isinstance(value, str) else np.asarray(value, dtype=np.float32).tobytes())


class FrozenSearcher:
    """Exact operational component output; reject a different query or scope."""
    def __init__(self, rows, *, expected_query, expected_scope=None):
        self.rows = rows
        self.expected_query = query_identity(expected_query)
        self.expected_scope = tuple(expected_scope) if expected_scope is not None else None

    def search(self, query, *, limit, card_keys=None):
        if query_identity(query) != self.expected_query or card_keys != self.expected_scope or limit != 50:
            raise ValueError('Frozen component query/scope/depth mismatch')
        return list(self.rows)


class FollowupExperiment:
    def __init__(self):
        F.prepare_inputs()
        # Prior helper imports create only already-existing empty directories.
        # Restore new-stage environment before tokenizer/index imports run.
        self.E = F.load_module('prior_experiment_for_followup', F.ROOT / 'notebooks/scripts/32_search_experiment.py')
        F.initialize()
        self.guard = TokenGuard()
        self.scorer = ReadThroughScorer(self.guard)
        self.E.C.STAGES = dict(self.E.C.STAGES)
        self.E.C.STAGES[31] = F.STAGES[34] / 'indexes'
        self.E.C.CachedAPI = ReadOnlyEmbeddingAPI
        self.E.CachedReranker = lambda: self.scorer
        self.base = self.E.Experiment()
        self.audit = []
        self.facts = self.base.facts
        self.api = self.base.api
        self.last_bundle_audit = []
        self.components = {}

    @staticmethod
    def component_key(question, profile, keys):
        return F.digest({'question': question, 'profile': profile, 'keys': list(keys) if keys is not None else None})

    def capture_component(self, question, embedding, profile, keys=None):
        from pickcardu_rag.retrieval import _candidate_dict
        _, lexical, vector, _ = self.base.data[profile]
        kwargs = {'limit': 50}
        if keys is not None:
            kwargs['card_keys'] = tuple(keys)
        return {'bm25': [_candidate_dict(r) for r in lexical.search(question, **kwargs)],
                'vector': [_candidate_dict(r) for r in vector.search(embedding, **kwargs)],
                'provenance': 'new unmodified operational retriever output captured once; no historical component trace available'}

    def freeze_components(self):
        path = F.STAGES[34] / 'component_freeze.json'
        if path.exists():
            frozen = F.read_json(path)
            value = dict(frozen)
            claimed = value.pop('sha256')
            if F.digest(value) != claimed:
                raise RuntimeError('Component freeze hash mismatch')
            if frozen['index_identity'] != self.base.index_identity:
                raise RuntimeError('Frozen components refer to different indexes')
            self.components = frozen['components']
            return frozen
        freeze = self.E.freeze_dataset()
        plans = {r['query_id']: r['plan'] for r in F.read_json(F.OLD[33] / 'parser_results.json')['records']}
        prior = {(r['query_id'], r['profile'], r['mode']): r for r in
                 F.read_json(F.OLD[33] / 'retrieval_results.json')['records']}
        origin = []
        for case in freeze['queries']:
            plan = plans[case['query_id']]
            selection = self.E.DB.select_cards(self.facts, plan)
            fallback = not selection['eligible_cards'] or bool(plan.get('unresolved')) or plan['route'] == 'unstructured'
            embedding = self.api.embeddings([case['question']])[0]
            for profile in self.E.PROFILES:
                for mode in ['U', 'S_THEN_U']:
                    if mode == 'S_THEN_U' and fallback:
                        continue
                    keys = (plan.get('card_keys') or None) if mode == 'U' else selection['eligible_cards']
                    key = self.component_key(case['question'], profile, keys)
                    if key in self.components:
                        continue
                    original = prior[(case['query_id'], profile, mode)]
                    if original['status'] == 'ok':
                        trace = original['result']['trace']
                        component = {'bm25': trace['bm25'], 'vector': trace['vector'],
                                     'provenance': 'exact saved33 operational component trace',
                                     'original_record_sha256': F.digest(original)}
                    else:
                        component = self.capture_component(case['question'], embedding, profile, keys)
                    component['identity'] = {'question_sha256': query_identity(case['question']),
                        'embedding_sha256': query_identity(embedding), 'profile': profile,
                        'scope': list(keys) if keys is not None else None,
                        'chunks_sha256': F.digest((F.OLD[31] / f'{profile}_chunks.jsonl').read_bytes())}
                    self.components[key] = component
                    origin.append({'query_id': case['query_id'], 'profile': profile, 'mode': mode,
                                   'scope_cards': list(keys) if keys is not None else None,
                                   'key': key, 'provenance': component['provenance']})
        frozen = {'components': self.components, 'origins': origin, 'dataset_sha256': freeze['sha256'],
                  'index_identity': self.base.index_identity,
                  'new_llm_calls': 0, 'new_embeddings': 0,
                  'reason': 'Chroma approximate replay Q10 changed component candidates while final cards/evidence stayed equal. Freeze operational components to isolate bundle policy.'}
        frozen['sha256'] = F.digest(frozen)
        F.write_json(path, frozen)
        return frozen

    def search(self, question, embedding, profile, keys=None, policy='fixed5'):
        from pickcardu_rag.retrieval import (RagPipeline, SearchConfig, CHUNKING_PROFILES, parent_child_bundles)
        if policy not in POLICIES:
            raise ValueError('Unknown bundle policy')
        chunks, lexical, vector, _ = self.base.data[profile]
        key = self.component_key(question, profile, keys)
        if self.components:
            from pickcardu_rag.retrieval import Candidate
            fixture = self.components[key]
            identity = fixture['identity']
            if (identity['question_sha256'] != query_identity(question) or
                identity['embedding_sha256'] != query_identity(embedding) or
                identity['scope'] != (list(keys) if keys is not None else None) or
                identity['profile'] != profile or identity['chunks_sha256'] !=
                F.digest((F.OLD[31] / f'{profile}_chunks.jsonl').read_bytes())):
                raise RuntimeError('Frozen component identity mismatch')
            owners = {chunk.chunk_id: chunk.card_key for chunk in chunks}
            for name in ['bm25', 'vector']:
                candidates = fixture[name]
                if len(candidates) > 50 or [r['rank'] for r in candidates] != list(range(1, len(candidates) + 1)):
                    raise RuntimeError('Frozen component depth/order mismatch')
                if any(r['chunk_id'] not in owners or (keys is not None and owners[r['chunk_id']] not in keys)
                       for r in candidates):
                    raise RuntimeError('Frozen component chunk/scope mismatch')
            lexical = FrozenSearcher([Candidate(**r) for r in fixture['bm25']], expected_query=question,
                                     expected_scope=tuple(keys) if keys is not None else None)
            vector = FrozenSearcher([Candidate(**r) for r in fixture['vector']], expected_query=embedding,
                                    expected_scope=tuple(keys) if keys is not None else None)
        if keys is not None:
            lexical = self.E.ScopedSearcher(lexical, tuple(keys))
            vector = self.E.ScopedSearcher(vector, tuple(keys))
        self.last_bundle_audit = []
        def builder(rows, mapping):
            fixed = parent_child_bundles(rows, mapping, max_chunks=int(policy[-1]))
            # Capture offending identities/tokens BEFORE a reranker exception.
            baseline_audit = []
            for bundle in fixed:
                baseline_audit.append({
                    'card_key': bundle['card_key'], 'seed_chunk_id': bundle['seed'].chunk_id,
                    'selected_chunk_ids': bundle['selected_chunk_ids'],
                    'bundle_sha256': bundle['bundle_sha256'],
                    **self.guard.lengths(question, bundle['text']),
                    'chunks': [{'chunk_id': key, 'text_sha256': F.digest(mapping[key].text),
                                'raw_text_tokens': len(self.guard.tokenizer(mapping[key].text,
                                      truncation=False, add_special_tokens=False)['input_ids'])}
                               for key in bundle['selected_chunk_ids']],
                })
            self.last_bundle_audit = baseline_audit
            if policy.startswith('budget'):
                result = build_budgeted(rows, mapping, max_chunks=int(policy[-1]),
                                        fits=lambda text: self.guard.fits(question, text))
                selected_audit = []
                for bundle in result:
                    selected_audit.append({'card_key': bundle['card_key'],
                        'seed_chunk_id': bundle['seed'].chunk_id, 'selected_chunk_ids': bundle['selected_chunk_ids'],
                        'bundle_sha256': bundle['bundle_sha256'], **self.guard.lengths(question, bundle['text'])})
                self.last_bundle_audit = selected_audit
                return result
            return fixed
        pipeline = RagPipeline(chunks, lexical, vector, self.scorer, profile=CHUNKING_PROFILES[profile])
        config = SearchConfig(profile=profile, vector_weight=.4, component_depth=50, candidate_depth=20,
                              top_k=3, reranker='bge', reranker_route='all' if profile == 'parent_child_bundle' else 'selective',
                              answer_payload_bytes=64000)
        return isolated_search(builder)(pipeline, question, embedding, config)


def frozen_inputs(experiment):
    freeze = experiment.E.freeze_dataset()
    plans = {r['query_id']: r['plan'] for r in F.read_json(F.OLD[33] / 'parser_results.json')['records']
             if r['status'] == 'ok'}
    protocol = {
        'version': 'whole-chunk-bundle-followup-v1', 'policies': POLICIES,
        'dataset_sha256': freeze['sha256'], 'plans_sha256': F.digest(plans),
        'queries': [{k: case[k] for k in ['query_id', 'split', 'subgroup', 'question']} for case in freeze['queries']],
        'selection_rule': 'Development20 only; Q05/Q28 U+S_PLUS repaired; no new failures; no S_THEN hit pass loss; S_THEN witness mean not lower. Max S_THEN witness then min total document tokens; ties fixed5,budget5,budget8,fixed4. No passing variant => keep fixed5 and disclose unresolved failures.',
        'verification_label': '13 previously seen questions after selection; not independent holdout or confirmatory evidence',
        'document_limit': experiment.guard.document_limit, 'pair_limit': experiment.guard.max_length,
        'code': {name: F.digest((F.ROOT / 'notebooks/scripts' / name).read_bytes())
                 for name in ['34_followup_common.py', '34_bundle_experiment.py']},
        'original_retrieval_sha256': F.digest((F.ROOT / 'packages/rag-core/src/pickcardu_rag/retrieval.py').read_bytes()),
        'index_identity': experiment.base.index_identity,
        'component_freeze_sha256': F.read_json(F.STAGES[34] / 'component_freeze.json')['sha256'],
        'component_contract': 'Exact saved operational BM25/vector outputs; only Q05/Q28 failed U outputs newly captured once. Same fixed candidates in all bundle policies; not a fresh engine run per policy.',
        'new_llm_calls': 0, 'new_embeddings': 0,
        'unknown_diagnostic': 'Pre-registered separate strict+unknown experiment in36; excluded from primary six-condition winner selection',
    }
    protocol['sha256'] = F.digest(protocol)
    path = F.STAGES[34] / 'protocol.json'
    if path.exists() and F.read_json(path) != protocol:
        raise RuntimeError('Bundle protocol changed after freeze')
    F.write_json(path, protocol)
    return freeze, plans, protocol


def comparable_result(result):
    return {'cards': result['cards'], 'evidence': result['evidence'],
            'trace': {key: result['trace'].get(key) for key in
                      ['vector', 'bm25', 'rrf', 'leaf', 'rerank', 'evidence_hydration', 'bundles',
                       'card', 'evidence_budget', 'scope', 'profile']}}


def diagnose(experiment, freeze, plans):
    records = []
    for case in freeze['queries']:
        if case['query_id'] not in ['Q05', 'Q28']:
            continue
        embedding = experiment.api.embeddings([case['question']])[0]
        try:
            experiment.search(case['question'], embedding, 'parent_child_bundle',
                              plans[case['query_id']].get('card_keys') or None, 'fixed5')
            status, error = 'ok', None
        except Exception as exc:
            status, error = 'failed', f'{type(exc).__name__}: {exc}'
        audit = experiment.last_bundle_audit
        records.append({'query_id': case['query_id'], 'question': case['question'], 'status': status,
            'error': error, 'bundles': audit,
            'over_document_limit': [row for row in audit if row['document_tokens'] > experiment.guard.document_limit],
            'over_pair_limit': [row for row in audit if row['pair_tokens'] > experiment.guard.max_length]})
    out = {'records': records, 'new_llm_calls': 0, 'new_embeddings': 0,
           'new_scorer_pairs': sum(c['new_pairs'] for c in experiment.scorer.calls),
           'document_token_limit': experiment.guard.document_limit, 'pair_limit': experiment.guard.max_length}
    F.write_json(F.STAGES[34] / 'failure_token_audit.json', out)
    print('failure token audit', F.canonical({r['query_id']: [(b['card_key'], b['document_tokens'])
          for b in r['over_document_limit']] for r in records}), flush=True)
    return out


def run_cases(experiment, cases, plans, protocol):
    prior = {(r['query_id'], r['profile'], r['mode']): r for r in
             F.read_json(F.OLD[33] / 'retrieval_results.json')['records']}
    outputs = []
    for case in cases:
        plan = plans[case['query_id']]
        selection = experiment.E.DB.select_cards(experiment.facts, plan)
        scope = selection['eligible_cards']
        fallback = not scope or bool(plan.get('unresolved')) or plan['route'] == 'unstructured'
        embedding = experiment.api.embeddings([case['question']])[0]
        for policy in POLICIES:
            cache = {}
            for mode in experiment.E.MODES:
                key = f'{case["query_id"]}_{policy}_{mode}'
                path = F.STAGES[34] / 'retrieval' / (key + '.json')
                if path.exists():
                    record = F.read_json(path)
                    if record['protocol_sha256'] != protocol['sha256']:
                        raise RuntimeError('Stale bundle result')
                    outputs.append(record)
                    cache[mode] = record.get('result')
                    continue
                started = time.perf_counter()
                experiment.last_bundle_audit = []
                try:
                    if mode == 'U':
                        result = experiment.search(case['question'], embedding, 'parent_child_bundle',
                                                   plan.get('card_keys') or None, policy)
                    elif mode == 'S_THEN_U':
                        result = cache['U'] if fallback else experiment.search(
                            case['question'], embedding, 'parent_child_bundle', scope, policy)
                    else:
                        if cache['U'] is None or cache['S_THEN_U'] is None:
                            raise RuntimeError('Required prior component failed')
                        result = cache['U'] if fallback else experiment.E.merge_results(cache['U'], cache['S_THEN_U'], case['question'])
                    if result is None:
                        raise RuntimeError('Required prior component failed')
                    record = {'status': 'ok', 'result': result,
                              'metrics': experiment.E.evaluate_retrieval(case, result, selection)}
                    cache[mode] = result
                except Exception as exc:
                    record = {'status': 'failed', 'error': f'{type(exc).__name__}: {exc}'}
                    cache[mode] = None
                record.update(query_id=case['query_id'], profile='parent_child_bundle', policy=policy,
                    mode=mode, split=case['split'], subgroup=case['subgroup'],
                    fallback=fallback if mode != 'U' else False, structured_card_count=len(scope),
                    unknown_card_count=len(selection['unknown_cards']), bundles=experiment.last_bundle_audit,
                    wall_seconds=time.perf_counter() - started, protocol_sha256=protocol['sha256'])
                if policy == 'fixed5':
                    original = prior[(case['query_id'], 'parent_child_bundle', mode)]
                    equal = record['status'] == original['status'] and (
                        record['status'] != 'ok' or comparable_result(result) == comparable_result(original['result']))
                    record['original_replay_equal'] = equal
                    if not equal:
                        F.write_json(F.STAGES[34] / 'parity_failure.json', {'new': record, 'prior': original})
                        raise RuntimeError('Fixed5 differs from saved original pipeline result')
                F.write_json(path, record)
                outputs.append(record)
                print('bundle', key, record['status'], round(record['wall_seconds'], 2), flush=True)
    return outputs


def metric_mean(rows, key):
    values = [r.get('metrics', {}).get(key, 0) if r['status'] == 'ok' else 0 for r in rows]
    return sum(values) / len(values) if values else None


def select_policy(rows, cases, protocol):
    positive = {case['query_id'] for case in cases if case['gold_cards']}
    witness = {case['query_id'] for case in cases if case['witness_groups']}
    baseline = {(r['query_id'], r['mode']): r for r in rows if r['policy'] == 'fixed5'}
    baseline_s = [r for r in baseline.values() if r['mode'] == 'S_THEN_U' and r['query_id'] in witness]
    candidates = []
    for policy in POLICIES:
        subset = [r for r in rows if r['policy'] == policy]
        failures = sum(r['status'] != 'ok' for r in subset)
        target = [r for r in subset if r['query_id'] in ['Q05', 'Q28'] and r['mode'] in ['U', 'S_PLUS_U']]
        target_repaired = len(target) == 4 and all(r['status'] == 'ok' for r in target)
        s = [r for r in subset if r['mode'] == 'S_THEN_U' and r['query_id'] in positive]
        losses = [r['query_id'] for r in s if baseline[(r['query_id'], 'S_THEN_U')].get('metrics', {}).get('anchor_hit_at_3') == 1
                  and r.get('metrics', {}).get('anchor_hit_at_3', 0) != 1]
        w = metric_mean([r for r in s if r['query_id'] in witness], 'witness_recall_at_3')
        bw = metric_mean(baseline_s, 'witness_recall_at_3')
        tokens = sum(b['document_tokens'] for r in subset for b in r['bundles'])
        candidates.append({'policy': policy, 'failed_rows': failures, 'target_repaired': target_repaired,
            's_then_hit_losses': losses, 's_then_witness': w, 'baseline_witness': bw,
            'document_tokens': tokens, 'passes': failures == 0 and target_repaired and not losses and w >= bw})
    tie = {'fixed5': 0, 'budget5': 1, 'budget8': 2, 'fixed4': 3}
    eligible = [c for c in candidates if c['passes']]
    eligible.sort(key=lambda c: (-c['s_then_witness'], c['document_tokens'], tie[c['policy']]))
    selected = eligible[0]['policy'] if eligible else 'fixed5'
    result = {'selected_policy': selected, 'candidates': candidates, 'protocol_sha256': protocol['sha256'],
              'selected_before_remaining13': True, 'selection_split': 'development',
              'eligible': bool(eligible), 'production_adopted': False}
    result['sha256'] = F.digest(result)
    path = F.STAGES[34] / 'selection.json'
    if path.exists() and F.read_json(path) != result:
        raise RuntimeError('Selection changed after freezing')
    F.write_json(path, result)
    print('selected bundle policy', F.canonical(result), flush=True)
    return result


def run(stage='all'):
    started = time.perf_counter()
    experiment = FollowupExperiment()
    experiment.freeze_components()
    freeze, plans, protocol = frozen_inputs(experiment)
    diagnose(experiment, freeze, plans)
    if stage == 'diagnose':
        print('preservation', F.canonical(F.verify_preservation()), flush=True)
        return
    development = [case for case in freeze['queries'] if case['split'] == 'development']
    rest = [case for case in freeze['queries'] if case['split'] != 'development']
    rows = run_cases(experiment, development, plans, protocol)
    selection = select_policy(rows, development, protocol)
    rows.extend(run_cases(experiment, rest, plans, protocol))
    result = {'records': rows, 'protocol_sha256': protocol['sha256'], 'selection': selection,
              'scorer_calls': experiment.scorer.calls, 'wall_seconds': time.perf_counter() - started,
              'new_llm_calls': 0, 'new_embeddings': 0,
              'scope': '20 development before frozen selection;13 previously seen post-selection verification; full matrix is exploratory',
              'resource_note': 'Single cache-mixed CPU run; not serving latency; billed cost0 for this offline experiment'}
    F.write_json(F.STAGES[34] / 'retrieval_results.json', result)
    print('preservation', F.canonical(F.verify_preservation()), flush=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['diagnose', 'all'], default='all', nargs='?')
    run(parser.parse_args().stage)
