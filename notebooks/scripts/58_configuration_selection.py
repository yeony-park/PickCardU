"""Testable eight-configuration selection; no API, gold inference or reports.

Manual per-answer decisions enter here only after source review. The same
deterministic rule freezes the setting before59 creates its new questions.
"""
from collections import defaultdict
from decimal import Decimal
from itertools import product
import statistics


def select(rows,*,profiles,query_ids,repeats=(1,2)):
    if len(profiles)!=2 or len(set(profiles))!=2:
        raise ValueError('Exactly two different profiles are required')
    expected=set(product(query_ids,profiles,['A','B'],['old','new'],repeats))
    keyed={}
    for row in rows:
        key=tuple(row[k] for k in ['query_id','profile','evidence_variant','prompt_variant','repeat'])
        if key in keyed or key not in expected:
            raise ValueError('Duplicate or unexpected answer matrix identity')
        if (type(row.get('passed')) is not bool or type(row.get('scope_violation')) is not bool
                or not isinstance(row.get('important_error_codes'),list)
                or not isinstance(row.get('missing_material_fact_ids'),list)):
            raise ValueError('Missing manual quality judgment')
        keyed[key]=row
    if set(keyed)!=expected:raise ValueError('Incomplete answer matrix')

    def measure(profile,evidence,prompt):
        values=[keyed[(qid,profile,evidence,prompt,repeat)] for qid in query_ids for repeat in repeats]
        per_query={qid:statistics.mean(float(r['passed']) for r in values if r['query_id']==qid) for qid in query_ids}
        errors=defaultdict(set);missing=defaultdict(set);scope=set()
        for row in values:
            errors[row['query_id']].update(row['important_error_codes'])
            missing[row['query_id']].update(row['missing_material_fact_ids'])
            if row['scope_violation']:scope.add(row['query_id'])
        cost=None if any(r.get('cost_usd') is None for r in values) else sum((Decimal(str(r['cost_usd'])) for r in values),Decimal(0))
        return dict(pass_macro=statistics.mean(per_query.values()),per_query_pass=per_query,
            errors=errors,missing=missing,scope=scope,extra_searches=sum(r['extra_searches'] for r in values),
            payload_bytes=sum(r['payload_bytes'] for r in values),cost=cost)

    baseline={profile:measure(profile,'A','old') for profile in profiles}
    configurations=[]
    for prompt,first,second in product(['old','new'],['A','B'],['A','B']):
        evidence_by_profile=dict(zip(profiles,[first,second]));metrics={};failures=[];net=0
        for profile in profiles:
            current=measure(profile,evidence_by_profile[profile],prompt);base=baseline[profile]
            new_errors={qid:sorted(current['errors'][qid]-base['errors'][qid]) for qid in query_ids
                        if current['errors'][qid]-base['errors'][qid]}
            new_missing={qid:sorted(current['missing'][qid]-base['missing'][qid]) for qid in query_ids
                         if current['missing'][qid]-base['missing'][qid]}
            if current['pass_macro']<base['pass_macro']:failures.append(profile+':PASS_regression')
            if new_errors:failures.append(profile+':new_important_error')
            if new_missing:failures.append(profile+':new_material_condition_loss')
            if current['scope']:failures.append(profile+':scope_violation')
            delta=sum(current['per_query_pass'][qid]-base['per_query_pass'][qid] for qid in query_ids)
            net+=delta
            metrics[profile]=dict(pass_macro=current['pass_macro'],per_query_pass=current['per_query_pass'],
                query_macro_net_pass_gain=delta,new_important_errors=new_errors,new_material_condition_loss=new_missing,
                scope_violation_queries=sorted(current['scope']),
                wins=sum(current['per_query_pass'][q]>base['per_query_pass'][q] for q in query_ids),
                losses=sum(current['per_query_pass'][q]<base['per_query_pass'][q] for q in query_ids),
                ties=sum(current['per_query_pass'][q]==base['per_query_pass'][q] for q in query_ids))
        components=[measure(p,evidence_by_profile[p],prompt) for p in profiles]
        cost=None if any(c['cost'] is None for c in components) else sum((c['cost'] for c in components),Decimal(0))
        configurations.append(dict(config_id=f'{prompt}:{first}:{second}',prompt_variant=prompt,
            evidence_variant_by_profile=evidence_by_profile,eligible=not failures,failures=failures,
            query_macro_net_pass_gain=net,changed_axes=int(prompt!='old')+int(first!='A')+int(second!='A'),
            extra_searches=sum(c['extra_searches'] for c in components),payload_bytes=sum(c['payload_bytes'] for c in components),
            measured_cost_usd=None if cost is None else str(cost),profiles=metrics))
    eligible=[r for r in configurations if r['eligible'] and r['query_macro_net_pass_gain']>0]
    def order(row):
        cost=row['measured_cost_usd']
        return (-row['query_macro_net_pass_gain'],row['changed_axes'],row['extra_searches'],row['payload_bytes'],
                Decimal(cost) if cost is not None else Decimal('Infinity'),row['config_id'])
    chosen=min(eligible,key=order)['config_id'] if eligible else 'old:A:A'
    return dict(selected_config_id=chosen,configurations=configurations,question_denominator=len(query_ids),
        repetitions_per_question=len(repeats),quality_unit='mean repeats within question; macro over questions',
        important_error_guard='no new error code or missing material fact in the per-question union over repeats',
        status='selection_requires_bound_manual_judgments')
