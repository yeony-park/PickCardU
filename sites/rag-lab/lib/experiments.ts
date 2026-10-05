import { database } from '@/db';
import { ApiError, asset, configuration, manifest, object, options, search, textField } from './runtime';
import type { EvaluationCaseResult, EvaluationMetrics, EvaluationRun, ExperimentDetail, ExperimentSummary, ExperimentCompareResponse, MetricName, SearchResponse, RagConfiguration } from './contracts';

type User = {userId:string;email:string};
type StoredSearch = {id:string;response:SearchResponse;configuration:RagConfiguration;created_by:string;created_at:string};
export async function recordSearch(response:SearchResponse, user:User) {
  const id=crypto.randomUUID(), now=new Date().toISOString();
  const payload:StoredSearch={id,response,configuration:configuration(response),created_by:user.email,created_at:now};
  await database().prepare('INSERT INTO search_runs(id,created_at,created_by,payload) VALUES(?,?,?,?)').bind(id,now,user.userId,JSON.stringify(payload)).run();
  return {...response,search_run_id:id};
}
export async function listExperiments() {
  const rows=await database().prepare('SELECT payload FROM experiments ORDER BY created_at DESC LIMIT 100').all<{payload:string}>();
  return {items:rows.results.map(r=>((({search_snapshot,...summary})=>{void search_snapshot;return summary;})(JSON.parse(r.payload))) as ExperimentSummary),next_cursor:null};
}
export async function detail(id:string):Promise<ExperimentDetail & {search_snapshot:SearchResponse}> {
  const row=await database().prepare('SELECT payload FROM experiments WHERE id=?').bind(id).first<{payload:string}>();
  if(!row) throw new ApiError(404,'not_found','실험을 찾지 못했습니다.');
  const experiment=JSON.parse(row.payload);
  const runs=await database().prepare('SELECT payload FROM evaluations WHERE experiment_id=?').bind(id).all<{payload:string}>();
  const cases=await database().prepare('SELECT payload FROM evaluation_cases WHERE run_id=? ORDER BY id').bind(id).all<{payload:string}>();
  return {...experiment,evaluation_runs:runs.results.map(r=>JSON.parse(r.payload)),case_results:cases.results.map(r=>JSON.parse(r.payload))};
}
export async function createExperiment(input:unknown,user:User) {
  const p=object(input), searchId=textField(p.search_run_id), name=textField(p.name,120);
  const row=await database().prepare('SELECT payload,created_by FROM search_runs WHERE id=?').bind(searchId).first<{payload:string;created_by:string}>();
  if(!row || row.created_by!==user.userId) throw new ApiError(404,'search_not_found','저장할 검색을 먼저 실행해 주세요.');
  const saved=JSON.parse(row.payload) as StoredSearch;
  const id=searchId; // Retrying the same save cannot create duplicate experiments.
  const exp={id,name,description:saved.response.query,dataset_id:manifest.dataset_id,configuration:saved.configuration,tags:[saved.response.mode],status:'draft',created_by:user.email,created_at:new Date().toISOString(),completed_at:null,metrics:null,search_snapshot:saved.response};
  await database().prepare('INSERT INTO experiments(id,created_at,created_by,payload) VALUES(?,?,?,?) ON CONFLICT(id) DO NOTHING').bind(id,exp.created_at,user.userId,JSON.stringify(exp)).run();
  return detail(id);
}

