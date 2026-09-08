import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, symlinkSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { setImmediate as tick } from "node:timers/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
const root=resolve(dirname(fileURLToPath(import.meta.url)),".."),out=mkdtempSync(resolve(tmpdir(),"commons-project-create-"));
execFileSync(resolve(root,"node_modules/.bin/tsc"),["--ignoreConfig","--target","ES2022","--module","ESNext","--moduleResolution","Bundler","--lib","ES2022,DOM,DOM.Iterable","--jsx","react-jsx","--outDir",out,resolve(root,"src/components/ProjectCreateDialog.tsx"),resolve(root,"src/projectWorkspace.ts")],{cwd:root});symlinkSync(resolve(root,"node_modules"),resolve(out,"node_modules"),"dir");
const load=file=>import(pathToFileURL(resolve(out,file)).href);
const {ProjectCreation,parseFolderSelection,validateProjectDraft,targetPath}=await load("projectCreation.js");
const {ProjectRegistryApi}=await load("projectWorkspace.js");const {ApiProblem,WorkApi}=await load("api.js");const {ProjectCreateDialog}=await load("components/ProjectCreateDialog.js");const {projectCreationText}=await load("projectCreationStrings.js");
const selected=(extra={})=>({schema:"agent_commons.project_folder_selection.v1",status:"selected",path:"/Users/person/My Project",name:"My Project",...extra});
const inspection=(extra={})=>({inspectionId:`inspection.${"a".repeat(32)}`,mode:"existing",name:"My Project",initializationRequired:true,expiresAt:"2026-09-08T00:00:00Z",...extra});
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function existing(){const state=new ProjectCreation();state.show();state.setMode("existing");state.setLocation("/Users/person/My Project");return state;}

