import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const plugin=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const hash=value=>createHash('sha256').update(value).digest('hex');

test('resume preserves completed evidence, schedules only missing cases and rejects changed snapshots',()=>{
 const out=fs.mkdtempSync(path.join(plugin,'.fixtures','resume-test-'));
 const frozen='{"version":"frozen-test"}';
 fs.mkdirSync(path.join(out,'plugin-snapshot'),{recursive:true});
 fs.writeFileSync(path.join(out,'plugin-snapshot/package.json'),frozen);
 const manifest={'plugin/package.json':hash(frozen)},digest=hash(JSON.stringify(manifest));
 fs.writeFileSync(path.join(out,'source-manifest.json'),JSON.stringify(manifest));
 const trace=path.join(out,'saved-trace.json'),evidence=JSON.stringify({source_snapshot_sha256:digest,events:[]});
 fs.writeFileSync(trace,evidence);
 const status={suite:'qpm-model-acceptance/1',model:'deepseek-flash',source_snapshot_sha256:digest,results:[{case_id:'complete_shg',repeat:1,trace}]};
 const original=JSON.stringify(status);fs.writeFileSync(path.join(out,'status.json'),original);
 const invoke=(resume=true)=>spawnSync(process.execPath,['dev/model-evaluation.mjs','--output',out,'--dry-run',...(resume?['--resume']:[])],{cwd:plugin,encoding:'utf8',env:{...process.env,DEEPSEEK_API_KEY:'offline-test-placeholder'}});
 const result=invoke();assert.equal(result.status,0,result.stderr);
 const plan=JSON.parse(result.stdout);assert.equal(plan.completed,1);assert.equal(plan.pending.length,35);
 assert.ok(!plan.pending.some(x=>x.case_id==='complete_shg'&&x.repeat===1));
 assert.equal(fs.readFileSync(trace,'utf8'),evidence);assert.equal(fs.readFileSync(path.join(out,'status.json'),'utf8'),original);
 const fresh=invoke(false);assert.notEqual(fresh.status,0);assert.match(fresh.stderr,/Output already exists/);
 fs.writeFileSync(path.join(out,'plugin-snapshot/package.json'),frozen+' ');
 const corrupt=invoke();assert.notEqual(corrupt.status,0);assert.match(corrupt.stderr,/Snapshot changed/);
 assert.equal(fs.readFileSync(trace,'utf8'),evidence);
});
