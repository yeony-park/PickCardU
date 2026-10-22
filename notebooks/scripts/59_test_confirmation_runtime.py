"""Confirmation chronology and question-stratum regressions; no paid calls."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

spec=importlib.util.spec_from_file_location('confirmation59_tests',Path(__file__).with_name('59_confirmation_runtime.py'))
N=importlib.util.module_from_spec(spec);sys.modules[spec.name]=N;spec.loader.exec_module(N)


def sample():
    rows=[]
    for group,count in N.GROUP_COUNTS.items():
        for i in range(count):
            rows.append(dict(query_id=f'N{len(rows)+1:02}',question=group+str(i)+' 혜택 질문',subgroup=group))
    contract=dict(selected_at_epoch=10,config_id='old:A:A')
    payload=dict(configuration_contract_sha256=N.P.sha(contract),created_at_epoch=11,queries=rows)
    return payload,contract


def test_questions_must_follow_configuration_freeze_and_match_fixed32_strata():
    payload,contract=sample()
    N.validate_questions(payload,contract,[])
    with pytest.raises(ValueError,match='chronology'):
        N.validate_questions({**payload,'created_at_epoch':9},contract,[])
    with pytest.raises(ValueError,match='strata'):
        N.validate_questions({**payload,'queries':payload['queries'][:-1]},contract,[])


def test_question_or_id_reuse_cannot_be_reported_as_new_confirmation():
    payload,contract=sample()
    with pytest.raises(ValueError,match='previous'):
        N.validate_questions(payload,contract,[payload['queries'][0]])
    bad={**payload,'queries':[dict(r) for r in payload['queries']]}
    bad['queries'][1]['query_id']=bad['queries'][0]['query_id']
    with pytest.raises(ValueError,match='unique'):
        N.validate_questions(bad,contract,[])


def test_checkpoint_identity_profile_and_card_scope_are_checked_on_resume():
    row=dict(identity='q:benefit:A:old:r1',query_id='q',profile='benefit',repeat=1,binding='bound',
        result=dict(cards=[dict(card_key='in')],evidence=[]))
    seal=dict(binding='bound',row_sha256=N.P.sha(row))
    N.validate_retrieval_checkpoint(row,seal,'bound',row['identity'],'q','benefit',1,['in'])
    with pytest.raises(RuntimeError,match='checkpoint changed'):
        N.validate_retrieval_checkpoint(row,seal,'bound','q:parent:B:old:r1','q','parent',1,['in'])
    with pytest.raises(RuntimeError,match='escaped'):
        N.validate_retrieval_checkpoint(row,seal,'bound',row['identity'],'q','benefit',1,['different'])


def test_uncertain_source_is_reserved_for_the_two_negative_questions():
    q=dict(question='커피 혜택 카드',subgroup='recommend_single')
    ref=dict(question_sha256=N.P.sha(q['question']),reviewed_by='main Codex',reason='원문 검토',
        facts=[dict(source_status='uncertain')])
    with pytest.raises(RuntimeError,match='unresolved source'):
        N.validate_reference(q,ref)
    N.validate_reference({**q,'subgroup':'negative_uncertain'},ref)


@pytest.fixture
def selection_seal(tmp_path,monkeypatch):
    stage=tmp_path/'stage58'
    monkeypatch.setattr(N.R,'ROOT',tmp_path)
    monkeypatch.setattr(N.G,'BASE',stage)
    monkeypatch.setitem(N.P.F.STAGES,58,stage)
    monkeypatch.setattr(N.P.F,'ROOT',tmp_path)
    selection=dict(status='selected_after_source_fact_review',selected_config_id='old:A:A',
        configurations=[dict(config_id='old:A:A',eligible=True,failures=[])])
    answers={'records':[]};judgments={'records':[]};evaluation={'records':[]}
    review_path='stage58/independent_review.json'

    def seal(selection_value=None,review_change=None):
        selected=selection_value or selection
        documents={'configuration_selection.json':selected,'answer_results.json':answers,
            'source_judgments.json':judgments,'fact_evaluation.json':evaluation}
        for name,value in documents.items():N.R.write(stage/name,value)
        files={f'stage58/{name}':N.P.sha((stage/name).read_bytes()) for name in documents}
        bindings=dict(configuration_selection_sha256=N.P.sha(selected),answer_results_sha256=N.P.sha(answers),
            source_judgments_sha256=N.P.sha(judgments),fact_evaluation_sha256=N.P.sha(evaluation))
        review=dict(status='accepted',reviewer='registered Codex reviewer',independent_review=True,
            blocking_findings=0,major_findings=0,bindings=bindings,reviewed_evaluation_files_sha256=files)
        if review_change:review_change(review)
        N.R.write(tmp_path/review_path,review)
        receipt_hash=N.P.sha((tmp_path/review_path).read_bytes())
        completion=dict(status='completed_after_independent_review',
            configuration_selection_sha256=N.P.sha(selected),answer_results_sha256=N.P.sha(answers),
            judgments_sha256=N.P.sha(judgments),fact_evaluation_sha256=N.P.sha(evaluation),
            independent_review_path=review_path,independent_review_file_sha256=receipt_hash,
            evaluation_files_sha256={**files,review_path:receipt_hash})
        return selected,completion,answers,judgments
    return seal


def test_configuration_freeze_requires_bound_accepted_independent_review(selection_seal):
    args=selection_seal();N.validate_selection_gate(*args)
    with pytest.raises(RuntimeError):
        N.validate_selection_gate(args[0],{**args[1],'status':'draft'},args[2],args[3])
    with pytest.raises(RuntimeError):
        N.validate_selection_gate(args[0],args[1],args[2],{'changed':'manual judgment'})


@pytest.mark.parametrize('problem',['missing','duplicate','ineligible','failures'])
def test_resealed_ineligible_selection_cannot_open_confirmation(selection_seal,problem):
    selected=selection_seal()[0]
    if problem=='missing':selected['selected_config_id']='absent'
    elif problem=='duplicate':selected['configurations']*=2
    elif problem=='ineligible':selected['configurations'][0]['eligible']=False
    else:selected['configurations'][0]['failures']=['new_important_error']
    args=selection_seal(selected)
    with pytest.raises(RuntimeError):N.validate_selection_gate(*args)


@pytest.mark.parametrize('problem',['draft','no_reviewer','not_independent','blocking','major','stale_binding','missing_core_file','wrong_file_hash'])
def test_resealed_unaccepted_or_unbound_review_cannot_open_confirmation(selection_seal,problem):
    def mutate(review):
        if problem=='draft':review['status']='draft'
        elif problem=='no_reviewer':review['reviewer']=''
        elif problem=='not_independent':review['independent_review']=False
        elif problem=='blocking':review['blocking_findings']=1
        elif problem=='major':review['major_findings']=1
        elif problem=='stale_binding':review['bindings']['source_judgments_sha256']='old'
        elif problem=='missing_core_file':review['reviewed_evaluation_files_sha256'].pop('stage58/fact_evaluation.json')
        else:review['reviewed_evaluation_files_sha256']['stage58/fact_evaluation.json']='wrong'
    args=selection_seal(review_change=mutate)
    with pytest.raises(RuntimeError):N.validate_selection_gate(*args)


def test_review_receipt_and_reviewed_files_are_checked_from_disk(selection_seal):
    args=selection_seal();completion=args[1];review_path=completion['independent_review_path']
    without_receipt={**completion,'evaluation_files_sha256':{k:v for k,v in completion['evaluation_files_sha256'].items() if k!=review_path}}
    with pytest.raises(RuntimeError):N.validate_selection_gate(args[0],without_receipt,args[2],args[3])
    N.R.write(N.G.BASE/'source_judgments.json',{'changed':'after review'})
    with pytest.raises(RuntimeError):N.validate_selection_gate(*args)


@pytest.fixture
def embedding_setup(tmp_path,monkeypatch):
    monkeypatch.setattr(N,'BASE',tmp_path)
    monkeypatch.setitem(N.P.F.STAGES,59,tmp_path)
    monkeypatch.setattr(N.P.F,'ROOT',tmp_path)
    monkeypatch.setattr(N.X,'guard_manifest',lambda m:None)
    monkeypatch.setattr(N.R,'guard_files',lambda m:None)
    monkeypatch.setattr(N.X,'budget_upper_ledger',lambda:dict(known_cost_usd='0',unknown_cost_records=0))
    monkeypatch.setattr(N.R,'phase_price',lambda stages:dict(known_cost_usd='0',unknown_cost_records=0))
    queries=sample()[0]['queries'];calls=[]
    request=dict(model='text-embedding-3-small',input=[q['question'] for q in queries],dimensions=1536)
    raw_response=dict(data=[dict(index=i,embedding=[float(i)]*1536) for i in range(32)],usage={'total_tokens':32,'prompt_tokens':32})
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(model_dump=lambda **kwargs:raw_response)
    monkeypatch.setattr(N.P.E.C,'api_client',lambda:SimpleNamespace(embeddings=SimpleNamespace(create=create)))
    manifest=dict(critical_data_sha256={},maximum_physical_requests=481)
    target=tmp_path/'api_cache'/('embedding_batch_'+N.P.sha(request)+'.json')
    return queries,manifest,target,request,calls


def test_successful_embedding_raw_restores_vectors_without_second_api_call(embedding_setup):
    import numpy as np
    queries,manifest,target,request,calls=embedding_setup
    first=N.embeddings(queries,manifest,allow_network=True)
    second=N.embeddings(queries,manifest,allow_network=False)
    assert np.array_equal(first,second) and len(calls)==1
    assert N.R.read(target)['request']==request


@pytest.mark.parametrize('status',['started','failed'])
def test_unfinished_embedding_never_retries_the_provider(embedding_setup,status):
    queries,manifest,target,request,calls=embedding_setup
    N.R.write(target,dict(status=status,request=request))
    with pytest.raises(RuntimeError,match='no automatic retry'):
        N.embeddings(queries,manifest,allow_network=True)
    assert not calls


def test_embedding_vector_and_contract_mutation_are_rejected(embedding_setup):
    import numpy as np
    queries,manifest,target,request,calls=embedding_setup
    matrix=N.embeddings(queries,manifest,allow_network=True)
    np.save(N.BASE/'query_vectors.npy',matrix+1)
    with pytest.raises(RuntimeError,match='differ from provider'):
        N.embeddings(queries,manifest,allow_network=False)
    np.save(N.BASE/'query_vectors.npy',matrix)
    N.R.write(N.BASE/'embedding_contract.json',dict(wrong='contract'))
    with pytest.raises(RuntimeError,match='Frozen input changed'):
        N.embeddings(queries,manifest,allow_network=False)
    assert len(calls)==1


@pytest.mark.parametrize('problem',['global_unknown','phase_cap'])
def test_embedding_budget_failure_precedes_provider_and_started_journal(embedding_setup,monkeypatch,problem):
    queries,manifest,target,request,calls=embedding_setup
    if problem=='global_unknown':
        monkeypatch.setattr(N.X,'budget_upper_ledger',lambda:dict(known_cost_usd='0',unknown_cost_records=1))
    else:
        monkeypatch.setattr(N.R,'phase_price',lambda stages:dict(known_cost_usd='2',unknown_cost_records=0))
    with pytest.raises(RuntimeError):N.embeddings(queries,manifest,allow_network=True)
    assert not calls and not target.exists()
