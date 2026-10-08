// Executed by Desktop's Electron-as-Node runtime. No boot, model or solver.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {createRequire,registerHooks}=require('node:module');
const {pathToFileURL}=require('node:url');
const readJson=p=>JSON.parse(fs.readFileSync(p,'utf8').replace(/^\uFEFF/,''));
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
const bytes=p=>fs.existsSync(p)?fs.readFileSync(p):Buffer.alloc(0);
let withFileLock;
async function atomic(file,data,expected){return withFileLock(file,async()=>{
 if(hash(bytes(file))!==expected)throw Error('configuration_changed_concurrently');
 const temp=file+'.qpm-'+crypto.randomUUID()+'.tmp';fs.writeFileSync(temp,data);
 try{if(hash(bytes(file))!==expected)throw Error('configuration_changed_concurrently');fs.renameSync(temp,file);}finally{if(fs.existsSync(temp))fs.unlinkSync(temp);}
},{waitMs:10000});}
async function main(){
 const q=readJson(process.argv[2]);
 const anchor=path.join(q.harnessRoot,'resources/app.asar/dsh/node_modules/@deepseek-ai/dsh/package.json');
 const runtime=createRequire(anchor);
 registerHooks({resolve(spec,ctx,next){try{return next(spec,ctx);}catch(e){if(spec.startsWith('@deepseek-ai/'))return {url:pathToFileURL(runtime.resolve(spec)).href,shortCircuit:true};throw e;}}});
 const boot=await import(pathToFileURL(runtime.resolve('@deepseek-ai/dsh-app-boot')).href);
 ({withFileLock}=await import(pathToFileURL(runtime.resolve('@deepseek-ai/dsh-atomic-write')).href));
 const include=await import(pathToFileURL(runtime.resolve('@deepseek-ai/cordis-plugin-include')).href);
 const yaml=runtime('js-yaml');
 const patch=path.join(q.profileDir,'cordis.patch.yml'),manifest=path.join(q.profileDir,'package.json');
 const original=bytes(patch),text=original.toString('utf8').replace(/^\uFEFF/,''),rows=yaml.load(text,{schema:include.entryListSchema})??[];
 if(!Array.isArray(rows))throw Error('profile_patch_must_be_array');
 const owned=r=>['qpm-runtime','preset-qpm'].includes(r?.id);
 const state=()=>{const p=readJson(manifest);return {dependency:p.dependencies?.['dsh-qpm-agent']??null,bundleIndex:p.dsh?.profile?.bundles?.indexOf('dsh-qpm-agent')??-1,patchHash:hash(original),patchBase64:original.toString('base64'),qpmRows:rows.filter(owned)};};
 // Preserve untouched block-list entries byte for byte. Flow-style input has a
 // semantic round-trip fallback with the official !!js dialect, never eval.
 function edited(newRows){
  const starts=[...text.matchAll(/^-(?=\s)/gm)].map(m=>m.index);
  if(starts.length===rows.length&&starts.length){
   let output=text.slice(0,starts[0]);
   for(let i=0;i<rows.length;i++)if(!owned(rows[i]))output+=text.slice(starts[i],starts[i+1]??text.length);
   output=output.replace(/^# (?:BEGIN|END) QPM INSTALL\s*$/gm,'');
   return output.trimEnd()+'\n'+(newRows.length?yaml.dump(newRows,{schema:include.entryListSchema,lineWidth:-1,noRefs:true}):'');
  }
  return yaml.dump([...rows.filter(r=>!owned(r)),...newRows],{schema:include.entryListSchema,lineWidth:-1,noRefs:true});
 }
 if(q.action==='snapshot')return state();
 if(q.action==='prepare'){
  const existing=boot.composeEntries([rows]).find(r=>r.id==='qpm-runtime')?.config??{};
  // Overrides target a bundle row absent from an empty base: collect config
  // values directly, preserving user fields without executing expressions.
  const config=Object.assign({},...rows.filter(r=>r.id==='qpm-runtime').map(r=>r.config||{}),existing);
  const first=!Object.keys(config).length;
  config.pythonExecutable=q.pythonExecutable;config.pythonModuleRoot=q.pythonModuleRoot;
  config.outputRoot??=path.join(q.releaseRoot,'results');config.extensionsRoot??=path.join(q.releaseRoot,'extensions');
  config.defaultBackend??='demo';config.enableComsol??=false;
  await validateConfig(config);
  const presetRows=rows.filter(r=>r.id==='preset-qpm').map(r=>structuredClone(r));
  // A prior full preset override can hide new bundle plugins. Preserve its
  // custom settings and plugin rows, adding only the two QPM skill entries.
  const bundle=yaml.load(fs.readFileSync(path.join(q.releaseRoot,'cordis.patch.yml'),'utf8'),{schema:include.entryListSchema});
  const standardPreset=bundle.flatMap(r=>r.insert||[]).find(r=>r.id==='preset-qpm');
  const standard=standardPreset.config.plugins;
  for(const row of presetRows){
   row.config={...structuredClone(standardPreset.config),...(row.config||{})};
   if(Array.isArray(row.config.plugins)){
   for(const moduleName of ['dsh-qpm-agent/skill','@deepseek-ai/dsh-tool-skill']){
    if(!row.config.plugins.some(p=>p.name===moduleName))row.config.plugins.push(structuredClone(standard.find(p=>p.name===moduleName)));
   }
   }
  }
  return {...state(),config,candidateBase64:Buffer.from(edited([{id:'qpm-runtime',config},...presetRows])).toString('base64'),firstInstall:first};
 }
 if(q.action==='apply'){
  const data=q.remove?Buffer.from(edited([])):Buffer.from(q.candidateBase64,'base64');
  await atomic(patch,data,q.expectedHash);return {patchHash:hash(data)};
 }
 if(q.action==='restore-patch'){await atomic(patch,Buffer.from(q.patchBase64,'base64'),q.expectedHash);return {restored:true};}
 if(q.action==='restore-owned'){await atomic(patch,Buffer.from(edited(q.qpmRows)),hash(original));return {restored:true};}
 if(q.action==='restore-bundle'){
  const raw=bytes(manifest),p=readJson(manifest);p.dsh??={};p.dsh.profile??={};
  const list=(p.dsh.profile.bundles||[]).filter(n=>n!=='dsh-qpm-agent');
  if(q.bundleIndex>=0)list.splice(Math.min(q.bundleIndex,list.length),0,'dsh-qpm-agent');
  p.dsh.profile.bundles=list;await atomic(manifest,Buffer.from(JSON.stringify(p,null,2)+'\n'),hash(raw));return {restored:true};
 }
 if(q.action==='removed'){
  const p=readJson(manifest);
  if(p.dependencies?.['dsh-qpm-agent']||p.dsh?.profile?.bundles?.includes('dsh-qpm-agent')||rows.some(owned))throw Error('qpm_uninstall_incomplete');
  return {uninstalled:true};
 }
 async function validateConfig(config){
  const {Config}=await import(pathToFileURL(path.join(q.releaseRoot,'lib/config.js')).href);
  const {resolveConfig}=await import(pathToFileURL(runtime.resolve('@deepseek-ai/cordis')).href);
  resolveConfig({Config},config);
 }
 if(q.action==='validate'){
  const p=readJson(manifest);
  if(!p.dependencies?.['dsh-qpm-agent']||!p.dsh?.profile?.bundles?.includes('dsh-qpm-agent'))throw Error('qpm_package_or_bundle_not_registered');
  const loaded=boot.loadProfileDirectory('qpm-check',q.profileDir,anchor);
  if(loaded.skippedBundles.some(r=>r.packageName==='dsh-qpm-agent'))throw Error('qpm_bundle_skipped');
  const home=boot.loadOptionalPatches('qpm-check',path.join(q.dshHome,'cordis.patch.yml'))||[];
  const entries=boot.composeEntries([...loaded.layers.map(l=>l.patches),loaded.patches,home]);
  const roots=entries.filter(r=>r.id==='qpm-runtime'),presets=entries.filter(r=>r.id==='preset-qpm');
  if(roots.length!==1||presets.length!==1||roots[0].disabled||presets[0].disabled)throw Error('qpm_entries_missing_duplicate_or_disabled');
  if(roots[0].name!=='dsh-qpm-agent'||presets[0].config?.id!=='qpm')throw Error('qpm_entry_identity_invalid');
  const tools=presets[0].config.plugins.filter(r=>r.name==='dsh-qpm-agent/tools'&&!r.disabled);
  if(tools.length!==1)throw Error('qpm_tools_missing_or_duplicate');
  for(const moduleName of ['dsh-qpm-agent/skill','@deepseek-ai/dsh-tool-skill']){
   if(presets[0].config.plugins.filter(r=>r.name===moduleName&&!r.disabled).length!==1)throw Error('qpm_skill_missing_or_duplicate');
  }
  await validateConfig(roots[0].config||{});
  if(q.expectedPython!==undefined&&(roots[0].config?.pythonExecutable!==q.expectedPython||roots[0].config?.pythonModuleRoot!==q.expectedModuleRoot))throw Error('effective_python_configuration_overridden');
  const installed=boot.resolveBundleDir('qpm-check','dsh-qpm-agent',anchor,q.profileDir);
  for(const file of ['lib/index.js','lib/history.js','lib/config.js','lib/tools.js','lib/client.js','lib/skill.js','skills/qpm-design/SKILL.md','skills/qpm-design/task.schema.json','cordis.patch.yml'])if(!fs.existsSync(path.join(installed,file)))throw Error('missing_plugin_entry:'+file);
  for(const file of ['lib/index.js','lib/history.js','lib/config.js','lib/tools.js','lib/client.js','lib/skill.js','skills/qpm-design/SKILL.md','skills/qpm-design/task.schema.json','cordis.patch.yml']){
   if(hash(bytes(path.join(installed,file)))!==hash(bytes(path.join(q.releaseRoot,file))))throw Error('qpm_installed_bytes_mismatch');
  }
  const contract=readJson(path.join(installed,'skills/qpm-design/task.schema.json'));
  if(contract.type!=='object'||!contract.properties.propagation_axis||!contract.properties.required_metrics)throw Error('qpm_task_contract_invalid');
  return {static_config_valid:true,desktop_load_verified:false,solver_started:false,model_called:false};
 }
 throw Error('unknown_helper_action');
}
main().then(value=>process.stdout.write(JSON.stringify({ok:true,value})+'\n')).catch(e=>{
 // YAML exceptions can contain configuration source, including credentials.
 const code=/^[a-z_]+(?::lib\/[a-z.]+)?$/.test(e.message)?e.message:`configuration_parse_or_schema_error (${e.name})`;
 process.stderr.write('QPM helper: '+code+'\n');process.exitCode=1;
});
