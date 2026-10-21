const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..');fs.mkdirSync(path.join(root,'lib'),{recursive:true});
for(const n of ['config','index','tools','skill','history','delivery'])fs.copyFileSync(path.join(root,'src',n+'.ts'),path.join(root,'lib',n+'.js'));
const client=fs.readFileSync(path.join(root,'src/client.ts'),'utf8');
fs.writeFileSync(path.join(root,'lib/client.js'),`window.__ModuleLoader__.load({id:"dsh-qpm-agent",factory(require){const module={exports:{}};const exports=module.exports;\n${client}\nreturn module.exports;}});`);
const fixture=path.join(root,'.fixtures/runtime/node_modules/dsh-qpm-agent');
if(fs.existsSync(path.dirname(fixture))){fs.mkdirSync(fixture,{recursive:true});for(const n of ['package.json','cordis.patch.yml'])fs.copyFileSync(path.join(root,n),path.join(fixture,n));for(const n of ['lib','skills'])fs.cpSync(path.join(root,n),path.join(fixture,n),{recursive:true});}
console.log('Built precompiled host modules and native browser settings module.');
