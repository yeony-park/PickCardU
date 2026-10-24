"""Offline paid-run guards: no model/index/API calls in these regressions."""
import importlib.util
from pathlib import Path
import sys
import json
import pytest

spec = importlib.util.spec_from_file_location('runtime65_tests', Path(__file__).with_name('63_65_experiment_runtime.py'))
T = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = T
spec.loader.exec_module(T)


def job(repeat=1, profile='card_page_section_benefit', question='커피 할인 카드'):
    return dict(repeat=repeat, profile=profile, question=question, result=dict(evidence=[]))


def test_reuse_requires_same_repeat_profile_and_question():
    signature = T.reuse_signature(job())
    assert signature == T.reuse_signature(job())
    assert signature != T.reuse_signature(job(repeat=2))
    assert signature != T.reuse_signature(job(profile='parent_child_bundle'))
    assert signature != T.reuse_signature(job(question='커피 적립 카드'))


def test_fixed_question_freeze_is_checked_before_paid_preparation(tmp_path, monkeypatch):
    monkeypatch.setattr(T, 'B65', tmp_path)
    (tmp_path/'question_freeze.json').write_text(json.dumps(dict(files_sha256={'missing_file': 'wrong'})))
    with pytest.raises((FileNotFoundError, RuntimeError)):
        T.guard_freeze()


def test_logical_path_calls_only_scoped_u_once(tmp_path, monkeypatch):
    monkeypatch.setitem(T.P.F.STAGES, 65, tmp_path)
    monkeypatch.setattr(T.X, 'guard_manifest', lambda manifest: None)
    monkeypatch.setattr(T.R, 'guard_files', lambda files: None)
    monkeypatch.setattr(T, 'write', T.E.write)
    monkeypatch.setattr(T, 'frozen', T.E.write)
    calls = []
    class FakeRuntime:
        def __init__(self, stage):
            pass
        def search(self, question, vector, profile, *, scope):
            calls.append((profile, scope))
            return dict(cards=[], evidence=[])
        def resources(self):
            return dict(api_calls=0)
    monkeypatch.setattr(T, 'Runtime', FakeRuntime)
    import numpy as np
    q = dict(query_id='testq', question='BC카드 중에서 커피 할인 카드 찾아줘')
    parsers = {(q['query_id'], r): dict(status='ok', plan=T.Q.plan([T.Q.requirement(['커피'])])) for r in (1,2,3)}
    rows = T.search_matrix(65, [q], np.zeros((1,1536), dtype=np.float32), parsers, dict(critical_data_sha256={}))
    assert len(rows) == 12
    assert len(calls) == 4  # two profiles × U/SEQ; identical repeats reuse retrieval only
    for profile, keys in calls:
        assert keys and all(k.startswith('BC/') for k in keys)
    assert all('supplement' not in r['result'] for r in rows)


def test_retrieval_token_failure_remains_in_the_matrix(tmp_path, monkeypatch):
    from pickcardu_rag.errors import RerankerUnavailable
    monkeypatch.setitem(T.P.F.STAGES, 65, tmp_path)
    monkeypatch.setattr(T.X, 'guard_manifest', lambda manifest: None)
    monkeypatch.setattr(T.R, 'guard_files', lambda files: None)
    monkeypatch.setattr(T, 'write', T.E.write)
    monkeypatch.setattr(T, 'frozen', T.E.write)
    class Unavailable:
        def __init__(self, stage):
            pass
        def search(self, *args, **kwargs):
            raise RerankerUnavailable('pair token bound')
        def resources(self):
            return {}
    monkeypatch.setattr(T, 'Runtime', Unavailable)
    import numpy as np
    q = dict(query_id='failedq', question='커피 할인 카드')
    parsers = {(q['query_id'], r): dict(status='ok', plan=T.Q.plan([])) for r in (1,2,3)}
    rows = T.search_matrix(65, [q], np.zeros((1,1536), dtype=np.float32), parsers, dict(critical_data_sha256={}))
    assert len(rows) == 12 and all(r['retrieval_status'] == 'failed' for r in rows)
    assert all(r['result']['technical_failure'] for r in rows)


