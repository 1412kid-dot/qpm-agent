import test from 'node:test';import assert from 'node:assert/strict';
import {Config,prepareTask} from '../.fixtures/runtime/node_modules/dsh-qpm-agent/lib/config.js';
import {apply} from '../.fixtures/runtime/node_modules/dsh-qpm-agent/lib/tools.js';
import QpmRuntime from '../.fixtures/runtime/node_modules/dsh-qpm-agent/lib/index.js';
import {spawn} from 'node:child_process';import fs from 'node:fs/promises';import path from 'node:path';
const core=path.resolve('..'),root=path.resolve('.fixtures/results');
const defaults={pythonExecutable:(process.env.QPM_TEST_PYTHON||'python'),pythonModuleRoot:core,outputRoot:root,extensionsRoot:path.resolve('.fixtures/extensions'),defaultBackend:'demo',enableComsol:false,lengthMm:5,wavelengthStepNm:20,dispersionStepNm:2,fwDerivativeNm:2,shDerivativeNm:1,thicknessDerivativeNm:2,thicknessToleranceNm:0,widthToleranceNm:0,maxGeometryCount:32,maxSolverCalls:1000,maxWallTimeS:1800,maxIterations:2,threads:4,comsolVersion:'6.4',comsolBin:''};
const task={material:'LN',crystal_cut:'x',cladding:'AIR',geometry:{thickness_nm:600,etch_depth_nm:300,top_width_nm:1200},process:'SHG'};
test('SDK imports/configuration without solver',()=>{assert.ok(Config);assert.equal(prepareTask(task,defaults).length_mm,5);assert.throws(()=>prepareTask({...task,backend:'comsol'},defaults));assert.throws(()=>prepareTask({...task,budget:{max_solver_calls:1001}},defaults));});
test('native tools structured output',()=>{let rows=[];apply({effect:f=>f(),tools:{register:t=>{rows.push(t);return ()=>{};}},jobs:{},qpmRuntime:{}});assert.equal(rows.length,25);assert.equal(new Set(rows.map(r=>r.name )).size,25);const schema=rows.find(r=>r.name==='qpm_submit').parameters.properties.task;assert.equal(schema.type,'object');assert.deepEqual(schema.properties.crystal_cut.enum,['x','z']);assert.ok(schema.properties.required_metrics);});
function runtime(){const r=Object.create(QpmRuntime.prototype);r.config=defaults;r.active=new Set();r.records=new Map();r.iterations=new Map();r.running=0;r.workspaceConfig=()=>({...r.snapshot(),outputRoot:root});r.ctx={subprocess:{resolveExecutable:async p=>p,spawn(spec){const child=spawn(spec.argv[0],spec.argv.slice(1),{cwd:spec.cwd,env:{...process.env,...spec.env},windowsHide:true});const done=new Promise((resolve,reject)=>{child.once('error',reject);child.once('exit',(exitCode,signal)=>resolve({exitCode,signal}));});child.stderr.on('data',d=>console.log('worker stderr',String(d)));return {stdin:child.stdin,stdout:child.stdout,collected:{stderr:{snapshot:()=>({bytes:Buffer.alloc(0)})}},done,terminate:async()=>{child.kill();await done;},waitForExit:()=>done};}}};return r;}
test('estimate remains read-only and defaults to three requested-band points',async()=>{
 const r=runtime();const result=await r.query('estimate',{...task,wavelengths_nm:[1550],required_metrics:['gvd']});
 assert.equal(result.logical_mode_points,3);assert.equal(result.derivative_points_default,3);
});
test('durable history resume does not consume a design iteration',async()=>{
 const r=runtime(),id=(await import('node:crypto')).randomUUID(),out=path.join(root,id);await fs.mkdir(out,{recursive:true});
 const t=prepareTask({...task,wavelengths_nm:[1550],required_metrics:['gvd']},defaults);await fs.writeFile(path.join(out,'task.json'),JSON.stringify(t));
 r.iterations.set('new-session',2);let hook;
 const ctx={jobs:{start(spec){hook=spec.run({updateProgress(){},append(){}});return 'resumed-'+id;}}};
 assert.ok((await r.listRuns()).some(x=>x.legacy_id===id));
 const result=await r.resume(ctx,{agent:{id:'new-session'}},id);await hook.done;
 assert.equal(r.status(result.job_id,'new-session').status,'completed');assert.equal(r.iterations.get('new-session'),2);
 await assert.rejects(()=>r.resume(ctx,{agent:{id:'new-session'}},'../outside'));
 await assert.rejects(()=>r.resume(ctx,{agent:{id:'new-session'}},id,{max_wall_time_s:defaults.maxWallTimeS+1}));
});
test('transient process recovery is bounded and does not consume another iteration',async()=>{
 const r=runtime();let calls=0,hook;
 r.launch=async()=>({cancel:async()=>{},done:++calls===1?Promise.reject(Error('subprocess-local: unexpected process exit')):Promise.resolve({completion:'completed'})});
 const ctx={jobs:{start(spec){hook=spec.run({updateProgress(){},append(){}});return 'recover';}}};
 r.submit(ctx,{agent:{id:'recover-owner'}},task);await hook.done;
 assert.equal(calls,2);assert.equal(r.record('recover','recover-owner').recovery_count,1);assert.equal(r.iterations.get('recover-owner'),1);
});
test('budget exhaustion is paused and is not blindly retried',async()=>{
 const r=runtime();let calls=0,hook;
 r.launch=async()=>({cancel:async()=>{},done:(calls++,Promise.resolve({completion:'incomplete',failures:['wall-time budget exhausted']}))});
 const ctx={jobs:{start(spec){hook=spec.run({updateProgress(){},append(){}});return 'paused';}}};
 r.submit(ctx,{agent:{id:'paused-owner'}},task);await hook.done;
 assert.equal(calls,1);assert.equal(r.status('paused','paused-owner').status,'paused');
});
test('Python plan, demo background, owner fence and postprocess',async()=>{const r=runtime();await fs.mkdir(root,{recursive:true});assert.ok(await r.query('capabilities'));assert.ok(await r.query('plan',task));let hook;const ctx={jobs:{start(spec){hook=spec.run({updateProgress(){},append(){}});return 'test-job';}}};assert.equal(r.submit(ctx,{agent:{id:'session-a'}},task).job_id,'test-job');assert.throws(()=>r.status('test-job','session-b'));assert.equal((await hook.done).status,'completed');const result=r.status('test-job','session-a');assert.ok(result.result.result_file);assert.ok((await fs.readFile(result.result.result_file,'utf8')).includes('curves'));assert.ok(await r.postprocess('test-job','session-a'));assert.equal(r.running,0);});
test('budget and iteration limits',()=>{const r=runtime();r.running=1;assert.throws(()=>r.submit({}, {agent:{id:'a'}},task));r.running=0;r.iterations.set('a',2);assert.throws(()=>r.submit({}, {agent:{id:'a'}},task));});
test('extension stage does not execute',async()=>{const r=runtime();const s=await r.stageExtension('a','sample','raise RuntimeError("must not execute")','assert False');assert.equal(s.status,'pending_human_review');assert.ok(await fs.readFile(path.join(s.directory,'extension.py'),'utf8'));});




