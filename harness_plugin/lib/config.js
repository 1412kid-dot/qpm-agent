import Schema from '@deepseek-ai/schemastery';
import { homedir } from 'node:os';
import path from 'node:path';

const text = (v) => Schema.string().default(v).volatile();
const number = (v, min = 0) => Schema.number().min(min).default(v).volatile();
const integer = (v) => Schema.number().min(1).step(1).default(v).volatile();
export const Config = Schema.object({
  pythonExecutable: text('python'),
  pythonModuleRoot: text(''),
  comsolBin: text(''), comsolVersion: text('6.4'),
  outputRoot: text(path.join(homedir(), 'QPM-Agent', 'results')),
  extensionsRoot: text(path.join(homedir(), 'QPM-Agent', 'extensions')),
  defaultBackend: Schema.union(['demo','comsol','replay']).default('demo').volatile(),
  enableComsol: Schema.boolean().default(false).volatile(),
  lengthMm: number(5,.001), wavelengthStepNm:number(20,.001),
  dispersionStencilPoints:Schema.union([3,5,7]).default(3).volatile(),
  autoResume:Schema.boolean().default(true).volatile(), recoveryAttempts:Schema.number().min(0).max(2).step(1).default(2).volatile(),
  dispersionStepNm:number(2,.001), fwDerivativeNm:number(2,.001), shDerivativeNm:number(1,.001), thicknessDerivativeNm:number(2,.001),
  thicknessToleranceNm:number(0), widthToleranceNm:number(0),
  maxGeometryCount:integer(32), maxSolverCalls:integer(1000), maxWallTimeS:number(1800,1),
  maxIterations:integer(2), threads:integer(4),
  approvedExtensions:text(''), historySources:text('[]'),
});

export function snapshot(config) {
  return Object.fromEntries(Object.entries(config).map(([k,v]) => [k,v?.get ? v.get() : v]));
}
export function prepareTask(raw,c,execution=true) {
  if(!raw||typeof raw!=='object'||Array.isArray(raw))throw Error('任务必须是配置对象');
  const task=structuredClone(raw);
  task.provided_fields ??=Object.keys(raw).filter(k=>k!=='provided_fields');
  task.length_defaulted ??=raw.length_mm===undefined;
  for(const [key,value] of Object.entries({backend:c.defaultBackend,length_mm:c.lengthMm,wavelength_step_nm:c.wavelengthStepNm,
    dispersion_stencil_points:c.dispersionStencilPoints??3,dispersion_step_nm:c.dispersionStepNm,fw_derivative_nm:c.fwDerivativeNm,sh_derivative_nm:c.shDerivativeNm,thickness_derivative_nm:c.thicknessDerivativeNm})) {
    if(task[key]===undefined) task[key]=value;
  }
  if(execution&&task.backend==='comsol'&&!c.enableComsol) throw Error('真实 COMSOL 尚未在插件设置中启用');
  task.budget ??={};
  for(const [k,v] of Object.entries({max_geometry_count:c.maxGeometryCount,max_solver_calls:c.maxSolverCalls,max_wall_time_s:c.maxWallTimeS})) {
    if(task.budget[k]!==undefined && task.budget[k]>v) throw Error(`任务 ${k} 超过插件预算上限 ${v}`);
    task.budget[k] ??=v;
  }
  task.solver ??={}; task.solver.version ??=c.comsolVersion; task.solver.threads ??=c.threads;
  if(task.backend==='comsol'&&task.solver.comsol_bin&&task.solver.comsol_bin!==c.comsolBin)throw Error('COMSOL路径必须由插件设置管理');
  if(c.comsolBin) task.solver.comsol_bin ??=c.comsolBin;
  task.tolerances_nm ??={};
  if(c.thicknessToleranceNm && task.tolerances_nm.thickness_nm===undefined) task.tolerances_nm.thickness_nm=c.thicknessToleranceNm;
  if(c.widthToleranceNm && task.tolerances_nm.top_width_nm===undefined) task.tolerances_nm.top_width_nm=c.widthToleranceNm;
  return task;
}
