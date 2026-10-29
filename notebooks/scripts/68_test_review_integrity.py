"""Regression cases for supplemental proof ownership and negative-claim binding."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import fitz
import pytest


def module():
    path = Path(__file__).with_name('68_review_integrity.py')
    assert path.exists(), 'Missing evaluator integrity gate'
    spec = importlib.util.spec_from_file_location('integrity68_test', path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def fixture(tmp_path):
    pdf = tmp_path/'data/raw/issuer/card.pdf'
    pdf.parent.mkdir(parents=True)
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72,72), 'Coffee discount 10 percent')
        doc.save(pdf)
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    primary = dict(records=[dict(card_key='issuer/card',source_pdf_sha256=sha,facts=[])])
    fact = dict(condition_id='issuer/card:coffee.rate',source_status='verified',source_statement='Coffee discount 10 percent',source_support=[dict(path='data/raw/issuer/card.pdf',sha256=sha,page=1,quote='Coffee discount 10 percent')])
    supplement = dict(records=[dict(card_key='issuer/card',source_pdf_sha256=sha,facts=[fact])],files_sha256={})
    for name, value in [('primary.json',primary),('supplement.json',supplement)]:
        (tmp_path/name).write_text(json.dumps(value))
    return primary,supplement


def test_proof_union_binds_owning_pdf_and_literal(tmp_path):
    # Removing proof validation would allow an unverified reference to enter scoring.
    fixture(tmp_path);m=module()
    union=m.source_union(tmp_path/'primary.json',tmp_path/'supplement.json',tmp_path)
    refs=m.verify_union(union,tmp_path)
    assert set(refs)=={('issuer/card','issuer/card:coffee.rate')}
    assert refs[('issuer/card','issuer/card:coffee.rate')]['source_status']=='verified'


@pytest.mark.parametrize('change',['quote','pdf_owner','missing_card'])
def test_union_rejects_invalid_source_or_incomplete_coverage(tmp_path,change):
    primary,supplement=fixture(tmp_path)
    if change=='quote':supplement['records'][0]['facts'][0]['source_support'][0]['quote']='Invented benefit'
    elif change=='pdf_owner':supplement['records'][0]['card_key']='issuer/other'
    else:supplement['records']=[]
    (tmp_path/'supplement.json').write_text(json.dumps(supplement))
    with pytest.raises(ValueError):module().source_union(tmp_path/'primary.json',tmp_path/'supplement.json',tmp_path)


def test_union_rejects_post_freeze_status_change(tmp_path):
    fixture(tmp_path);m=module();union=m.source_union(tmp_path/'primary.json',tmp_path/'supplement.json',tmp_path)
    union['records'][0]['source_status']='uncertain'
    with pytest.raises(ValueError):m.verify_union(union,tmp_path)


def test_uncertain_reference_cannot_support_correct_span(tmp_path):
    fixture(tmp_path);m=module();union=m.source_union(tmp_path/'primary.json',tmp_path/'supplement.json',tmp_path)
    refs=m.verify_union(union,tmp_path);refs[('issuer/card','issuer/card:coffee.rate')]['source_status']='uncertain'
    review=dict(surface_audits=[dict(spans=[dict(kind='factual',status='correct',quote='Coffee discount',source_refs=[dict(card_key='issuer/card',condition_id='issuer/card:coffee.rate',quote='Coffee discount')])])])
    with pytest.raises(ValueError):m.verify_review_sources(review,refs)


def input_scope(m):
    job=dict(question='Recommend coffee card',result=dict(evidence=[dict(card_key='issuer/card',chunk_id='one',text='Coffee benefit')]))
    scope=dict(kind='input',attributes=['discount_percent'],criterion='Read the entire supplied card evidence for a numeric discount linked to coffee.',finding='not_stated',reviewed_by='main Codex',reason='Only a target is supplied.',question_sha256=m.E.digest(job['question']),card_keys=['issuer/card'],evidence_sha256_by_id={'one':m.E.digest('Coffee benefit')})
    return job,scope


def test_absence_proof_requires_entire_current_owning_input():
    m=module();job,scope=input_scope(m)
    m.verify_absence_scope(job,scope)
    job['result']['evidence'].append(dict(card_key='issuer/card',chunk_id='two',text='10% discount'))
    with pytest.raises(ValueError):m.verify_absence_scope(job,scope)


def test_question_absence_cannot_reuse_prior_question():
    m=module();job,scope=input_scope(m);scope.update(kind='question',attributes=['preferred_merchants'],card_keys=[],evidence_sha256_by_id={})
    m.verify_absence_scope(job,scope);job['question']='Recommend coffee card for Starbucks'
    with pytest.raises(ValueError):m.verify_absence_scope(job,scope)


def test_visible_absence_statement_requires_scope_receipt():
    m=module();job,scope=input_scope(m)
    review=dict(surface_audits=[dict(spans=[dict(kind='factual',quote='제공된 근거에서는 할인율을 확인할 수 없어요.',card_keys=['issuer/card'])])])
    with pytest.raises(ValueError):m.verify_review_absences(job,review)
    review['surface_audits'][0]['spans'][0]['absence_scope']=[scope]
    m.verify_review_absences(job,review)


def test_union_cannot_reuse_receipts_from_changed_proof_validator(tmp_path):
    fixture(tmp_path);m=module();union=m.source_union(tmp_path/'primary.json',tmp_path/'supplement.json',tmp_path)
    union['proof_validator_sha256']='0'*64
    with pytest.raises(ValueError):m.verify_union(union,tmp_path)


def test_literal_user_question_is_not_an_absence_assertion():
    m=module();job,scope=input_scope(m)
    review=dict(surface_audits=[dict(spans=[dict(kind='question_context',quote=job['question'])])])
    m.verify_review_absences(job,review)


def test_comparative_factual_uncertainty_cannot_drop_required_scope():
    m=module();job,scope=input_scope(m)
    review=dict(surface_audits=[dict(spans=[dict(kind='factual',quote='세 카드의 조건이 달라 절대적인 1위로 비교하기는 어렵습니다.',card_keys=['issuer/card'],absence_scope_required=True)])])
    with pytest.raises(ValueError):m.verify_review_absences(job,review)
    review['surface_audits'][0]['spans'][0]['absence_scope']=[scope]
    m.verify_review_absences(job,review)


def patch_module():
    # Execute only the isolated metadata-migration cell, never a paid runner.
    from types import SimpleNamespace
    m = module()
    notebook = json.loads(Path(__file__).parents[1].joinpath('68_structured_evidence_generation.ipynb').read_text())
    matches = [c for c in notebook['cells'] if c['id'] == 'exp68-review-patch-01']
    scope = {'I': m}
    if matches:
        exec(compile(''.join(matches[0]['source']), 'notebook_review_patch', 'exec'), scope)
    return SimpleNamespace(E=m.E, apply_review_patches=scope.get('apply_review_patches'))


def patch_fixture(m):
    old=dict(start=0,end=3,quote='abc',kind='question_context',status='correct',reason='original',question_quotes=['abc'])
    review=dict(identity='q:p:m:r1',requirements=[dict(id='D1',status='satisfied')],surface_audits=[dict(surface='answer_text',status='correct',spans=[old])])
    first={**old,'absence_scope':[dict(kind='question')]}
    second={k:v for k,v in first.items()if k!='question_quotes'}
    second.update(kind='factual',card_keys=['issuer/card'],source_refs=[],input_quotes=[],input_status='supported')
    amendment=dict(records=[dict(identity=review['identity'],original_record_sha256=m.E.digest(review),span_patches=[dict(surface='answer_text',start=0,replacement=first)])])
    followup=dict(application_order=['amend','format','require'],format_reclassifications=[dict(identity=review['identity'],surface='answer_text',start=0,replacement=second)],required_absence_spans=[dict(identity=review['identity'],surface='answer_text',start=0,quote_sha256=m.E.digest('abc'))])
    contract=dict(phases=['amendments','formats','required_scopes'],declared_application_order=followup['application_order'],format_predecessors=[dict(identity=review['identity'],surface='answer_text',start=0,predecessor_sha256=m.E.digest(first),successor_sha256=m.E.digest(second))])
    return review,amendment,followup,contract


def test_patch_preserves_semantics_and_requires_explicit_predecessor():
    m=patch_module();assert callable(m.apply_review_patches),'Missing semantic-preserving notebook patch application'
    original,a,f,c=patch_fixture(m);out=m.apply_review_patches({original['identity']:original},a,f,c)
    span=out['q:p:m:r1']['surface_audits'][0]['spans'][0]
    assert span['kind']=='factual'and span['status']=='correct'and span['absence_scope_required']is True
    assert out['q:p:m:r1']['requirements']==[dict(id='D1',status='satisfied')]
    assert original['surface_audits'][0]['spans'][0]['kind']=='question_context'


@pytest.mark.parametrize('mutation',['status','duplicate_identity','duplicate_target','orphan','predecessor','order','duplicate_required'])
def test_patch_rejects_semantic_drift_or_unapplied_targets(mutation):
    m=patch_module();assert callable(m.apply_review_patches),'Missing semantic-preserving notebook patch application'
    original,a,f,c=patch_fixture(m)
    if mutation=='status':a['records'][0]['span_patches'][0]['replacement']['status']='incorrect'
    elif mutation=='duplicate_identity':a['records'].append(copy.deepcopy(a['records'][0]))
    elif mutation=='duplicate_target':a['records'][0]['span_patches'].append(copy.deepcopy(a['records'][0]['span_patches'][0]))
    elif mutation=='orphan':f['format_reclassifications'][0]['identity']='not-a-real-answer'
    elif mutation=='predecessor':c['format_predecessors'][0]['predecessor_sha256']='0'*64
    elif mutation=='order':c['phases']=['formats','amendments','required_scopes']
    else:f['required_absence_spans'].append(copy.deepcopy(f['required_absence_spans'][0]))
    with pytest.raises(ValueError):m.apply_review_patches({original['identity']:original},a,f,c)


def request_patch_module():
    m=module();notebook=m.E.read(Path(__file__).parents[1]/'68_structured_evidence_generation.ipynb')
    scope={'I':m};exec(''.join(next(c for c in notebook['cells']if c['id']=='exp68-review-patch-01')['source']),scope)
    return scope.get('verify_draft_requests'),m.E


@pytest.mark.parametrize('change',[None,'raw','extra','order'])
def test_draft_requests_accept_only_actual_optional_metadata(tmp_path,change):
    fn,E=request_patch_module();assert callable(fn),'Missing exact optional raw-request metadata verification'
    raw=dict(request={'input':['question']},status='ok');p=tmp_path/'one.json';p.write_text(json.dumps(raw))
    expected=[dict(path='one.json',raw_sha256=E.file_digest(p))]
    before=[dict(**expected[0],request_sha256=E.digest(raw['request']),actual_input_sha256=E.digest(raw['request']['input']),provider_status='ok')]
    if change=='raw':before[0]['raw_sha256']='bad'
    if change=='extra':before[0]['provider_status']='failed'
    if change=='order':before.append(before[0])
    if change is None:
        fn(before,expected,tmp_path);fn(expected,expected,tmp_path)
    else:
        with pytest.raises(ValueError):fn(before,expected,tmp_path)