def registry_fixture():
    result = dict(cards=[dict(card_key='a')], evidence=[dict(card_key='a',text='커피 10%')])
    j = dict(identity='q:p:U:r1', query_id='q', result=result)
    fact = dict(condition_id='coffee', benefit_id='coffee', roles=['core','extended'],
                source_statement='커피 할인', source_status='verified', source_support=[dict(sha256='pdf-a')])
    catalog = dict(records=[dict(query_id='q', card_key='a', source_pdf_sha256='pdf-a', relevant=True,
                                reason='source reviewed', core_paths=[['coffee']], facts=[fact])])
    reviewed_fact = dict(fact,input_status='supported',input_quotes=['커피 10%'],input_reason='reviewed')
    review = dict(records=[dict(identity=j['identity'], result_sha256=T.P.sha(result), reviewed_by='tester', reason='reviewed',
        card_reviews=[dict(card_key='a',source_pdf_sha256='pdf-a',relevant=True,reason='reviewed',facts=[reviewed_fact])])])
    return [j], catalog, review


def test_registry_rejects_empty_partial_and_wrong_card_proof():
    jobs, catalog, reviews = registry_fixture()
    with pytest.raises(ValueError, match='registry'):
        T.validate_registry_coverage(jobs, dict(records=[]), reviews, {'a':'pdf-a'}, lambda proof: None)
    with pytest.raises(ValueError, match='identity'):
        T.validate_registry_coverage(jobs, catalog, dict(records=[]), {'a':'pdf-a'}, lambda proof: None)
    catalog['records'][0]['facts'][0]['source_support'][0]['sha256']='pdf-b'
    with pytest.raises(ValueError, match='ownership'):
        T.validate_registry_coverage(jobs,catalog,reviews,{'a':'pdf-a'},lambda proof:None)


def test_complete_registry_coverage_is_not_a_quality_filter():
    jobs,catalog,reviews=registry_fixture()
    catalog['records'][0]['relevant']=False
    reviews['records'][0]['card_reviews'][0]['relevant']=False
    result=T.validate_registry_coverage(jobs,catalog,reviews,{'a':'pdf-a'},lambda proof:None)
    assert result['jobs']==1 and result['query_cards']==1


def test_registry_source_conditions_cannot_change_by_search_arm():
    jobs,catalog,reviews=registry_fixture()
    reviews['records'][0]['card_reviews'][0]['facts'][0]['source_statement']='다른 규칙'
    with pytest.raises(ValueError, match='source condition'):
        T.validate_registry_coverage(jobs,catalog,reviews,{'a':'pdf-a'},lambda proof:None)


def test_status_only64_acceptance_does_not_open65(tmp_path,monkeypatch):
    monkeypatch.setattr(T,'B64',tmp_path)
    (tmp_path/'completion.json').write_text(json.dumps(dict(status='accepted_scope_and_retrieval_review',files_sha256={})))
    with pytest.raises(ValueError, match='64 acceptance'):
        T.accepted64_closure()


def cache_fixture(tmp_path,monkeypatch):
    class Schema:
        @staticmethod
        def model_json_schema():
            return {'type':'object'}
    normalized=dict(model='gpt-6-luna',reasoning={'effort':'medium'},max_output_tokens=3000,
                    instructions='fixed',input=[{'role':'user','content':'coffee'}],text_format=Schema)
    cache=object.__new__(T.Cache)
    cache.stage=65;cache.kind='parser';cache.identity='q:parser:r1';cache.cache=tmp_path
    cache.check_contract=lambda value:value
    cache.wire_kwargs=lambda value:{'input':'coffee'}
    cache.manifest=dict(source_sha256={},allowed_identities={'parser':[cache.identity]},
        allowed_request_by_identity={cache.identity:T.P.sha(normalized['input'])},
        parser_instructions_sha256=T.P.sha('fixed'),parser_schema_sha256=T.P.sha(Schema.model_json_schema()),
        maximum_parser_requests=288,maximum_physical_requests=1442,stage_cost_cap_usd='8')
    return cache,normalized


