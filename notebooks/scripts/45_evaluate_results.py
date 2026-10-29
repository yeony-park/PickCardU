"""Existing automatic diagnostics and mode-hidden source-review packets."""
from collections import Counter
import importlib.util
import json
from pathlib import Path
import sys

spec=importlib.util.spec_from_file_location('io45_evaluate',Path(__file__).with_name('45_experiment_io.py'))
I=importlib.util.module_from_spec(spec);sys.modules[spec.name]=I;spec.loader.exec_module(I)
P=I.load('packages45_evaluate','45_prepare_packages.py')
RUBRIC=I.load('rubric45_evaluate','39_answer_rubric.py')


def decide(outcomes):
    return 'B_development_candidate' if 'win' in outcomes and not set(outcomes)&{'loss','uncertain'} else 'A'


def evaluation_case(label):
    # Match 42's saved scorer adapter; gold data is attached only after generation.
    return {**label,'plan':label['gold_plan']}


def source_coverage(package, side, mapping, chunks):
    keys=sorted({link['mapping_key'] for link in package['links']})
    hits=0
    for key in keys:
        fact=mapping[key]
        if fact['status']!='verified':continue
        owned=[e for e in side['evidence'] if e['card_key']==fact['document_id']]
        if all(any(a['page'] in chunks[e['chunk_id']]['metadata']['source_pages'] and
                   P.M.norm(a['source_line']) in P.M.norm(e['text']) for e in owned) for a in fact['anchors']):hits+=1
    return {'hit_facts':hits,'planned_facts':len(keys),'ratio':hits/len(keys) if keys else None}