test('approved extension must test first, pins digest and rechecks budgets',async()=>{const r=runtime();r.config={...defaults,approvedExtensions:''};const s=await r.stageExtension('a','adapter-'+Date.now(),'def adapt_task(task):\n    return {**task, "length_mm": 7}\n','from extension import adapt_task\ndef test_length():\n    assert adapt_task({})["length_mm"] == 7\n');await assert.rejects(()=>r.approvedExtension(s.review_token,'extension_test'),/未经人工/);r.config.approvedExtensions=s.review_token;await assert.rejects(()=>r.approvedExtension(s.review_token,'extension_adapt',task));const tested=await r.approvedExtension(s.review_token,'extension_test');assert.equal(tested.tests_passed,1);assert.equal((await r.approvedExtension(s.review_token,'extension_adapt',task)).length_mm,7);await fs.appendFile(path.join(s.directory,'extension.py'),'# changed\n');await assert.rejects(()=>r.approvedExtension(s.review_token,'extension_test'),/变化/);});
test('cancel before worker creation cleans reservation',async()=>{const r=runtime();let hook;const ctx={jobs:{start(spec){hook=spec.run({updateProgress(){},append(){}});return 'cancel-job';}}};r.submit(ctx,{agent:{id:'a'}},task);hook.cancel();assert.equal((await hook.done).status,'killed');assert.equal(r.running,0);assert.equal(r.status('cancel-job','a').status,'cancelled');});
test('native definition rejects malformed arguments before Python',async()=>{let rows=[];apply({effect:f=>f(),tools:{register:t=>{rows.push(t);return ()=>{};}},jobs:{},qpmRuntime:{submit(){throw Error('must not reach');}}});await assert.rejects(()=>rows.find(r=>r.name==='qpm_submit').execute({},{}));});

