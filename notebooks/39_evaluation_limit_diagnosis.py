"""Record a frozen scorer limitation without changing evaluation or API inputs."""
import importlib.util
from pathlib import Path
import sys

def load(name,file):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(file))
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module
C=load('limitation_common','37_followup_common.py');T=load('limitation_fixtures','39_test_answer_rubric.py')
C.initialize(39)
case={'task':'detail','gold_cards':['a'],'plan':{'groups':[[{'targets':['스타벅스'],'action':'earn','min_percent':1.5,
      'max_spend_krw':None,'min_cap_krw':None,'cap_period':'any','exclude_targets':[]}]],'card_keys':['a']},
      'answer_checks':[],'calculations':[T.RULE]}
answer={'answer_status':'answered','recommendations':[],'claims':[T.claim()],'answer_text':'스타벅스 기본1%와 추가0.5%를 합하면1.5%입니다.'}
supported=T.M.percent_supported(answer['claims'][0],T.E,[T.RULE]);scores=T.M.score(case,answer,T.E)
assert supported and scores['known_positive_complete']==0
result={'post_freeze_diagnostic':True,'changes_primary_scores':False,'new_api_requests':0,
        'registered_sum_percent_supported':supported,'complete_score':scores['known_positive_complete'],
        'case':case,'answer':answer,'evidence':T.E,'scorer_sha256':C.digest((C.ROOT/'notebooks/39_answer_rubric.py').read_bytes()),
        'limitation':'Registered sum is supported by percent_supported, but minimum-percent requirement still demands the combined value literally in cited text; no registered sums occur in new18 questions.'}
C.write(C.STAGES[39]/'evaluation_limit_diagnosis.json',result)
print('Frozen sum boundary confirmed',supported,scores['known_positive_complete'])
