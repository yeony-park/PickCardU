import json
from decimal import Decimal
import pytest
from scripts.resume_structured_search import remaining_identities, ReplayTransport, ResumeBudget


def test_exact_remaining_seventeen_exclude_all_seven_generated():
    ids = remaining_identities()
    assert len(ids) == len(set(ids)) == 17
    assert 'C11:card_page_section_benefit' not in ids
    assert sum(i.endswith('parent_child_bundle') for i in ids) == 12


def test_replay_requires_exact_body_and_never_reserves_network(tmp_path):
    import httpx2
    row={'request':{'model':'x'},'raw_response':{'id':'saved'},'http_status':200}
    (tmp_path/'C11:card_page_section_benefit:extract:1.json').write_text(json.dumps(row))
    t=ReplayTransport(tmp_path)
    t.identity='C11:card_page_section_benefit'
    good=httpx2.Request('POST','https://api.openai.com/v1/responses',json={'model':'x'})
    assert t.handle_request(good).json()=={'id':'saved'}
    with pytest.raises(RuntimeError,match='request'):
        t.handle_request(httpx2.Request('POST','https://api.openai.com/v1/responses',json={'model':'changed'}))


def test_resume_budget_forbids_generated_identity_without_touching_old_halt(tmp_path):
    b=ResumeBudget(tmp_path/'requests', Decimal('2.854166615'))
    with pytest.raises(RuntimeError,match='approved remaining'):
        b.reserve('C01:card_page_section_benefit','extract',{})
    assert b.check()==Decimal('2.854166615')


def test_only_exact_recorded_grounding_failure_can_be_continued(tmp_path):
    from scripts.resume_structured_search import digest
    halt=tmp_path/'HALTED.json'
    halt.write_text(json.dumps({'identity':'C01:parent_child_bundle','response':{'code':'LLM_UNGROUNDED','message':'mixed_card_citations'}}))
    b=ResumeBudget(tmp_path/'requests',Decimal('2.8'))
    assert b.halted()
    (tmp_path/'continue_known_output_failure.json').write_text(json.dumps({'halt_sha256':digest(halt),'policy':'count_known_grounding_failure_without_regeneration'}))
    assert not b.halted()
    halt.write_text('{}')
    assert b.halted()


def test_completed_run_and_budget_never_dispatch_network(monkeypatch):
    import scripts.resume_structured_search as m
    monkeypatch.setattr(m, 'journal_transport', lambda *_: pytest.fail('network transport constructed'))
    with pytest.raises(RuntimeError, match='completed.*network'):
        m.run(m.DEFAULT_OUTPUT)
    with pytest.raises(RuntimeError, match='completed.*network'):
        m.ResumeBudget(m.DEFAULT_OUTPUT/'requests',Decimal('0')).reserve('C01:parent_child_bundle','extract',{})


@pytest.mark.parametrize('damage', ['missing', 'tampered'])
def test_failed_answer_cannot_be_regenerated_after_damage(tmp_path,damage):
    import shutil
    import scripts.resume_structured_search as m
    shutil.copytree(m.DEFAULT_OUTPUT,tmp_path/'batch')
    root=tmp_path/'batch'; answer=root/'answers/C01:parent_child_bundle.json'
    if damage=='missing': answer.unlink()
    else: answer.write_text('{}')
    with pytest.raises(RuntimeError,match='completed answer.*C01:parent_child_bundle'):
        m.run(root)
    with pytest.raises(RuntimeError,match='completed answer.*C01:parent_child_bundle'):
        m.ResumeBudget(root/'requests',Decimal('0')).reserve('C01:parent_child_bundle','answer',{})


@pytest.mark.parametrize('damage',['missing','tampered'])
def test_missing_or_changed_completion_seal_blocks_run_and_reserve(tmp_path,damage):
    import shutil
    import scripts.resume_structured_search as m
    shutil.copytree(m.DEFAULT_OUTPUT,tmp_path/'batch');root=tmp_path/'batch'
    seal=root/'completion_seal.json'
    if damage=='missing':seal.unlink()
    else:seal.write_text('{}')
    with pytest.raises(RuntimeError,match='completion seal'):
        m.run(root)
    with pytest.raises(RuntimeError,match='completion seal'):
        m.ResumeBudget(root/'requests',Decimal('0')).reserve('C01:parent_child_bundle','extract',{})