test('native settings card mounts and uses revision-checked persistence',async()=>{const vm=await import('node:vm');let exports,Card,options,edits;const value={...defaults,approvedExtensions:''};const scope={getSnapshot:()=>({value,revision:9,writable:true}),subscribe:()=>()=>{},mutate:async(ops,revision)=>{edits={ops,revision};return true;}};const React={useSyncExternalStore:(_s,get)=>get(),useState:v=>[v,()=>{}],useEffect(){},createElement:(type,props,...children)=>({type,props,children})};vm.runInNewContext(await fs.readFile('lib/client.js','utf8'),{window:{__ModuleLoader__:{load(module){exports=module.factory(()=>React);}}}});exports.apply({configForms:{get:ns=>{assert.equal(ns,'qpm-runtime');return scope;},whileServed:(_names,f)=>f()},effect:f=>f(),slots:{inject:(_name,f)=>f(),register:(o,c)=>{options=o;Card=c;return ()=>{};}}});assert.equal(options.id,'qpm');const tree=Card({...options.inject(),view:'details'});const nodes=[];function walk(t){if(t&&typeof t==='object'){nodes.push(t);for(const c of t.children||[])walk(c);}}walk(tree);const checkbox=nodes.find(n=>n.type==='input'&&n.props.type==='checkbox');await checkbox.props.onChange({target:{checked:false}});assert.equal(edits.revision,9);assert.equal(edits.ops[0].path[0],'enableComsol');});

