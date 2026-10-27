"""Exact source/checkpoint reuse must not conceal changed questions or scope."""
import copy
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

s=importlib.util.spec_from_file_location('runtime70_test',Path(__file__).with_name('70_execution_runtime.py'))
M=importlib.util.module_from_spec(s);s.loader.exec_module(M)


def test_stage70_is_in_the_same_budget_scan_and_actual_cache_registries():
    assert M.T.LEAVES[70]==M.LEAF
    assert all(shared.STAGES[70]==M.BASE for shared in M.T.stage_registries())
    assert M.R.P.STAGES[70]==M.R.X.STAGES[70]==M.LEAF


@pytest.fixture
def prior(tmp_path,monkeypatch):
    monkeypatch.setattr(M.T,'ROOT',tmp_path)
    f=tmp_path/'fixed';f.write_text('operating-index-and-code')
    manifest=dict(source_sha256={'fixed':M.T.E.file_digest(f)},notebook_phase_code_sha256={},parameters={'topk':3})
    q=dict(query_id='Q',question='커피 할인');v=np.zeros(1536,dtype=np.float32)
    parser=dict(plan={'groups':[]},status='ok');parsers={('Q',1):parser}
    route=dict(scope=['issuer/A'],route='SEQ',db_wall_seconds=.1)
    row=dict(identity='Q:profile:SEQ_REPAIRED:r1',query_id='Q',question=q['question'],profile='profile',mode='SEQ_REPAIRED',repeat=1,
             routing=route,original_scope=dict(card_keys=None,clarify=False),result=dict(cards=[{'card_key':'issuer/A'}],evidence=[{'card_key':'issuer/A','text':'커피10%'}]))
    row['binding']=M.R.P.sha(dict(question=q,vector=M.R.P.sha(v.tobytes()),profile='profile',repeat=1,
        routing={k:w for k,w in route.items()if k!='db_wall_seconds'},original=row['original_scope'],parser_binding=M.R.P.sha(parser),
        manifest=M.R.P.sha(manifest),mode=row['mode']))
    (tmp_path/'retrievals').mkdir()
    (tmp_path/'retrievals'/(row['identity']+'.seal.json')).write_text(json.dumps({'row_sha256':M.R.P.sha(row)}))
    return tmp_path,manifest,q,v,parsers,row


def call(prior,**changes):
    base,manifest,q,v,parsers,row=prior
    values=dict(rows=[row],source_base=base,source_manifest=manifest,target_manifest=copy.deepcopy(manifest),queries=[q],vectors=[v],parsers=parsers)
    values.update(changes)
    return M.validated_prior_searches(**values)


def test_exact_prior_search_can_be_reused_and_remains_separate_from_scope_changes(prior):
    found=call(prior);row=prior[-1];q=prior[2];v=prior[3]
    key=M.search_key(q['question'],v,'profile',['issuer/A'],False)
    assert found[key]['result']==row['result']
    assert M.search_key(q['question'],v,'profile',None,False) not in found
    assert M.search_key(q['question'],v,'profile',['issuer/B'],False) not in found


@pytest.mark.parametrize('change',['question','vector','parser','profile_parameters','row','source'])
def test_changed_provenance_is_rejected_before_reuse(prior,change):
    base,manifest,q,v,parsers,row=prior
    if change=='question': q['question']='넷플릭스 할인'
    elif change=='vector': v[0]=1
    elif change=='parser': parsers['Q',1]['status']='failed'
    elif change=='profile_parameters':
        target=copy.deepcopy(manifest);target['parameters']['topk']=5
        with pytest.raises(RuntimeError):call(prior,target_manifest=target)
        return
    elif change=='row': row['result']['evidence'][0]['text']='새 근거'
    else:(base/'fixed').write_text('changed-index')
    with pytest.raises((RuntimeError,ValueError)):call(prior)


def test_reuse_is_a_copy_and_cannot_mutate_the_original_checkpoint(prior):
    row=prior[-1];found=call(prior)
    next(iter(found.values()))['result']['evidence'][0]['text']='local modification'
    assert row['result']['evidence'][0]['text']=='커피10%'


def test_scoped_sequential_U_result_cannot_fill_global_U_cache_key(prior):
    # SEQ row.result is scoped U *before* structured facts are appended.
    base,manifest,q,v,parsers,row=prior
    found=call(prior)
    assert row['mode']=='SEQ_REPAIRED'
    assert M.search_key(q['question'],v,row['profile'],None,False) not in found
    assert M.search_key(q['question'],v,row['profile'],['issuer/A'],False) in found


