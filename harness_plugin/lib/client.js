window.__ModuleLoader__.load({id:"dsh-qpm-agent",factory(require){const module={exports:{}};const exports=module.exports;
const React=require('react');
exports.inject=['slots','configForms','locale'];
const labels={historySources:'额外历史来源（绝对路径JSON数组）',pythonExecutable:'Python解释器',pythonModuleRoot:'Python包目录（安装脚本填写）',comsolBin:'COMSOL程序路径',comsolVersion:'COMSOL版本',outputRoot:'旧结果目录（新任务使用当前工作区）',extensionsRoot:'扩展目录',defaultBackend:'默认后端',enableComsol:'启用真实COMSOL',lengthMm:'器件长度 mm',wavelengthStepNm:'波长扫描步长 nm',dispersionStencilPoints:'色散差分点数（3、5或7）',autoResume:'预算内自动恢复',recoveryAttempts:'故障恢复次数（0至2）',dispersionStepNm:'色散辅助间距 nm',fwDerivativeNm:'基频GVM步长 nm',shDerivativeNm:'倍频GVM步长 nm',thicknessDerivativeNm:'厚度导数步长 nm',thicknessToleranceNm:'厚度公差 nm',widthToleranceNm:'顶宽公差 nm',maxGeometryCount:'结构数上限',maxSolverCalls:'实际求解次数上限',maxWallTimeS:'时长上限 秒',maxIterations:'旧轮次设置（现按设计预算执行）',threads:'求解线程数',approvedExtensions:'人工审核令牌（批准后独立测试）'};
function Card(props){
 const scope=props.scope;
 const state=React.useSyncExternalStore(fn=>scope.subscribe(fn),()=>scope.getSnapshot());
 const [error,setError]=React.useState('');
 const value=state.value||{};
 const [draft,setDraft]=React.useState(value);
 React.useEffect(()=>setDraft(value),[state.value]);
 async function save(k,v){try{if(!await scope.mutate([{op:'set',path:[k],value:v}],state.revision))setError('保存失败，请刷新配置后重试');else setError('');}catch(e){setError(String(e));}}
 if(props.view==='summary')return React.createElement('p',null,'QPM设计助手：Python、COMSOL、工艺与预算配置');
 return React.createElement('section',null,React.createElement('h3',null,'QPM 设计助手'),React.createElement('p',null,'模型与API沿用Harness配置。保存和检查配置不会启动COMSOL。真实仿真必须先启用。运行中任务使用启动时快照。'),
 ...Object.entries(labels).map(([k,label])=>React.createElement('label',{key:k,style:{display:'block',margin:'12px 0'}},label,' ',
 k==='defaultBackend'?React.createElement('select',{value:value[k]||'demo',disabled:!state.writable,onChange:e=>save(k,e.target.value)},...['demo','comsol','replay'].map(v=>React.createElement('option',{key:v,value:v},v))):
 React.createElement('input',{type:typeof value[k]==='boolean'?'checkbox':typeof value[k]==='number'?'number':'text',checked:typeof value[k]==='boolean'?value[k]:undefined,value:typeof value[k]==='boolean'?undefined:draft[k]??'',disabled:!state.writable,onChange:e=>typeof value[k]==='boolean'?save(k,e.target.checked):setDraft({...draft,[k]:e.target.value}),onBlur:()=>{if(typeof value[k]!=='boolean'&&String(draft[k])!==String(value[k]))save(k,typeof value[k]==='number'?Number(draft[k]):draft[k]);},style:{minWidth:'280px'}}))),React.createElement('p',{role:'alert'},error));
}
exports.apply=ctx=>{const scope=ctx.configForms.get('qpm-runtime');ctx.effect(()=>ctx.configForms.whileServed(['qpm-runtime'],()=>ctx.slots.inject('plugins.item',()=>ctx.slots.register({name:'plugins.item',id:'qpm',order:30,label:()=> 'QPM设计助手',inject:()=>({scope})},Card))));};


return module.exports;}});