test('postprocess extension review, independent tests, type checks and source preservation',async()=>{
 const r=runtime();r.config={...defaults,approvedExtensions:''};
 const conventions=await r.query('conventions',{topic:'extensions'});const e=conventions.content.example;
 const staged=await r.stageExtension('a',e.id+'-'+Date.now(),e.code,e.tests,e.metadata);
 const output=path.join(root,'analysis-source-'+Date.now());await fs.mkdir(output,{recursive:true});
 const source={task,validation:{data_kind:'SYNTHETIC DEMO',scientifically_validated:false},cost:{solver_calls:0},failures:['missing point'],curves:[
  {wavelength_nm:1530,signal_beta1_ps_mm:7,idler_beta1_ps_mm:8,valid:true},
  {wavelength_nm:1550,signal_beta1_ps_mm:null,idler_beta1_ps_mm:8,valid:false},
  {wavelength_nm:1570,signal_beta1_ps_mm:9,idler_beta1_ps_mm:8,valid:true}]};
 const result_file=path.join(output,'result.json');await fs.writeFile(result_file,JSON.stringify(source));const before=await fs.readFile(result_file);
 r.records.set('analysis-job',{owner:'a',output,status:'completed',result:{result_file}});
 await assert.rejects(()=>r.analyzeResult(staged.review_token,'analysis-job','b'),/当前会话/);
 await assert.rejects(()=>r.analyzeResult(staged.review_token,'analysis-job','a'),/未经人工/);
 r.config.approvedExtensions=staged.review_token;
 await assert.rejects(()=>r.analyzeResult(staged.review_token,'analysis-job','a'));
 assert.equal((await r.approvedExtension(staged.review_token,'extension_test')).tests_passed,3);
 await assert.rejects(()=>r.approvedExtension(staged.review_token,'extension_adapt',task),/不匹配/);
 const result=await r.analyzeResult(staged.review_token,'analysis-job','a');
 assert.deepEqual(result.analysis.curves[0].y,[-1000,null,1000]);assert.equal(result.solver_started,false);
 assert.equal(result.validation.scientifically_validated,false);assert.ok(await fs.readFile(result.figures[0]));
 assert.deepEqual(await fs.readFile(result_file),before);
 const manifest=JSON.parse(await fs.readFile(path.join(staged.directory,'manifest.json'),'utf8'));manifest.metadata.version='tampered';await fs.writeFile(path.join(staged.directory,'manifest.json'),JSON.stringify(manifest));
 await assert.rejects(()=>r.approvedExtension(staged.review_token,'extension_test'),/变化/);
});

test('adapter cannot introduce new propagation physics or required metrics',async()=>{
 const r=runtime();r.config={...defaults,approvedExtensions:''};
 const s=await r.stageExtension('a','bad-axis-'+Date.now(),'def adapt_task(task):\n    return {**task, "propagation_axis": "X"}\n','def test_smoke():\n    assert True\n');r.config.approvedExtensions=s.review_token;
 await r.approvedExtension(s.review_token,'extension_test');
 await assert.rejects(()=>r.approvedExtension(s.review_token,'extension_adapt',task),/propagation_axis/);
});

test('postprocess cannot promote source validation by mutating its context',async()=>{
 const r=runtime();r.config={...defaults,approvedExtensions:''};
 const code='def analyze_result(context):\n    context["validation"]["scientifically_validated"] = True\n    return {"metrics":[{"name":"example", "value":1,"unit":"dimensionless","definition":"test","valid":True,"source":"fixture"}]}\n';
 const s=await r.stageExtension('a','validation-'+Date.now(),code,'def test_smoke():\n    assert True\n',{kind:'postprocess',version:'1',description:'context isolation test',applicability:'fixture'});
 r.config.approvedExtensions=s.review_token;await r.approvedExtension(s.review_token,'extension_test');
 const output=path.join(root,'validation-source-'+Date.now());await fs.mkdir(output,{recursive:true});const result_file=path.join(output,'result.json');
 await fs.writeFile(result_file,JSON.stringify({task,validation:{data_kind:'SYNTHETIC DEMO',scientifically_validated:false},cost:{},failures:[]}));
 r.records.set('validation-job',{owner:'a',output,status:'completed',result:{result_file}});
 const result=await r.analyzeResult(s.review_token,'validation-job','a');assert.equal(result.validation.scientifically_validated,false);
});



test('source versions require native approval, test overlay, persist and detect edits',async()=>{
 const r=runtime();r.workspaceConfig=QpmRuntime.prototype.workspaceConfig;
 const workspace=path.resolve('.fixtures/source-version-'+Date.now());await fs.mkdir(workspace,{recursive:true});
 const exec={agent:{id:'source-review',session:{header:{cwd:workspace}}}};
 const before=await fs.readFile(path.join(core,'qpm_simulation/plotting.py'),'utf8');
 const staged=await r.stageSource(exec,{id:'paper-renderer',file:'qpm_simulation/plotting.py',code:before+'\n# reviewed rendering version\n',tests:'def test_overlay():\n    from qpm_simulation.physics import local_dispersion\n    assert local_dispersion([1548,1550,1552],[2,2,2],1550)["gvd_fs2_mm"] == 0\n',description:'Renderer version',applicability:'Exported results only'});
 let seen;const ctx={approval:{request:async request=>{seen=request.reason;return 'allowed-once';}}};
 const approved=await r.approveVersion(ctx,exec,staged.token);assert.equal(approved.status,'enabled');assert.ok(seen.includes('before'));
 const selected=r.workspaceConfig(exec);assert.ok(selected.pythonModuleRoot.endsWith('core'));
 const page=await r.source({file:'python/qpm_simulation/plotting.py',count:5},exec);assert.ok(page.sha256);
 await fs.appendFile(path.join(selected.pythonModuleRoot,'qpm_simulation/plotting.py'),'# changed');
 assert.throws(()=>r.workspaceConfig(exec),/变化/);
});