def test_prior_search_must_not_contain_already_appended_structured_fact(prior):
    base,manifest,q,v,parsers,row=prior
    row['result']['evidence'][0]['origin']='structured'
    (base/'retrievals'/(row['identity']+'.seal.json')).write_text(json.dumps({'row_sha256':M.R.P.sha(row)}))
    with pytest.raises(RuntimeError,match='structured fact'):call(prior)


def suffix_fixture():
    owner=dict(card_key='issuer/A',card_name='A',issuer='issuer')
    fact=dict(fact_id='f',document_id='issuer/A',ordinal=1,fields={'cap':'원문'},derived={},
              relation_scope={},field_evidence={},source_relative_path='source',source_path_sha256='source_sha',source_pdf_sha256='pdf_sha')
    row=dict(identity='Q:profile:SEQ_REPAIRED:r1',query_id='Q',question='커피',profile='profile',mode='SEQ_REPAIRED',repeat=1,
             routing=dict(matching_fact_ids={'issuer/A':['f']},requirement_fact_ids={},plan={'groups':[]}),
             result=dict(cards=[owner],evidence=[dict(**owner,chunk_id='u',text='원U')]))
    canonical=M.F.canonical_records(row['result'],row['routing'],{'f':fact})[0]
    suffix=dict(**owner,chunk_id='s68:f',text=M.F.render_json([canonical]))
    job={k:row[k]for k in('identity','query_id','question','profile','mode','repeat')}
    job.update(fixed_search_identity=row['identity'],retrieval_row_sha256=M.R.P.sha(row),result=copy.deepcopy(row['result']))
    job['result']['evidence'].append(suffix)
    return job,row,{'f':fact}


def test_duplicate_fact_suffix_is_rejected_even_when_both_copies_are_source_correct():
    job,row,facts=suffix_fixture()
    assert len(M.reconstruct_inventory([job],[row],facts))==1
    job['result']['evidence'].append(copy.deepcopy(job['result']['evidence'][-1]))
    with pytest.raises((AssertionError,RuntimeError)):
        M.reconstruct_inventory([job],[row],facts)


@pytest.fixture
def source_review(tmp_path):
    import fitz,hashlib
    job,row,facts=suffix_fixture();fact=facts['f']
    p=tmp_path/'data/raw/issuer/A.pdf';p.parent.mkdir(parents=True)
    with fitz.open()as doc:
        page=doc.new_page();page.insert_text((72,72),'Coffee discount 10 percent');doc.save(p)
    sha=hashlib.sha256(p.read_bytes()).hexdigest();fact['source_pdf_sha256']=sha
    proof=dict(path='data/raw/issuer/A.pdf',sha256=sha,page=1,quote='Coffee discount 10 percent');key=M.R.P.sha(proof)
    record=dict(fact_id='f',card_key='issuer/A',database_fact_sha256=M.R.P.sha(fact),fields=copy.deepcopy(fact['fields']),derived={},
                relation_scope={},field_evidence_sha256=M.R.P.sha({}),confirmed_conflict_fields=[],source_status='reviewed_source_supported_with_disclosed_omissions',
                source_proof_ids=[key],registered_visual_review_ids=[],field_reviews=[dict(field_path='fields.cap',stored_value=fact['fields']['cap'],status='source_supported',source_proof_ids=[key],registered_visual_review_ids=[])])
    review=dict(records=[record],source_proof_catalog={key:proof},registered_visual_reviews={},files_sha256={'data/raw/issuer/A.pdf':sha})
    actual=[dict(fact_id='f',card_key='issuer/A',database_fact_sha256=M.R.P.sha(fact),database_fact=fact)]
    return tmp_path,actual,review


def test_source_gate_checks_actual_records_in_addition_to_summary_maps(source_review):
    root,actual,review=source_review
    M.validate_source_records(actual,review,root)


@pytest.mark.parametrize('change',['missing_record','owner','sha','no_proof','field_value','conflict'])
def test_source_gate_rejects_incomplete_or_wrong_actual_source_record(source_review,change):
    root,actual,review=source_review;record=review['records'][0]
    if change=='missing_record':review['records']=[]
    elif change=='owner':record['card_key']='issuer/B'
    elif change=='sha':record['database_fact_sha256']='wrong'
    elif change=='no_proof':record['source_proof_ids']=[];record['field_reviews'][0]['source_proof_ids']=[]
    elif change=='field_value':record['field_reviews'][0]['stored_value']='different'
    else:record['field_reviews'][0]['status']='confirmed_conflict'
    with pytest.raises((ValueError,RuntimeError,AssertionError)):
        M.validate_source_records(actual,review,root)


