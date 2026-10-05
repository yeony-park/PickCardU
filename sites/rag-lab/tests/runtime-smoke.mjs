import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
const base=process.env.RAG_TEST_ORIGIN??'http://127.0.0.1:5176';
assert.ok(['127.0.0.1','localhost','[::1]'].includes(new URL(base).hostname),'Runtime smoke tests must use a local test server, never the hosted site.');
const headers=user=>({'Content-Type':'application/json','oai-authenticated-user-id':user,'oai-authenticated-user-email':user+'@example.test'});
async function call(path,body,user='test-alpha',extra={}){
 const r=await fetch(base+'/api/rag/'+path,{method:body?'POST':'GET',headers:{...headers(user),...extra},body:body?JSON.stringify(body):undefined});
 return {status:r.status,data:await r.json()};
}
assert.equal((await fetch(base+'/api/rag/v1/status')).status,401);
assert.equal((await call('v1/search',{query:'대중교통 할인',mode:'keyword'},'test-alpha',{'Origin':'https://untrusted.example'})).status,403);
assert.equal((await call('v1/search',{query:'test',top_k:99})).status,400);
assert.equal((await call('v1/search',{query:'test',mode:'vector',query_vector:[1,2]})).status,400);
const gold=JSON.parse(await readFile('public/corpus/gold.json','utf8'));
const vectors=JSON.parse(await readFile('public/corpus/gold-vectors.json','utf8'));
const saved=[];
for(const mode of ['keyword','vector','hybrid','weighted']){
 const response=await call('v1/search',{query:gold[0].question,mode,alpha:0.7,query_vector:mode==='keyword'?undefined:vectors[0]});
 assert.equal(response.status,200,JSON.stringify(response.data));assert.ok(response.data.results.length);
 const request={name:'Runtime check · '+mode,search_run_id:response.data.search_run_id};
 assert.equal((await call('internal/v1/experiments',request,'test-beta')).status,404);
 const created=await call('internal/v1/experiments',request);
 assert.equal(created.status,200);assert.equal(created.data.configuration.retrieval.mode,mode);
 assert.equal((await call('internal/v1/experiments',request)).data.id,created.data.id);
 const id=created.data.id;saved.push(id);
 const seen=await call('internal/v1/experiments',undefined,'test-beta');
 assert.ok(seen.data.items.some(x=>x.id===id));
 let evaluated=await call('internal/v1/evaluate',{experiment_id:id},'test-beta');
 assert.equal(evaluated.status,200,JSON.stringify(evaluated.data));assert.equal(evaluated.data.completed_cases,5);
 // A different actor and fresh request resume the same durable evaluation.
 do{evaluated=await call('internal/v1/evaluate',{experiment_id:id});assert.equal(evaluated.status,200,JSON.stringify(evaluated.data));}while(evaluated.data.status==='running');
 assert.equal(evaluated.data.metrics.case_count,130);
 assert.equal((await call('internal/v1/evaluate',{experiment_id:id})).data.completed_cases,130);
 console.log(JSON.stringify({mode,status:evaluated.data.status,metrics:evaluated.data.metrics}));
}
const compared=await call('internal/v1/experiments/compare',{baseline_experiment_id:saved[0],candidate_experiment_ids:saved.slice(1)},'test-beta');
assert.equal(compared.status,200);assert.equal(compared.data.case_deltas.length,130);
await writeFile('tests/runtime-report.json',JSON.stringify({authentication:'401 without identity; 403 cross-origin',sharing:'actor beta reads actor alpha experiment',snapshot:'server-owned configuration; cannot claim another actor search',evaluation:'resumable, idempotent, 130 cases per mode',modes:compared.data.experiments.map(({name,configuration,metrics})=>({name,configuration,metrics}))},null,2));
console.log('All four modes, independent actors, save retry, resumable evaluation and comparison passed.');
