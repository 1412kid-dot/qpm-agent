import {writeFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
import {createUserMessage} from '@deepseek-ai/dsh-llm';

export function checkDelivery(text,d){
 const issues=[];
 const numbers=[...(text||'').matchAll(/[-+]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?/g)].map(m=>Number(m[0]));
 if(!text?.trim()||/^(I'll load|我先加载|先加载).{0,80}$/s.test(text.trim()))issues.push('missing_result_body');
 const groups=Map.groupBy(d.facts||[],f=>f.metric);
 for(const [metric,facts] of groups){
  const pattern={period:/周期|period|Λ/i,gvd:/GVD|色散/i,beta1:/beta.?1|群延迟|β.?1/i,D:/色散|\bD\b/,gvm:/GVM|群速度失配/i,pmf:/PMF|相位匹配函数/i,normalized_efficiency:/效率|efficiency/i,process_coefficient:/系数|coefficient/i,thickness_sensitivity:/灵敏度|sensitivity/i}[metric];
  if(pattern&&!pattern.test(text||''))issues.push('missing_metric:'+metric);
  const fact=facts.find(f=>f.dataset==='nominal'&&Number.isFinite(f.center_value));
  if(fact&&!numbers.some(n=>Math.abs(n-fact.center_value)<=Math.max(1e-9,Math.abs(fact.center_value)*.002)))issues.push('missing_or_inconsistent_center_value:'+metric);
  const unit={period:/um|µm|μm|微米|\\mu\s*m/i,gvd:/fs|飞秒/i,beta1:/ps|皮秒/i,gvm:/ps|皮秒/i,normalized_efficiency:/W|瓦/i,process_coefficient:/W|瓦/i}[metric];
  if(fact&&unit&&!unit.test(text||''))issues.push('missing_unit:'+metric);
 }
 const provenance=/SYNTHETIC|DEMO/i.test(d.validation?.data_kind||'')?/demo|演示|合成/i:/COMSOL/i.test(d.validation?.data_kind||'')?/COMSOL/i:/回放|replay|数据来源/i;
 if(!provenance.test(text||''))issues.push('missing_provenance');
 if(!/验证|验收|收敛|validated|validation/i.test(text||''))issues.push('missing_validation');
 if(!/report\.md|process\.md|delivery\.json|result\.json/.test(text||''))issues.push('missing_evidence_link');
 const normalized=(text||'').replaceAll('\\','/');
 if(d.evidence?.result_file&&!normalized.includes(d.evidence.result_file.replaceAll('\\','/')))issues.push('missing_exact_result_path');
 if(d.evidence?.process_file&&!normalized.includes(d.evidence.process_file.replaceAll('\\','/')))issues.push('missing_exact_process_path');
 const delta=d.center_auxiliary?.delta_beta_m_inv;
 if(Number.isFinite(delta)){
  for(const line of (text||'').split('\n').filter(l=>/Δβ|δβ|delta_beta/.test(l)&&/\d/.test(l)&&!/2\s*\*?\s*pi|2π|2\\pi/.test(l))){
   const exponent=line.replace(/([\d.]+)\s*[×x*]\s*10([⁰¹²³⁴⁵⁶⁷⁸⁹⁻]+)/g,(_,a,b)=>a+'e'+[...b].map(c=>'⁰¹²³⁴⁵⁶⁷⁸⁹'.includes(c)?'⁰¹²³⁴⁵⁶⁷⁸⁹'.indexOf(c):'-').join(''));
   const nums=[...exponent.matchAll(/[-+]?\d+(?:\.\d*)?(?:e[-+]?\d+)?/gi)].map(m=>Number(m[0]));
   const factor=/µm|μm|um/.test(line)&&!/m[⁻^-]/.test(line)?1e6:1;
   if(!nums.some(n=>Math.abs(n*factor-delta)<=Math.abs(delta)*.002))issues.push('inconsistent_reported_delta_beta');
   if(/残差|residual/i.test(line))issues.push('uncompensated_mismatch_called_residual');
  }
 }
 return {passed:issues.length===0,issues,scope:'delivery completeness only; physical narrative still requires semantic review'};
}

// Observe committed public events. Never read or persist model reasoning.
export function installDelivery(ctx){
 const states=new Map();
 const state=agent=>{let s=states.get(agent.id);if(!s){s={agent,turn:0,step:0,text:'',textStep:-1,workStep:-1,repairs:0,repairSteps:0};states.set(agent.id,s);}return s;};
 ctx.on?.('session/event',(session,event)=>{
  for(const s of states.values())if(s.agent.session===session){
   if(event.type==='turn/start'){s.turn=event.data.turn;s.text='';s.textStep=-1;s.workStep=-1;s.repairs=0;s.repairSteps=0;s.delivery=null;s.status=null;s.pendingJob=null;s.skipDelivery=false;}
   if(event.type==='step/start'){s.step=event.data.step;if(s.repairing)s.repairSteps++;}
   if(event.type==='assistant/message'){
    const text=event.data.message.content.filter(b=>b.type==='text').map(b=>b.text).join('');
    if(text.trim()){s.text=text;s.textStep=s.step;}
   }
  }
 });
 const persist=async s=>{
  if(!s.delivery)return;
  const root=s.auditRoot?path.join(s.auditRoot,String(s.turn)):path.dirname(s.delivery.evidence.delivery_file);await mkdir(root,{recursive:true});
  const file=path.join(root,'delivery-status.json');
  await writeFile(file,JSON.stringify({contract:'qpm-delivery/1',turn:s.turn,status:s.status,repair_count:s.repairs,
   repair_steps:s.repairSteps,last_text_step:s.textStep,last_work_step:s.workStep,check:s.check,
   fallback_file:s.delivery.evidence.delivery_file},null,2),'utf8');
 };
 ctx.tools?.guard?.(exec=>{
  const s=states.get(exec.agent?.id);
  if(s?.repairing&&!['qpm_status','qpm_result','qpm_delivery','qpm_process','qpm_study_status','qpm_study_result','qpm_conventions'].includes(exec.name))
   return '摘要补写阶段不允许执行此工具';
 });
 ctx.on?.('agent/pre-step',async(payload,next)=>{
  const s=states.get(payload.agent.id);
  if(s?.repairing&&s.repairSteps>=2){
   s.status='deterministic_fallback';s.repairing=false;await persist(s);
   await ctx.qpmRuntime.deliveryFallback?.(payload.agent,s.delivery,payload.turn,s.auditRoot?path.join(s.auditRoot,String(payload.turn)):undefined);
   return {kind:'reject'};
  }
  return next();
 });
 ctx.on?.('agent/turn-stopping',async({agent,signal,turn})=>{
  const s=states.get(agent.id);if(!s||signal.aborted)return;
  if(s.skipDelivery)return;
  if(s.pendingJob&&ctx.qpmRuntime.deliveryJobRunning?.(agent,s.pendingJob))return;
  if(!s.delivery&&s.pendingJob)s.delivery=await ctx.qpmRuntime.completedDelivery?.(agent,s.pendingJob);
  if(!s.delivery)return;
  s.turn=turn;
  s.check=checkDelivery(s.textStep>s.workStep?s.text:'',s.delivery);
  if(s.check.passed){s.status=s.repairs?'model_repaired':'model_delivered';s.repairing=false;await persist(s);return;}
  if(!s.repairs){
   s.repairs=1;s.repairing=true;s.status='repairing';await persist(s);
   agent.steer(createUserMessage({source:{kind:'qpm-delivery',producer:'dsh-qpm-agent'},content:[{type:'text',text:
    '交付检查未通过：'+s.check.issues.join(', ')+'。计算已经结束。仅使用下列事实摘要交付中文结果、单位、数据来源、验证状态和报告链接；缺失项明确说明。摘要正文约300–600字，不重复整份配置或内部交付字段；完整路径仅列结果与过程报告。禁止新增求解、研究动作或绘图。不要增加未经证据支持的物理解释。\n'+JSON.stringify(s.delivery)}]}));
  }else{
   s.status='deterministic_fallback';s.repairing=false;await persist(s);
   // The tool card is explicitly program-generated; never forge a model message.
   await ctx.qpmRuntime.deliveryFallback?.(agent,s.delivery,turn,s.auditRoot?path.join(s.auditRoot,String(turn)):undefined);
  }
 });
 ctx.on?.('agent/disposed',({agent})=>states.delete(agent.id));
 return {
  before(name,e,args){
   const s=state(e.agent);
   s.auditRoot=ctx.qpmRuntime.deliveryAuditRoot?.(e.agent);
   if(s.repairing){
    const readonly=new Set(['qpm_status','qpm_result','qpm_delivery','qpm_process','qpm_study_status','qpm_study_result','qpm_conventions']);
    if(s.repairSteps>2||!readonly.has(name))throw Error('摘要补写阶段仅允许读取已完成结果，不允许启动计算、绘图或推进研究。');
   }
   return s;
  },
  async after(name,e,args,value){
   const s=state(e.agent);
   if(name==='qpm_cancel'||(name==='qpm_study_step'&&['pause','cancel'].includes(args.action?.kind))){s.skipDelivery=true;s.pendingJob=null;s.delivery=null;return value;}
   if(['qpm_submit','qpm_resume','qpm_study_step','qpm_plot','qpm_plot_preset'].includes(name))s.workStep=s.step;
   if(value?.job_id)s.pendingJob=value.job_id;
   let d=value?.delivery;
   if(!d&&value?.result?.delivery)d=value.result.delivery;
   if(!d&&name==='qpm_delivery')d=value;
   if(d?.contract==='qpm-delivery/1'){s.delivery=d;}
   return value;
  },
 };
}