type Gold = {query_id:string;question:string;expected_document_id:string;expected_page:number;expected_terms:string[];expected_parent_ids:string[]};
let goldPromise:Promise<Gold[]>|undefined;
let goldVectorsPromise:Promise<number[][]>|undefined;
const gold=()=>goldPromise??=asset('gold.json').then(r=>r.json<Gold[]>()).catch(e=>{goldPromise=undefined;throw e;});
const goldVectors=()=>goldVectorsPromise??=asset('gold-vectors.json').then(r=>r.json<number[][]>()).catch(e=>{goldVectorsPromise=undefined;throw e;});
export function metrics(cases:EvaluationCaseResult[]):EvaluationMetrics {
  const count=cases.length;
  const sum=(f:(c:EvaluationCaseResult)=>number)=>cases.reduce((s,c)=>s+f(c),0);
  const latencies=cases.map(c=>c.latency_ms).sort((a,b)=>a-b);
  const median=count?(latencies[Math.floor((count-1)/2)]+latencies[Math.floor(count/2)])/2:null;
  return {case_count:count,retrieval_recall_at_k:count?sum(c=>c.metrics.retrieval_hit??0)/count:null,mean_reciprocal_rank:count?sum(c=>c.metrics.reciprocal_rank??0)/count:null,ndcg_at_k:count?sum(c=>{const rr=c.metrics.reciprocal_rank??0;return rr?1/Math.log2(1/rr+1):0;})/count:null,p50_latency_ms:median,total_tokens:0,estimated_cost_usd:0,citation_precision:null,citation_recall:null,groundedness:null,answer_correctness:null,answer_rate:null};
}
// Each request processes five cases; progress survives page closes and Worker restarts.
export async function evaluate(input:unknown,user:User) {
  const p=object(input), id=textField(p.experiment_id);
  const exp=await detail(id);
  if(exp.configuration.index_version!==manifest.version) throw new ApiError(409,'index_mismatch','이 실험은 이전 인덱스로 저장되었습니다. 새 실험을 실행해 주세요.');
  const queries=await gold();
  const db=database();
  const now=new Date().toISOString();
  const initial:EvaluationRun={id,experiment_id:id,status:'running',created_at:now,started_at:now,completed_at:null,metrics:null,error:null};
  await db.prepare('INSERT INTO evaluations(id,experiment_id,created_by,payload) VALUES(?,?,?,?) ON CONFLICT(id) DO NOTHING').bind(id,id,user.userId,JSON.stringify(initial)).run();
  const previous=await db.prepare('SELECT payload FROM evaluations WHERE id=?').bind(id).first<{payload:string}>();
  const run=JSON.parse(previous!.payload) as EvaluationRun;
  if(run.status==='completed') return {...run,completed_cases:queries.length,total_cases:queries.length};
  const completed=new Set(exp.case_results.map(c=>c.case_id));
  const pending=queries.filter(q=>!completed.has(q.query_id)).slice(0,5);
  const queryVectors=exp.configuration.retrieval.mode==='keyword'?null:await goldVectors();
  const statements=[];
  for(const q of pending) {
    const opts=options({...exp.configuration.retrieval,query:q.question,embedding_model:exp.configuration.models.embedding});
    const response=await search(opts,queryVectors?.[queries.indexOf(q)]);
    const retrieved=response.results.map(r=>r.parent.chunk_id);
    const index=retrieved.findIndex(id=>q.expected_parent_ids.includes(id));
    const result:EvaluationCaseResult={case_id:q.query_id,query:q.question,expected_document_id:q.expected_document_id,expected_page:q.expected_page,expected_terms:q.expected_terms,resolved_expected_parent_ids:q.expected_parent_ids,retrieved_parent_ids:retrieved,answer:null,citations:[],metrics:{retrieval_hit:index>=0?1:0,reciprocal_rank:index>=0?1/(index+1):0,citation_precision:null,groundedness:null,answer_correctness:null},latency_ms:response.index_latency_ms,tokens:0,error:null};
    statements.push(db.prepare('INSERT INTO evaluation_cases(id,run_id,payload) VALUES(?,?,?) ON CONFLICT(id) DO NOTHING').bind(id+':'+q.query_id,id,JSON.stringify(result)));
  }
  if(statements.length)await db.batch(statements);
  const rows=await db.prepare('SELECT payload FROM evaluation_cases WHERE run_id=?').bind(id).all<{payload:string}>();
  const cases=rows.results.map(r=>JSON.parse(r.payload) as EvaluationCaseResult);
  const finished=cases.length===queries.length;
  const updated:EvaluationRun={...run,status:finished?'completed':'running',completed_at:finished?new Date().toISOString():null,metrics:finished?metrics(cases):null,error:null};
  const {case_results,evaluation_runs,...summary}=exp;
  void case_results;void evaluation_runs;
  await db.batch([
    db.prepare("UPDATE evaluations SET payload=? WHERE id=? AND json_extract(payload,'$.status') != 'completed'").bind(JSON.stringify(updated),id),
    db.prepare("UPDATE experiments SET payload=? WHERE id=? AND json_extract(payload,'$.status') != 'completed'").bind(JSON.stringify({...summary,status:updated.status,completed_at:updated.completed_at,metrics:updated.metrics}),id),
  ]);
  return {...updated,completed_cases:cases.length,total_cases:queries.length};
}
export async function compare(input:unknown):Promise<ExperimentCompareResponse> {
  const p=object(input), baselineId=textField(p.baseline_experiment_id);
  if(!Array.isArray(p.candidate_experiment_ids)||p.candidate_experiment_ids.length<1||p.candidate_experiment_ids.length>4)throw new ApiError(400,'invalid_request','비교할 실험을 1–4개 선택해 주세요.');
  const ids=[baselineId,...p.candidate_experiment_ids.map(id=>textField(id))];
  if(new Set(ids).size!==ids.length)throw new ApiError(400,'duplicate_experiment','서로 다른 실험을 선택해 주세요.');
  const experiments=await Promise.all(ids.map(detail));
  if(experiments.some(e=>e.status!=='completed'||e.dataset_id!==experiments[0].dataset_id||e.configuration.index_version!==experiments[0].configuration.index_version))throw new ApiError(409,'incomparable','같은 데이터와 인덱스로 평가를 완료한 실험끼리 비교할 수 있습니다.');
  const names:MetricName[]=['retrieval_recall_at_k','mean_reciprocal_rank','ndcg_at_k','p50_latency_ms'];
  const baseline=experiments[0];
  return {baseline_experiment_id:baselineId,experiments,metric_deltas:names.map(metric=>({metric,higher_is_better:metric!=='p50_latency_ms',baseline_value:baseline.metrics?.[metric]??null,candidates:experiments.slice(1).map(e=>({experiment_id:e.id,value:e.metrics?.[metric]??null,delta_from_baseline:(e.metrics?.[metric]??0)-(baseline.metrics?.[metric]??0)}))})),case_deltas:baseline.case_results.map(c=>({case_id:c.case_id,query:c.query,metric:'mean_reciprocal_rank',baseline_value:c.metrics.reciprocal_rank,candidates:experiments.slice(1).map(e=>{const match=e.case_results.find(x=>x.case_id===c.case_id);return {experiment_id:e.id,value:match?.metrics.reciprocal_rank??null,delta_from_baseline:(match?.metrics.reciprocal_rank??0)-(c.metrics.reciprocal_rank??0)};})}))};
}
