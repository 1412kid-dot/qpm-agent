import {index as historyIndex,resolve as resolveRun,sources as historySources,historyConfig} from './history.js';
import { mkdirSync, existsSync, readdirSync, readFileSync, writeFileSync, renameSync } from 'node:fs';
import { Service } from '@deepseek-ai/cordis';
import { Config, snapshot, prepareTask } from './config.js';
import { mkdir, writeFile, readFile, lstat, readdir, rename, unlink, cp } from 'node:fs/promises';
import path from 'node:path';
import { randomUUID, createHash } from 'node:crypto';
import { createInterface } from 'node:readline';

async function atomicJson(file,value){const tmp=file+'.'+randomUUID()+'.tmp';await writeFile(tmp,JSON.stringify(value,null,2),'utf8');await rename(tmp,file);}
function studyView(value){
  if(!value?.state)return value;
  const events=value.state.events||[];
  return {...value,state:{...value.state,event_count:events.length,events:events.slice(-3).map(e=>({seq:e.seq,time:e.time,kind:e.kind,
    data:{run_id:e.data?.run_id,reason:e.data?.reason,evidence:e.data?.evidence}}))},events_file:path.join(value.output_dir,'events.json')};
}
export { Config };
export const name='qpm-runtime';

export default class QpmRuntime extends Service {
  static inject=['subprocess']; static Config=Config;
  constructor(ctx,config) {
    super(ctx,'qpmRuntime'); this.config=config; this.active=new Set(); this.records=new Map(); this.iterations=new Map(); this.running=0;
    ctx.effect(()=>async()=>{await Promise.allSettled([...this.active].map(worker=>worker.cancel()));});
  }
  workspaceConfig(exec,ignoreVersion=false){
    const c=this.snapshot(),cwd=exec?.agent?.session?.header?.cwd;
    if(!cwd)throw Error('请先在Harness选择工作区');
    const result={...c,outputRoot:path.resolve(cwd,'QPM'),extensionsRoot:path.resolve(cwd,'QPM','.qpm','versions')};
    if(ignoreVersion||!existsSync(path.join(result.extensionsRoot,'active-source.json')))return result;
    try{const selected=JSON.parse(readFileSync(path.join(result.extensionsRoot,'active-source.json'),'utf8'));const directory=path.join(result.extensionsRoot,selected.id,selected.digest);const grant=JSON.parse(readFileSync(path.join(directory,'approval.json'),'utf8'));const test=JSON.parse(readFileSync(path.join(directory,'test-result.json'),'utf8'));if(grant.digest!==selected.digest||grant.decision!=='allowed-once'||!test.tests_passed||test.digest!==selected.digest)throw Error('未批准源码版本');const manifest=JSON.parse(readFileSync(path.join(directory,'manifest.json'),'utf8'));for(const [file,digest] of Object.entries(manifest.core_hashes||{})){if(createHash('sha256').update(readFileSync(path.join(directory,'core',file))).digest('hex')!==digest)throw Error('源码版本发生变化；需重新审核');}
    result.pythonModuleRoot=path.join(directory,'core');result.sourceVersion=selected;}catch(e){throw Error('当前源码版本不可用：'+e.message+'；历史查询仍可使用，qpm_versions select=packaged可回退');}
    return result;
  }
  newRunPath(task,c,owner,requestedDesign){
    const g=task.geometry,n=v=>String(v).replace('.','p');
    const slug=`${String(task.crystal_cut).toLowerCase().startsWith('x')?'X':'Z'}cut${task.material}_T${n(g.thickness_nm)}_E${n(g.etch_depth_nm)}_W${n(g.top_width_nm)}_A${n(g.sidewall_angle_deg??67)}_${task.cladding}`;
    if(slug.length>150)throw Error('结构参数名称过长；请使用合理数值精度');
    const descriptor=structuredClone(task);for(const key of ['budget','provided_fields','assumptions','length_defaulted'])delete descriptor[key];
    const registryFile=path.join(c.outputRoot,'.qpm','designs.json');let ledger={};try{ledger=JSON.parse(readFileSync(registryFile,'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;}
    const designIdentity=requestedDesign||createHash('sha256').update(JSON.stringify({owner,descriptor})).digest('hex').slice(0,16);
    if(requestedDesign&&(!/^[a-f0-9]{16}$/.test(requestedDesign)||!ledger[requestedDesign]))throw Error('未知design_id；先查询原运行');
    const design=requestedDesign?ledger[requestedDesign]:path.join(c.outputRoot,'projects',slug,'designs',`${task.process}_P${task.modes?.pump||(task.crystal_cut==='z'?'TM0':'TE0')}_S${task.modes?.signal||(task.crystal_cut==='z'?'TM0':'TE0')}_I${task.modes?.idler||(task.crystal_cut==='z'?'TM0':'TE0')}_F${task.center_nm||1550}_${task.objective?.metric||'gvd'}${task.objective?.role||'signal'}_${designIdentity}`);if(!path.resolve(design).startsWith(path.resolve(c.outputRoot)+path.sep))throw Error('设计目录不在工作区');if(design.length>210)throw Error('工作区路径过长；请选择更短路径');mkdirSync(design,{recursive:true});ledger[designIdentity]=design;mkdirSync(path.dirname(registryFile),{recursive:true});const temporary=registryFile+'.'+randomUUID()+'.tmp';writeFileSync(temporary,JSON.stringify(ledger),'utf8');
    // Same-process synchronous reservation avoids losing concurrent design registration.
    renameSync(temporary,registryFile);
    for(let i=1;;i++){const out=path.join(design,'r'+String(i).padStart(3,'0'));try{mkdirSync(out);return out;}catch(e){if(e.code!=='EEXIST')throw e;}}
  }
  async source(args={},exec){
    const c=exec?.agent?.session?.header?.cwd?this.workspaceConfig(exec):this.snapshot(),roots={python:path.resolve(c.pythonModuleRoot||path.join(path.dirname(new URL(import.meta.url).pathname.replace(/^\/(\w:)/,'$1')),'..','python-unconfigured')),plugin:path.resolve(path.dirname(new URL(import.meta.url).pathname.replace(/^\/(\w:)/,'$1')),'..')};
    if(args.file){const [kind,...parts]=args.file.split('/'),root=roots[kind];if(!root)throw Error('未知源码根');
      const file=path.resolve(root,...parts);if(!file.startsWith(root+path.sep)||! /\.(py|js|ts|md|yml|ps1)$/.test(file))throw Error('仅允许插件源码');
      if((await lstat(file)).isSymbolicLink())throw Error('不读取链接');
      const raw=await readFile(file,'utf8'),lines=raw.split('\n'),start=Math.max(1,args.start||1),count=Math.min(300,args.count||100);
      return {file,sha256:createHash('sha256').update(raw).digest('hex'),start,total_lines:lines.length,text:lines.slice(start-1,start-1+count).join('\n')};}
    const files=[];async function walk(base,prefix,depth=0){if(depth>3)return;for(const e of await readdir(base,{withFileTypes:true}).catch(()=>[])){if(e.isSymbolicLink()||e.name.startsWith('.')||['runtime','backups','dist','node_modules'].includes(e.name))continue;if(e.isDirectory())await walk(path.join(base,e.name),prefix+'/'+e.name,depth+1);else if(/\.(py|js|ts|md|yml|ps1)$/.test(e.name))files.push(prefix+'/'+e.name);}}
    for(const [key,root] of Object.entries(roots))await walk(root,key);return {roots,files:files.filter(f=>!args.search||f.includes(args.search))};
  }
  async plotEnvironment(exec){
    const c=this.workspaceConfig(exec,true),policy=this.ctx?.get?.('sandboxPolicy')?.resolve?.({session:exec?.agent?.session});let fontFiles=[],fontDiagnostic;
    try{fontFiles=(await readdir(path.join(process.env.SystemRoot||'C:/Windows','Fonts'))).filter(f=>/\.(ttf|ttc|otf)$/i.test(f));}catch(e){fontDiagnostic=e.message;}
    return {output_root:path.join(c.outputRoot,'plots','comparisons'),formats:['png','svg','pdf'],script_api:['plot.option(**literal_options)','plot.series(**literal_series)'],timeout_s:120,max_sources:30,max_panels:12,max_source_bytes:134217728,memory_limit_bytes:1073741824,font_files:fontFiles,font_diagnostic:fontDiagnostic??null,policy_mode:policy?.mode||'unspecified',write_permission_checked_at_export:true};
  }
  async plotOutput(exec,requested){
    const c=this.workspaceConfig(exec,true),fallback=path.join(c.outputRoot,'plots','comparisons');let reason;
    const check=async root=>{
      await this.writableRun(exec,{output_dir:root});
      let cursor=path.resolve(root);for(;;){try{const stat=await lstat(cursor);if(stat.isSymbolicLink())throw Error('输出路径包含目录链接');}catch(e){if(e.code!=='ENOENT')throw e;}const parent=path.dirname(cursor);if(parent===cursor)break;cursor=parent;}
      await mkdir(root,{recursive:true});const probe=path.join(root,'.write-'+randomUUID());await writeFile(probe,'',{flag:'wx'});await unlink(probe);
    };
    let root=requested?path.resolve(requested):fallback;
    try{await check(root);}catch(e){if(!requested)throw Error('绘图输出不可写：'+e.message);reason=e.message;root=fallback;await check(root);}
    const output=path.join(root,'plot-'+randomUUID().slice(0,12));await mkdir(output);return {output,fallback_reason:reason??null,c};
  }
  async plot(exec,args){
    const c=this.workspaceConfig(exec,true);const name=args.name||'默认比较';
    if(typeof name!=='string'||name.length>64||/[\\/:*?"<>|]/.test(name)||name==='.'||name==='..')throw Error('预设名称无效');
    const presetFile=path.join(c.outputRoot,'presets','plots',createHash('sha256').update(name).digest('hex').slice(0,16)+'.json');
    let saved;if(!args.preset){try{saved=JSON.parse(await readFile(presetFile,'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;try{saved={preset:JSON.parse(await readFile(path.join(c.outputRoot,'presets','plots',name+'.json'),'utf8'))};}catch(old){if(old.code!=='ENOENT')throw old;}}}
    const preset=args.preset||saved?.preset;if(!preset)throw Error('请提供preset或已保存预设名称');
    const requested=args.sources||(args.run_id?[{alias:'baseline',run_id:args.run_id}]:saved?.sources||[]);
    if(!requested.length||requested.length>30)throw Error('需要1至30个来源');
    const sources=[];const aliases=new Set();
    for(const source of requested){if(typeof source.alias!=='string'||aliases.has(source.alias))throw Error('来源别名缺失或重复');aliases.add(source.alias);const run=await resolveRun(this,exec,source.run_id);if(!run.plottable)throw Error('来源缺少绘图数据：'+source.run_id);sources.push({alias:source.alias,run_id:run.run_id,file:path.join(run.output_dir,'result.json')});}
    const location=await this.plotOutput(exec,args.output_dir);const output=location.output;
    const runtime=this.snapshot();const executable=await this.ctx.subprocess.resolveExecutable(runtime.pythonExecutable);
    const tempLocation=await this.plotOutput(exec,path.join(c.outputRoot,'.qpm','p'));const tempRoot=tempLocation.output;if(tempLocation.fallback_reason||tempRoot.length>220)throw Error('绘图临时目录不可用或路径过长，请选择较短的可写工作区；图表输出仍可指定长路径');
    for(const folder of ['tmp','mpl-cache'])await mkdir(path.join(tempRoot,folder));
    const handle=this.ctx.subprocess.spawn({argv:[executable,'-u','-m','qpm_simulation.comparison_plot'],cwd:path.dirname(executable),env:{PYTHONUTF8:'1',PYTHONDONTWRITEBYTECODE:'1',PYTHONPATH:runtime.pythonModuleRoot,MPLCONFIGDIR:path.join(tempRoot,'mpl-cache'),TEMP:path.join(tempRoot,'tmp'),TMP:path.join(tempRoot,'tmp')},stdio:{stdin:'pipe',stdout:'pipe',stderr:{maxBytes:16384}},signal:exec.signal,graceMs:5000});
    handle.stdin.end(JSON.stringify({sources,preset,output,script:args.script||saved?.script}));
    let response;const reader=createInterface({input:handle.stdout});reader.on('line',line=>{try{response=JSON.parse(line);}catch{}});
    let timedOut=false;const timeout=setTimeout(()=>{timedOut=true;handle.terminate?.();},120000);
    const worker={cancel:()=>handle.terminate()};this.active.add(worker);let exit;try{exit=await handle.done;await handle.waitForExit();}catch(e){if(!response?.error&&!timedOut)throw e;}finally{clearTimeout(timeout);reader.close();this.active.delete(worker);}
    if(!response?.result)throw Error('绘图失败：'+(timedOut?'绘图超时（120秒）':response?.error||JSON.stringify(exit)));
    await mkdir(path.dirname(presetFile),{recursive:true});await atomicJson(presetFile,{name,preset,sources:requested,script:args.script||saved?.script});
    return {...response.result,output_dir:output,preset_file:presetFile,fallback_reason:location.fallback_reason??null,solver_calls:0};
  }
  async plotPreset(exec,args){return this.plot(exec,args);}
  async analyzeRun(exec,args){
    const run=await resolveRun(this,exec,args.run_id);if(!run.readable||['running','starting'].includes(run.status))throw Error('需要已导出的历史结果');
    const result_file=path.join(run.output_dir,'result.json'),output=(await this.plotOutput(exec)).output;
    return this.approvedExtension(args.token,'extension_postprocess',{result_file,output},exec.signal,exec);
  }
  async historical(exec,id){
    const run=await resolveRun(this,exec,id);if(!run.readable)throw Error(run.unavailable_reason);
    const result=JSON.parse(await readFile(path.join(run.output_dir,'result.json'),'utf8'));
    return {run,...result,curves:result.curves?.slice(0,10),note:'仅返回前10行；完整数据保存在result.json'};
  }
  async delivery(exec,args){
    if(!!args.run_id===!!args.job_id)throw Error('指定run_id或job_id其中一个');
    const output=args.run_id?(await resolveRun(this,exec,args.run_id)).output_dir:this.record(args.job_id,exec.agent.id).output;
    const worker=await this.launch('delivery',{run_dir:output},undefined,exec.signal,()=>{},this.workspaceConfig(exec));
    return worker.done;
  }
  async completedDelivery(agent,jobId){
    const record=this.record(jobId,agent.id);
    if(['starting','running'].includes(record.status))return null;
    const file=path.join(record.output,'delivery.json');
    if(existsSync(file))return JSON.parse(await readFile(file,'utf8'));
    if(record.result?.delivery)return record.result.delivery;
    return null;
  }
  deliveryJobRunning(agent,jobId){return ['starting','running'].includes(this.record(jobId,agent.id).status);}
  deliveryAuditRoot(agent){
    const root=this.workspaceConfig({agent}).outputRoot;
    return path.join(root,'.qpm','deliveries',createHash('sha256').update(agent.id).digest('hex').slice(0,16));
  }
  async deliveryFallback(agent,d,turn,auditRoot){
    // Persist an explicit program notice. Do not fabricate model-authored text.
    const root=auditRoot||path.dirname(d.evidence.delivery_file);await mkdir(root,{recursive:true});
    const text=d.summary||await readFile(path.join(path.dirname(d.evidence.delivery_file),'summary.md'),'utf8').catch(()=>JSON.stringify(d));
    await atomicJson(path.join(root,'delivery-fallback.json'),{source:'dsh-qpm-agent deterministic report',model_delivery_passed:false,text,
      report:d.evidence.report_file,process:d.evidence.process_file});
    const fallback=path.join(root,'delivery-fallback.md');
    await writeFile(fallback,'# 程序生成的基础摘要\n\n模型解释未完成。以下内容直接来自保存结果。\n\n'+text,'utf8');
    agent.session.append('deliverables/presented',{turn,callId:'qpm-delivery-'+randomUUID(),
      files:[{path:fallback,description:'程序生成的基础摘要；模型解释未完成'}]});
  }
  snapshot(){return snapshot(this.config);}
  async launch(action, task, output, signal, progress=()=>{}, captured=this.snapshot()) {
    const executable=await this.ctx.subprocess.resolveExecutable(captured.pythonExecutable);
    const plotEnv={};if(action==='postprocess_export'){const tempRoot=path.join(captured.outputRoot,'.qpm','p',randomUUID().slice(0,8));if(tempRoot.length>220)throw Error('绘图临时目录路径过长，请选择较短工作区');let cursor=path.resolve(tempRoot);for(;;){try{if((await lstat(cursor)).isSymbolicLink())throw Error('绘图临时目录包含链接');}catch(e){if(e.code!=='ENOENT')throw e;}const parent=path.dirname(cursor);if(parent===cursor)break;cursor=parent;}for(const folder of ['tmp','mpl-cache'])await mkdir(path.join(tempRoot,folder),{recursive:true});Object.assign(plotEnv,{TEMP:path.join(tempRoot,'tmp'),TMP:path.join(tempRoot,'tmp'),MPLCONFIGDIR:path.join(tempRoot,'mpl-cache')});}
    const handle=this.ctx.subprocess.spawn({argv:[executable,'-u','-m','qpm_simulation.harness_worker'],
      cwd:action==='postprocess_export'?path.dirname(executable):existsSync(captured.outputRoot)?path.resolve(captured.outputRoot):path.dirname(new URL(import.meta.url).pathname.replace(/^\/(\w:)/,'$1')),env:{...plotEnv,PYTHONUTF8:'1',PYTHONDONTWRITEBYTECODE:'1',...(captured.pythonModuleRoot?{PYTHONPATH:captured.pythonModuleRoot}:{})},
      stdio:{stdin:'pipe',stdout:'pipe',stderr:{maxBytes:16384}},graceMs:5000,signal});
    let value,problem; const worker={cancel:async(reason='user')=>{
      if(output) await writeFile(path.join(output,reason==='budget'?'.deadline':'.cancel'),reason).catch(()=>{});
      if(handle.stdin&&!handle.stdin.destroyed) handle.stdin.write(JSON.stringify({action:'cancel'})+'\n');
      const finished=await Promise.race([handle.done.then(()=>true,()=>true),new Promise(resolve=>{const timer=setTimeout(()=>resolve(false),30000);timer.unref?.();})]);
      if(!finished)await handle.terminate(); await handle.waitForExit();
    }};
    this.active.add(worker);
    const lines=createInterface({input:handle.stdout});
    lines.on('line',line=>{
      try{const msg=JSON.parse(line); if(msg.protocol!=='qpm/1')return;
        if(msg.kind==='result')value=msg.value; else if(msg.kind==='error')problem=msg.message;
        else if(msg.kind==='progress') progress(msg);
      }catch{/* Non-protocol solver messages do not become results. */}
    });
    handle.stdin.write(JSON.stringify({action,task,output,workspace:captured.outputRoot})+'\n');
    const done=(async()=>{
      try{const outcome=await handle.done; await handle.waitForExit();
        if(problem || outcome.exitCode!==0 || value===undefined) throw Error(problem||`Python worker failed (${outcome.exitCode}): ${handle.collected.stderr?.snapshot()?.bytes?.toString('utf8')||''}`);
        return value;
      }finally{lines.close(); this.active.delete(worker);}
    })();
    return {done,cancel:worker.cancel};
  }
  async query(action,raw,signal,exec) {
    const c=exec?.agent?.session?.header?.cwd?this.workspaceConfig(exec):this.snapshot(); 
    const task=raw?(action==='conventions'?raw:prepareTask(raw,c,false)):undefined; const worker=await this.launch(action,task,undefined,signal,()=>{},c);
    const value=await worker.done;
    if(action==='doctor')return {...value,deployment:{default_backend:c.defaultBackend,comsol_enabled:c.enableComsol,
      budget_ceilings:{max_solver_calls:c.maxSolverCalls,max_geometry_count:c.maxGeometryCount,max_wall_time_s:c.maxWallTimeS},max_research_decisions:20}};
    if(action==='capabilities'&&value?.validation){
      const v=value.validation,physics=v.physics||{};
      return {...value,validation:{release:v.release,physics:{representative_acceptance_passed:physics.representative_acceptance_passed,
        scope:'代表性物理检查通过；不代表所有材料、晶向、几何及非线性过程均已验证。'},
        plugin_workflow:'插件当前已实现范围内的工作流已完成初步验收。',
        future_scope:'超出当前插件能力范围的语义任务和更完整的自主研究流程将在后续版本扩展。'},
        limitations:(value.limitations||[]).filter(x=>!String(x).toLowerCase().includes('semantic')&&!String(x).toLowerCase().includes('autonomous research acceptance'))
          .concat('当前插件能力范围外的语义需求和更完整自主研究流程将在后续版本扩展。')};
    }
    return value;
  }
  async research(exec,args){
    const c=this.workspaceConfig(exec),request=structuredClone(args);
    if(request.command==='create'){
      request.spec.task=prepareTask(request.spec.task,c,false);
      request.ceilings={max_geometry_count:c.maxGeometryCount,max_solver_calls:c.maxSolverCalls,max_wall_time_s:c.maxWallTimeS,max_decisions:20};
      await this.writableRun(exec,{output_dir:path.join(c.outputRoot,'studies')});
    }
    const worker=await this.launch('research',request,undefined,exec.signal,()=>{},c);
    return studyView(await worker.done);
  }
  async researchStep(ctx,exec,args){
    const c=this.workspaceConfig(exec),id=args.study_id;
    if(!/^[a-f0-9]{32}$/.test(id))throw Error('invalid study_id');
    const output=path.join(c.outputRoot,'studies',id);
    await this.writableRun(exec,{output_dir:output});
    if(args.action.kind==='cancel')return this.research(exec,{command:'step',...args});
    if(!exec.agent?.id)throw Error('研究动作需要当前会话');
    if(this.running)throw Error('已有QPM任务运行；先等待或取消');
    const status=await this.research(exec,{command:'status',study_id:id});
    c.pythonModuleRoot=status.implementation_root;
    if(['evaluate','refine','verify','resume'].includes(args.action.kind))prepareTask(status.spec.task,c,true);
    // Reading is available for previous studies; a new execution must also
    // respect today's deployment ceilings, not only its saved task budget.
    for(const [key,limit] of Object.entries({max_solver_calls:c.maxSolverCalls,max_geometry_count:c.maxGeometryCount,max_wall_time_s:c.maxWallTimeS}))
      if(status.spec.budget[key]>limit)throw Error('研究预算高于当前插件设置：'+key);
    this.running++;
    let jobId;
    const record={owner:exec.agent.id,output,status:'starting',result:null};
    try{
      jobId=ctx.jobs.start({kind:'qpm',label:'QPM研究：'+args.action.kind,owner:exec.agent.id,run:handle=>{
        let worker,cancelled=false,deadline=false,timer;
        const done=(async()=>{
          record.status='running';
          try{
            if(cancelled)return {status:'killed'};
            worker=await this.launch('research',{command:'step',study_id:id,action:args.action},output,exec.signal,
              msg=>handle.updateProgress(msg.message||msg.stage),c);
            timer=setTimeout(()=>{deadline=true;void worker.cancel('budget').catch(()=>{});},Math.max(1,status.remaining.max_wall_time_s)*1000+120000);
            if(cancelled)await worker.cancel();
            record.result=studyView(await worker.done);record.status=record.result.state?.status||'completed';
            handle.append(JSON.stringify({study_id:id,status:record.status})+'\n');
            return {status:cancelled?'killed':'completed',detail:record.status,result:record.result};
          }catch(e){record.status=cancelled?'cancelled':deadline?'paused':'failed';record.error=deadline?'研究时间预算耗尽；保留检查点':String(e);return {status:cancelled?'killed':'failed',detail:record.error};}
          finally{clearTimeout(timer);this.running--;await atomicJson(path.join(output,'research-job.json'),record).catch(()=>{});}
        })();
        return {done,cancel:()=>{cancelled=true;void writeFile(path.join(output,'.cancel'),'user cancellation').catch(()=>{});if(worker)void worker.cancel().catch(()=>{});}};
      }});
      this.records.set(jobId,record);
      return {job_id:jobId,study_id:id,output_dir:output,status:'background'};
    }catch(e){this.running--;throw e;}
  }
  submit(ctx,exec,raw,label,resumed,designId) {
    const c=this.workspaceConfig(exec,!!resumed?.snapshot);if(resumed?.snapshot){c.pythonModuleRoot=resumed.snapshot.pythonModuleRoot;c.sourceVersion=resumed.snapshot.sourceVersion;} const owner=exec.agent?.id;
    if(!owner)throw Error('QPM提交需要当前会话');
    const policy=this.ctx?.get?.('sandboxPolicy')?.resolve?.({session:exec.agent.session});if(policy?.mode==='read-only'||policy?.sandboxMode==='read-only')throw Error('当前Harness工作区为只读；请选择可写工作区');
    if(this.running>=1)throw Error('已有QPM任务运行；请先等待或取消');
    const used=this.iterations.get(owner)||0;
    const prepared=prepareTask(raw,c),task=resumed?.snapshot?Object.fromEntries(Object.keys(raw).map(key=>[key,prepared[key]])):prepared, output=resumed?.output||this.newRunPath(task,c,owner,designId);
    const record={owner,output,status:'starting',snapshot:c,task,progress:null,result:null,recovery_count:resumed?.recovery_count||0};
    this.running++; this.iterations.set(owner,used+(resumed?0:1));
    let jobId;
    try{
      jobId=ctx.jobs.start({kind:'qpm',label:label||'QPM设计仿真',owner,
        run:handle=>{
          let worker,cancelled=false,timer,timedOut=false;
          const done=(async()=>{
            try{
              await mkdir(output,{recursive:true});
              if(!resumed){
                const siblings=await readdir(path.dirname(output),{withFileTypes:true});let calls=0,elapsed=0,geometries=0;const scores=[];
                for(const d of siblings){if(!d.isDirectory()||path.join(path.dirname(output),d.name)===output)continue;try{const cp=JSON.parse(await readFile(path.join(path.dirname(output),d.name,'checkpoint.json'),'utf8'));calls+=cp.cost?.solver_calls||0;elapsed+=cp.cost?.elapsed_s||0;geometries+=cp.cost?.unique_computed_geometries||0;try{const rr=JSON.parse(await readFile(path.join(path.dirname(output),d.name,'result.json'),'utf8'));const score=rr.search?.best?.value;if(Number.isFinite(score))scores.push({name:d.name,value:Math.abs(score)});}catch{}}catch{}}
                scores.sort((a,b)=>a.name.localeCompare(b.name));if(scores.length>=3){const last=scores.slice(-3);if(last[1].value>=last[0].value&&last[2].value>=last[1].value)throw Error('同一设计连续两轮搜索无改善；请调整目标或用户明确启动新设计');}
                if(calls>=task.budget.max_solver_calls||elapsed>=task.budget.max_wall_time_s||geometries>=task.budget.max_geometry_count)throw Error('同一设计累计预算耗尽；不能通过新运行重置');
                task.budget={max_solver_calls:task.budget.max_solver_calls-calls,max_wall_time_s:task.budget.max_wall_time_s-elapsed,max_geometry_count:task.budget.max_geometry_count-geometries};
              }
              await atomicJson(path.join(output,'plugin-config.json'),c);await atomicJson(path.join(output,'task.json'),task);
              const designDir=path.dirname(output),projectDir=path.resolve(designDir,'..','..');
              if(output.includes(path.sep+'projects'+path.sep)){
              if(!existsSync(path.join(projectDir,'project.json')))await atomicJson(path.join(projectDir,'project.json'),{project_id:createHash('sha256').update(projectDir).digest('hex').slice(0,16),material:task.material,crystal_cut:task.crystal_cut,cladding:task.cladding,nominal_geometry:task.geometry,units:{dimensions:'nm',sidewall_angle:'degrees from horizontal'}});
              if(!existsSync(path.join(designDir,'design.json')))await atomicJson(path.join(designDir,'design.json'),{design_id:path.basename(designDir).split('_').at(-1),process:task.process,modes:task.modes,center_nm:task.center_nm,objective:task.objective,search_axes:task.search_axes,budget:task.budget});
              }
              await atomicJson(path.join(output,'run-manifest.json'),{run_id:path.relative(c.outputRoot,output),design_id:path.basename(path.dirname(output)).split('_').at(-1),geometry:task.geometry,task_kind:task.task_kind,created_at:new Date().toISOString(),config_snapshot:'plugin-config.json'});
              if(cancelled){record.status='cancelled'; return {status:'killed',detail:'cancelled before startup'};}
              await unlink(path.join(output,'.cancel')).catch(()=>{});
              record.status='running';
              for(;;){
                let elapsed=0;
                try{elapsed=JSON.parse(await readFile(path.join(output,'checkpoint.json'),'utf8')).cost.elapsed_s||0;}catch{}
                if(elapsed>=task.budget.max_wall_time_s){record.status='paused';record.error='累计时间预算耗尽；已保留完成点，增加允许预算后续算';break;}
                await atomicJson(path.join(output,'plugin-job.json'),record);
                const launchStarted=Date.now();
                worker=await this.launch('run',task,output,undefined,msg=>{record.progress=msg;handle.updateProgress(msg.message||`${msg.stage}: ${JSON.stringify(msg.cost||{})}`);},c);
                timer=setTimeout(()=>{timedOut=true;void worker.cancel().catch(()=>{});},Math.max(1,task.budget.max_wall_time_s-elapsed)*1000+120000);
                if(cancelled)await worker.cancel();
                try{
                  record.result=await worker.done;
                  record.status=cancelled?'cancelled':timedOut?'paused':record.result.completion==='incomplete'?'paused':record.result.completion||'completed';
                  break;
                }catch(e){
                  try{const cp=JSON.parse(await readFile(path.join(output,'checkpoint.json'),'utf8'));cp.cost.elapsed_s=Math.max(cp.cost.elapsed_s||0,elapsed+(Date.now()-launchStarted)/1000);await atomicJson(path.join(output,'checkpoint.json'),cp);}catch{}
                  const text=String(e);
                  const transient=/Python worker failed|Windows Job runner|subprocess-local|process.*exit/i.test(text)&&! /No module named|ModuleNotFoundError|ImportError|FileNotFound|PermissionError|ValidationError/i.test(text);
                  if(cancelled||timedOut||!transient||c.autoResume===false||record.recovery_count>=(c.recoveryAttempts??2))throw e;
                  record.recovery_count++;handle.updateProgress(`进程异常，恢复第${record.recovery_count}次；复用已完成模式点`);
                  await unlink(path.join(output,'.cancel')).catch(()=>{});
                }finally{clearTimeout(timer);}
              }
              handle.append(JSON.stringify(record.result)+'\n');
              return {status:record.status==='cancelled'?'killed':'completed',result:record.result,detail:record.status};
            }catch(e){record.status=cancelled?'cancelled':timedOut?'paused':'failed'; record.error=timedOut?'任务超时；完成点已保存，可续算':String(e);return {status:cancelled?'killed':'failed',detail:record.error};}
            finally{clearTimeout(timer);this.running--;await atomicJson(path.join(output,'plugin-job.json'),record).catch(()=>{});}
          })();
          return {done,cancel:()=>{cancelled=true; if(worker)void worker.cancel().catch(()=>{});}};
        }});
      this.records.set(jobId,record); return {job_id:jobId,output_dir:output,design_id:path.basename(path.dirname(output)).split('_').at(-1),status:'background',iteration_policy:'cumulative_design_budget'};
    }catch(e){this.running--;this.iterations.set(owner,used);throw e;}
  }
  async listRuns(exec){return (await historyIndex(this,exec)).runs;}
  async historySources(exec,args={}){
    const data=await historySources(this,exec);
    if(args.add||args.remove){
      if(!exec?.agent?.session?.header?.cwd)throw Error('登记修改需要选择工作区');
      const root=args.add||args.remove;if(!path.isAbsolute(root))throw Error('历史来源必须为绝对路径');
      let registered=data.registered.filter(p=>path.resolve(p).toLowerCase()!==path.resolve(root).toLowerCase());if(args.add)registered.push(path.resolve(root));
      await this.writableRun(exec,{output_dir:path.dirname(data.registry)});await mkdir(path.dirname(data.registry),{recursive:true});await atomicJson(data.registry,registered);
    }
    return historyIndex(this,exec);
  }
  async writableRun(exec,run){
    const policy=this.ctx?.get?.('sandboxPolicy')?.resolve?.({session:exec?.agent?.session});
    if(policy?.mode==='read-only'||policy?.sandboxMode==='read-only')throw Error('当前权限为只读；旧结果可读，不能写入新产物');
    const allowed=policy?.writableRoots||(policy?.mode==='workspace-write'?[policy.workspaceRoot]:undefined);if(Array.isArray(allowed)&&!allowed.some(p=>{const rel=path.relative(p,run.output_dir);return !rel.startsWith('..')&&!path.isAbsolute(rel);}))throw Error('历史目录不在Harness允许写入范围；不会转移输出或扩大权限');
  }
  async derivedOutput(exec,run,kind){
    await this.writableRun(exec,run);const root=path.join(run.output_dir,kind);await mkdir(root,{recursive:true});
    for(let i=1;;i++){const out=path.join(root,'p'+String(i).padStart(3,'0'));try{await mkdir(out);return out;}catch(e){if(e.code!=='EEXIST')throw Error('旧目录不可写：'+e.message);}}
  }
  async resume(ctx,exec,id,budget){
    const run=await resolveRun(this,exec,id);if(!run.resume_available)throw Error('运行不可续算：'+run.status);await this.writableRun(exec,run);
    const c=this.workspaceConfig(exec,true),output=run.output_dir,task=structuredClone(run.task);
    let saved;try{saved=JSON.parse(await readFile(path.join(output,'plugin-config.json'),'utf8'));}catch(e){if(e.code!=='ENOENT')throw Error('原执行配置损坏：'+e.message);}
    if(saved?.pythonModuleRoot&&!existsSync(path.join(saved.pythonModuleRoot,'qpm_simulation','harness_worker.py')))throw Error('原执行版本缺失：'+saved.pythonModuleRoot+'；需恢复该版本后续算');
    if(saved?.sourceVersion){const directory=path.dirname(saved.pythonModuleRoot),manifest=JSON.parse(await readFile(path.join(directory,'manifest.json'),'utf8'));const grant=JSON.parse(await readFile(path.join(directory,'approval.json'),'utf8'));const tested=JSON.parse(await readFile(path.join(directory,'test-result.json'),'utf8'));if(grant.digest!==saved.sourceVersion.digest||tested.digest!==saved.sourceVersion.digest||!tested.tests_passed)throw Error('原源码版本审核记录无效');for(const [file,hash] of Object.entries(manifest.core_hashes||{}))if(createHash('sha256').update(await readFile(path.join(saved.pythonModuleRoot,file))).digest('hex')!==hash)throw Error('原源码版本变化，不能续算');}
    if(budget){
      const keys={max_solver_calls:'maxSolverCalls',max_wall_time_s:'maxWallTimeS',max_geometry_count:'maxGeometryCount'},used={max_solver_calls:0,max_wall_time_s:0,max_geometry_count:0};
      if(output.includes(path.sep+'projects'+path.sep))for(const d of await readdir(path.dirname(output),{withFileTypes:true})){if(!d.isDirectory()||path.join(path.dirname(output),d.name)===output)continue;try{const cp=JSON.parse(await readFile(path.join(path.dirname(output),d.name,'checkpoint.json'),'utf8'));used.max_solver_calls+=cp.cost?.solver_calls||0;used.max_wall_time_s+=cp.cost?.elapsed_s||0;used.max_geometry_count+=cp.cost?.unique_computed_geometries||0;}catch{}}
      const adjusted={};for(const [key,value] of Object.entries(budget)){if(!Number.isFinite(value)||value<=0||value>c[keys[key]])throw Error('续算累计预算超过插件上限');adjusted[key]=value-used[key];if(adjusted[key]<=0)throw Error('同设计其他运行已耗尽预算');}task.budget={...task.budget,...adjusted};
    }
    let previous={};try{previous=JSON.parse(await readFile(path.join(output,'plugin-job.json'),'utf8'));}catch{}
    if([...this.records.values()].some(r=>r.output===output&&['running','starting'].includes(r.status)))throw Error('该任务仍运行');
    try{await readFile(path.join(output,'checkpoint.json'),'utf8');}catch(e){
      if(e.code!=='ENOENT')throw e;
      const cost=previous.result?.cost||previous.progress?.cost;
      if(cost)await atomicJson(path.join(output,'checkpoint.json'),{stage:'legacy_resume',cost,geometries:[JSON.stringify(task.geometry,Object.keys(task.geometry).sort())],failures:[],cost_record_incomplete:true});
    }
    
    return this.submit(ctx,exec,task,'QPM续算',{output,recovery_count:previous.recovery_count||0,snapshot:saved});
  }
  record(id,owner) {const r=this.records.get(id);if(!r||r.owner!==owner)throw Error('任务不存在或不属于当前会话');return r;}
  status(id,owner){const r=this.record(id,owner);return {job_id:id,status:r.status,progress:r.progress,result:r.result,error:r.error||null,output_dir:r.output};}
  async postprocess(id,owner,signal,exec,runId){
    const record=runId?await resolveRun(this,exec,runId):this.record(id,owner);
    if(['starting','running'].includes(record.status))throw Error('任务仍在运行');
    const run=runId?record:{output_dir:record.output};
    const output=exec?(await this.plotOutput(exec)).output:await this.derivedOutput(exec,run,'postprocess');const c=exec?this.workspaceConfig(exec,true):record.snapshot;
    const w=await this.launch('postprocess_export',{run_dir:run.output_dir},output,signal,()=>{},c);return w.done;
  }
  async stageExtension(owner,id,code,tests,metadata={kind:'adapter',version:'1',description:'Task configuration adapter',applicability:'Validated QPM task configuration'}) {
    if(!/^[a-z][a-z0-9_-]{0,48}$/.test(id))throw Error('扩展ID仅允许小写字母、数字、下划线和连字符');
    if(code.length+tests.length>131072)throw Error('扩展代码超过128 KiB');
    if(!['adapter','postprocess'].includes(metadata.kind)||!['version','description','applicability'].every(k=>typeof metadata[k]==='string'&&metadata[k].trim())||Object.keys(metadata).some(k=>!['kind','version','description','applicability'].includes(k)))throw Error('扩展需提供kind、version、description、applicability；只支持配置或后处理');
    const extensionConfig=arguments[5]?this.workspaceConfig(arguments[5]):this.snapshot();
    metadata=Object.fromEntries(Object.entries(metadata).sort(([a],[b])=>a.localeCompare(b)));
    const metadataJson=JSON.stringify(metadata);
    const digest=createHash('sha256').update(code+'\n'+tests+'\n'+metadataJson).digest('hex');
    const root=path.resolve(extensionConfig.extensionsRoot,id,digest);await mkdir(root,{recursive:true});
    await writeFile(path.join(root,'extension.py'),code);await writeFile(path.join(root,'test_extension.py'),tests);
    await writeFile(path.join(root,'manifest.json'),JSON.stringify({id,digest,owner,metadata,metadataJson,status:'pending_human_review',dynamic_tests_run:false},null,2));
    const c=extensionConfig;await mkdir(c.outputRoot,{recursive:true});
    const worker=await this.launch('extension_check',{directory:root},undefined,undefined,()=>{},c);
    const check=await worker.done;
    return {id,digest,directory:root,status:'pending_human_review',check,review_token:`${id}:${digest}`,note:'仅完成语法检查；未执行。调用qpm_approve_version申请原生审核，获批后自动测试并启用。'};
  }
  async stageSource(exec,args){
    const c=this.workspaceConfig(exec);if(!/^[a-z][a-z0-9_-]{0,48}$/.test(args.id))throw Error('非法版本名');
    if(!/^qpm_simulation\/[a-z_]+\.py$/.test(args.file))throw Error('仅支持QPM Python功能源码；原生插件需单独构建');
    const before=await readFile(path.join(c.pythonModuleRoot,args.file),'utf8');
    const metadata={kind:'source',version:args.version||'1',description:args.description,applicability:args.applicability,file:args.file,parent:c.sourceVersion||'packaged',before_sha256:createHash('sha256').update(before).digest('hex')};
    const metadataJson=JSON.stringify(metadata),digest=createHash('sha256').update(args.code+'\n'+args.tests+'\n'+metadataJson).digest('hex');
    const directory=path.join(c.extensionsRoot,args.id,digest);await mkdir(directory,{recursive:true});
    await cp(path.join(c.pythonModuleRoot,'qpm_simulation'),path.join(directory,'core','qpm_simulation'),{recursive:true,filter:src=>!src.includes('__pycache__')});
    for(const file of ['LN_LT_COMSOL_SHG_QPM_Automation.py','dispersion_core.py'])await cp(path.join(c.pythonModuleRoot,file),path.join(directory,'core',file)).catch(e=>{if(e.code!=='ENOENT')throw e;});
    await writeFile(path.join(directory,'core',args.file),args.code,'utf8');await writeFile(path.join(directory,'extension.py'),args.code,'utf8');await writeFile(path.join(directory,'test_extension.py'),args.tests,'utf8');
    const affectsModes=!['plotting.py','plot_presets.py','reporting.py'].includes(path.basename(args.file));
    await atomicJson(path.join(directory,'core','qpm_simulation','source-version.json'),{digest,affects_modes:affectsModes});
    if(affectsModes)await atomicJson(path.join(directory,'core','qpm_simulation','cache_compatibility.json'),{verified_legacy_fingerprints:[]});
    const core_hashes={};async function hashes(base,relative=''){for(const file of await readdir(base,{withFileTypes:true})){if(file.isDirectory())await hashes(path.join(base,file.name),path.join(relative,file.name));else if(file.isFile())core_hashes[path.join(relative,file.name)]=createHash('sha256').update(await readFile(path.join(base,file.name))).digest('hex');}}await hashes(path.join(directory,'core'));
    await atomicJson(path.join(directory,'manifest.json'),{id:args.id,digest,metadata,metadataJson,core_hashes,status:'pending_human_review',before,code:args.code});
    const worker=await this.launch('extension_check',{directory},undefined,exec.signal,()=>{},c);await worker.done;
    return {token:args.id+':'+digest,directory,metadata,status:'pending_human_review',note:'独立源码副本；审核前不运行；不会覆盖原源码'};
  }
  async versions(exec,args={}){
    const c=this.workspaceConfig(exec,args.select==='packaged'),items=[];
    if(args.select==='packaged'){await unlink(path.join(c.extensionsRoot,'active-source.json')).catch(()=>{});return {active:'packaged'};}
    for(const d of await readdir(c.extensionsRoot,{withFileTypes:true}).catch(()=>[])){if(!d.isDirectory())continue;for(const v of await readdir(path.join(c.extensionsRoot,d.name),{withFileTypes:true})){if(!v.isDirectory())continue;try{const manifest=JSON.parse(await readFile(path.join(c.extensionsRoot,d.name,v.name,'manifest.json'),'utf8'));let tests;try{tests=JSON.parse(await readFile(path.join(c.extensionsRoot,d.name,v.name,'test-result.json'),'utf8'));}catch{}items.push({token:d.name+':'+v.name,metadata:manifest.metadata,tests_passed:tests?.tests_passed||false});}catch{}}}
    if(args.select){const selected=items.find(v=>v.token===args.select&&v.tests_passed&&v.metadata.kind==='source');if(!selected)throw Error('仅可切换已审核且测试通过的源码版本');const [id,digest]=args.select.split(':');await atomicJson(path.join(c.extensionsRoot,'active-source.json'),{id,digest});}
    return {items,active:args.select||c.sourceVersion||'packaged'};
  }
  async approveVersion(ctx,exec,token){
    if(!/^[a-z][a-z0-9_-]{0,48}:[a-f0-9]{64}$/.test(token))throw Error('非法版本');
    const c=this.workspaceConfig(exec),[id,digest]=token.split(':'),directory=path.resolve(c.extensionsRoot,id,digest);
    const manifest=JSON.parse(await readFile(path.join(directory,'manifest.json'),'utf8'));
    const code=await readFile(path.join(directory,'extension.py'),'utf8'),tests=await readFile(path.join(directory,'test_extension.py'),'utf8');
    if(createHash('sha256').update(code+'\n'+tests+'\n'+manifest.metadataJson).digest('hex')!==digest)throw Error('代码已变化');
    for(const [file,hash] of Object.entries(manifest.core_hashes||{}))if(createHash('sha256').update(await readFile(path.join(directory,'core',file))).digest('hex')!==hash)throw Error('源码副本已变化，需重新提交');
    if(!ctx.approval)throw Error('Harness未提供审批服务；版本保留待审核');
    const decision=await ctx.approval.request({agent:exec.agent,toolName:'qpm_approve_version',signal:exec.signal,reason:JSON.stringify({token,metadata:manifest.metadata,before:manifest.before,code,tests})});
    if(decision!=='allowed-once')return {status:'pending_human_review',decision,token};
    await atomicJson(path.join(directory,'approval.json'),{digest,decision,approved_at:new Date().toISOString()});
    const result=await this.approvedExtension(token,'extension_test',undefined,exec.signal,exec);
    if(manifest.metadata.kind==='source')await atomicJson(path.join(c.extensionsRoot,'active-source.json'),{id,digest});
    return {token,status:'enabled',test_result:result,directory};
  }
  async approvedExtension(token,action,task,signal,exec){
    const c=exec?this.workspaceConfig(exec):this.snapshot();
    if(!/^[a-z][a-z0-9_-]{0,48}:[a-f0-9]{64}$/.test(token))throw Error('非法扩展版本');
    const [id,digest]=token.split(':'),directory=path.resolve(c.extensionsRoot,id,digest);
    const manifest=JSON.parse(await readFile(path.join(directory,'manifest.json'),'utf8'));
    let grant;try{grant=JSON.parse(await readFile(path.join(directory,'approval.json'),'utf8'));}catch{}
    if(!c.approvedExtensions?.split(/[\s,;]+/).includes(token)&&!(grant?.digest===digest&&grant.decision==='allowed-once'))throw Error('扩展未经人工审核启用');
    const code=await readFile(path.join(directory,'extension.py'),'utf8'),tests=await readFile(path.join(directory,'test_extension.py'),'utf8');
    if(manifest.metadataJson&&JSON.stringify(manifest.metadata)!==manifest.metadataJson)throw Error('扩展描述已变化，需要重新审核');
    if(createHash('sha256').update(code+'\n'+tests+(manifest.metadataJson?'\n'+manifest.metadataJson:'')).digest('hex')!==digest)throw Error('扩展文件已变化，需要重新审核');
    for(const [file,hash] of Object.entries(manifest.core_hashes||{}))if(createHash('sha256').update(await readFile(path.join(directory,'core',file))).digest('hex')!==hash)throw Error('源码副本变化，需要重新审核');
    const kind=manifest.metadata?.kind||'adapter';
    if((action==='extension_adapt'&&kind!=='adapter')||(action==='extension_postprocess'&&kind!=='postprocess'))throw Error('扩展类型与调用入口不匹配');
    if(!['extension_test','extension_adapt','extension_postprocess'].includes(action))throw Error('未知扩展操作');
    if(action!=='extension_test'){
      const checked=JSON.parse(await readFile(path.join(directory,'test-result.json'),'utf8'));
      if(!checked.tests_passed||checked.digest!==digest)throw Error('扩展必须先通过独立测试');
    }
    await mkdir(c.outputRoot,{recursive:true});
    const specification=action==='extension_postprocess'?{directory,...task,extension:{id,digest,metadata:manifest.metadata}}:{directory,task};
    const effective=kind==='source'?{...c,pythonModuleRoot:path.join(directory,'core')}:c;
    const w=await this.launch(action,specification,action==='extension_postprocess'?task.output:undefined,signal,()=>{},effective);
    const timer=setTimeout(()=>{void w.cancel().catch(()=>{});},c.maxWallTimeS*1000);
    let result;try{result=await w.done;}finally{clearTimeout(timer);}
    if(action==='extension_test')await writeFile(path.join(directory,'test-result.json'),JSON.stringify({...result,digest}));
    if(action==='extension_adapt'){
      const normalized=prepareTask(result,c);const check=await this.query('plan',normalized,signal);return check.task;
    }
    return result;
  }
  async analyzeResult(token,id,owner,signal,exec){
    const record=this.record(id,owner);
    if(['starting','running'].includes(record.status)||!record.result?.result_file)throw Error('需要当前会话已导出的仿真结果');
    const result_file=path.join(record.output,'result.json');
    if(path.resolve(record.result.result_file)!==path.resolve(result_file))throw Error('结果路径不属于任务');
    const output=await this.derivedOutput(exec,{output_dir:record.output},'extensions');
    return this.approvedExtension(token,'extension_postprocess',{result_file,output},signal,exec);
  }
}
