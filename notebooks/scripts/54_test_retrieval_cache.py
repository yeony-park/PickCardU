"""Run the actual draft notebook loop without models/API to check path isolation."""
from pathlib import Path
from types import SimpleNamespace
import copy
import hashlib
import json
import time

import nbformat


def test_repeat_and_resume_never_mix_answer_supplement_into_independent_U(tmp_path):
    def digest(v):
        return hashlib.sha256(json.dumps(v, sort_keys=True).encode()).hexdigest()

    def write(p, v):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(v))

    def frozen(p, v):
        if p.exists():
            assert json.loads(p.read_text()) == v
        else:
            write(p, v)

    searches = []
    profiles = ['benefit', 'parent']

    def pack(units):
        keys = sorted({u['card_key'] for u in units})
        return dict(cards=[dict(card_key=k) for k in keys], evidence=copy.deepcopy(units), trace={})

    class Runtime:
        def __init__(self, stage):
            self.last_embedding_seconds = .01

        def embeddings(self, texts, **kwargs):
            return [[i] for i in range(len(texts))]

        def profile(self, pf):
            pass

        def search(self, q, vec, pf, scope):
            searches.append((q, pf, tuple(scope)))
            return {**pack([dict(card_key=k, text=q+k, origin='unstructured') for k in scope]),
                    'experiment_scope': scope}

        def resources(self):
            return {}

    def structured(route):
        return [dict(card_key=k, text='db:'+k, origin='structured') for k in route['eligible_cards']]

    def supplement(q, base, route):
        result = copy.deepcopy(base)
        keys = {c['card_key'] for c in base['cards']}
        result['evidence'] += [u for u in structured(route) if u['card_key'] in keys]
        result['supplement'] = dict(no_eviction=True)
        return result

    def units(rt, result, pf):
        assert all(e['origin'] == 'unstructured' for e in result['evidence'])
        assert 'supplement' not in result
        return result['evidence']

    def bind(stage, row, **kwargs):
        scope = kwargs['parser_record']['original_scope']['card_keys']
        if kwargs['mode'] == 'SEQ':
            scope = kwargs['parser_record']['routing']['scope']
        assert all(e['card_key'] in scope for e in row['result']['evidence'])
        if kwargs['mode'] == 'PAR':
            assert kwargs['independent_u'] is not None
        return row

    questions = [dict(query_id=f'F{i:02}', question=f'question{i}') for i in [1, 2]]
    records = {}
    for q in questions:
        for repeat in [1, 2, 3]:
            eligible = ['a'] if repeat == 1 else ['b'] if repeat == 2 else []
            records[q['query_id'], repeat] = dict(original_scope=dict(card_keys=['a','b','c'], clarify=False),
                routing=dict(eligible_cards=eligible, scope=eligible or ['a','b','c']))
    M = SimpleNamespace(rank_units=lambda rt,q,us: pack(us), structured_units=structured,
        unstructured_units=units, union_units=lambda a,b: a+b, bind_retrieval=bind, supplement=supplement)
    P = SimpleNamespace(sha=digest,F=SimpleNamespace(STAGES={54:tmp_path},write_json=write,
        read_json=lambda p: json.loads(p.read_text())))
    X = SimpleNamespace(PROFILES=profiles, ledger=lambda: {}, frozen=frozen)
    source = nbformat.read(Path(__file__).parents[1]/'54_new_question_confirmation.ipynb',4).cells[4].source
    source = source[source.index('runtime=BudgetRuntime(54)'):]

    def run():
        ns = dict(PATH=tmp_path, X=X, P=P, M=M, BudgetRuntime=Runtime, queries=questions,
            by_query=records, manifest={}, ALLOW_NETWORK=False, time=time,copy=copy,
            selected_AB=dict(benefit='B',parent='A'),selection={'fixture':True})
        exec(compile(source,'54 retrieval loop','exec'),ns)
        return ns['retrievals']

    first = run()
    assert len(first) == 42
    assert len(searches) == 12  # 2 questions x 2 profiles x original/a/b scopes.
    assert len(set(searches)) == len(searches)
    for row in first:
        origins = {e['origin'] for e in row['result']['evidence']}
        if row['mode'] == 'U':
            assert origins == {'unstructured'}
        if row['mode']=='SEQ' and row['profile']=='benefit' and row['repeat']<3:
            assert origins == {'structured','unstructured'}
    searches.clear()
    assert run() == first
    assert not searches
