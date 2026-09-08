import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { stripTypeScriptTypes } from 'node:module';
import { setImmediate as tick } from 'node:timers/promises';
import test from 'node:test';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');

const source = readFileSync(resolve(root, 'src/main.tsx'), 'utf8');
// Execute the production coordinator with controlled state sinks and deferred I/O.
// TypeScript's normal build separately checks its actual React/API types.
const coordinator = source.slice(source.indexOf('  async function perform('), source.indexOf('  function submitRole('));
const compiled = stripTypeScriptTypes(coordinator);
const bind = new Function('dependencies', `const { routeRef, projectSelectionRef, readRequestRef, setActiveAction, clearActionError, recordActionError, recordRefreshError, setState, ApiProblem } = dependencies; ${compiled}; return { perform, refreshAfterConfirmed };`);
const projectSource = readFileSync(resolve(root, 'src/projectWorkspace.ts'), 'utf8');
const readClass = projectSource.slice(projectSource.indexOf('export class ProjectReadRequest'), projectSource.indexOf('/** Drafts live'));
const ProjectReadRequest = new Function(stripTypeScriptTypes(readClass.replace('export class', 'class')) + '; return ProjectReadRequest;')();
const apiSource = readFileSync(resolve(root, 'src/api.ts'), 'utf8');
const problemClass = apiSource.slice(apiSource.indexOf('export class ApiProblem'), apiSource.indexOf('function isObject'));
const ApiProblem = new Function(stripTypeScriptTypes(problemClass.replace('export class', 'class')) + '; return ApiProblem;')();
function deferred() { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b; }); return {promise,resolve,reject}; }
function fixture() {
  let generation=0, active=null, state={kind:'ready',data:'initial',notice:null};
  const errors={}; const routeRef={current:{projectId:'A'}}; const reads = new ProjectReadRequest();
  const api = bind({routeRef, projectSelectionRef:{current:{currentGeneration:()=>generation,isCurrent:(n)=>n===generation}},readRequestRef:{current:reads},
    setActiveAction:(v)=>{active=v;},clearActionError:(a)=>{delete errors[a];},
    recordActionError:(a,error,retry)=>{errors[a]={kind:'mutation',error,retry};},
    recordRefreshError:(a,error,retry)=>{errors[a]={kind:'refresh',error,retry};},
    setState:(v)=>{state=typeof v==='function'?v(state):v;},ApiProblem});
  return {...api,errors,reads,get active(){return active;},get state(){return state;}, checking(){state={kind:'checking'};}, switchProject(id){generation++;routeRef.current.projectId=id;active=null;state={kind:'ready',data:id,notice:null};}};
}
test('confirmed write resolves and clears busy while its workspace read is still pending', async()=>{
  const f=fixture(), read=deferred();let settled=false, posts=0;
  const result=f.perform('role',{load:()=>read.promise},async()=>{posts++;},'created').then(v=>{settled=v;});
  await tick(); assert.equal(f.active,null); assert.equal(f.state.notice,'created');assert.equal(settled,true);assert.equal(posts,1);
  read.resolve('fresh'); await result;await tick();assert.equal(f.state.data,'fresh');
});
test('an old refresh cannot clear a newer mutation busy state or publish its old snapshot', async()=>{
  const f=fixture(), oldRead=deferred(), newWrite=deferred(), newRead=deferred();
  void f.perform('role',{load:()=>oldRead.promise},async()=>{},'created');await tick();
  void f.perform('runtime',{load:()=>newRead.promise},()=>newWrite.promise,'configured');await tick();
  oldRead.resolve('stale');await tick();assert.equal(f.active,'runtime');assert.equal(f.state.data,'initial');
  newWrite.resolve();await tick();newRead.resolve('latest');await tick();assert.equal(f.state.data,'latest');assert.equal(f.state.notice,'configured');
});
test('refresh failure keeps confirmed success and recovery performs reads only',async()=>{
  const f=fixture();let posts=0, reads=0;
  const client={load:async()=>{if(++reads===1)throw new TypeError('read offline');return 'fresh';}};
  assert.equal(await f.perform('role',client,async()=>{posts++;},'created'),true);await tick();
  assert.equal(f.state.notice,'created');assert.equal(f.errors.role.kind,'refresh');
  f.errors.role.retry();await tick();assert.equal(posts,1);assert.equal(reads,2);assert.equal(f.state.data,'fresh');assert.equal(f.errors.role,undefined);
});
test('uncertain mutation retries its original closure and never becomes a refresh retry',async()=>{
  const f=fixture();const bodies=[];let reads=0;const original=Object.freeze({project:'A',key:'same-key',name:'original'});
  const work=async()=>{bodies.push(original);if(bodies.length===1)throw new TypeError('response lost');};
  await f.perform('role',{load:async()=>{reads++;return 'fresh';}},work,'created');
  assert.equal(f.errors.role.kind,'mutation');assert.equal(reads,0);f.errors.role.retry();await tick();assert.equal(bodies.length,2);assert.equal(bodies[0],bodies[1]);assert.equal(reads,1);
});
for(const phase of ['write','read'])test(`A to B switch during ${phase} ignores late A completion`,async()=>{
  const f=fixture(), write=deferred(), read=deferred();
  void f.perform('role',{load:()=>read.promise},()=>write.promise,'created');
  if(phase==='read'){write.resolve();await tick();}
  f.switchProject('B');const bWrite=deferred();void f.perform('runtime',{load:async()=>'B-fresh'},()=>bWrite.promise,'configured');
  write.resolve();read.resolve('A-stale');await tick();assert.equal(f.state.data,'B');assert.equal(f.active,'runtime');assert.equal(f.state.notice,null);
  bWrite.resolve();await tick();assert.equal(f.state.data,'B-fresh');
});
test('an older confirmed read cannot overwrite a newer explicit workspace read',async()=>{
  const f=fixture(), read=deferred();void f.perform('role',{load:()=>read.promise},async()=>{},'created');await tick();
  f.reads.begin();read.resolve('old');await tick();assert.equal(f.state.data,'initial');
});
test('already-configured refusal replaces the cancelled confirmed refresh without replaying setup',async()=>{
  const f=fixture(), oldRead=deferred(), freshRead=deferred();let posts=0, reads=0;
  const client={load:()=>++reads===1?oldRead.promise:freshRead.promise};
  const work=async()=>{if(++posts===2)throw new ApiProblem(409,{code:'setup_configured'});};
  assert.equal(await f.perform('runtime',client,work,'configured'),true);
  assert.equal(await f.perform('runtime',client,work,'configured'),false);
  oldRead.resolve('setup_unconfigured');await tick();assert.equal(f.state.data,'initial');
  freshRead.resolve('setup_configured');await tick();
  assert.equal(f.state.data,'setup_configured');assert.equal(f.state.notice,'configured');
  assert.equal(posts,2);assert.equal(reads,2);assert.equal(f.errors.runtime,undefined);
});
test('already-configured recovery failure retains success and retries only its read',async()=>{
  const f=fixture(), oldRead=deferred();let posts=0, reads=0;
  const client={load:async()=>{reads++;if(reads===1)return oldRead.promise;if(reads===2)throw new TypeError('offline');return 'setup_configured';}};
  const work=async()=>{if(++posts===2)throw new ApiProblem(409,{code:'setup_configured'});};
  await f.perform('runtime',client,work,'configured');await f.perform('runtime',client,work,'configured');await tick();
  assert.equal(f.errors.runtime.kind,'refresh');assert.equal(f.state.notice,'configured');
  f.errors.runtime.retry();await tick();oldRead.resolve('old');await tick();
  assert.equal(posts,2);assert.equal(reads,3);assert.equal(f.state.data,'setup_configured');
});
test('a refused first setup invents no success notice while replacing a checking snapshot',async()=>{
  const f=fixture(), read=deferred();
  assert.equal(await f.perform('runtime',{load:()=>read.promise},async()=>{throw new ApiProblem(409,{code:'setup_configured'});},'configured'),false);
  f.checking();read.resolve('setup_configured');await tick();
  assert.equal(f.state.notice,null);assert.equal(f.errors.runtime,undefined);
});
test('late already-configured recovery cannot publish into another project',async()=>{
  const f=fixture(), read=deferred();
  await f.perform('runtime',{load:()=>read.promise},async()=>{throw new ApiProblem(409,{code:'setup_configured'});},'configured');
  f.switchProject('B');read.resolve('A-configured');await tick();assert.equal(f.state.data,'B');assert.equal(f.state.notice,null);
});
test('a different runtime refusal remains a mutation error',async()=>{
  const f=fixture();let reads=0;
  await f.perform('runtime',{load:async()=>{reads++;}},async()=>{throw new ApiProblem(409,{code:'setup_unconfigured'});},'configured');
  assert.equal(f.errors.runtime.kind,'mutation');assert.equal(reads,0);
});
