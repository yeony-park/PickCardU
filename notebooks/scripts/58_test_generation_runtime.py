"""Offline regressions for paid prompt and evidence ownership boundaries."""
import importlib.util
from pathlib import Path
import sys

import pytest

spec=importlib.util.spec_from_file_location('runtime58_tests',Path(__file__).with_name('58_generation_runtime.py'))
G=importlib.util.module_from_spec(spec);sys.modules[spec.name]=G;spec.loader.exec_module(G)


def test_frozen_prompt_contract_accepts_actual_retry_but_rejects_extra_instructions(monkeypatch):
    old=G.C.operating_prompts()
    cache=G.AnswerCache.__new__(G.AnswerCache)
    cache.identity='H08:benefit:A:new:r1';cache.kind='answer'
    cache.policy='2400';cache.token_truncated=False
    cache.manifest=dict(critical_data_sha256={},prompt_variant_by_identity={cache.identity:'new'},
        answer_instructions_sha256_by_identity={cache.identity:[G.P.sha(G.C.transform_prompt(p,'new')) for p in old]})
    monkeypatch.setattr(G.X,'guard_manifest',lambda m:None)
    monkeypatch.setattr(G.R,'guard_files',lambda m:None)
    for prompt in old:
        normalized=cache.check_contract(dict(instructions=prompt,max_output_tokens=2400))
        assert normalized['instructions']==prompt+G.C.CONDITION_GUIDE
        assert normalized['max_output_tokens']==2400
        assert cache.normalized(normalized)==normalized
    with pytest.raises(RuntimeError,match='instructions differ'):
        cache.check_contract(dict(instructions=old[0]+' 추가 정답 힌트',max_output_tokens=2400))


def test_serialized_evidence_and_dynamic_citation_schema_are_frozen_together():
    item=dict(card_key='a/card',card_name='카드',issuer='발급사',text='원문',chunk_id='one')
    one=G.payload_contract('id','질문',[item],{'stage':58})
    changed=G.payload_contract('id','질문',[{**item,'text':'다른 내용'}],{'stage':58})
    two=G.payload_contract('id','질문',[item,{**item,'chunk_id':'two'}],{'stage':58})
    assert one['input_sha256_by_identity']!=changed['input_sha256_by_identity']
    assert one['schema_sha256_by_identity']==changed['schema_sha256_by_identity']
    assert one['schema_sha256_by_identity']!=two['schema_sha256_by_identity']


def test_saved_answer_revalidates_ordered_prompt_even_without_parse(monkeypatch):
    identity='H08:benefit:A:new:r1'
    row=dict(identity=identity,events=[dict(key='first'),dict(key='second')],reuse_source_sha256=None)
    manifest=dict(stage=58,answer_instructions_sha256_by_identity={identity:[G.P.sha('base'),G.P.sha('retry')]})
    raw={'first':dict(experiment_identity=identity,request=dict(instructions='base')),
         'second':dict(experiment_identity=identity,request=dict(instructions='retry'))}
    monkeypatch.setattr(G.R,'read',lambda p:raw[p.stem])
    G.validate_checkpoint_prompt(row,manifest,{})
    raw['second']['request']['instructions']='base'
    with pytest.raises(RuntimeError,match='ordered base/retry'):
        G.validate_checkpoint_prompt(row,manifest,{})


def test_b_reuse_requires_validated_a_and_its_unchanged_prompt(monkeypatch):
    aid='H08:benefit:A:new:r1';bid='H08:benefit:B:new:r1'
    a=dict(identity=aid,events=[dict(key='one')],reuse_source_sha256=None)
    b=dict(identity=bid,events=[],reuse_source_sha256=G.P.sha(a))
    manifest=dict(stage=58,answer_instructions_sha256_by_identity={aid:[G.P.sha('base'),G.P.sha('retry')]})
    monkeypatch.setattr(G.R,'read',lambda p:dict(experiment_identity=aid,request=dict(instructions='wrong')))
    with pytest.raises(RuntimeError,match='lacks its validated'):
        G.validate_checkpoint_prompt(b,manifest,{})
    with pytest.raises(RuntimeError,match='ordered base/retry'):
        G.validate_checkpoint_prompt(b,manifest,{aid:a})


def test_partial_answer_matrix_cannot_start_paid_execution():
    with pytest.raises(ValueError,match='logical count'):
        G.run([],dict(stage=58,logical_answers=240),allow_network=True)
    with pytest.raises(ValueError,match='logical count'):
        G.run([],dict(stage=59,logical_answers=192),allow_network=True)
