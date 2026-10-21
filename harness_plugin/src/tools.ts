import { defineTool } from '@deepseek-ai/dsh-tools';
import {readFileSync} from 'node:fs';
import {installDelivery} from './delivery.js';
// Convert standard JSON Schema required arrays to Harness parameter DSL flags.
function parameter(schema,required=false){
 const {required:keys,properties,items,oneOf,...rest}=schema;
 return {...rest,...(required?{required:true}:{}),
  ...(properties?{properties:Object.fromEntries(Object.entries(properties).map(([k,v])=>[k,parameter(v,keys?.includes(k))]))}:{}),
  ...(items?{items:parameter(items)}:{}),...(oneOf?{oneOf:oneOf.map(s=>parameter(s))}:{})};
}
const taskContract=JSON.parse(readFileSync(new URL('../skills/qpm-design/task.schema.json',import.meta.url),'utf8'));
const studyContract=JSON.parse(readFileSync(new URL('../skills/qpm-design/study.schema.json',import.meta.url),'utf8'));
export const inject=['tools','jobs','qpmRuntime'];
// Harness validates the value before the renderer calls JSON.stringify.
// Keep optional fields explicit and missing numeric data null at this boundary.
export function jsonSafe(value,seen=new Set()){
 if(value===undefined||value===null)return null;
 if(typeof value==='number')return Number.isFinite(value)?value:null;
 if(typeof value==='string'||typeof value==='boolean')return value;
 if(typeof value!=='object')throw Error('Tool result contains a non-JSON value');
 if(seen.has(value))throw Error('Tool result contains a cycle');
 seen.add(value);
 try{return Array.isArray(value)?value.map(v=>jsonSafe(v,seen)):Object.fromEntries(Object.entries(value).map(([k,v])=>[k,jsonSafe(v,seen)]));}
 finally{seen.delete(value);}
}
export function apply(ctx){
 const delivery=installDelivery(ctx);
 const register=(name,description,parameters,execute)=>ctx.effect(()=>ctx.tools.register(defineTool({name,description,parameters,
  output:{schema:{type:'json'},render:(_args,value)=>[{type:'text',text:JSON.stringify(jsonSafe(value))}]},execute:async(a,e)=>{delivery.before(name,e,a);return jsonSafe(await delivery.after(name,e,a,await execute(a,e)));}})));
 const task={task:parameter(taskContract,true)};
 const id={job_id:{type:'string',required:true}};
 const studyId={study_id:{type:'string',required:true}};
 register('qpm_delivery','读取完整事实摘要，供最终交付；历史结果只读，不启动求解器。指定run_id或当前job_id。',{run_id:{type:'string'},job_id:{type:'string'}},(a,e)=>ctx.qpmRuntime.delivery(e,a));
 register('qpm_process','分页读取持久研究的实际事件；不推测未记录的决策。',{...studyId,after_seq:{type:'number'},limit:{type:'number'}},(a,e)=>ctx.qpmRuntime.research(e,{command:'events',...a}));
 register('qpm_study_create','建立持久研究任务：先查capabilities和conventions。spec包含task、goal、constraints、budget、permissions、knowledge；预算贯穿所有批次，不启动求解器。',
  {spec:parameter(studyContract,true)},(a,e)=>ctx.qpmRuntime.research(e,{command:'create',...a}));
 register('qpm_studies','列出当前工作区研究任务，跨会话恢复入口',{},(_a,e)=>ctx.qpmRuntime.research(e,{command:'list'}));
 register('qpm_study_status','读取研究目标、候选、累计成本、剩余预算和最近证据。不启动求解器。',studyId,(a,e)=>ctx.qpmRuntime.research(e,{command:'status',...a}));
 register('qpm_study_result','读取研究批次结果；run_id从研究状态获取，不猜测。',{...studyId,run_id:{type:'string',required:true}},(a,e)=>ctx.qpmRuntime.research(e,{command:'result',...a}));
 register('qpm_study_diagnose','检查实际结果的缺失、模式、求导与收敛证据；建议是待模型选择的动作。',{...studyId,run_id:{type:'string'}},(a,e)=>ctx.qpmRuntime.research(e,{command:'diagnose',...a}));
 register('qpm_study_propose','按工艺格点和已有实算建议下一批候选；GP预测不是仿真结果。',{...studyId,count:{type:'number'}},(a,e)=>ctx.qpmRuntime.research(e,{command:'propose',...a}));
 register('qpm_study_step','执行一项研究动作：evaluate、refine、verify、resume、recover、pause、finalize、cancel。每批后读取证据再选择下一步。计算动作带reason和已有run_id的evidence；geometry不得超出工艺约束。返回后台job_id，用jobs等待和qpm_status读取。',
  {...studyId,action:{type:'object',required:true,additionalProperties:false,properties:{kind:{type:'string',required:true,enum:['evaluate','refine','verify','resume','recover','pause','finalize','cancel']},reason:{type:'string'},evidence:{type:'array',items:{type:'string'}},geometry:parameter(taskContract.properties.geometry),stencil_points:{type:'number',enum:[3,5,7]}}}},(a,e)=>ctx.qpmRuntime.researchStep(ctx,e,a));
 register('qpm_plot','跨run绘图；所有登记历史来源只读，输出当前工作区。不启动COMSOL、不耗仿真预算。script仅支持plot.option与plot.series字面量调用',{name:{type:'string'},sources:{type:'array',items:{type:'object',additionalProperties:false,properties:{alias:{type:'string',required:true},run_id:{type:'string',required:true}}}},preset:{type:'object',additionalProperties:true,description:'series:[{source,metric,x,dataset,label,color,linestyle,marker,axis:left/right,kind:line/scatter,unit,scale,normalize,x_range,interpolate_nm,smooth_window}],layout:overlay/subplots/dual_axis/heatmaps,formats:[png,svg,pdf],title,xlabel,ylabel,figsize,font,fontsize,legend_location,xlim,ylim,xticks,yticks,xscale,yscale,errors:true/envelope,metric_units,metric_definitions'},script:{type:'string'},output_dir:{type:'string'}},(a,e)=>ctx.qpmRuntime.plot(e,a));
 register('qpm_plot_environment','查询独立绘图API、系统字体、输出位置与限制，不启动求解器',{},(_a,e)=>ctx.qpmRuntime.plotEnvironment(e));
 register('qpm_plot_preset','从历史结果重新绘图并保存命名预设；同单位曲线可叠加，缺失点保持断开；不启动求解器',{run_id:{type:'string',required:true},name:{type:'string',required:true},preset:{type:'object',additionalProperties:true,description:'series:[{metric,label,color,linestyle,dataset}],title,xlabel,ylabel,xlim,ylim,xscale,yscale,legend,dpi,heatmap,xticks,yticks,errors；省略则加载已保存预设'}},(a,e)=>ctx.qpmRuntime.plotPreset(e,a));
 register('qpm_analyze_run','使用已审核并测试通过的后处理版本分析工作区历史结果；保留源数据，不启动求解器',{run_id:{type:'string',required:true},token:{type:'string',required:true}},(a,e)=>ctx.qpmRuntime.analyzeRun(e,a));
 register('qpm_result','跨会话读取工作区中历史结果摘要',{run_id:{type:'string',required:true}},(a,e)=>ctx.qpmRuntime.historical(e,a.run_id));
 register('qpm_stage_source_version','保存独立Python功能源码版本及测试，保留父版本；审核前不执行、不覆盖核心',{id:{type:'string',required:true},file:{type:'string',required:true,description:'qpm_simulation/xxx.py，从qpm_source读取当前实现后提交完整新源码'},code:{type:'string',required:true},tests:{type:'string',required:true},version:{type:'string'},description:{type:'string',required:true},applicability:{type:'string',required:true}},(a,e)=>ctx.qpmRuntime.stageSource(e,a));
 register('qpm_versions','查询保存版本；select指定已测试源码版本，或packaged回退原包；不影响运行中任务',{select:{type:'string'}},(a,e)=>ctx.qpmRuntime.versions(e,a));
 register('qpm_source','只读浏览实际安装源码，按文件和行号读取；不启动COMSOL',{file:{type:'string'},search:{type:'string'},start:{type:'number'},count:{type:'number'}},(a,e)=>ctx.qpmRuntime.source(a,e));
 register('qpm_capabilities','查询已实现能力、物理限制和验证状态，不启动COMSOL',{},()=>ctx.qpmRuntime.query('capabilities'));
 register('qpm_environment','只读检查Python与仿真依赖，不启动COMSOL',{},(_a,e)=>ctx.qpmRuntime.query('doctor',undefined,e.signal,e));
 register('qpm_conventions','只读查询QPM规范/模板，不启动求解器。陌生任务先查templates；晶向疑问查crystal，波长角色查wavelengths。执行前加载skill(qpm-design)。',{
  topic:{type:'string',enum:['overview','crystal','geometry','wavelengths','metrics','routing','extensions','templates','research','quality','plotting'],description:'规范主题；自主研究查research，异常查quality，绘图列名和dataset查plotting'},
  template:{type:'string',enum:['fixed','tolerance','search','spdc_pmf','sfg_2d'],description:'仅topic=templates时选择一类任务示例'}},(a,e)=>ctx.qpmRuntime.query('conventions',a,e.signal));
 register('qpm_plan','执行前校验并解释晶轴/波长角色/默认值/可用指标。fixed曲线；search筛选结构；spectral_2d仅SFG/SPDC。将明确输出填写required_metrics，缺口不得忽略。预算不超过设置。',task,(a,e)=>ctx.qpmRuntime.query('plan',a.task,e.signal,e));
 register('qpm_estimate','只读估算必要采点、缓存和用时范围；不启动COMSOL、不提高预算',task,(a,e)=>ctx.qpmRuntime.query('estimate',a.task,e.signal,e));
 register('qpm_history_sources','查询历史来源与诊断；add/remove登记用户明确指定的绝对路径，不迁移文件',{add:{type:'string'},remove:{type:'string'}},(a,e)=>ctx.qpmRuntime.historySources(e,a));
 register('qpm_runs','列出已配置结果目录中的历史任务及可续算状态；优先发现已完成模式点',{},(_a,e)=>ctx.qpmRuntime.historySources(e));
 register('qpm_resume','按run_id续算已有任务，只补缺失点；不占设计轮次。预算为累计总上限，不能超过设置。停止预算耗尽需用户允许增加预算。',{run_id:{type:'string',required:true},budget:{type:'object',additionalProperties:false,properties:Object.fromEntries(['max_wall_time_s','max_solver_calls','max_geometry_count'].map(k=>[k,{type:'number'}]))}},(a,e)=>ctx.qpmRuntime.resume(ctx,e,a.run_id,a.budget));
 register('qpm_submit','提交明确的仿真或搜索任务；返回后台任务ID。默认演示后端，预算内执行；迭代必须携带返回的design_id继承同一设计预算',{...task,design_id:{type:'string'}},(a,e)=>ctx.qpmRuntime.submit(ctx,e,a.task,undefined,undefined,a.design_id));
 register('qpm_status','读取当前会话任务状态、成本与结果文件',id,(a,e)=>ctx.qpmRuntime.status(a.job_id,e.agent.id));
 register('qpm_cancel','取消当前会话任务并清理该任务拥有的进程',id,(a,e)=>{ctx.qpmRuntime.record(a.job_id,e.agent.id);return ctx.jobs.kill(a.job_id,e.agent.id,'user cancellation');});
 register('qpm_postprocess','跨会话按run_id或当前job_id重新生成图表；新产物保存到当前工作区独立绘图目录，保留原结果，不启动求解器',{run_id:{type:'string'},job_id:{type:'string'}},(a,e)=>ctx.qpmRuntime.postprocess(a.job_id,e.agent.id,e.signal,e,a.run_id));
 register('qpm_stage_extension','保存独立扩展代码和测试供人工审核，不执行代码、不覆盖求解脚本',{
  id:{type:'string',required:true},code:{type:'string',required:true,description:'adapter: adapt_task(dict)->dict; postprocess: analyze_result(context)->analysis. 不调用求解器、不修改核心代码。'},tests:{type:'string',required:true,description:'独立test_函数或unittest；包括缺失点、单位、边界情况'},
  metadata:{type:'object',additionalProperties:false,properties:{kind:{type:'string',enum:['adapter','postprocess'],required:true},version:{type:'string',required:true},description:{type:'string',required:true},applicability:{type:'string',required:true}},description:'类型、版本、说明和适用条件；省略时兼容旧adapter入口'}},(a,e)=>ctx.qpmRuntime.stageExtension(e.agent.id,a.id,a.code,a.tests,a.metadata,e));
 register('qpm_approve_version','展示保存版本的代码和测试，申请Harness原生审核；获批后自动测试并启用',{token:{type:'string',required:true}},(a,e)=>ctx.qpmRuntime.approveVersion(ctx,e,a.token));
 register('qpm_test_extension','仅测试设置页已人工批准的扩展；未批准时拒绝执行',{token:{type:'string',required:true}},(a,e)=>ctx.qpmRuntime.approvedExtension(a.token,'extension_test',undefined,e.signal,e));
 register('qpm_adapt_task','使用已批准且测试通过的扩展调整任务配置；仍需计划校验和正常提交',{token:{type:'string',required:true},...task},(a,e)=>ctx.qpmRuntime.approvedExtension(a.token,'extension_adapt',a.task,e.signal,e));
 register('qpm_analyze_result','使用已人工批准且测试通过的postprocess扩展读取当前会话导出结果，输出新指标/表格/曲线；保留原结果，不启动求解器。',{token:{type:'string',required:true},...id},(a,e)=>ctx.qpmRuntime.analyzeResult(a.token,a.job_id,e.agent.id,e.signal,e));
}
