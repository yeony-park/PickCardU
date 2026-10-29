"""The full global grid must not hide a profile regression behind the mean."""
import importlib.util
from pathlib import Path

import pytest

spec=importlib.util.spec_from_file_location('select58',Path(__file__).with_name('58_configuration_selection.py'))
S=importlib.util.module_from_spec(spec);spec.loader.exec_module(S)


def rows():
    result=[]
    for qid in ['q1','q2']:
        for profile in ['benefit','parent']:
            for evidence in ['A','B']:
                for prompt in ['old','new']:
                    for repeat in [1,2]:
                        result.append(dict(query_id=qid,profile=profile,evidence_variant=evidence,prompt_variant=prompt,
                            repeat=repeat,passed=False,important_error_codes=[],missing_material_fact_ids=[],scope_violation=False,
                            extra_searches=int(evidence=='B'),payload_bytes=100+int(evidence=='B'),cost_usd='0.001'))
    return result


def test_no_gain_keeps_baseline_and_all_eight_configurations_are_reported():
    result=S.select(rows(),profiles=['benefit','parent'],query_ids=['q1','q2'])
    assert len(result['configurations'])==8
    assert result['selected_config_id']=='old:A:A'


def test_benefit_gain_cannot_offset_parent_regression():
    values=rows()
    for r in values:
        if r['profile']=='parent':r['passed']=r['prompt_variant']=='old'
        else:r['passed']=r['prompt_variant']=='new'
    result=S.select(values,profiles=['benefit','parent'],query_ids=['q1','q2'])
    assert result['selected_config_id']=='old:A:A'
    assert all(not r['eligible'] for r in result['configurations'] if r['prompt_variant']=='new')


def test_new_important_error_blocks_an_improving_candidate():
    values=rows()
    for r in values:
        if r['prompt_variant']=='new':
            r['passed']=True;r['important_error_codes']=['wrong_benefit_cap']
    result=S.select(values,profiles=['benefit','parent'],query_ids=['q1','q2'])
    assert result['selected_config_id']=='old:A:A'
    with pytest.raises(ValueError,match='matrix'):
        S.select(values[:-1],profiles=['benefit','parent'],query_ids=['q1','q2'])


def test_stage57_rejected_evidence_cannot_be_selected_from_answer_gain():
    values=rows()
    for row in values:row['passed']=row['evidence_variant']=='B'
    result=S.select(values,profiles=['benefit','parent'],query_ids=['q1','q2'],
        allowed_evidence_by_profile={'benefit':['A'],'parent':['A']})
    assert result['selected_config_id']=='old:A:A'
