import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {retrieve} from '../lib/search-engine.ts';
const corpus=JSON.parse(readFileSync('public/corpus/index.json'));
const blob=readFileSync('public/corpus/openai-vectors.f32');
const vectors=new Float32Array(blob.buffer.slice(blob.byteOffset,blob.byteOffset+blob.byteLength));
// Original norms come from the exact preserved float32 embeddings.
corpus.norms=corpus.children.map((_,i)=>Math.sqrt(vectors.subarray(i*1536,(i+1)*1536).reduce((s,v)=>s+v*v,0)));
const fixtures=JSON.parse(readFileSync('tests/parity.json'));
for(const f of fixtures){
 const q=f.vector_ordinal===undefined?undefined:Array.from(vectors.subarray(f.vector_ordinal*1536,(f.vector_ordinal+1)*1536));
 const result=retrieve(corpus,{query:f.query,mode:f.mode,top_k:5,candidate_k:50,alpha:f.alpha??0.5},vectors,q);
 assert.deepEqual(result.map(x=>x.parent.chunk_id),f.parents.map(x=>x.chunk_id),`${f.mode}: ${f.query}`);
 result.forEach((r,i)=>assert.ok(Math.abs(r.score-f.parents[i].score)<1e-9,`${f.mode} score ${i}`));
}
console.log(`${fixtures.length} Python / TypeScript ranking and score parity cases passed.`);
