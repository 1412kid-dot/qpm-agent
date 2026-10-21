import test from 'node:test';import assert from 'node:assert/strict';
import fs from 'node:fs';import os from 'node:os';import path from 'node:path';import {spawnSync} from 'node:child_process';
test('evaluation guard enforces request ceiling before network and retains no secrets',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'qpm-eval-budget-')),ledger=path.join(dir,'ledger.jsonl');
 const code=`let called=0;globalThis.fetch=async request=>{called++;let body=await request.json();if(body.max_tokens!==8192)throw Error('cap missing');return new Response('data: {"model":"test","usage":{"prompt_tokens":20,"completion_tokens":0}}\\n\\n',{headers:{'content-type':'text/event-stream'}})};require(${JSON.stringify(path.resolve('dev/evaluation-budget.cjs'))});(async()=>{let input={model:'deepseek-flash',messages:[{role:'user',content:'test'}],max_tokens:256000};await fetch('https://api.deepseek.com/chat/completions',{method:'POST',headers:{authorization:'Bearer NEVER_LOG_THIS'},body:JSON.stringify(input)});try{await fetch('https://api.deepseek.com/chat/completions',{method:'POST',body:JSON.stringify(input)});throw Error('second must block')}catch(e){if(!e.message.includes('budget exhausted'))throw e;}if(called!==1)throw Error('network after budget');})().catch(e=>{console.error(e);process.exitCode=1});`;
 const run=spawnSync(process.execPath,['-e',code],{env:{...process.env,QPM_EVAL_LEDGER:ledger,QPM_EVAL_CAP_CNY:'.1'},encoding:'utf8'});
 assert.equal(run.status,0,run.stderr);const raw=fs.readFileSync(ledger,'utf8');assert.ok(!raw.includes('NEVER_LOG_THIS'));
 assert.equal(raw.split('\n').filter(Boolean).map(JSON.parse).filter(r=>r.kind==='reservation').length,1);
});