def evaluate():
    prepared=I.read(I.OUT/'prepared_packages.json')
    P.validate_prepared(prepared);P.validate_change_audit(prepared,I.read(I.OUT/'package_change_audit.json'))
    answers=I.read(I.OUT/'answers.json')['records']
    lookup={(r['query_id'],r['profile']):r for r in answers}
    dataset=I.read(I.DATA/'42_expanded_query_evaluation/dataset.json')
    cases={r['query_id']:evaluation_case(r) for r in dataset['queries']}
    chunks={p:{r['chunk_id']:r for r in map(json.loads,(I.DATA/'31_operational_chunking_comparison'/f'{p}_chunks.jsonl').read_text().splitlines())} for p in ['card_page_section_benefit','parent_child_bundle']}
    rows=[];packets=[];assignments=[];baseline_scores_match=0
    requirements={r['query_id']:r for r in I.read(I.DATA/'43_offline_validation/review_contract.json')['requirements']}
    docs={r['document_id']:r['source_relative_path'] for r in map(json.loads,(I.DATA/'30_structured_benefit_db/documents.jsonl').read_text().splitlines())}
    for p in prepared['records']:
        case=cases[p['query_id']];b=lookup[(p['query_id'],p['profile'])];pair={}
        for mode,row in [('A',p['A']['answer_row']),('B',b)]:
            evidence=p[mode]['evidence']
            automatic=(RUBRIC.score(case,row['answer'],evidence) if row['status']=='ok' else
                {'known_positive_complete':0 if case['gold_cards'] else None,'citation_ownership':0,
                 'unsupported_percent_claims':0,'explicit_scope_preserved':0})
            if mode=='A':
                if automatic!=row['scores']:raise RuntimeError('Existing automatic score changed: '+p['query_id'])
                baseline_scores_match+=1
            scope_ok=(row['status']=='ok' and {c['card_key'] for c in row['answer']['claims']+row['answer']['recommendations']} <= {c['card_key'] for c in p['A']['cards']})
            rows.append({'query_id':p['query_id'],'profile':p['profile'],'mode':mode,'group':case['subgroup'],
                'diagnostic':p['query_id'] in ['E45','E46','E47','E48'],'treatment_status':p['treatment_status'],
                'status':row['status'],'automatic':automatic,'fixed_card_scope_ok':scope_ok,
                'witness':source_coverage(p,p[mode],prepared['mapping'],chunks[p['profile']]),
                'payload_bytes':P.measure_answer_payload(p['question'],evidence)[0],'evidence_count':len(evidence)})
            cited={cid for c in (row.get('answer') or {}).get('claims',[])+(row.get('answer') or {}).get('recommendations',[]) for cid in c['citations']}
            pair[mode]={'status':row['status'],'answer':row.get('answer'),'evidence':[e for e in evidence if e['chunk_id'] in cited]}
        if p['treatment_status']=='changed':
            packet_id='P'+I.digest({'qid':p['query_id'],'profile':p['profile']})[:12]
            swap=int(I.digest(p['question']+p['profile'])[:2],16)%2
            left,right=('B','A') if swap else ('A','B')
            packet={'packet_id':packet_id,'query_id':p['query_id'],'question':p['question'],'requirements':requirements[p['query_id']],
                    'left':pair[left],'right':pair[right],
                    'original_source_paths':{card['card_key']:docs[card['card_key']] for card in p['A']['cards']}}
            I.freeze('review_packets/'+packet_id+'.json',packet)
            packets.append({'packet_id':packet_id,'query_id':p['query_id'],'path':'review_packets/'+packet_id+'.json'})
            assignments.append({'packet_id':packet_id,'query_id':p['query_id'],'profile':p['profile'],'left':left,'right':right,
                                'packet_sha256':I.digest((I.OUT/'review_packets'/f'{packet_id}.json').read_bytes())})
    if len(rows)!=192 or len(packets)!=18:raise RuntimeError('Incomplete paired result matrix')
    I.freeze('review_assignments.json',{'records':assignments,'review_protocol_sha256':I.digest((I.OUT/'paired_review_protocol.json').read_bytes())})
    I.write(I.OUT/'review_packet_index.json',{'records':packets})
    metrics=[]
    for profile in chunks:
        for group in ['all',*sorted({r['group'] for r in rows if not r['diagnostic']})]:
            for mode in ['A','B']:
                selected=[r for r in rows if r['profile']==profile and r['mode']==mode and not r['diagnostic'] and (group=='all' or r['group']==group)]
                metrics.append({'profile':profile,'group':group,'mode':mode,'n':len(selected),
                    'automatic_complete':sum(r['automatic']['known_positive_complete'] or 0 for r in selected),
                    'success':sum(r['status']=='ok' for r in selected),
                    'unsupported_percent_claims':sum(r['automatic']['unsupported_percent_claims'] for r in selected),
                    'citation_ownership':sum(r['automatic']['citation_ownership'] for r in selected),
                    'fixed_scope':sum(r['fixed_card_scope_ok'] for r in selected),
                    'witness_hit_facts':sum(r['witness']['hit_facts'] for r in selected),
                    'witness_planned_facts':sum(r['witness']['planned_facts'] for r in selected)})
    out={'rows':rows,'metrics':metrics,'baseline_automatic_scores_reproduced':baseline_scores_match,
         'changed_review_pairs':18,'unchanged_response_pairs':78,'absolute_manual_parent_rates':'미측정',
         'posthoc':True,'independent_holdout':False,'production_adopted':False}
    I.write(I.OUT/'automatic_evaluation.json',out)
    print({'rows':len(rows),'baseline_score_matches':baseline_scores_match,'changed_review_packets':len(packets)})
    return out


def validate_review(review,packet):
    def require(ok,message):
        if not ok:raise RuntimeError('Invalid source review: '+message)
    require(review['packet_id']==packet['packet_id'],'packet identity')
    require(type(review.get('uncertain')) is bool and bool(review.get('summary')),'explicit certainty and summary')
    for side in ['left','right']:
        require(review[side+'_core'] in ['PASS','PARTIAL','FAIL','UNCERTAIN'],'explicit verdict')
    for finding in review['findings']:
        side=finding['side'];require(side in ['left','right'],'finding side')
        require(finding['kind'] in ['improvement','regression'],'finding kind')
        owned={e['chunk_id']:e for e in packet[side]['evidence']}
        require(bool(finding['citations']) and set(finding['citations'])<=set(owned),'owned citations')
        path=finding['source_path'];quote=finding['source_quote']
        require(all(packet['original_source_paths'][owned[cid]['card_key']]==path for cid in finding['citations']),'source ownership')
        require(bool(quote) and any(P.M.norm(quote) in P.M.norm(page['text'])
            for page in I.read(I.ROOT/path)['pages']),'original source quotation')


