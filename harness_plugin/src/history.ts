import path from 'node:path';
import {readFile,readdir,lstat,realpath} from 'node:fs/promises';
import {createHash} from 'node:crypto';

const sha=s=>createHash('sha256').update(s).digest('hex');
const canonical=p=>path.resolve(p).toLowerCase();
export function historyConfig(runtime,exec){return exec?.agent?.session?.header?.cwd?runtime.workspaceConfig(exec,true):runtime.snapshot();}
export async function sources(runtime,exec){
 const c=historyConfig(runtime,exec),original=runtime.snapshot(),roots=[c.outputRoot,original.outputRoot],source_diagnostics=[];
 for(let current=original.pythonModuleRoot,depth=0;current&&depth<6;current=path.dirname(current),depth++){if(/^QPM-Agent-0\.[12]\.0$/i.test(path.basename(current))){roots.push(path.join(current,'results'));break;}}
 let configured=[];try{configured=JSON.parse(original.historySources||'[]');if(!Array.isArray(configured)||configured.some(p=>typeof p!=='string'||!path.isAbsolute(p)))throw Error('historySources必须为绝对路径数组');}catch(e){configured=[];source_diagnostics.push({code:'INVALID_HISTORY_CONFIG',message:e.message});}
 roots.push(...configured);
 const registry=path.join(c.outputRoot,'.qpm','history-sources.json');
 let registered=[];try{registered=JSON.parse((await readFile(registry,'utf8')).replace(/^\uFEFF/,''));roots.push(...registered);}catch(e){registered=[];if(e.code!=='ENOENT')source_diagnostics.push({path:registry,code:'INVALID_HISTORY_REGISTRY',message:e.message});}
 for(const root of [...roots]){
  const parent=path.dirname(root);
  if(/^QPM-Agent-0\.[12]\.0$/i.test(path.basename(parent))){
   for(const name of ['QPM-Agent-0.1.0','QPM-Agent-0.2.0'])roots.push(path.join(path.dirname(parent),name,'results'));
  }
 }
 const unique=new Map();for(const root of roots.filter(Boolean)){const key=canonical(root);if(!unique.has(key))unique.set(key,{source_id:'src-'+sha(key).slice(0,12),root:path.resolve(root)});}
 return {sources:[...unique.values()],registry,registered,source_diagnostics};
}
export async function index(runtime,exec){
 const inventory=await sources(runtime,exec),runs=[],diagnostics=[...inventory.source_diagnostics];const seen=new Set();
 for(const source of inventory.sources){
  async function json(file){try{return JSON.parse((await readFile(file,'utf8')).replace(/^\uFEFF/,''));}catch(e){if(e.code!=='ENOENT')diagnostics.push({source_id:source.source_id,path:file,code:e.code||'INVALID_JSON',message:e.message});return null;}}
  async function visit(base,depth=0){
   if(depth>8){diagnostics.push({path:base,code:'DEPTH_LIMIT'});return;}
   let entries;try{if((await lstat(base)).isSymbolicLink()){diagnostics.push({path:base,code:'LINK_SKIPPED'});return;}entries=await readdir(base,{withFileTypes:true});}catch(e){diagnostics.push({source_id:source.source_id,path:base,code:e.code,message:e.message});return;}
   const names=new Set(entries.map(e=>e.name)),marker=['task.json','result.json','checkpoint.json'].some(n=>names.has(n))||names.has('cache');
   if(marker){
    const physical=canonical(base);if(seen.has(physical))return;seen.add(physical);
    const taskFile=await json(path.join(base,'task.json')),result=await json(path.join(base,'result.json')),checkpoint=await json(path.join(base,'checkpoint.json')),record=await json(path.join(base,'plugin-job.json'))||{};
    const task=taskFile||result?.task||checkpoint?.task;const manifest=await json(path.join(base,'run-manifest.json'));const design=await json(path.join(path.dirname(base),'design.json'));
    const live=[...runtime.records.values()].some(r=>canonical(r.output)===physical&&['starting','running'].includes(r.status));
    const status=live?'running':result&&!result.failures?.length?'completed':['starting','running'].includes(record.status)?'interrupted':record.status||'incomplete';
    const relative=path.relative(source.root,base)||'.',run_id=source.source_id+':'+Buffer.from(relative).toString('base64url');
    runs.push({run_id,design_id:manifest?.design_id||design?.design_id||'legacy-'+sha(physical).slice(0,16),legacy_id:relative,source_id:source.source_id,source_root:source.root,output_dir:base,status,material:task?.material,cut:task?.crystal_cut,task_kind:task?.task_kind,task,
     readable:!!result,plottable:!!result&&(!!result.curves?.length||!!result.spectral),resume_available:!!task&&status!=='running'&&status!=='completed',cache_available:names.has('cache'),
     unavailable_reason:!result?'缺少可读取的result.json':null,cost:checkpoint?.cost||result?.cost||record.progress?.cost||null});return;
   }
   for(const entry of entries)if(entry.isDirectory()&&!entry.isSymbolicLink()&&!['plots','figures','cache','.qpm','extensions','backups','runtime','.install-work','presets'].includes(entry.name))await visit(path.join(base,entry.name),depth+1);
  }
  await visit(source.root);
 }
 return {...inventory,runs,diagnostics};
}
export async function resolve(runtime,exec,id){
 if(typeof id!=='string'||!id)throw Error('需要run_id');const data=await index(runtime,exec);
 const matches=data.runs.filter(r=>r.run_id===id||r.legacy_id===id||path.basename(r.output_dir)===id);
 if(matches.length!==1)throw Error(JSON.stringify({code:matches.length?'AMBIGUOUS_RUN':'RUN_NOT_FOUND',candidates:matches.map(r=>({run_id:r.run_id,output_dir:r.output_dir})),sources:data.sources,diagnostics:data.diagnostics}));
 return matches[0];
}
