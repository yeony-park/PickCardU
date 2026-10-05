import { getChatGPTUser } from '../../../chatgpt-auth';
import { ApiError, object, options, search, status } from '@/lib/runtime';
import { createExperiment, detail, evaluate, listExperiments, compare, recordSearch } from '@/lib/experiments';

type Context={params:Promise<{path:string[]}>};
export const dynamic='force-dynamic';
async function handle(request:Request,context:Context) {
  const json=(value:unknown,status=200)=>Response.json(value,{status,headers:{'Cache-Control':'private, no-store'}});
  try {
    const user=await getChatGPTUser();
    if(!user)throw new ApiError(401,'unauthorized','ChatGPT 로그인이 필요합니다.');
    const path=(await context.params).path.join('/');
    if(request.method==='GET'){
      if(path==='v1/status')return json(status());
      if(path==='internal/v1/experiments')return json(await listExperiments());
      if(path.startsWith('internal/v1/experiments/'))return json(await detail(path.slice('internal/v1/experiments/'.length)));
    }
    if(request.method==='POST'){
      const origin=request.headers.get('origin');
      if(request.headers.get('sec-fetch-site')==='cross-site'||(origin&&origin!==new URL(request.url).origin))throw new ApiError(403,'invalid_origin','같은 사이트에서 요청해 주세요.');
      if(!request.headers.get('content-type')?.startsWith('application/json'))throw new ApiError(415,'invalid_content_type','JSON 요청이 필요합니다.');
      const raw=await request.text();
      if(raw.length>50000)throw new ApiError(413,'request_too_large','요청이 너무 큽니다.');
      let body;try{body=object(JSON.parse(raw));}catch{throw new ApiError(400,'invalid_json','요청 형식을 확인해 주세요.');}
      if(path==='v1/search')return json(await recordSearch(await search(options(body),body.query_vector),user));
      if(path==='internal/v1/experiments')return json(await createExperiment(body,user));
      if(path==='internal/v1/evaluate')return json(await evaluate(body,user));
      if(path==='internal/v1/experiments/compare')return json(await compare(body));
    }
    throw new ApiError(404,'not_found','요청한 기능을 찾지 못했습니다.');
  }catch(error){
    if(error instanceof ApiError)return json({ok:false,error:{code:error.code,message:error.message}},error.status);
    console.error('RAG operation failed',error);
    return json({ok:false,error:{code:'service_unavailable',message:'검색 또는 공동 저장소에 연결하지 못했습니다. 입력은 유지됩니다. 잠시 후 다시 시도해 주세요.'}},503);
  }
}
export const GET=handle;
export const POST=handle;
