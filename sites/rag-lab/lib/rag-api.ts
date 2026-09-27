import type { ErrorResponse,EvaluationRun,ExperimentDetail,ExperimentListResponse,ExperimentCompareRequest,ExperimentCompareResponse,SearchRequest,SearchResponse,StatusResponse } from './contracts';
import manifest from './corpus-manifest.json';
import embeddingConfig from './embedding-config.json';
import {embedQuery} from './browser-embedding';
export type SavedSearch=SearchResponse & {search_run_id:string};
export type EvaluationProgress=EvaluationRun & {completed_cases:number;total_cases:number};
async function jsonRequest<T>(path:string,init?:RequestInit):Promise<T>{
 const response=await fetch(`/api/rag/${path}`,{...init,cache:'no-store',headers:{'Content-Type':'application/json',...init?.headers}});
 const body=await response.json() as T|ErrorResponse;
 if(!response.ok)throw new Error((body as ErrorResponse).error?.message??'요청에 실패했습니다.');
 return body as T;
}
const post=<T>(path:string,body:unknown)=>jsonRequest<T>(path,{method:'POST',body:JSON.stringify(body)});
export const getStatus=()=>jsonRequest<StatusResponse>('v1/status');
export async function runSearch(request:SearchRequest,onProgress?:(message:string)=>void):Promise<SavedSearch>{
 if(request.mode&&request.mode!=='keyword'&&JSON.stringify(Object.entries(embeddingConfig).sort())!==JSON.stringify(Object.entries(manifest.embedding_config).sort()))throw new Error('임베딩 설정이 인덱스와 다릅니다. 문서·질문 벡터를 다시 생성해 주세요.');
 const start=performance.now();
 const query_vector=request.mode&&request.mode!=='keyword'?await embedQuery(request.query,onProgress):undefined;
 const embeddingMs=performance.now()-start;
 onProgress?.('카드 문서 검색 중…');
 const response=await post<SavedSearch>('v1/search',{...request,query_vector,embedding_model:manifest.model,index_version:manifest.version});
 return {...response,embedding_latency_ms:query_vector?embeddingMs:null};
}
export const listExperiments=()=>jsonRequest<ExperimentListResponse>('internal/v1/experiments');
export const getExperiment=(id:string)=>jsonRequest<ExperimentDetail & {search_snapshot:SearchResponse}>(`internal/v1/experiments/${encodeURIComponent(id)}`);
export const createExperiment=(request:{name:string;search_run_id:string})=>post<ExperimentDetail>('internal/v1/experiments',request);
export async function evaluateExperiment(request:{experiment_id:string},progress?:(run:EvaluationProgress)=>void){
 let run:EvaluationProgress;
 do{run=await post<EvaluationProgress>('internal/v1/evaluate',request);progress?.(run);}while(run.status==='running'||run.status==='queued');
 return run;
}
export const compareExperiments=(request:ExperimentCompareRequest)=>post<ExperimentCompareResponse>('internal/v1/experiments/compare',request);
