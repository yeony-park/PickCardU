'use client';
import {useCallback,useEffect,useRef,useState} from 'react';
import Link from 'next/link';
import type {SearchMode,SearchRequest,StatusResponse} from '@/lib/contracts';
import {createExperiment,evaluateExperiment,getStatus,runSearch,type SavedSearch} from '@/lib/rag-api';
import {registerSiteTools} from '@/lib/site-tools';
import {documentSource,documentUrl} from '@/lib/document-source';
import embeddingConfig from '@/lib/embedding-config.json';

const labels:Record<SearchMode,string>={keyword:'Keyword',vector:'Vector',hybrid:'Hybrid',weighted:'Weighted'};
const descriptions:Record<SearchMode,string>={keyword:'문서의 단어 일치 · BM25',vector:'질문과 문서의 의미 유사도 · Cosine',hybrid:'두 검색의 순위를 결합 · RRF',weighted:'정규화한 점수를 가중 합산'};
export function Playground({user}:{user:{displayName:string;email:string}}){
 const [status,setStatus]=useState<StatusResponse|null>(null),[mode,setMode]=useState<SearchMode>('keyword');
 const [alpha,setAlpha]=useState(0.5),[topK,setTopK]=useState(5),[candidateK,setCandidateK]=useState(50);
 const [query,setQuery]=useState(''),[name,setName]=useState(''),[result,setResult]=useState<SavedSearch|null>(null);
 const [error,setError]=useState(''),[progress,setProgress]=useState(''),[message,setMessage]=useState('');
 const [busy,setBusy]=useState(false),[saving,setSaving]=useState(false),[toolsReady,setToolsReady]=useState(false);
 const lock=useRef(false),saveLock=useRef(false);
 useEffect(()=>{getStatus().then(setStatus).catch(e=>setError(e.message));},[]);
 const execute=useCallback(async(request:SearchRequest)=>{
   if(lock.current)throw new Error('현재 검색이 진행 중입니다.');
   if(!request||typeof request.query!=='string'||!request.query.trim()||request.query.length>2000)throw new Error('질문을 1–2,000자로 입력해 주세요.');
   const m=request.mode??'keyword',k=request.top_k??5,c=request.candidate_k??50,a=request.alpha??0.5;
   if(!Object.hasOwn(labels,m)||!Number.isInteger(k)||k<1||k>20||!Number.isInteger(c)||c<k||c>200||typeof a!=='number'||!Number.isFinite(a)||a<0||a>1)throw new Error('검색 설정이 유효하지 않습니다.');
   lock.current=true;setBusy(true);setError('');setMessage('');setProgress('검색 준비 중…');
   setQuery(request.query);setMode(request.mode??'keyword');setAlpha(request.alpha??0.5);setTopK(request.top_k??5);setCandidateK(request.candidate_k??50);
   try{const response=await runSearch(request,setProgress);setResult(response);return response;}
   catch(e){setError(e instanceof Error?e.message:'검색에 실패했습니다.');throw e;}
   finally{lock.current=false;setBusy(false);setProgress('');}
 },[]);
 const save=useCallback(async(experimentName:string)=>{
   if(!result)throw new Error('검색을 먼저 실행해 주세요.');
   if(saveLock.current)throw new Error('실험을 저장하고 있습니다.');
   saveLock.current=true;setSaving(true);setError('');setMessage('실험 저장 중…');
   try{
     const experiment=await createExperiment({name:experimentName.trim()||`${labels[result.mode]} · ${result.query.slice(0,30)}`,search_run_id:result.search_run_id});
     setMessage('공동 저장 완료 · 평가 시작…');
     const evaluation=await evaluateExperiment({experiment_id:experiment.id},r=>setMessage(`공동 저장 완료 · 평가 ${r.completed_cases}/${r.total_cases}`));
     setMessage('130개 질문 평가 완료 · 팀 비교에서 확인하세요.');
     return {experiment_id:experiment.id,status:evaluation.status,metrics:evaluation.metrics};
   }catch(e){setError(e instanceof Error?e.message:'저장에 실패했습니다.');setMessage('저장된 실험은 팀 비교에서 평가를 이어갈 수 있습니다.');throw e;}
   finally{saveLock.current=false;setSaving(false);}
 },[result]);
 useEffect(()=>registerSiteTools([
   {name:'rag_search',title:'카드 문서 검색',description:'실제 카드 문서를 Keyword, Vector, Hybrid, Weighted로 검색하고 화면에 근거를 표시합니다. 검색 실행 기록이 공동 저장소에 저장됩니다. 공개 모델만 사용하며 API 키가 필요 없습니다.',inputSchema:{type:'object',properties:{query:{type:'string',minLength:1,maxLength:2000},mode:{type:'string',enum:Object.keys(labels)},top_k:{type:'integer',minimum:1,maximum:20},candidate_k:{type:'integer',minimum:1,maximum:200},alpha:{type:'number',minimum:0,maximum:1}},required:['query'],additionalProperties:false},annotations:{readOnlyHint:false,untrustedContentHint:true},execute:async(input)=>{const r=await execute(input as SearchRequest);return {query:r.query,mode:r.mode,results:r.results,search_run_id:r.search_run_id};}},
   {name:'rag_save_and_evaluate',title:'실험 저장 및 평가',description:'현재 화면의 마지막 검색 설정과 결과를 팀 공용 실험으로 저장하고 130개 고정 질문으로 평가합니다.',inputSchema:{type:'object',properties:{name:{type:'string',minLength:1,maxLength:120}},required:['name'],additionalProperties:false},annotations:{readOnlyHint:false,untrustedContentHint:true},execute:async(input)=>{const n=(input as {name?:unknown})?.name;if(typeof n!=='string'||!n.trim()||n.length>120)throw new Error('실험 이름이 필요합니다.');setName(n);return save(n);}},
 ],setToolsReady),[execute,save]);
 return <main className="lab-shell">
  <header className="lab-header"><div className="brand-lockup"><span className="brand-mark" aria-hidden="true"><i/><i/></span><span>PickCardU</span><b>RAG LAB</b></div><nav aria-label="메뉴"><Link className="active" href="/">검색 실험</Link><Link href="/compare">팀 비교</Link></nav><div className="header-status"><span title={user.email}>{user.displayName}</span><a href="/signout-with-chatgpt?return_to=/" target="_top">로그아웃</a></div></header>
  <section className="workspace">
   <aside className="config-panel"><div className="panel-heading"><div><span className="eyebrow">EXPERIMENT</span><h2>검색 설정</h2></div><span className="env-badge">TEAM</span></div>
    <fieldset disabled={busy||saving}><legend>Search mode</legend><div className="mode-switch four-modes">{(Object.keys(labels) as SearchMode[]).map(m=><button type="button" key={m} aria-pressed={mode===m} className={mode===m?'selected':''} onClick={()=>setMode(m)}>{labels[m]}</button>)}</div><p className="mode-description">{descriptions[mode]}</p>
    <div className="slider-heading"><label htmlFor="alpha">Vector 가중치</label><strong>{Math.round(alpha*100)}%</strong></div><input id="alpha" className="weight-range" type="range" min="0" max="1" step="0.05" value={alpha} disabled={mode!=='weighted'} onChange={e=>setAlpha(Number(e.target.value))}/><div className="weight-labels"><span>Keyword {Math.round((1-alpha)*100)}%</span><span>Vector {Math.round(alpha*100)}%</span></div>
    <div className="field-grid"><label>Top K<input type="number" min={1} max={20} value={topK} onChange={e=>setTopK(Number(e.target.value))}/></label><label>Candidate K<input type="number" min={topK} max={200} value={candidateK} onChange={e=>setCandidateK(Number(e.target.value))}/></label></div></fieldset>
    <div className="model-card"><span className="eyebrow">EMBEDDING</span><strong>{embeddingConfig.model.split('/').pop()}</strong><p>{embeddingConfig.dimensions}차원 · 공개 모델 · API 키 불필요</p><small>첫 벡터 검색 시 모델을 내려받습니다. 질문 임베딩은 이 브라우저에서 계산됩니다.</small></div>
    <div className="index-facts"><div><span>문서</span><strong>{status?.index.documents??'—'}</strong></div><div><span>상위 청크</span><strong>{status?.index.parents.toLocaleString()??'—'}</strong></div><div><span>검색 청크</span><strong>{status?.index.children.toLocaleString()??'—'}</strong></div></div>
    <p className="agent-note">문서 기준: <a href={`${documentSource.repository}/tree/${documentSource.commit}/${documentSource.directory}`} target="_blank" rel="noreferrer">PickCardU · {documentSource.ref} · {documentSource.commit.slice(0,7)}</a><br/>원문 106개 검증 · 저장소 변경은 다음 인덱스 배포 시 반영됩니다.</p>
    <p className="agent-note">{toolsReady?'Codex·Work 사이트 도구 연결됨':'Codex·Work의 내장 브라우저에서 사이트 도구를 사용할 수 있습니다.'} <a href="https://learn.chatgpt.com/docs/webmcp" target="_blank" rel="noreferrer">사용 안내</a></p>
   </aside>
   <section className="conversation"><div className="conversation-intro"><span className="eyebrow">RETRIEVAL PLAYGROUND</span><h1>같은 질문,<br/><em>다른 검색 방식.</em></h1><p>카드 문서에서 근거를 찾고, 검색 설정을 팀과 비교하세요.</p></div>
    <form className="composer" onSubmit={e=>{e.preventDefault();void execute({query,mode,top_k:topK,candidate_k:candidateK,alpha}).catch(()=>{});}}><label htmlFor="question">검색 질문</label><textarea id="question" value={query} maxLength={2000} disabled={busy||saving} rows={3} placeholder="예: 대중교통과 편의점 할인이 있는 카드는?" onChange={e=>setQuery(e.target.value)} onKeyDown={e=>{if((e.metaKey||e.ctrlKey)&&e.key==='Enter')e.currentTarget.form?.requestSubmit();}}/><div><span>⌘ / Ctrl + Enter</span><button type="submit" disabled={!status||!query.trim()||busy||saving}>{busy?'검색 중…':'검색 실행'} ↗</button></div></form>
    {!result&&!busy?<div className="query-examples">{['대중교통 할인','해외 결제 수수료','스타벅스 할인'].map(q=><button key={q} type="button" onClick={()=>setQuery(q)}>{q}</button>)}</div>:null}
    {progress?<p className="save-message" role="status">{progress}</p>:null}{error?<p className="run-error" role="alert">{error}</p>:null}
    {result?<section className="search-summary"><span className="eyebrow">{labels[result.mode].toUpperCase()} RESULT</span><h2>{result.query}</h2><p>근거 {result.results.length}개 · 검색 계산 {result.index_latency_ms.toFixed(1)} ms{result.embedding_latency_ms!=null?` · 질문 임베딩 ${(result.embedding_latency_ms/1000).toFixed(1)}초`:''}</p><p>Top K {result.top_k} · Candidate K {result.candidate_k}{result.mode==='weighted'?` · α ${result.alpha}`:''}</p>
     <label className="field-label" htmlFor="experiment-name">팀에 저장할 실험 이름</label><input id="experiment-name" maxLength={120} value={name} placeholder={`${labels[result.mode]} 실험`} onChange={e=>setName(e.target.value)}/><button className="trace-button save-experiment" type="button" disabled={saving||busy} onClick={()=>void save(name).catch(()=>{})}>{saving?'저장 및 평가 중…':'팀에 저장하고 130문항 평가'}</button><small>평가는 같은 고정 질문으로 검색 정확도를 측정합니다. 페이지를 닫아도 저장된 진행 상황은 팀 비교에서 이어갈 수 있습니다.</small>
    </section>:null}
    {message?<p className="save-message" role="status">{message} <Link href="/compare">팀 비교 →</Link></p>:null}
   </section>
   <aside className="evidence-panel"><div className="evidence-heading"><div><span className="eyebrow">EVIDENCE</span><h2>검색 근거</h2></div><span>{result?.results.length??0}개</span></div><div className="evidence-list">{result?.results.length?result.results.map(item=><article className="open" key={item.parent.chunk_id}><div className="evidence-rank"><span>{String(item.rank).padStart(2,'0')}</span><strong>{item.score?.toFixed(3)}</strong></div><div className="evidence-copy"><div><strong>{item.card_name}</strong><span>p. {item.page_start}{item.page_end!==item.page_start?`–${item.page_end}`:''}</span></div><p>{item.child?.text??item.parent.text}</p><div className="score-parts"><span>BM25 {item.keyword_score?.toFixed(3)??'—'}</span><span>Cosine {item.vector_score?.toFixed(3)??'—'}</span></div><p>{documentUrl(item.source_path)?<a href={documentUrl(item.source_path)!} target="_blank" rel="noreferrer">GitHub 원문 PDF 보기 ↗</a>:null}</p><details><summary>상위 청크 전체 보기</summary><p className="full-text">{item.parent.text}</p></details></div></article>):<p className="evidence-empty">{result?'일치하는 근거를 찾지 못했습니다. 질문이나 검색 방식을 바꿔 보세요.':'검색하면 카드명, 문서 페이지, 원문과 점수를 여기서 확인할 수 있습니다.'}</p>}</div></aside>
  </section>
 </main>;
}
