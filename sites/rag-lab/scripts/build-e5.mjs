import { pipeline, env } from '@huggingface/transformers';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
const config = JSON.parse(await readFile('lib/embedding-config.json', 'utf8'));
const model = config.model;
if (!model || !/^[a-f0-9]{40}$/.test(config.revision) || !Number.isInteger(config.dimensions) || config.dimensions < 1)
  throw Error('Use an explicit model, immutable revision and positive embedding dimensions.');
if (config.dtype !== 'q8' || config.pooling !== 'mean' || config.normalize !== true || config.max_length !== 512)
  throw Error('This E5 runner supports q8 / mean / L2 / model-tokenizer limit 512. Other architectures need runner changes.');
env.cacheDir = '.sites-runtime/models';
env.allowLocalModels = false;
await mkdir('.sites-runtime', { recursive: true });
console.log(JSON.stringify({model,revision:config.revision,status:'loading'}));
const extractor = await pipeline('feature-extraction', model, { dtype: 'q8', revision: config.revision, session_options: { intraOpNumThreads: 4, interOpNumThreads: 1 } });
const corpus = JSON.parse(await readFile('public/corpus/index.json', 'utf8'));
const gold = JSON.parse(await readFile('public/corpus/gold.json','utf8'));
const checkpointKey=createHash('sha256').update(JSON.stringify({children:corpus.children,config,gold})).digest('hex');
let done=[];
try { const saved=JSON.parse(await readFile('.sites-runtime/e5-checkpoint.json','utf8')); if(saved.key===checkpointKey)done=saved.vectors; } catch {}
const items=[...corpus.children.map(c=>config.passage_prefix+c.text),...gold.map(q=>config.query_prefix+q.question)];
const started=Date.now();
for(let i=done.length;i<items.length;i++) {
  const output=await extractor(items[i],{pooling:'mean',normalize:true});
  if (output.data.length !== config.dimensions || Array.from(output.data).some(v=>!Number.isFinite(v))) throw Error('Embedding output does not match configured dimensions.');
  done.push(Array.from(output.data));
  if(done.length%25===0 || done.length===items.length) {
    await writeFile('.sites-runtime/e5-checkpoint.json',JSON.stringify({key:checkpointKey,vectors:done}));
    console.log(JSON.stringify({embedded:done.length,total:items.length,seconds:Math.round((Date.now()-started)/1000)}));
  }
}
const vectors=new Float32Array(done.slice(0,corpus.children.length).flat());
await writeFile('public/corpus/e5-vectors.f32',Buffer.from(vectors.buffer));
corpus.norms=done.slice(0,corpus.children.length).map(v=>Math.sqrt(v.reduce((s,x)=>s+x*x,0)));
await writeFile('public/corpus/index.json',JSON.stringify(corpus));
await writeFile('public/corpus/gold-vectors.json',JSON.stringify(done.slice(corpus.children.length)));
const manifest=JSON.parse(await readFile('public/corpus/manifest.json','utf8'));
manifest.model=model;manifest.dimensions=config.dimensions;manifest.embedding_config=config;
manifest.version=createHash('sha256').update(JSON.stringify({corpus:manifest.metadata.chunk_corpus_sha256,config})).digest('hex');
for(const name of ['index.json','e5-vectors.f32','gold-vectors.json'])manifest.assets[name]=createHash('sha256').update(await readFile('public/corpus/'+name)).digest('hex');
await writeFile('public/corpus/manifest.json',JSON.stringify(manifest));
await writeFile('lib/corpus-manifest.json',JSON.stringify(manifest));
console.log(JSON.stringify({status:'completed',dimensions:config.dimensions,children:corpus.children.length,gold:gold.length,version:manifest.version}));