def test_third_attempt_is_rejected_before_any_network(tmp_path,monkeypatch):
    cache,normalized=cache_fixture(tmp_path,monkeypatch)
    for i in range(2):
        (tmp_path/f'{i}.json').write_text(json.dumps(dict(experiment_identity=cache.identity,request={'max_output_tokens':3000})))
    with pytest.raises(RuntimeError,match='attempt cap'):
        cache.reserve(normalized)


def test_global_cap_counts_conservative_prior_debit(tmp_path,monkeypatch):
    cache,normalized=cache_fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(T.P,'ledger',lambda:dict(known_cost_usd='7.9999',unknown_cost_records=0))
    with pytest.raises(RuntimeError,match='8 USD'):
        cache.reserve(normalized)
    assert not list(tmp_path.glob('*.json'))


def test_new_unknown_usage_stops_before_a_following_paid_request(tmp_path,monkeypatch):
    cache,normalized=cache_fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(T.P,'ledger',lambda:dict(known_cost_usd='1.289765390',unknown_cost_records=1))
    with pytest.raises(RuntimeError,match='Unknown'):
        cache.reserve(normalized)


def test_empty_preanswer_acceptance_cannot_open_answer_calls(tmp_path,monkeypatch):
    monkeypatch.setattr(T,'B65',tmp_path)
    monkeypatch.setattr(T.X,'guard_manifest',lambda value:None)
    monkeypatch.setattr(T.R,'guard_files',lambda value:None)
    (tmp_path/'source_registry_acceptance.json').write_text(json.dumps(dict(
        status='accepted_before_answer_generation',files_sha256={},jobs_raw_sha256='claimed')))
    with pytest.raises(ValueError,match='registry acceptance'):
        T.preanswer_closure([],dict(critical_data_sha256={}))


def test_postanswer_grade_cannot_drop_a_frozen_same_card_and_condition():
    _,catalog,_=registry_fixture()
    card=catalog['records'][0]
    second=dict(card['facts'][0],condition_id='netflix',benefit_id='netflix')
    card['facts'].append(second);card['core_paths']=[['coffee','netflix']]
    manual=dict(query_id='q',group='explicit',core_paths=[dict(card_keys=['a'],requirements=[dict(
        card_key='a',condition_id='coffee',statement=card['facts'][0]['source_statement'],
        source_status='verified',source_support=card['facts'][0]['source_support'])])])
    with pytest.raises(ValueError,match='predeclared core path'):
        T.validate_core_paths(manual,catalog,{'a'})


def test_stale64_file_is_rejected_even_with_accepted_status(tmp_path,monkeypatch):
    base=tmp_path/'b64';base.mkdir()
    monkeypatch.setattr(T,'ROOT',tmp_path);monkeypatch.setattr(T.R,'ROOT',tmp_path);monkeypatch.setattr(T,'B64',base)
    files={}
    for name in ['evaluation.json','retrieval_results.json','jobs.json','scope_comparisons.json','source_review/retrieval_judgments.json']:
        p=base/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{}')
        files[str(p.relative_to(tmp_path))]=T.P.sha(p.read_bytes())
    review=tmp_path/'review.json';review.write_text(json.dumps(dict(status='accepted',reviewed_files_sha256=files)))
    files['review.json']=T.P.sha(review.read_bytes())
    completion=dict(status='accepted_scope_and_retrieval_review',files_sha256=files,independent_review_path='review.json',
        independent_review_raw_sha256=files['review.json'],evaluation_raw_sha256=files['b64/evaluation.json'],
        retrieval_raw_sha256=files['b64/retrieval_results.json'],source_review_raw_sha256=files['b64/source_review/retrieval_judgments.json'])
    (base/'completion.json').write_text(json.dumps(completion))
    (base/'retrieval_results.json').write_text('{"changed":true}')
    with pytest.raises(RuntimeError,match='Frozen data changed'):
        T.accepted64_closure()


