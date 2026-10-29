"""Source-bound whole-chunk mapping and fixed-card evidence packaging."""
from collections import Counter
from copy import deepcopy
import unicodedata


def norm(text):
    return ''.join(unicodedata.normalize('NFKC',str(text)).casefold().split())


def map_fact(fact, pages, chunks):
    registry = {f"P{p['page']:04d}-L{i:04d}": (p['page'],line)
                for p in pages for i,line in enumerate(p['text'].splitlines(),1) if line.strip()}
    result = {'fact_id':fact['fact_id'],'document_id':fact['document_id'],
              'status':'unmapped','chunk_ids':[],'anchors':[],'reason':None}
    scope = set((fact.get('relation_scope') or {}).get('line_ids',[]))
    refs = [(field,ref) for field,values in (fact.get('field_evidence') or {}).items() for ref in values]
    def fail(reason, status='invalid_source'):
        return {**result,'status':status,'reason':reason}
    if not refs or not scope:
        return fail('no source fragments or relationship scope','unmapped')
    if not scope <= set(registry):
        return fail('relationship scope has unknown line')
    for field,ref in refs:
        lid=ref.get('line_id'); start=ref.get('char_start'); end=ref.get('char_end')
        if lid not in scope or lid not in registry:
            return fail('field reference outside relationship scope')
        page,line=registry[lid]
        if type(start)!=int or type(end)!=int or not 0<=start<end<=len(line):
            return fail('invalid fragment coordinates')
        if not norm(ref.get('fragment','')) or norm(line[start:end])!=norm(ref['fragment']):
            return fail('fragment does not match exact source coordinates')
        result['anchors'].append({'field':field,**ref,'page':page,'source_line':line})
    lines={a['line_id']:a for a in result['anchors']}
    source_frequency=Counter((p,norm(line)) for p,line in registry.values())
    scope_text=norm('\n'.join(registry[lid][1] for lid in sorted(scope)))
    covered_by={}
    for chunk in chunks:
        if chunk['document_id']!=fact['document_id']:
            continue
        text=norm(chunk['text']); cp=set(chunk['metadata'].get('source_pages',[]))
        hit={lid for lid,a in lines.items() if a['page'] in cp and norm(a['source_line']) in text
             and (source_frequency[(a['page'],norm(a['source_line']))]==1 or scope_text in text)}
        if hit:
            covered_by[chunk['chunk_id']]=hit
    missing=set(lines); selected=[]
    by_id={c['chunk_id']:c for c in chunks}
    while missing:
        options=[key for key,hit in covered_by.items() if hit&missing and key not in selected]
        if not options:
            return {**result,'reason':'required source lines not uniquely covered by same-card/page chunks',
                    'missing_line_ids':sorted(missing)}
        key=min(options,key=lambda k:(-len(covered_by[k]&missing),len(by_id[k]['text'].encode()),k))
        selected.append(key);missing-=covered_by[key]
    return {**result,'status':'verified','chunk_ids':selected,'reason':'all source-bound field lines covered',
            'coverage_by_chunk':{key:sorted(covered_by[key]) for key in selected}}


def pack(question, cards, baseline, groups, size, *, limit=64000):
    """Reserve baseline first rows; round-robin conditions, then old remainder.

    `size` is the actual production serialized answer payload byte measurement.
    Whole rows are skipped when they do not fit; source text is never sliced.
    """
    original=deepcopy(baseline)
    if not groups:
        return {'cards':deepcopy(cards),'evidence':original,'excluded':[]}
    keys=[c['card_key'] for c in cards]
    chosen=[];seen=set();counts=Counter();excluded=[]
    def add(row):
        key=row['chunk_id']; card=row['card_key']
        if key in seen:
            return
        reason=('outside fixed card scope' if card not in keys else
                'per-card evidence limit' if counts[card]>=5 else
                'payload byte limit' if size([*chosen,row])>limit else None)
        if reason:
            excluded.append({'chunk_id':key,'reason':reason});return
        seen.add(key);counts[card]+=1;chosen.append(deepcopy(row))
    for card in keys:
        first=next((e for e in baseline if e['card_key']==card),None)
        if first is None:
            raise ValueError('fixed card has no baseline evidence')
        add(first)
    if set(keys)!={e['card_key'] for e in chosen}:
        raise ValueError('baseline first evidence cannot fit fixed card scope')
    for depth in range(max(map(len,groups),default=0)):
        for group in groups:
            if depth<len(group):add(group[depth])
    for row in baseline:add(row)
    if not set(e['chunk_id'] for e in chosen)-set(e['chunk_id'] for e in baseline):
        chosen=original
    else:
        for rank,row in enumerate(chosen,1):row['rank']=rank
    output_cards=deepcopy(cards)
    for card in output_cards:
        if 'evidence_count' in card:
            card['evidence_count']=sum(e['card_key']==card['card_key'] for e in chosen)
    return {'cards':output_cards,'evidence':chosen,'excluded':excluded}
