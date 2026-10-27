"""Boundary tests for offline requirement coverage, not semantic gold tests."""
import importlib.util
from pathlib import Path
import pytest

PATH = Path(__file__).with_name('71_evidence_metrics.py')


def module():
    spec = importlib.util.spec_from_file_location('metrics71', PATH)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def contract(**kw):
    return dict(query_id='X', units=['coffee', 'netflix'], path_kind='same_card', **kw)


def path(card, states, eligible='verified'):
    return dict(cards=[card], eligibility=eligible, units=dict(zip(['coffee','netflix'],states)))


def test_different_cards_cannot_complete_one_card_and():
    m=module();v=m.score(dict(paths=[path('A',['supported','absent']),path('B',['absent','supported'])]),contract())
    assert v['coverage']==.5 and v['complete'] is False and v['card_hit'] is True


def test_one_card_complete_and():
    assert module().score(dict(paths=[path('A',['supported','supported'])]),contract())['complete'] is True


@pytest.mark.parametrize('state',['absent','ambiguous','contradicted'])
def test_only_supported_counts(state):
    assert module().score(dict(paths=[path('A',['supported',state])]),contract())['coverage']==.5


def test_unresolved_source_card_is_not_known_miss_or_valid_path():
    r=module().score(dict(paths=[path('A',['supported','supported'],'unresolved')]),contract())
    assert r['card_hit'] is None and r['coverage'] is None and r['complete'] is None


def test_explicitly_ineligible_card_is_not_full_path():
    r=module().score(dict(paths=[path('A',['supported','supported'],'rejected')]),contract())
    assert r['card_hit'] is False and r['complete'] is False and r['coverage']==0


def test_comparison_can_have_multiple_cards_but_same_card_and_cannot():
    p=dict(cards=['A','B'],eligibility='verified',units={'coffee':'supported','netflix':'supported'})
    assert module().score(dict(paths=[p]),dict(query_id='X',units=['coffee','netflix'],path_kind='comparison'))['complete']
    with pytest.raises(ValueError):module().score(dict(paths=[p]),contract())


def test_missing_unit_is_rejected_not_silently_removed_from_denominator():
    with pytest.raises(ValueError):module().score(dict(paths=[dict(cards=['A'],eligibility='verified',units={'coffee':'supported'})]),contract())


def test_unasked_optional_condition_is_not_added_to_denominator():
    v=module().score(dict(paths=[dict(cards=['A'],eligibility='verified',units={'coffee':'supported'})]),dict(query_id='X',units=['coffee'],path_kind='same_card'))
    assert v['coverage']==1 and v['complete']


def test_not_applicable_is_not_zero():
    v=module().score(dict(paths=[]),dict(query_id='X',units=[],path_kind='not_applicable'))
    assert v['coverage'] is None and v['complete'] is None


def test_unknown_state_and_duplicate_cards_are_rejected():
    with pytest.raises(ValueError):module().score(dict(paths=[path('A',['unknown','supported'])]),contract())
    p=dict(cards=['A','A'],eligibility='verified',units={'coffee':'supported','netflix':'supported'})
    with pytest.raises(ValueError):module().score(dict(paths=[p]),dict(query_id='X',units=['coffee','netflix'],path_kind='comparison'))


def fixture_review(m):
    packet=dict(input_id='one',cards=[dict(card_key='A')],evidence=[dict(card_key='A',text='coffee discount',issuer='issuerA')])
    citation=dict(evidence_index=0,card_key='A',start=0,end=6,quote='coffee')
    src=dict(condition_id='A:fact',card_key='A',quote='coffee')
    detail=dict(input_quotes=[citation],source_quotes=[src],reason='manual interpretation')
    review=dict(input_id='one',packet_sha256=m.sha(packet),paths=[dict(cards=['A'],eligibility='verified',units={'coffee':'supported'},unit_evidence={'coffee':detail},source_quotes=[src],reason='manual source eligibility')])
    catalog={'A:fact':dict(card_key='A',literal_quotes=['coffee discount'],source_status='verified')}
    return packet,review,catalog,dict(query_id='X',units=['coffee'],path_kind='same_card')


def test_valid_source_and_input_bound_review():
    m=module();p,r,c,g=fixture_review(m);assert m.validate(r,g,p,c)


@pytest.mark.parametrize('change',['wrong_quote','wrong_owner','negative_index','other_input','unreviewed_card','missing_source','missing_input'])
def test_citation_or_coverage_tamper_is_rejected(change):
    m=module();p,r,c,g=fixture_review(m)
    detail=r['paths'][0]['unit_evidence']['coffee']
    if change=='wrong_quote':detail['input_quotes'][0]['quote']='wrong!'
    if change=='wrong_owner':detail['source_quotes'][0]['card_key']='B'
    if change=='negative_index':detail['input_quotes'][0]['evidence_index']=-1
    if change=='other_input':r['packet_sha256']='not the packet'
    if change=='unreviewed_card':p['cards'].append(dict(card_key='B'));r['packet_sha256']=m.sha(p)
    if change=='missing_source':detail['source_quotes']=[]
    if change=='missing_input':detail['input_quotes']=[]
    with pytest.raises(ValueError):m.validate(r,g,p,c)


def test_transmitted_issuer_metadata_quote_is_allowed():
    m=module();p,r,c,g=fixture_review(m)
    q=dict(evidence_index=0,field='issuer',card_key='A',start=0,end=7,quote='issuerA')
    m.check_quote(q,p)


def test_same_card_alternative_paths_remain_distinct():
    a=path('A',['supported','absent']);a['path_id']='basic'
    b=path('A',['supported','supported']);b['path_id']='special'
    m=module()
    assert m.path_key(a)!=m.path_key(b)
    assert m.score(dict(paths=[a,b]),contract())['complete']


def test_duplicate_path_without_identity_is_rejected():
    with pytest.raises(ValueError):module().score(dict(paths=[path('A',['supported','absent']),path('A',['absent','supported'])]),contract())


def test_benefit_cannot_be_supported_by_issuer_metadata():
    m=module();p,r,c,g=fixture_review(m)
    r['paths'][0]['unit_evidence']['coffee']['input_quotes']=[dict(evidence_index=0,field='issuer',card_key='A',start=0,end=7,quote='issuerA')]
    with pytest.raises(ValueError):m.validate(r,g,p,c)


def test_scope_unit_metadata_requires_explicit_field_policy():
    m=module();p,r,c,g=fixture_review(m)
    q=dict(evidence_index=0,field='issuer',card_key='A',start=0,end=7,quote='issuerA')
    v=r['paths'][0];v['units']={'issuer':'supported'};v['unit_evidence']={'issuer':dict(input_quotes=[q],source_quotes=v['source_quotes'],reason='manual issuer scope')};g['units']=['issuer']
    with pytest.raises(ValueError):m.validate(r,g,p,c)
    assert m.validate(r,g,p,c,{'issuer':['text','issuer','card_name']})


def test_uncertain_source_cannot_certify_supported_unit():
    m=module();p,r,c,g=fixture_review(m)
    c['A:fact']['source_status']='uncertain'
    with pytest.raises(ValueError):m.validate(r,g,p,c)