def test_postanswer_registry_gate_and_manual_map_rewrite_is_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(T,'ROOT',tmp_path);monkeypatch.setattr(T.R,'ROOT',tmp_path);monkeypatch.setattr(T,'B65',tmp_path)
    registry=tmp_path/'source_registry.json';registry.write_text('{"before":true}')
    gate=tmp_path/'source_registry_acceptance.json';gate.write_text('{"version":1}')
    manifest={'stage':65}
    phase=dict(execution_manifest_sha256=T.P.sha(manifest),critical_data_sha256={
        'source_registry.json':T.P.sha(registry.read_bytes()),'source_registry_acceptance.json':T.P.sha(gate.read_bytes())})
    path=tmp_path/'answer_phase_manifest.json';path.write_text(json.dumps(phase))
    (tmp_path/'answers').mkdir();(tmp_path/'answers/a.json').write_text(json.dumps(dict(answer_phase_manifest_sha256=T.P.sha(path.read_bytes()))))
    registry.write_text('{"after":true}');gate.write_text('{"version":2}')
    phase['critical_data_sha256']={n:T.P.sha((tmp_path/n).read_bytes()) for n in phase['critical_data_sha256']}
    path.write_text(json.dumps(phase));(tmp_path/'manual_judgments.json').write_text(json.dumps(dict(source_files_sha256=phase['critical_data_sha256'])))
    with pytest.raises(ValueError,match='answer phase'):
        T.guard_answer_phase(manifest)


def test_unstamped_crash_checkpoint_needs_validation_before_stamp(tmp_path,monkeypatch):
    monkeypatch.setattr(T,'ROOT',tmp_path);monkeypatch.setattr(T.R,'ROOT',tmp_path);monkeypatch.setattr(T,'B65',tmp_path)
    manifest={'stage':65};phase=dict(execution_manifest_sha256=T.P.sha(manifest),critical_data_sha256={})
    (tmp_path/'answer_phase_manifest.json').write_text(json.dumps(phase));(tmp_path/'answers').mkdir()
    path=tmp_path/'answers/q.json';job=dict(identity='q',question='커피',result=dict(evidence=[]))
    row=dict(identity='q',evidence_sha256=T.P.sha([]),retrieval_sha256=T.P.sha(job['result']))
    path.write_text(json.dumps(row));phase_sha=T.guard_answer_phase(manifest,allow_unstamped=True)
    monkeypatch.setattr(T,'write',T.E.write)
    def invalid(*args,**kwargs):
        raise ValueError('raw provider mismatch')
    monkeypatch.setattr(T.G.M.R,'validate_generated_answer',invalid)
    with pytest.raises(ValueError,match='raw provider mismatch'):
        T.validate_and_stamp(row,job,manifest,phase_sha)
    assert 'answer_phase_manifest_sha256' not in json.loads(path.read_text())
    checked=[]
    monkeypatch.setattr(T.G.M.R,'validate_generated_answer',lambda *a,**kw:checked.append('raw_validated'))
    monkeypatch.setattr(T.G,'validate_checkpoint_prompt',lambda *a,**kw:checked.append('prompt_validated'))
    T.validate_and_stamp(row,job,manifest,phase_sha)
    assert checked==['raw_validated','prompt_validated']
    assert json.loads(path.read_text())['answer_phase_manifest_sha256']==phase_sha
    assert T.guard_answer_phase(manifest)==phase_sha