def summarize_reviews():
    assignments=I.read(I.OUT/'review_assignments.json')['records']
    reviews=I.read(I.OUT/'source_reviews.json')['records']
    lookup={r['packet_id']:r for r in reviews}
    if len(reviews)!=18 or len(lookup)!=18 or set(lookup)!={r['packet_id'] for r in assignments}:raise RuntimeError('Incomplete source reviews')
    results=[]
    for a in assignments:
        review=lookup[a['packet_id']]
        packet=I.read(I.OUT/'review_packets'/f"{a['packet_id']}.json")
        if I.digest((I.OUT/'review_packets'/f"{a['packet_id']}.json").read_bytes())!=a['packet_sha256']:raise RuntimeError('Review packet changed')
        validate_review(review,packet)
        bside=next(side for side in ['left','right'] if a[side]=='B');aside='right' if bside=='left' else 'left'
        regressions=[f for f in review['findings'] if f['side']==bside and f['kind']=='regression']
        improvements=[f for f in review['findings'] if f['side']==bside and f['kind']=='improvement']
        ca,cb=review[aside+'_core'],review[bside+'_core']
        if ca=='PASS' and cb!='PASS':regressions.append({'kind':'core_PASS_loss'})
        if packet[aside]['status']=='ok' and packet[bside]['status']!='ok':regressions.append({'kind':'answer_success_loss'})
        uncertain=review.get('uncertain',False) or 'UNCERTAIN' in [ca,cb]
        outcome='loss' if regressions else 'uncertain' if uncertain else 'win' if improvements or (ca!='PASS' and cb=='PASS') else 'tie'
        results.append({'packet_id':a['packet_id'],'query_id':a['query_id'],'profile':a['profile'],
                        'A_core':ca,'B_core':cb,'outcome':outcome,'regressions':regressions,'improvements':improvements,'finding':review['summary']})
    summary=[]
    effects=I.read(I.OUT/'package_change_audit.json')['records']
    for profile in ['card_page_section_benefit','parent_child_bundle']:
        subset=[r for r in results if r['profile']==profile]
        for kind in ['all_changed','append_only','replacement']:
            ids={r['query_id'] for r in effects if r['profile']==profile and (kind=='all_changed' or r['effect_type']==kind)}
            picked=[r for r in subset if r['query_id'] in ids]
            counts=Counter(r['outcome'] for r in picked)
            summary.append({'profile':profile,'effect_type':kind,'n':len(picked),**{key:counts[key] for key in ['win','loss','tie','uncertain']}})
    decision={profile:decide([r['outcome'] for r in results if r['profile']==profile]) for profile in ['card_page_section_benefit','parent_child_bundle']}
    out={'records':results,'summary':summary,'decision':decision,'overall_paired':[
        {'profile':p,'n':48,'win':sum(r['outcome']=='win' for r in results if r['profile']==p),
         'loss':sum(r['outcome']=='loss' for r in results if r['profile']==p),
         'tie':48-sum(r['outcome']!='tie' for r in results if r['profile']==p),
         'uncertain':sum(r['outcome']=='uncertain' for r in results if r['profile']==p)} for p in decision],
        'source_reviews_sha256':I.digest((I.OUT/'source_reviews.json').read_bytes()),'production_adopted':False}
    I.write(I.OUT/'source_review_summary.json',out)
    return out


if __name__=='__main__':
    if '--reviews' in sys.argv:print(summarize_reviews()['decision'])
    else:evaluate()
