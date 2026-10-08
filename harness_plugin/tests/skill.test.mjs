import test from 'node:test';import assert from 'node:assert/strict';
import {Context,Service} from '../.fixtures/runtime/node_modules/@deepseek-ai/cordis/lib/index.js';
import Skills from '../.fixtures/runtime/node_modules/@deepseek-ai/dsh-skill/lib/index.js';
import {apply as skillApply} from '../.fixtures/runtime/node_modules/dsh-qpm-agent/lib/skill.js';
import {apply as toolSkillApply} from '../.fixtures/runtime/node_modules/@deepseek-ai/dsh-tool-skill/lib/index.js';

test('actual Harness registry discovers and skill tool loads packaged QPM instructions',async()=>{
 const ctx=new Context();
 class Agents extends Service{constructor(c){super(c,'agents');}}
 new Agents(ctx);new Skills(ctx);const registry=ctx.skills;
 const tools=new Map();ctx.provide('tools',{register(t){tools.set(t.name,t);return ()=>tools.delete(t.name);}});
 skillApply(ctx);toolSkillApply(ctx);
 try{
  const catalog=await ctx.skills.list({});
  assert.equal(catalog.filter(s=>s.name==='qpm-design').length,1);
  const definition=await ctx.skills.get('qpm-design',{});
  assert.match(definition.content,/GVD/);assert.match(definition.content,/Y/);assert.equal(definition.invocation.modelInvocable,true);
  const loaded=await tools.get('skill').execute({name:'qpm-design'},{signal:new AbortController().signal});
  assert.equal(loaded.content,definition.content);assert.equal(loaded.resourceBase.kind,'directory');
  const rendered=tools.get('skill').output.render({name:'qpm-design'},loaded);
  assert.match(rendered[0].text,/skill_content name="qpm-design"/);
 }finally{await ctx.fiber.dispose();}
 assert.equal((await registry.list({})).length,0);
});
