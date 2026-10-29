"""Hand-specified selector/recovery cases, frozen before replay execution."""
import importlib.util
from pathlib import Path
import sys

spec=importlib.util.spec_from_file_location('fixture43_io',Path(__file__).with_name('43_offline_validation.py'))
C=importlib.util.module_from_spec(spec);sys.modules[spec.name]=C;spec.loader.exec_module(C)

def requirement(target,action='discount',rate=10,**extra):
    return {'targets':[target],'action':action,'min_percent':rate,'max_spend_krw':None,'min_cap_krw':None,
            'cap_period':'any','exclude_targets':[],**extra}

def plan(groups=None,scope=None,**extra):
    return {'route':'structured_then_unstructured','groups':groups if groups is not None else [[requirement('커피')]],
            'card_keys':scope or [],'unresolved':[],**extra}

def fact(card,target,action,percent,spend,cap,period):
    return {'document_id':card,'fact_id':card+'_'+target,'fields':{'target':target,'action':action,'value':str(percent),'unit':'%'},
            'derived':{'target_normalized':target,'action_kind':action,'action_status':'true','percent':percent,
                'percent_state':'known' if percent is not None else 'unknown','spend_threshold_krw':spend,
                'spend_state':'known' if spend is not None else 'unknown','cap_krw':cap,'cap_state':'known',
                'cap_period':period,'cap_period_state':'known','exceptions_normalized':''}}

def prepare():
    facts=[fact('A','커피','discount',10,300000,5000,'month'),fact('A','넷플릭스','discount',10,300000,5000,'month'),
           fact('B','커피','discount',20,500000,6000,'month'),fact('C','넷플릭스','discount',30,300000,10000,'month'),
           fact('D','커피','earn',10,300000,5000,'month'),fact('E','커피','discount',5,100000,5000,'day'),
           fact('F','병원','discount',10,300000,4000,'month'),fact('F','약국','discount',3,300000,4000,'month'),
           fact('G','약국','discount',10,300000,4000,'month'),fact('H','커피','discount',None,300000,5000,'month'),
           fact('I','커피','discount',10,None,5000,'month')]
    cases=[]
    def add(name,value,eligible,fallback,scope,*,parser_failed=False,unknown=None,oracle=None,risk=None):
        cases.append({'name':name,'plan':value,'parser_failed':parser_failed,'expected':{'eligible_cards':eligible,
            'fallback':fallback,'effective_scope':scope,'unknown_cards':unknown or []},
            'oracle_eligible_cards':oracle,'risk_kind':risk})
    add('할인과적립구분',plan(scope=['A','B','D']),['A','B'],False,['A','B'])
    add('적립만선택',plan([[requirement('커피','earn')]],['A','D']),['D'],False,['D'])
    add('같은카드AND',plan([[requirement('커피'),requirement('넷플릭스')]]),['A'],False,['A'])
    add('OR대안',plan([[requirement('커피')],[requirement('넷플릭스')]]),['A','B','C','I'],False,['A','B','C','I'],unknown=['H'])
    add('월한도',plan([[requirement('커피',rate=5,min_cap_krw=5000,cap_period='month')]],['A','E']),['A'],False,['A'])
    add('일한도',plan([[requirement('커피',rate=5,min_cap_krw=5000,cap_period='day')]],['A','E']),['E'],False,['E'])
    add('전월실적경계와unknown',plan([[requirement('커피',max_spend_krw=300000)]],['A','B','I']),['A'],False,['A'],unknown=['I'])
    add('최소율경계10포함',plan(scope=['A']),['A'],False,['A'])
    add('최소율경계11제외',plan([[requirement('커피',rate=11)]],['A','B']),['B'],False,['B'])
    add('월한도6000경계',plan([[requirement('커피',min_cap_krw=6000,cap_period='month')]],['A','B']),['B'],False,['B'])
    add('unknown율명시범위복귀',plan(scope=['H']),[],True,['H'],unknown=['H'])
    add('null율은필터안함',plan([[requirement('커피',rate=None)]],['H']),['H'],False,['H'])
    add('후보0명시범위보존',plan([[requirement('병원')]],['A']),[],True,['A'])
    add('unresolved명시범위보존',plan(scope=['A'],unresolved=['해석불가']),[],True,['A'],unknown=['A'])
    add('일반경로명시범위보존',plan([],['A'],route='unstructured'),['A'],True,['A'])
    add('추출실패복귀',plan(),list('ABCDEFGHI'),True,None,parser_failed=True)
    add('추출실패명시범위소실',plan(scope=['A']),list('ABCDEFGHI'),True,None,parser_failed=True,risk='parser_failure_scope_reset')
    add('형식잘못된퍼센트단위',plan([[requirement('커피',rate='10%')]]),list('ABCDEFGHI'),True,None)
    add('숫자문자열정규화',plan([[requirement('커피',rate='10',max_spend_krw='300000')]],['A']),['A'],False,['A'])
    add('알수없는카드키',plan(scope=['Z']),list('ABCDEFGHI'),True,None,risk='catalog_failure_scope_reset')
    add('빈외부그룹복귀',plan([]),[],True,None,unknown=list('ABCDEFGHI'))
    add('빈내부AND무조건참',plan([[]]),list('ABCDEFGHI'),False,list('ABCDEFGHI'),risk='empty_and_vacuous_true')
    add('의미오류대상변경',plan([[requirement('넷플릭스',rate=20)]]),['C'],False,['C'],oracle=['B'],risk='semantic_target')
    add('의미오류할인에서적립',plan([[requirement('커피','earn')]]),['D'],False,['D'],oracle=['A','B','I'],risk='semantic_action')
    add('의미오류20에서10',plan(),['A','B','I'],False,['A','B','I'],unknown=['H'],oracle=['B'],risk='semantic_number')
    add('의미오류AND에서OR',plan([[requirement('커피')],[requirement('넷플릭스')]]),['A','B','C','I'],False,['A','B','C','I'],unknown=['H'],oracle=['A'],risk='semantic_boolean')
    add('의미오류월에서일',plan([[requirement('커피',rate=5,min_cap_krw=5000,cap_period='day')]],['A','E']),['E'],False,['E'],oracle=['A'],risk='semantic_period')
    add('의미오류알려진다른카드',plan(scope=['B']),['B'],False,['B'],oracle=['A'],risk='semantic_card_scope')
    return C.R.freeze(C.PATH/'routing_fixtures.json',{'facts':facts,'catalog':list('ABCDEFGHI'),'cases':cases,
        'expected_defined_before_replay':True,'synthetic_not_error_frequency':True,'new_api_requests':0})

if __name__=='__main__':
    print('frozen controlled cases:',len(prepare()['cases']))