test("native picker DTO is closed and cancelled selection contains no path",()=>{
 assert.deepEqual(parseFolderSelection(selected()),{status:"selected",path:"/Users/person/My Project",name:"My Project"});assert.deepEqual(parseFolderSelection(selected({status:"cancelled",path:null,name:null})),{status:"cancelled",path:null,name:null});
 for(const extra of [{path:"relative/path"},{path:"/a\u0000b"},{name:"x".repeat(161)},{status:"cancelled"},{privateConfig:"leak"}])assert.throws(()=>parseFolderSelection(selected(extra)));
});
test("existing folder infers its name while manual name edits survive picker changes",async()=>{
 const state=existing();assert.equal(state.snapshot().name,"My Project");state.setName("Friendly name");await state.pick(async()=>parseFolderSelection(selected({path:"/elsewhere/Folder",name:"Folder"})));assert.equal(state.snapshot().name,"Friendly name");assert.equal(state.snapshot().location,"/elsewhere/Folder");
 const fresh=existing();await fresh.pick(async(purpose)=>{assert.equal(purpose,"existing");return parseFolderSelection(selected({path:"/a/Another",name:"Another"}));});assert.equal(fresh.snapshot().name,"Another");
});
test("new folder joins parent and safe child name without changing connect path",()=>{
 assert.equal(targetPath({mode:"new",location:"/",name:"App"}),"/App");assert.equal(targetPath({mode:"new",location:"/Projects/",name:"My App"}),"/Projects/My App");assert.equal(targetPath({mode:"existing",location:"/Projects/Repo",name:"Friendly"}),"/Projects/Repo");assert.equal(targetPath({mode:"new",location:"C:\\Projects\\",name:"App"}),"C:\\Projects\\App");
 for(const name of ["..","a/b","a\\b","a:b","Ж".repeat(128)])assert.equal(validateProjectDraft({mode:"new",name,location:"/parent"}),"folderName");assert.equal(validateProjectDraft({mode:"existing",name:"Ж".repeat(160),location:"/parent"}),null);
});
test("picker cancellation preserves prior choices and failure exposes manual fallback",async()=>{
 const state=existing();await state.pick(async()=>({status:"cancelled",path:null,name:null}));assert.equal(state.snapshot().location,"/Users/person/My Project");assert.equal(state.snapshot().problem,null);
 await state.pick(async()=>{throw new ApiProblem(503,{code:"project_picker_unavailable"});});assert.equal(state.snapshot().manual,true);assert.equal(state.snapshot().problem,"picker");
});
test("late picker and inspection results cannot alter a closed or reopened dialog",async()=>{
 const state=existing(),p=deferred();const picking=state.pick(()=>p.promise);state.close();state.show();state.setLocation("/new/Location");p.resolve(parseFolderSelection(selected()));await picking;assert.equal(state.snapshot().location,"/new/Location");
 const i=deferred(),checking=state.inspect(()=>i.promise);state.close();state.show();i.resolve(inspection({name:"Location"}));await checking;assert.equal(state.snapshot().inspection,null);assert.equal(state.snapshot().busy,null);
});
test("inspection performs no creation and existing initialization requires explicit consent",async()=>{
 const state=existing(),calls=[];await state.inspect(async(input)=>{calls.push(input);return inspection();});assert.deepEqual(calls,[{mode:"existing",path:"/Users/person/My Project",name:"My Project"}]);assert.equal(state.canCreate,false);let creates=0;await state.create(async()=>{creates++;});assert.equal(creates,0);state.setConsent(true);assert.equal(state.canCreate,true);await state.create(async()=>{creates++;});assert.equal(creates,1);assert.equal(state.snapshot().open,false);
});
test("already initialized folder and new child folder do not require redundant consent",async()=>{
 const state=existing();await state.inspect(async()=>inspection({initializationRequired:false}));assert.equal(state.canCreate,true);
 const fresh=new ProjectCreation();fresh.setName("App");fresh.setLocation("/parent");await fresh.inspect(async(input)=>{assert.equal(input.path,"/parent/App");return inspection({mode:"new",name:"App"});});assert.equal(fresh.canCreate,true);
});
test("uncertain create survives close and freezes exact inspection until confirmed retry",async()=>{
 const state=existing();await state.inspect(async()=>inspection());state.setConsent(true);const requests=[];await state.create(async(value)=>{requests.push(value);throw Error("response lost");});const fixed=state.snapshot().inspection;assert.equal(state.snapshot().uncertain,true);state.close();state.show();state.back();state.setName("Other");state.setLocation("/other");state.setMode("new");assert.equal(state.snapshot().inspection,fixed);assert.equal(state.snapshot().name,"My Project");await state.create(async(value)=>requests.push(value));assert.equal(requests[0],requests[1]);assert.equal(state.snapshot().inspection,null);
});
test("creation completion after close is recorded and never repeated by reopening",async()=>{
 const state=existing();await state.inspect(async()=>inspection({initializationRequired:false}));const result=deferred();let requests=0;const pending=state.create(async()=>{requests++;await result.promise;});state.close();result.resolve();await pending;state.show();await state.create(async()=>requests++);assert.equal(requests,1);assert.equal(state.snapshot().inspection,null);
});
test("ambiguous initialization recovers the exact child folder in connect mode",async()=>{
 const state=new ProjectCreation();state.setName("App");state.setLocation("/parent");await state.inspect(async()=>inspection({mode:"new",name:"App"}));await state.create(async()=>{throw new ApiProblem(409,{code:"project_initialization_ambiguous"});});assert.equal(state.snapshot().mode,"existing");assert.equal(state.snapshot().location,"/parent/App");assert.equal(state.snapshot().inspection,null);assert.equal(state.snapshot().problem,"ambiguous");
});
test("expiry permits a new inspection while other errors retain frozen retry",async()=>{
 const state=existing();await state.inspect(async()=>inspection({initializationRequired:false}));await state.create(async()=>{throw new ApiProblem(409,{code:"project_inspection_expired"});});assert.equal(state.snapshot().inspection,null);assert.equal(state.snapshot().problem,"expired");assert.equal(state.snapshot().uncertain,false);
});
test("real host picker transport retains exact purpose and never uses a selected project prefix",async()=>{
 const prior=globalThis.fetch,calls=[];const work=Object.assign(new WorkApi(),{apiBase:"/api/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}).forProject(`project.${"b".repeat(32)}`);globalThis.fetch=async(url,init)=>{calls.push({url,init});return new Response(JSON.stringify(selected()),{headers:{"Content-Type":"application/json"}});};try{const api=new ProjectRegistryApi(work);await api.pickFolder("parent",new AbortController().signal);assert.equal(calls[0].url,"/api/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/projects/pick-folder");assert.deepEqual(JSON.parse(calls[0].init.body),{purpose:"parent"});assert.equal(calls[0].init.method,"POST");await assert.rejects(api.pickFolder("unsupported",new AbortController().signal));assert.equal(calls.length,1);}finally{globalThis.fetch=prior;}
});
test("both languages render native labelled folder controls and explicit initialization consent",async()=>{
 const state=existing();const props={creation:state,writable:true,onInspect:async()=>inspection(),onCreate:async()=>{},onClose(){}};
 for(const locale of ["en","ru"]){const markup=renderToStaticMarkup(createElement(ProjectCreateDialog,{...props,locale}));assert.ok(markup.includes(projectCreationText(locale).chooseFolder));assert.match(markup,/aria-labelledby=/);assert.match(markup,/type="radio"/);assert.match(markup,/autoComplete="off"/);}
 await state.inspect(async()=>inspection());for(const locale of ["en","ru"]){const markup=renderToStaticMarkup(createElement(ProjectCreateDialog,{...props,locale}));assert.ok(markup.includes(projectCreationText(locale).initialize));assert.match(markup,/type="checkbox"/);assert.match(markup,/<button type="button" class="button button-primary" disabled="">/);}
 const source=readFileSync(resolve(root,"src/projectCreation.ts"),"utf8");assert.doesNotMatch(source,/localStorage|sessionStorage/);
});


test("manual own-home paths reach inspection while native picker results remain absolute",async()=>{
 for(const location of ["~","~/","~/Projects/My App"]){assert.equal(validateProjectDraft({mode:"existing",name:"App",location}),null);assert.equal(validateProjectDraft({mode:"new",name:"App",location}),null);}
 for(const location of ["~other/repo","~//etc","~/../elsewhere","~/a/../../b","~\\repo","~/a\nb"]){assert.equal(validateProjectDraft({mode:"existing",name:"App",location}),"path");}
 assert.throws(()=>parseFolderSelection(selected({path:"~/App"})));
 const state=new ProjectCreation();state.setMode("existing");state.setLocation("~/Projects/My App");let input;
 await state.inspect(async(value)=>{input=value;return inspection({name:"My App"});});assert.equal(input.path,"~/Projects/My App");assert.equal(state.snapshot().problem,null);
 const fresh=new ProjectCreation();fresh.setName("App");fresh.setLocation("~");await fresh.inspect(async(value)=>{assert.equal(value.path,"~/App");return inspection({mode:"new",name:"App"});});assert.equal(fresh.snapshot().problem,null);
});
