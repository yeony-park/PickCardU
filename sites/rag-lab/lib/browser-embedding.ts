let worker:Worker|undefined;
export function embedQuery(query:string,onProgress?:(message:string)=>void):Promise<number[]> {
  worker??=new Worker('/embedding-worker.js',{type:'module'});
  const activeWorker=worker,id=crypto.randomUUID();
  onProgress?.('공개 모델 준비 중 · 첫 실행 시 모델을 내려받습니다.');
  return new Promise((resolve,reject)=>{
    const clean=()=>{activeWorker.removeEventListener('message',message);activeWorker.removeEventListener('error',failure);clearTimeout(timer);};
    const message=(event:MessageEvent)=>{
      if(event.data.id!==id)return;
      if(event.data.progress)onProgress?.(event.data.progress);
      if(event.data.vector){clean();resolve(event.data.vector);}
      if(event.data.error){clean();reject(new Error('공개 모델을 불러오지 못했습니다. 인터넷 연결을 확인하고 다시 시도해 주세요.'));}
    };
    const failure=()=>{clean();activeWorker.terminate();worker=undefined;reject(new Error('이 브라우저에서 임베딩을 실행하지 못했습니다. 최신 데스크톱 브라우저에서 다시 시도해 주세요.'));};
    const timer=setTimeout(failure,300000);
    activeWorker.addEventListener('message',message);activeWorker.addEventListener('error',failure);
    activeWorker.postMessage({id,query});
  });
}
