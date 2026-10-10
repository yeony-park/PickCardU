"""Failure replay must preserve operational retry without paid calls."""
from pathlib import Path
import importlib.util
import sys
from types import SimpleNamespace
import json
import pytest
from pydantic import ValidationError

s=importlib.util.spec_from_file_location('answer_failure_followup',Path(__file__).with_name('32_answer_failure_followup.py'))
F=importlib.util.module_from_spec(s);sys.modules[s.name]=F;s.loader.exec_module(F)

def test_cached_eof_preserves_validation_exception_for_operational_retry(tmp_path):
    api=F.OfflineRetryAPI();api.cache=tmp_path
    kwargs={'text_format':F.E.QueryPlan,'model':F.C.MODEL,'max_output_tokens':2400}
    key=F.request_key(kwargs)
    (tmp_path/f'{key}.json').write_text(json.dumps({'status':'failed','error':'ValidationError: Invalid JSON: EOF while parsing a value'}))
    with pytest.raises(ValidationError) as error:api.parse(**kwargs)
    assert error.value.errors()[0]['type']=='json_invalid'

def test_offline_cache_miss_does_not_create_request_or_call_provider(tmp_path):
    api=F.OfflineRetryAPI();api.cache=tmp_path
    with pytest.raises(RuntimeError,match='Offline'):api.parse(text_format=F.E.QueryPlan,model=F.C.MODEL)
    assert list(tmp_path.iterdir())==[] and api.client is None

def test_budget_followup_changes_only_token_limit(monkeypatch):
    seen=[]
    monkeypatch.setattr(F.C.CachedAPI,'parse',lambda self,**kw:seen.append(kw) or SimpleNamespace())
    api=F.LargerBudgetAPI();api.parse(text_format=F.E.QueryPlan,model=F.C.MODEL,max_output_tokens=2400,input='same')
    assert seen[0]['max_output_tokens']==4800 and seen[0]['input']=='same'

@pytest.mark.parametrize('second_cached',[True,False])
def test_operational_retry_reuses_second_cache_without_provider_or_new_files(tmp_path,second_cached):
    from pickcardu_rag.answering import OpenAIService
    from pickcardu_rag.errors import LlmUnavailable
    evidence=[{'chunk_id':'c1','card_key':'card','card_name':'테스트 카드','issuer':'테스트','text':'근거'}]
    requests=[]
    class Capture(F.OfflineRetryAPI):
        def parse(self,**kwargs):
            requests.append(kwargs)
            raise ValidationError.from_exception_data('JSON',[
                {'type':'json_invalid','loc':(),'input':None,'ctx':{'error':'EOF while parsing a value'}}])
    with pytest.raises(LlmUnavailable):OpenAIService(api_key=None,client=Capture(),llm_model=F.C.MODEL).answer('질문',evidence)
    assert len(requests)==2 and requests[0]['instructions']!=requests[1]['instructions']
    first=tmp_path/f'{F.request_key(requests[0])}.json'
    first.write_text(json.dumps({'status':'failed','error':'ValidationError: EOF while parsing a value'}))
    if second_cached:
        (tmp_path/f'{F.request_key(requests[1])}.json').write_text(json.dumps({'status':'ok','usage':{},'parsed':
            {'answer_status':'insufficient_evidence','answer_text':'근거 부족','recommendations':[],'claims':[]}}))
    before={p.name:p.read_bytes() for p in tmp_path.iterdir()};api=F.OfflineRetryAPI();api.cache=tmp_path
    provider=OpenAIService(api_key=None,client=api,llm_model=F.C.MODEL)
    if second_cached:
        answer,usage=provider.answer('질문',evidence)
        assert answer.answer_status=='insufficient_evidence' and usage['attempt_count']==2
        assert api.events and all(e['cache_hit'] for e in api.events)
    else:
        with pytest.raises(LlmUnavailable):provider.answer('질문',evidence)
    assert api.client is None and before=={p.name:p.read_bytes() for p in tmp_path.iterdir()}
