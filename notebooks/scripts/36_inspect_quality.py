"""Persist inspected score limitations and source-relative PDF spot checks."""
import importlib.util
from pathlib import Path
import sys
import re

spec=importlib.util.spec_from_file_location('quality_inspection_common',Path(__file__).with_name('34_followup_common.py'))
F=importlib.util.module_from_spec(spec);sys.modules[spec.name]=F;spec.loader.exec_module(F)


def inspect():
    import fitz
    base=F.STAGES[36]
    primary=F.read_json(base/'primary_results.json')
    diagnostics=F.read_json(base/'primary_diagnostics.json')
    by={(r['query_id'],r['profile'],r['mode']):r for r in primary['retrievals']}
    answers={(r['query_id'],r['profile'],r['mode']):r for r in primary['answers']}
    entries=[]
    fixtures=[('Q10','BC/BC_K_FRIST',['0.5%','1%']),
              ('Q12','samsung/Samsung_taptap_DIGITAL',['올리브영','5,000']),
              ('Q29','ibk/IBK_Bliss5',['국내호텔','3%']),
              ('Q30','BC/BC_Baro_KaPick',['편의점','7%'])]
    for query,card,terms in fixtures:
        path=F.ROOT/'data/raw'/f'{card}.pdf'
        pages=[]
        with fitz.open(path) as document:
            for number,page in enumerate(document,1):
                text=page.get_text();compact=re.sub(r'\s+','',text)
                if all(term in compact for term in terms):
                    pages.append({'page_1_based':number,'text':text})
        entries.append({'query_id':query,'card_key':card,'pdf':str(path.relative_to(F.ROOT)),
            'pdf_sha256':F.digest(path.read_bytes()),'required_literal_terms':terms,'pages':pages,
            'limitation':'Read-only text-layer spot check; not PDF-wide benefit truth labels or OCR validation.'})
    for query in ['Q31','Q32','Q33']:
        for profile in ['card_page_section_benefit','parent_child_bundle']:
            row=answers[(query,profile,'S_THEN_U')]
            assert row['status']=='ok' and not row['answer']['recommendations']
            assert row['scores']['known_positive_complete']==0
    notes=[
        {'query_id':'Q10','finding':'평가가 합산 값을 인정하지 못한 사례',
         'evidence':'benefit의 BC K FIRST claim 본문은 기본1%와 추가0.5%를 분리 설명하지만 value=1.5, unit=%를 함께 출력했습니다. 인용 원문에는1%·0.5%가 있고1.5%라는 값은 없어 비율 미확인1건입니다. 같은 질문의 정답 카드 둘은 조건 구성요소1을 통과하지만 점수 규칙은 응답 전체의 미확인1건 때문에 조건 동시 충족0을 줍니다.',
         'conclusion':'이 질문의0대1 차이는 해당 평가 규칙과 출력 표현의 영향입니다. 같은 청크가 조건을 못 찾았다는 근거로 삼지 않습니다.'},
        {'query_id':'Q12','finding':'혜택과 한도를 별도 claim에 둔 사례',
         'evidence':'parent-child는 올리브영 할인과 월5천원 한도를 별도 claim으로 출력했습니다. 한도 claim에는 올리브영 대상명이 없고 할인 claim에는 한도가 없어 한 claim의 최대 구성요소 지지는2/3입니다. benefit의 taptap DIGITAL 할인 claim은 조건에 통합 월5천원을 함께 기록해1을 받습니다.',
         'conclusion':'두 답변에 필요한 내용이 존재해도 분리된 claim을 합치지 않는 점수는 parent-child를 낮게 셉니다. 별도 claim 간 같은 혜택 관계는 자동으로 증명되지 않으므로 원래 점수는 유지합니다.'},
        {'query_id':'Q29','finding':'상세 설명과 추천 목록의 차이',
         'evidence':'두 방식 모두 BLISS.5와 바루카드의 국내 호텔 할인 원문과 할인 claim을 반환했습니다. parent-child는 recommendations=[]로 설명형 응답을 만들었고, benefit은 두 카드를 추천 목록에 담았습니다. 기존 점수는 추천 카드만 조건·정답 포함을 계산하므로 parent-child는0입니다.',
         'conclusion':'검색 성공과 설명 내용이 있어도 추천 중심 평가에서는0이 됩니다. 이 질문은 구조화 후보가 비어 두 방식 모두 전체 비구조화 검색으로 돌아간 사례이므로 구조화 선별의 직접 승리로 해석하지 않습니다.'},
        {'query_id':'Q30','finding':'적립 질문에 할인 카드 답변',
         'evidence':'두 검색 모두 알려진 정답 BC 바로 KaPick을 Top3에 포함했습니다. benefit은 편의점·통신요금7% 포인트 적립을 설명했습니다. parent-child는 My WE:SH의10% 할인과 원더카드의 선택 서비스 크기를 추천·설명하고 KaPick을 추천하지 않아 요청한 적립 행동을 충족하지 못했습니다.',
         'conclusion':'정답 카드 검색만으로 적립 조건을 설명하는 답변이 보장되지 않습니다. parent-child의 최종 KaPick 근거를 아래 별도 점검에 보존합니다.'},
        {'query_id':'Q31/Q32/Q33','finding':'카드 상세 답변의 공통0점과 내용 차이',
         'evidence':'양쪽6개 답변 모두 정상 실행·recommendations=[]이며 기존 추천 지표는0입니다. 올바른FLEX와 그린카드 v2에서 benefit은 여러 할인·적립을 설명하지만 parent-child는 일부 공통 조건 또는 친환경 혜택 위주로 설명했습니다.',
         'conclusion':'공통0점이 같은 실제 답변 품질을 뜻하지 않습니다. 정해진 혜택 목록에 대한 별도 상세 질문 평가가 필요하며 이번에 원래 점수를 사후 변경하지 않습니다.'},
        {'query_id':'Q18','finding':'불가능한 혜택을 부정하는 답변과 상태값의 차이',
         'evidence':'양쪽 모두 편의점1,000% 할인은 확인되지 않는다고 답했습니다. answer_status=answered이므로 기존 negative_abstention은0이며 parent-child는1,000원 혜택으로 해석할 경우의 대안 추천도 포함합니다.',
         'conclusion':'상태값만으로 불가능한 혜택을 인정했다고 판단할 수 없습니다. 대안 제시의 적절성은 별도 기준이 필요합니다.'},
        {'query_id':'Q26','finding':'정답 미주석 질문의 수치 경계',
         'evidence':'양쪽은 BC 바로 클리어 플러스의 전월실적15만원 이상 조건을 설명합니다. 질문은 실적 기준15만원 이하로, 혜택 이용에 필요한 최소 기준이15만원이면 경계에 해당합니다. 기존 gold_cards는 비어 있어 정답 카드·조건 동시 충족은 미평가입니다.',
         'conclusion':'미주석을 오답 또는 음성 대조로 바꾸지 않습니다. 이 질문을 포함하는 신규 정답 주석은 별도 실험에 필요합니다.'},
    ]
    q30=[]
    for profile in ['card_page_section_benefit','parent_child_bundle']:
        retrieval=by[('Q30',profile,'S_THEN_U')]
        evidence=[e for e in retrieval['result']['evidence'] if e['card_key']=='BC/BC_Baro_KaPick']
        q30.append({'profile':profile,'evidence':evidence,
            'earn_word_present':any('적립' in e['text'] for e in evidence),
            'both_target_words_in_one_evidence':any('편의점' in e['text'] and '통신' in e['text'] for e in evidence)})
    result={'notes':notes,'q30_gold_card_evidence':q30,'pdf_spot_checks':entries,
        'primary_diagnostics_sha256':F.digest(diagnostics),'primary_results_sha256':F.digest(primary),
        'old_scores_changed':False,'new_truth_or_judge_scores':False,
        'method':'Main-agent inspection of actual claims, conditions, recommendations, owned evidence and frozen scorer; source PDF text-layer spot checks only.'}
    F.write_json(base/'quality_inspection.json',result)
    print(F.canonical({'notes':len(notes),'pdf_sample_pages':{r['query_id']:[p['page_1_based'] for p in r['pages']] for r in entries},
        'q30_evidence_flags':[{k:v for k,v in r.items() if k!='evidence'} for r in q30]}))
    return result


if __name__=='__main__':
    F.initialize(36)
    inspect()
