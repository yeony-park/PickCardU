import { env } from 'cloudflare:workers';
import { retrieve, type Corpus, type SearchOptions } from './search-engine';
import type { SearchResponse, RagConfiguration, StatusResponse } from './contracts';
import manifest from './corpus-manifest.json';
import { documentUrl } from './document-source';

export { manifest };
export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}
export function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new ApiError(400, 'invalid_request', '요청 형식을 확인해 주세요.');
  return value as Record<string, unknown>;
}
export function textField(value: unknown, max = 200): string {
  if (typeof value !== 'string' || !value.trim() || value.length > max) throw new ApiError(400, 'invalid_request', `문자열 길이는 1–${max}자여야 합니다.`);
  return value.trim();
}
export function options(value: unknown): SearchOptions {
  const p = object(value);
  const query = textField(p.query, 2000);
  const mode = p.mode ?? 'keyword', top_k = p.top_k ?? 5, candidate_k = p.candidate_k ?? 50, alpha = p.alpha ?? 0.5;
  if (!['keyword','vector','hybrid','weighted'].includes(mode as string) || !Number.isInteger(top_k) || Number(top_k) < 1 || Number(top_k) > 20 || !Number.isInteger(candidate_k) || Number(candidate_k) < Number(top_k) || Number(candidate_k) > 200 || typeof alpha !== 'number' || !Number.isFinite(alpha) || alpha < 0 || alpha > 1) throw new ApiError(400,'invalid_configuration','검색 모드, Top K, Candidate K, 가중치를 확인해 주세요.');
  if (p.index_version && p.index_version !== manifest.version) throw new ApiError(409,'index_mismatch','인덱스가 갱신되었습니다. 페이지를 새로고침해 주세요.');
  if (p.embedding_model && p.embedding_model !== manifest.model) throw new ApiError(409,'model_mismatch','현재 인덱스와 임베딩 모델이 다릅니다.');
  for (const key of ['issuer','card_name']) if (p[key] != null && typeof p[key] !== 'string') throw new ApiError(400,'invalid_filter','필터 형식이 올바르지 않습니다.');
  return {query,mode,top_k,candidate_k,alpha,issuer:p.issuer??null,card_name:p.card_name??null} as SearchOptions;
}
let corpusPromise: Promise<Corpus> | undefined;
let vectorPromise: Promise<Float32Array> | undefined;
export async function asset(name: string): Promise<Response> {
  if (!env.ASSETS) throw new Error('검색 데이터에 연결할 수 없습니다.');
  const response = await env.ASSETS.fetch(new Request(`http://localhost/corpus/${name}`));
  if (!response.ok) throw new Error('검색 데이터를 읽지 못했습니다.');
  return response;
}
export async function corpus() {
  return corpusPromise ??= asset('index.json').then(r=>r.json<Corpus>()).catch(e=>{corpusPromise=undefined;throw e;});
}
async function vectors() {
  return vectorPromise ??= asset('e5-vectors.f32').then(r=>r.arrayBuffer()).then(b=>new Float32Array(b)).catch(e=>{vectorPromise=undefined;throw e;});
}
export function configuration(p: SearchOptions): RagConfiguration {
  return {retrieval:{mode:p.mode,top_k:p.top_k,candidate_k:p.candidate_k,alpha:p.alpha,issuer:p.issuer,card_name:p.card_name,reranking:{enabled:false,top_n:20,weight:0}},models:{embedding:manifest.model,reranker:null,generation:'external-agent'},generation:{reasoning:'medium',prompt_id:'retrieval-only-v1'},index_version:manifest.version};
}
export async function search(p: SearchOptions, queryVector?: unknown): Promise<SearchResponse> {
  const started=performance.now();
  let vector: number[] | undefined;
  if(p.mode!=='keyword') {
    if(!Array.isArray(queryVector) || queryVector.length!==manifest.dimensions || queryVector.some(x=>typeof x!=='number'||!Number.isFinite(x)) || !queryVector.some(x=>x!==0)) throw new ApiError(400,'embedding_required','브라우저에서 질문 임베딩을 계산한 후 다시 시도해 주세요.');
    vector=queryVector;
  }
  const c=await corpus(), v=p.mode==='keyword'?undefined:await vectors();
  const indexStarted=performance.now();
  const results=retrieve(c,p,v,vector,manifest.dimensions).map(result=>({...result,source_url:documentUrl(result.source_path)}));
  return {ok:true,...p,latency_ms:performance.now()-started,index_latency_ms:performance.now()-indexStarted,embedding_used:p.mode!=='keyword',embedding_latency_ms:null,embedding_tokens:0,embedding_model:manifest.model,reranker_model:null,results};
}
export function status(): StatusResponse {
  const defaults=configuration({query:'status',mode:'keyword',top_k:5,candidate_k:50,alpha:0.5});
  return {ok:true,service:'rag-api',index:{documents:manifest.documents,parents:manifest.parents,children:manifest.children,version:manifest.version},embedding:{available:true,indexed:true,model:manifest.model},generation:{available:false,external:false,provider:'Codex / ChatGPT Work',model:'external-agent',reasoning:'medium',context_char_budget:0,max_parent_ids:20,transmitted_data:[]},modes:{keyword:{available:true,external:false},vector:{available:true,external:false},hybrid:{available:true,external:false},weighted:{available:true,external:false}},filters:{issuers:[]},limits:{query_chars:2000,top_k_max:20,candidate_k_max:200,answer_parent_ids_max:20},model_catalog:{embeddings:[{id:manifest.model,label:'Multilingual E5 Small · 공개 모델',provider:'Hugging Face',available:true,external:false}],rerankers:[],generation:[]},defaults};
}
