import test from 'node:test';
import assert from 'node:assert/strict';
import {jsonSafe,apply} from '../.fixtures/runtime/node_modules/dsh-qpm-agent/lib/tools.js';

test('optional and nested missing values are explicit JSON null',()=>{
 const value=jsonSafe({fallback_reason:undefined,font_diagnostic:undefined,nested:{missing:undefined},points:[NaN,Infinity,undefined,1]});
 assert.deepEqual(value,{fallback_reason:null,font_diagnostic:null,nested:{missing:null},points:[null,null,null,1]});
 assert.deepEqual(JSON.parse(JSON.stringify(value)),value);
});
test('cycles and executable result values fail explicitly',()=>{
 const cycle={};cycle.self=cycle;
 assert.throws(()=>jsonSafe(cycle),/cycle/);
 assert.throws(()=>jsonSafe({callback(){}}),/non-JSON/);
});
test('native tool execute normalizes before Harness validates',async()=>{
 const registered=new Map();
 apply({effect:f=>f(),tools:{register:t=>registered.set(t.name,t)},qpmRuntime:{plot:async()=>({fallback_reason:undefined,figures:['plot.png']}),plotEnvironment:async()=>({font_diagnostic:undefined})}});
 const exec={agent:{id:'serialization'}};
 assert.equal((await registered.get('qpm_plot').execute({name:'test',preset:{}},exec)).fallback_reason,null);
 assert.equal((await registered.get('qpm_plot_environment').execute({},exec)).font_diagnostic,null);
});
