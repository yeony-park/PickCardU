import { env, pipeline } from '@huggingface/transformers';
import config from './embedding-config.json';
env.allowLocalModels=false;
env.backends.onnx.wasm!.numThreads=1;
let extractor:ReturnType<typeof pipeline<'feature-extraction'>>|undefined;
let queue=Promise.resolve();
self.onmessage=(event:MessageEvent<{id:string;query:string}>)=>{
  const {id,query}=event.data;
  queue=queue.then(async()=>{
    try{
      extractor??=pipeline<'feature-extraction'>('feature-extraction',config.model,{dtype:'q8',device:'wasm',revision:config.revision,progress_callback:(p)=>{
        if(p.status==='progress')self.postMessage({id,progress:`모델 준비 중 · ${Math.round(p.progress)}%`});
      }});
      const model=await extractor;
      self.postMessage({id,progress:'질문 임베딩 계산 중…'});
      const output=await model(config.query_prefix+query,{pooling:'mean',normalize:true});
      self.postMessage({id,vector:Array.from(output.data)});
    }catch(error){extractor=undefined;self.postMessage({id,error:error instanceof Error?error.message:'임베딩 계산 실패'});}
  });
};
