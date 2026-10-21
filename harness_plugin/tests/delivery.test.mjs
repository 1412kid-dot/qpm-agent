import test from 'node:test';import assert from 'node:assert/strict';
import {checkDelivery,installDelivery} from '../.fixtures/runtime/node_modules/dsh-qpm-agent/lib/delivery.js';
import {mkdtemp,readFile,rm} from 'node:fs/promises';import os from 'node:os';import path from 'node:path';
import {Session} from '../.fixtures/runtime/node_modules/@deepseek-ai/dsh-session/lib/index.js';
import {createUserMessage} from '../.fixtures/runtime/node_modules/@deepseek-ai/dsh-llm/lib/index.js';
import QpmRuntime from '../.fixtures/runtime/node_modules/dsh-qpm-agent/lib/index.js';
const d={contract:'qpm-delivery/1',validation:{data_kind:'SYNTHETIC DEMO'},facts:[{metric:'period',dataset:'nominal',center_value:11}],evidence:{delivery_file:'unused/delivery.json'}};
test('opening text cannot pass delivery; evidence and provenance required',()=>{
 assert.equal(checkDelivery("I'll load the QPM design skill first.",d).passed,false);
 assert.equal(checkDelivery('周期11 um，demo演示，独立验证未通过。详见 result.json 和 process.md。',d).passed,true);
 assert.equal(checkDelivery('周期11 um',d).passed,false);
 assert.equal(checkDelivery('周期99 um，demo，验证未通过，result.json。',d).passed,false);
});
test('deterministic fallback publishes a real file card without re-opening the model loop',async()=>{
 const root=await mkdtemp(path.resolve('.fixtures/qpm-fallback-'));
 const agent={session:new Session('fallback-test'),inject(){throw Error('fallback must not enqueue another model step');}};
 try{
  const runtime=Object.create(QpmRuntime.prototype);
  await runtime.deliveryFallback(agent,{...d,summary:'程序摘要：周期11 um，demo，独立验证未通过。'},1,root);
  assert.match(await readFile(path.join(root,'delivery-fallback.md'),'utf8'),/模型解释未完成/);
  assert.equal(agent.session.seq,1);
 }finally{await rm(root,{recursive:true,force:true});}
});
test('missing final body repairs once, blocks computation, and falls back after two steps',async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'qpm-delivery-'));const events={};let steer=0,fallback=0;
 const ctx={on:(n,f)=>events[n]=f,qpmRuntime:{deliveryFallback:async()=>{fallback++;}}};const guard=installDelivery(ctx);
 const agent={id:'test',session:new Session('delivery-test'),steer(input){
  assert.equal(input.role,'user');assert.ok(input.id);
  this.session.append('user/message',input,{surfaceOp:'append'});steer++;
 }};const e={agent};
 try{
 guard.before('qpm_status',e,{});
 const send=(type,data)=>events['session/event'](agent.session,{type,data});
 send('turn/start',{turn:1});send('step/start',{step:4});
 await guard.after('qpm_plot',e,{},{});
 await guard.after('qpm_status',e,{}, {delivery:{...d,evidence:{delivery_file:path.join(root,'delivery.json')}}});
 const payload={agent,turn:1,signal:new AbortController().signal};
 await events['agent/turn-stopping'](payload);
 assert.equal(steer,1);assert.throws(()=>guard.before('qpm_submit',e,{}),/摘要/);
 send('step/start',{step:5});send('step/start',{step:6});
 assert.deepEqual(await events['agent/pre-step'](payload,()=>{throw Error('third request forbidden');}),{kind:'reject'});
 assert.equal(fallback,1);
 const status=JSON.parse(await readFile(path.join(root,'delivery-status.json'),'utf8'));
 assert.equal(status.repair_count,1);assert.equal(status.status,'deterministic_fallback');
 }finally{await rm(root,{recursive:true,force:true});}
});
