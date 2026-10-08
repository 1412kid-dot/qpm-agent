import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
// Use the native registry. Packaged resources resolve relative to this module,
// not the user's working directory and not a YAML !!js expression.
export const inject=['skills'];
export function apply(ctx){
 const url=new URL('../skills/qpm-design/SKILL.md',import.meta.url);
 const source=readFileSync(url,'utf8');
 const content=source.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/,'');
 ctx.skills.register({name:'qpm-design',source:'packaged-qpm',description:'LN/LT薄膜QPM仿真规范：晶轴、TE/TM、波长角色、工具选择、工艺公差、GVD/GVM搜索及审核后的指标扩展。执行相关设计任务前加载。',
   invocation:{modelInvocable:true,userInvocable:true},resourceBase:{kind:'directory',path:fileURLToPath(new URL('../skills/qpm-design/',import.meta.url))},content});
}