@pytest.fixture
def visual_source_review(source_review):
    import fitz
    root,actual,review=source_review;record=review['records'][0]
    pdf=root/'data/raw/issuer/A.pdf'
    with fitz.open(pdf)as doc:
        image=doc[0].get_pixmap(matrix=fitz.Matrix(2,2)).tobytes('png')
    path=root/'source.png';path.write_bytes(image)
    visual=dict(card_key='issuer/A',pdf_path='data/raw/issuer/A.pdf',pdf_sha256=M.T.E.file_digest(pdf),page=1,
                image_path='source.png',image_sha256=M.T.E.file_digest(path),render_scale=2,visually_reviewed=True,
                reviewed_by='test fixture reviewer',observation='Coffee discount 10 percent')
    key=M.R.P.sha(visual);review['registered_visual_reviews']={key:visual}
    review['files_sha256']['source.png']=M.T.E.file_digest(path)
    record['source_proof_ids']=[];record['registered_visual_review_ids']=[key]
    field=record['field_reviews'][0];field['source_proof_ids']=[];field['registered_visual_review_ids']=[key]
    field['visual_field_observation']=dict(fact_id='f',field_path='fields.cap',stored_value_sha256=M.R.P.sha(field['stored_value']),
        database_fact_sha256=record['database_fact_sha256'],relation_scope_sha256=M.R.P.sha({}),field_evidence_sha256=M.R.P.sha({}),
        visual_review_ids=[key],reviewed_by='test fixture reviewer',observed_text='Coffee discount 10 percent',
        manual_relation_reason='Fixture field interpretation of this exact page; not automatic visual truth certification')
    receipt=root/'visual_receipt.json';receipt.write_text(json.dumps({'observations':[field['visual_field_observation']]}))
    review['visual_field_receipt']=dict(path='visual_receipt.json',raw_sha256=M.T.E.file_digest(receipt))
    review['files_sha256']['visual_receipt.json']=M.T.E.file_digest(receipt)
    return root,actual,review


def test_visual_only_review_binds_exact_page_render_and_field_observation(visual_source_review):
    root,actual,review=visual_source_review
    M.validate_source_records(actual,review,root)


@pytest.mark.parametrize('change',['wrong_page','wrong_render','unrelated_field','missing_observation'])
def test_visual_only_review_rejects_wrong_page_render_or_field_receipt(visual_source_review,change):
    import fitz
    root,actual,review=visual_source_review;record=review['records'][0];visual=next(iter(review['registered_visual_reviews'].values()))
    if change=='wrong_page':visual['page']=2
    elif change=='wrong_render':
        with fitz.open(root/visual['pdf_path'])as doc:image=doc[0].get_pixmap(matrix=fitz.Matrix(1,1)).tobytes('png')
        (root/'source.png').write_bytes(image);visual['image_sha256']=M.T.E.file_digest(root/'source.png')
        review['files_sha256']['source.png']=visual['image_sha256']
    elif change=='unrelated_field':record['field_reviews'][0]['visual_field_observation']['field_path']='derived.spend_state'
    else:record['field_reviews'][0].pop('visual_field_observation')
    # Keep the visual dictionary's self-hash valid, so page/render checks must
    # reject changes independently of a stale dictionary key.
    newkey=M.R.P.sha(visual);review['registered_visual_reviews']={newkey:visual}
    record['registered_visual_review_ids']=[newkey];record['field_reviews'][0]['registered_visual_review_ids']=[newkey]
    if 'visual_field_observation'in record['field_reviews'][0]:record['field_reviews'][0]['visual_field_observation']['visual_review_ids']=[newkey]
    with pytest.raises((ValueError,RuntimeError,AssertionError,IndexError)):
        M.validate_source_records(actual,review,root)


@pytest.mark.parametrize('change',['missing_receipt','missing_field','inline_mismatch'])
def test_separate_visual_receipt_and_inline_observation_must_agree(visual_source_review,change):
    root,actual,review=visual_source_review
    if change=='missing_receipt':(root/'visual_receipt.json').unlink()
    elif change=='missing_field':
        (root/'visual_receipt.json').write_text(json.dumps({'observations':[]}))
        digest=M.T.E.file_digest(root/'visual_receipt.json')
        review['visual_field_receipt']['raw_sha256']=review['files_sha256']['visual_receipt.json']=digest
    else:review['records'][0]['field_reviews'][0]['visual_field_observation']['manual_relation_reason']='Different observation'
    with pytest.raises((AssertionError,ValueError,FileNotFoundError)):
        M.validate_source_records(actual,review,root)
