"""Sequential, resumable experiment orchestration in the approved environment.

Wait for the already-running oracle search, preserve its outputs, then replay
using cached scores after answer wiring, memoization and identity guards.
Paid calls use the previously authorized card/question/evidence scope.
"""
from pathlib import Path
import importlib.util
import sys
import subprocess
import time
import resource

s=importlib.util.spec_from_file_location('orchestration_common',Path(__file__).with_name('30_structured_search_common.py'))
C=importlib.util.module_from_spec(s);sys.modules[s.name]=C;s.loader.exec_module(C)
LOG=C.STAGES[30]/'orchestration_progress.json'

def execute(label,args,history,force=False):
    previous=next((r for r in history if r['label']==label and r['status']=='ok'),None)
    if previous and not force:return
    started=time.time();before=resource.getrusage(resource.RUSAGE_CHILDREN)
    entry={'label':label,'status':'started','started_at':started,'command':['python','-B',*args]}
    history.append(entry);C.write_json(LOG,{'records':history})
    result=subprocess.run([sys.executable,'-B',*args],cwd=C.ROOT)
    after=resource.getrusage(resource.RUSAGE_CHILDREN)
    entry.update(status='ok' if result.returncode==0 else 'failed',exit_code=result.returncode,
       wall_seconds=time.time()-started,finished_at=time.time(),
       child_cpu_seconds=(after.ru_utime+after.ru_stime)-(before.ru_utime+before.ru_stime),
       cumulative_child_peak_rss_kib=after.ru_maxrss)
    C.write_json(LOG,{'records':history})
    if result.returncode:raise RuntimeError('Stage failed: '+label)

def run():
    history=C.read_json(LOG)['records'] if LOG.exists() else []
    archive=C.STAGES[32]/'superseded_v4_before_answer_constructor_fix'
    if not archive.exists():
        p=C.STAGES[32]/'retrieval_results.json'
        print('Waiting for the existing oracle process to finish its frozen grid',flush=True)
        while not p.exists():time.sleep(10)
        completed=C.read_json(p)
        if len(completed['records'])!=198:raise RuntimeError('Incomplete source grid; not archiving')
        archive.mkdir()
        for name in ['retrieval_results.json','retrieval','retrieval_progress.json','selections']:
            source=C.STAGES[32]/name
            if source.exists():source.rename(archive/name)
        C.write_json(archive/'reason.json',{'reason':'api_key=None constructor injection corrected; repeated metric fact decoding memoized by actual SQLite SHA. Read-only DB/export and source fingerprint guards added. Retrieval/ranking/scoring algorithms unchanged. Replay all search rows to bind current code hash; same BGE/embedding caches reused. No answers existed.',
                    'original_search_source_commit':'dd87843','archived_at':time.time()})
    execute('oracle_retrieval_replay',['notebooks/scripts/32_search_experiment.py','retrieval'],history)
    before=C.read_json(archive/'retrieval_results.json')['records'];after=C.read_json(C.STAGES[32]/'retrieval_results.json')['records']
    def comparable(row):
        return {'query':row['query_id'],'profile':row['profile'],'mode':row['mode'],'status':row['status'],
            'metrics':row.get('metrics'),'error':row.get('error'),
            'cards':row.get('result',{}).get('cards'), 'evidence':row.get('result',{}).get('evidence')}
    if [comparable(r) for r in before]!=[comparable(r) for r in after]:
        raise RuntimeError('Identity-bound replay changed retrieval quality/output')
    before_selections={p.name:C.read_json(p) for p in (archive/'selections').glob('*.json')}
    after_selections={p.name:C.read_json(p) for p in (C.STAGES[32]/'selections').glob('*.json')}
    if len(before_selections)!=33 or before_selections!=after_selections:
        raise RuntimeError('Identity-bound replay changed structured selection/fallback')
    C.write_json(C.STAGES[32]/'replay_verification.json',{'rows':len(after),'search_metrics_cards_evidence_identical':True,
      'structured_selection_unknown_fallback_identical':True,'selection_rows':len(before_selections),
      'reason':'Answer constructor wiring, memoization and read-only source/database identity guards changed; retrieval/ranking/scoring outputs verified identical and now bound to current source fingerprints.',
      'resource_limit':'Warm cache replay is not a cold latency measurement.'})
    execute('oracle_answers',['notebooks/scripts/32_search_experiment.py','answers'],history)
    answers=C.read_json(C.STAGES[32]/'answer_results.json')['records']
    if not any(r['status']=='ok' and r.get('usage',{}).get('provider_called') is not False for r in answers):
        raise RuntimeError('No provider-backed oracle answer succeeded; inspect before proceeding')
    execute('oracle_answer_scores',['notebooks/scripts/32_score_answers.py','--source','oracle'],history)
    execute('oracle_failure_diagnostics',['notebooks/scripts/32_failure_diagnostics.py'],history)
    execute('oracle_backend_scope_probe',['notebooks/scripts/32_backend_scope_probe.py'],history)
    execute('llm_query_plans',['notebooks/scripts/33_query_routing.py'],history)
    execute('llm_retrieval',['notebooks/scripts/32_search_experiment.py','retrieval','--source','llm'],history)
    execute('llm_answers',['notebooks/scripts/32_search_experiment.py','answers','--source','llm'],history)
    execute('llm_answer_scores',['notebooks/scripts/32_score_answers.py','--source','llm'],history)
    execute('llm_failure_diagnostics',['notebooks/scripts/32_failure_diagnostics.py','--source','llm'],history)
    execute('parser_repeat_probes',['notebooks/scripts/33_repeat_query_plans.py'],history)
    execute('answer_failure_followups',['notebooks/scripts/32_answer_failure_followup.py'],history)
    execute('compact_summaries',['notebooks/scripts/30_summarize_experiments.py'],history,force=True)
    execute('original_preservation',['notebooks/scripts/30_verify_preservation.py'],history,force=True)
    for stage in [30,31,32,33]:execute(f'notebook_{stage}',['notebooks/scripts/30_execute_experiment_notebooks.py','--stage',str(stage)],history,force=True)
    execute('reports_after_all_notebooks',['notebooks/scripts/30_render_experiment_reports.py'],history,force=True)
    execute('final_audit',['notebooks/scripts/30_verify_experiment_results.py'],history,force=True)
    print('Experiment stages terminal; final report/review/commit audit remains',flush=True)

if __name__=='__main__':run()
