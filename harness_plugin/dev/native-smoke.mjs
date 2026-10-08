import {Context,Service} from '../.fixtures/runtime/node_modules/@deepseek-ai/cordis/lib/index.js';
import Local from '../.fixtures/runtime/node_modules/@deepseek-ai/dsh-subprocess-local/lib/index.js';
import Jobs from '../.fixtures/runtime/node_modules/@deepseek-ai/dsh-jobs-local/lib/index.js';
import Qpm from '../.fixtures/runtime/node_modules/dsh-qpm-agent/lib/index.js';
import {apply} from '../.fixtures/runtime/node_modules/dsh-qpm-agent/lib/tools.js';
import fs from 'node:fs/promises';import path from 'node:path';
const ctx=new Context();const owner={id:'runtime-test',ctx};
class Agents extends Service{constructor(c){super(c,'agents');}get(id){return id===owner.id?owner:undefined;}}
new Agents(ctx);new Local(ctx);new Jobs(ctx,{maxConcurrentJobsPerOwner:1,retainBytes:65536,settledRetainBytes:16384,pumpPollMs:20});ctx.jobs.attachController('qpm-test');
const outputRoot=path.resolve('.fixtures/native-results');await fs.mkdir(outputRoot,{recursive:true});owner.session={header:{cwd:outputRoot}};
new Qpm(ctx,{pythonExecutable:(process.env.QPM_TEST_PYTHON||'python'),pythonModuleRoot:path.resolve('..'),outputRoot,extensionsRoot:path.resolve('.fixtures/extensions'),defaultBackend:'demo',enableComsol:false,lengthMm:5,wavelengthStepNm:20,dispersionStepNm:2,fwDerivativeNm:2,shDerivativeNm:1,thicknessDerivativeNm:2,thicknessToleranceNm:0,widthToleranceNm:0,maxGeometryCount:32,maxSolverCalls:1000,maxWallTimeS:60,maxIterations:5,threads:4,comsolVersion:'6.4',comsolBin:'',approvedExtensions:''});
const tools=new Map();ctx.provide('tools',{register(t){tools.set(t.name,t);return ()=>tools.delete(t.name);}});apply(ctx);
const exec={agent:owner,signal:new AbortController().signal};
console.log('actual desktop SDK query:',Object.keys(await tools.get('qpm_capabilities').execute({},exec)));
const task={material:'LN',crystal_cut:'x',cladding:'AIR',geometry:{thickness_nm:600,etch_depth_nm:300,top_width_nm:1200},process:'SHG',wavelengths_nm:[1550]};
await tools.get('qpm_plan').execute({task},exec);
const submitted=await tools.get('qpm_submit').execute({task},exec);console.log(submitted);
console.log('native job outcome',await ctx.jobs.wait(submitted.job_id,60000,owner.id,exec.signal));
const result=await tools.get('qpm_status').execute({job_id:submitted.job_id},exec);console.log(JSON.stringify(result));
if(result.status!=='completed')throw Error('native demo failed');
const cancelled=await tools.get('qpm_submit').execute({task:{...task,wavelengths_nm:Array.from({length:20},(_,i)=>1500+i*5)}},exec);
await new Promise(resolve=>setTimeout(resolve,500));
await tools.get('qpm_cancel').execute({job_id:cancelled.job_id},exec);
const stopped=await ctx.jobs.wait(cancelled.job_id,10000,owner.id,exec.signal);
if(stopped.status!=='killed')throw Error('native cancellation failed');
const timed=await tools.get('qpm_submit').execute({task:{...task,geometry:{...task.geometry,top_width_nm:2100+Date.now()%999},budget:{max_wall_time_s:1,max_solver_calls:1}}},exec);
await ctx.jobs.wait(timed.job_id,10000,owner.id,exec.signal);
const timedState=await tools.get('qpm_status').execute({job_id:timed.job_id},exec);
if(timedState.status!=='paused')throw Error('budget pause failed');
const inventory=await tools.get('qpm_runs').execute({},exec);const history=inventory.runs;if(!history.length)throw Error('durable runs missing');
const doneRun=history.find(r=>r.output_dir===result.output_dir);const plotted=await tools.get('qpm_plot_preset').execute({run_id:doneRun.run_id,name:'gvd_compare',preset:{series:[{metric:'signal_gvd_fs2_mm',label:'signal'},{metric:'pump_gvd_fs2_mm',label:'pump'}],title:'GVD'}},exec);if(!plotted.figures.length)throw Error('preset plotting failed');
const source=await tools.get('qpm_source').execute({file:'python/qpm_simulation/physics.py',count:5},exec);if(!source.sha256)throw Error('source read failed');
const estimate=await tools.get('qpm_estimate').execute({task:{...task,required_metrics:['gvd']}},exec);if(estimate.logical_mode_points!==3)throw Error('three-point planner failed');
ctx.qpmRuntime.config.pythonModuleRoot=path.resolve('.fixtures/missing-python-package');
let diagnostic='';try{await tools.get('qpm_environment').execute({},exec);}catch(e){diagnostic=String(e);}
if(!diagnostic.includes('No module named'))throw Error('Python startup diagnostic missing');
await fs.writeFile(path.resolve('.fixtures/native-validation.json'),JSON.stringify({sdk:'0.2.0-rc.2',demo:true,cancel:true,wall_budget:true,startup_diagnostic:true,actual_comsol_calls:0},null,2));
await ctx.fiber.dispose();console.log('native cancellation, wall budget and disposal passed');


