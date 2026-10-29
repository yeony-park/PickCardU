"""New sequential audits: private output paths and read-only prior imports."""
from pathlib import Path
import importlib.util
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('audit_followup_base',ROOT/'notebooks/scripts/34_followup_common.py')
F=importlib.util.module_from_spec(spec);sys.modules[spec.name]=F;spec.loader.exec_module(F)
STAGES={37:ROOT/'notebooks/data/37_operational_guard_audit',38:ROOT/'notebooks/data/38_query_condition_audit',
        39:ROOT/'notebooks/data/39_answer_rubric',40:ROOT/'notebooks/data/40_new_query_comparison'}
F.STAGES.update(STAGES)
read=F.read_json;write=F.write_json;digest=F.digest

def load(name,filename):return F.load_module(name,ROOT/'notebooks/scripts'/filename)
def initialize(n):F.initialize(n)
def freeze(path,value):
    value={**value,'sha256':digest(value)}
    if path.exists() and read(path)!=value:raise RuntimeError('Frozen contract changed: '+str(path))
    if not path.exists():write(path,value)
    return value

def protection():
    path=STAGES[37]/'prior_files.json'
    if path.exists():return read(path)
    tracked=__import__('subprocess').run(['git','ls-files','notebooks','docs/report'],cwd=ROOT,text=True,capture_output=True,check=True).stdout.splitlines()
    rows=[F.file_record(ROOT/p) for p in tracked if any(Path(p).name.startswith(str(n)+'_') for n in range(30,37)) or any('/'+str(n)+'_' in p for n in range(30,37))]
    result={'files':rows,'created_at':time.time(),'original_manifest':'notebooks/data/34_bundle_comparison/protected_files.json'}
    write(path,result);return result

def verify():
    old=F.verify_preservation(40)
    rows=read(STAGES[37]/'prior_files.json')['files']
    changed=[r['path'] for r in rows if F.file_record(ROOT/r['path'])!=r]
    result={'old':old,'prior_tracked_files':len(rows),'changed':changed,'passed':old['passed'] and not changed}
    write(STAGES[40]/'preservation.json',result)
    if not result['passed']:raise RuntimeError('Protected input changed')
    return result

def experiment(n):
    initialize(n)
    B=load('new_audit_bundle_'+str(n),'34_bundle_experiment.py')
    B.F.STAGES.update(STAGES);B.F.STAGES[34]=STAGES[n]
    # Reuse operational chunks in new independent index copies; never open old Chroma writable.
    x=B.FollowupExperiment();initialize(n)
    return B,x

def component(B,x,n,question,embedding,profile,keys):
    key=x.component_key(question,profile,keys);path=STAGES[n]/'components'/(key+'.json')
    identity={'question_sha256':B.query_identity(question),'embedding_sha256':B.query_identity(embedding),
        'profile':profile,'scope':list(keys) if keys is not None else None,
        'chunks_sha256':digest((F.OLD[31]/f'{profile}_chunks.jsonl').read_bytes())}
    if path.exists():value=read(path)
    else:
        value=x.capture_component(question,embedding,profile,keys);value['identity']=identity
        freeze(path,value);value=read(path)
    claimed=value['sha256'];assert digest({k:v for k,v in value.items() if k!='sha256'})==claimed
    assert value['identity']==identity
    x.components[key]={k:v for k,v in value.items() if k!='sha256'}
    return key