test('registered history survives workspace changes, diagnoses missing files and resolves collisions',async()=>{
 const r=runtime();r.workspaceConfig=QpmRuntime.prototype.workspaceConfig;
 const base=path.resolve('.fixtures/history-'+Date.now()),legacy=path.join(base,'legacy'),workspace=path.join(base,'new');
 await fs.mkdir(path.join(legacy,'same'),{recursive:true});await fs.mkdir(workspace,{recursive:true});
 r.config={...defaults,outputRoot:legacy,historySources:'[]'};
 const result={task,curves:[{wavelength_nm:1550,signal_gvd_fs2_mm:0}],validation:{data_kind:'SYNTHETIC DEMO'}};
 await fs.writeFile(path.join(legacy,'same/result.json'),JSON.stringify(result));
 const exec={agent:{id:'new-session',session:{header:{cwd:workspace}}}};
 const inventory=await r.historySources(exec);const old=inventory.runs.find(x=>x.legacy_id==='same');
 assert.ok(old.readable);assert.ok(old.plottable);assert.equal(old.resume_available,false);
 assert.equal((await r.historical(exec,old.run_id)).curves.length,1);
 const other=path.join(base,'other');await fs.mkdir(path.join(other,'same'),{recursive:true});
 await fs.writeFile(path.join(other,'same/result.json'),JSON.stringify(result));
 await r.historySources(exec,{add:other});await assert.rejects(()=>r.historical(exec,'same'),/AMBIGUOUS_RUN/);
 await fs.mkdir(path.join(legacy,'cache-only/cache'),{recursive:true});
 await fs.mkdir(path.join(legacy,'broken'),{recursive:true});await fs.writeFile(path.join(legacy,'broken/result.json'),'{');
 const after=await r.historySources(exec);assert.ok(after.runs.some(x=>x.legacy_id==='cache-only'&&!x.readable&&x.cache_available));
 assert.ok(after.diagnostics.some(x=>x.code==='INVALID_JSON'));
 await fs.mkdir(path.join(workspace,'QPM/.qpm/versions'),{recursive:true});await fs.writeFile(path.join(workspace,'QPM/.qpm/versions/active-source.json'),'{');
 assert.equal((await r.historical(exec,old.run_id)).curves.length,1);
 r.ctx.get=()=>({resolve:()=>({mode:'read-only'})});await assert.rejects(()=>r.derivedOutput(exec,old,'plots'),/只读/);
});

test('design identity separates tasks and explicit continuation preserves the same directory',async()=>{
 const r=runtime(),c={...defaults,outputRoot:path.resolve('.fixtures/design-identities-'+Date.now())};
 const a=r.newRunPath(task,c,'user-a'),b=r.newRunPath(task,c,'user-b');assert.notEqual(path.dirname(a),path.dirname(b));
 const id=path.basename(path.dirname(a)).split('_').at(-1);
 const next=r.newRunPath({...task,wavelengths_nm:[1540,1550,1560]},c,'user-a',id);
 assert.equal(path.dirname(next),path.dirname(a));
 assert.throws(()=>r.newRunPath(task,c,'user-a','ffffffffffffffff'),/未知/);
});
