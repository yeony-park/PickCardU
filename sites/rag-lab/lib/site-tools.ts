export type SiteTool={name:string;title:string;description:string;inputSchema:object;annotations:{readOnlyHint:boolean;untrustedContentHint:boolean};execute:(input:unknown)=>Promise<unknown>};
type ModelContext={registerTool:(tool:SiteTool,options:{signal:AbortSignal})=>void|Promise<void>};
export function registerSiteTools(tools:SiteTool[],status:(available:boolean)=>void){
 const context=(document as Document & {modelContext?:ModelContext}).modelContext;
 if(!context?.registerTool){status(false);return ()=>{};}
 const controller=new AbortController();
 Promise.all(tools.map(tool=>context.registerTool(tool,{signal:controller.signal}))).then(()=>status(true)).catch(()=>{controller.abort();status(false);});
 return ()=>controller.abort();
}
