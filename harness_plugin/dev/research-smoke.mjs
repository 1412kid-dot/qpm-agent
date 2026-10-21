// Native Harness services + actual Python workers. Synthetic data only.
import {Context,Service} from '../.fixtures/runtime/node_modules/@deepseek-ai/cordis/lib/index.js';
import Local from '../.fixtures/runtime/node_modules/@deepseek-ai/dsh-subprocess-local/lib/index.js';
import Jobs from '../.fixtures/runtime/node_modules/@deepseek-ai/dsh-jobs-local/lib/index.js';
import Qpm from '../.fixtures/runtime/node_modules/dsh-qpm-agent/lib/index.js';
import {apply} from '../.fixtures/runtime/node_modules/dsh-qpm-agent/lib/tools.js';
import fs from 'node:fs/promises';import path from 'node:path';import assert from 'node:assert/strict';

const ctx=new Context(),owner={id:'research-native',ctx};
class Agents extends Service{constructor(c){super(c,'agents');}get(id){return id===owner.id?owner:undefined;}}
new Agents(ctx);new Local(ctx);new Jobs(ctx,{maxConcurrentJobsPerOwner:1,retainBytes:65536,settledRetainBytes:16384,pumpPollMs:20});
ctx.jobs.attachController('qpm-research-test');
const outputRoot=path.resolve('.fixtures/research-native');await fs.mkdir(outputRoot,{recursive:true});owner.session={header:{cwd:outputRoot}};
const config={pythonExecutable:process.env.QPM_TEST_PYTHON||'python',pythonModuleRoot:path.resolve(process.env.QPM_CORE_ROOT||'..'),outputRoot,extensionsRoot:path.join(outputRoot,'versions'),
defaultBackend:'demo',enableComsol:false,lengthMm:5,wavelengthStepNm:20,dispersionStepNm:2,fwDerivativeNm:2,shDerivativeNm:1,thicknessDerivativeNm:2,
maxGeometryCount:5,maxSolverCalls:100,maxWallTimeS:120,maxIterations:2,threads:4,comsolVersion:'6.4',comsolBin:'',approvedExtensions:''};
new Qpm(ctx,config);
const tools=new Map();ctx.provide('tools',{register(t){tools.set(t.name,t);return()=>tools.delete(t.name);}});apply(ctx);
const exec={agent:owner,signal:new AbortController().signal};
const call=(name,args={})=>tools.get(name).execute(args,exec);
const task={material:'LN',crystal_cut:'x',cladding:'SIO2',geometry:{thickness_nm:600,etch_depth_nm:300,top_width_nm:1800},
process:'SHG',center_nm:1550,wavelengths_nm:[1550],required_metrics:['gvd'],backend:'demo',objective:{metric:'gvd',role:'signal',wavelength_nm:1550,tolerance:2}};
const output=path.resolve('../development/stages_0_2/native-research.json');
try{
 const conventions=await call('qpm_conventions',{topic:'research'});await call('qpm_capabilities');
 const created=await call('qpm_study_create',{spec:{title:'Native research smoke',task,
 constraints:{top_width_nm:{min:1700,max:1900,step:50}},goal:'Exercise measured-feedback loop',
 knowledge:{capabilities_checked:true,conventions_version:'qpm-design/1'}}});
 const study_id=created.state.study_id;
 async function step(action){const job=await call('qpm_study_step',{study_id,action});
  const outcome=await ctx.jobs.wait(job.job_id,60000,owner.id,exec.signal);assert.equal(outcome.status,'completed');
  const status=await call('qpm_status',{job_id:job.job_id});if(status.error)throw Error(status.error);return call('qpm_study_status',{study_id});}
 const first=await step({kind:'evaluate',reason:'Measure nominal synthetic objective'});
 assert.equal(first.state.status,'awaiting_decision');assert.equal(first.cost.actual_comsol_calls,0);
 const diagnostic=await call('qpm_study_diagnose',{study_id});assert.ok(diagnostic.issues.some(i=>i.category==='unverified'));
 const verified=await step({kind:'verify',reason:'Compare mesh and derivative step',evidence:['r0001']});
 assert.equal(verified.state.candidates.at(-1).numerically_verified,true);
 const finished=await step({kind:'finalize'});assert.equal(finished.state.status,'completed');
 const cancelled=await call('qpm_study_create',{spec:{task,knowledge:{capabilities_checked:true,conventions_version:'qpm-design/1'}}});
 const cancellation=await call('qpm_study_step',{study_id:cancelled.state.study_id,action:{kind:'cancel'}});
 assert.equal(cancellation.status,'cancellation_requested');
 await fs.writeFile(output,JSON.stringify({native_harness_sdk:'0.2.0-rc.2',passed:true,tool_count:tools.size,
 study_id,steps:['create','evaluate','diagnose','verify','finalize','cancel'],cost:finished.cost,
 scientifically_validated:false,model_called:false,contract:conventions.contract_version},null,2));
 console.log('Native research integration passed:',study_id);
}finally{await ctx.fiber.dispose();}
