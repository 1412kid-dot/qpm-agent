import {Context,Service} from '../.fixtures/runtime/node_modules/@deepseek-ai/cordis/lib/index.js';
import Local from '../.fixtures/runtime/node_modules/@deepseek-ai/dsh-subprocess-local/lib/index.js';
import Qpm from '../.fixtures/runtime/node_modules/dsh-qpm-agent/lib/index.js';
import fs from 'node:fs/promises';import path from 'node:path';import {randomUUID,createHash} from 'node:crypto';
const base=path.join(path.resolve('.fixtures/workspaces'),'.qpm-plot-'+randomUUID().slice(0,8));const workspace=path.join(base,'中文 工作区'),history=path.join(base,'历史');await fs.mkdir(history,{recursive:true});await fs.mkdir(workspace,{recursive:true});
const ctx=new Context();new Local(ctx);const owner={id:'plot-test',session:{header:{cwd:workspace}}},exec={agent:owner,signal:new AbortController().signal};
new Qpm(ctx,{pythonExecutable:(process.env.QPM_TEST_PYTHON||'python'),pythonModuleRoot:path.resolve('..'),outputRoot:history,historySources:'[]'});
ctx.provide('sandboxPolicy',{resolve:()=>({mode:'workspace-write',workspaceRoot:workspace})});
const r=ctx.qpmRuntime;
for(let i=0;i<2;i++){const dir=path.join(history,'r'+i);await fs.mkdir(dir);await fs.writeFile(path.join(dir,'result.json'),JSON.stringify({curves:[0,1,2].map(j=>({wavelength_nm:1500+j*(10+i),signal_gvd_fs2_mm:j===1?null:j,period_um:10+j})),spectral:{x_nm:[1,2],y_nm:[3,4],values:[[1,null],[2,3]],metric:'PMF',x_label:'signal nm',y_label:'idler nm'},validation:{data_kind:i?'real acceptance pending':'SYNTHETIC DEMO'},task:{material:'LN'}}));}
const inventory=await r.historySources(exec);const runs=inventory.runs.filter(v=>v.readable);if(runs.length!==2)throw Error('history discovery');
const sources=runs.map((v,i)=>({alias:'轮次'+i,run_id:v.run_id}));const hashes=await Promise.all(runs.map(v=>fs.readFile(path.join(v.output_dir,'result.json')).then(b=>createHash('sha256').update(b).digest('hex'))));
await fs.mkdir(path.join(workspace,'QPM','.qpm','versions'),{recursive:true});await fs.writeFile(path.join(workspace,'QPM','.qpm','versions','active-source.json'),'{broken');
const series=sources.map(s=>({source:s.alias,metric:'signal_gvd_fs2_mm'}));
const first=await r.plot(exec,{name:'两轮比较',sources,preset:{series,formats:['png','svg','pdf']},output_dir:path.join(base,'outside'),script:'plot.option(title="中文比较")'});
if(first.figures.length!==3||!first.fallback_reason||!first.output_dir.startsWith(workspace))throw Error('fallback/export failed');
await r.plot(exec,{name:'两轮比较'});
const env=await r.plotEnvironment(exec);if(!env.font_files.length)throw Error('font discovery failed');
const rebound=await r.plotPreset(exec,{name:'两轮比较',run_id:runs[1].run_id,preset:{series:[{metric:'period_um'}]}});const reboundManifest=JSON.parse(await fs.readFile(rebound.manifest,'utf8'));if(reboundManifest.sources[0].run_id!==runs[1].run_id)throw Error('explicit run ignored');
await r.plot(exec,{sources,preset:{layout:'subplots',series:[series[0],{source:sources[1].alias,metric:'period_um'}]}});
await r.plot(exec,{sources,preset:{layout:'dual_axis',series:[series[0],{source:sources[1].alias,metric:'period_um',axis:'right'}]}});
await r.plot(exec,{sources,preset:{layout:'heatmaps'}});
const longOutput=path.join(workspace,'长路径-'+ 'a'.repeat(65),'b'.repeat(65),'c'.repeat(65));const longPlot=await r.plot(exec,{sources,preset:{series,formats:['png','svg','pdf']},output_dir:longOutput});if(longPlot.figures.length!==3||longPlot.figures[0].length<260)throw Error('long output failed');
let denied=false;try{await r.plot(exec,{sources,preset:{series},script:"open('secret')"});}catch(e){denied=String(e).includes('unsupported script API');}if(!denied)throw Error('unsafe script accepted');
const link=path.join(workspace,'linked');await fs.symlink(history,link,'junction');const linked=await r.plot(exec,{sources,preset:{series},output_dir:link});if(!linked.fallback_reason)throw Error('junction not rejected');
ctx.sandboxPolicy.resolve=()=>({mode:'read-only',workspaceRoot:workspace});denied=false;try{await r.plot(exec,{sources,preset:{series}});}catch(e){denied=String(e).includes('只读');}if(!denied)throw Error('read-only not enforced');
for(let i=0;i<runs.length;i++){if(createHash('sha256').update(await fs.readFile(path.join(runs[i].output_dir,'result.json'))).digest('hex')!==hashes[i])throw Error('source changed');}
const receipt={passed:true,workspace,checks:['cross-run','registered input outside write root','fallback','three formats','saved Chinese preset','broken active solver version','subplots','dual axes','heatmaps','unsafe script rejected','junction rejected','read-only enforced','raw hashes unchanged','font discovery','explicit preset run override','long output with short process cwd/cache'],actual_comsol_calls:0};await fs.writeFile('.fixtures/plot-acceptance.json',JSON.stringify(receipt,null,2));console.log(JSON.stringify(receipt));await ctx.fiber.dispose();
