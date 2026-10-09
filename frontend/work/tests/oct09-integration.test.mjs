import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { copyFileSync, mkdtempSync, readFileSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { setImmediate as tick } from "node:timers/promises";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const compiled = mkdtempSync(resolve(tmpdir(), "agent-commons-oct09-"));
execFileSync(resolve(root, "node_modules/.bin/tsc"), ["--ignoreConfig", "--target", "ES2022", "--module", "ESNext", "--moduleResolution", "Bundler", "--lib", "ES2022,DOM", "--jsx", "react-jsx", "--resolveJsonModule", "--allowSyntheticDefaultImports", "--outDir", compiled, ...["components/TaskGraph.tsx", "taskHierarchy.ts", "components/TaskInspector.tsx", "components/ProjectEnvironment.tsx", "components/OutputsPanel.tsx", "conversationTextDraft.ts", "conversationState.ts", "workspaceLayout.ts", "launchBudget.ts", "taskResults.ts", "taskPresentation.ts"].map((name) => resolve(root, "src", name))], { cwd: root });
symlinkSync(resolve(root, "node_modules"), resolve(compiled, "node_modules"), "dir");
copyFileSync(resolve(root, "src/i18n.json"), resolve(compiled, "i18n.json"));
const load = (name) => import(pathToFileURL(resolve(compiled, name + ".js")).href);
const { ApiProblem, WorkApi } = await load("api");
const { SavedTextDraftController, ConversationTextDraftApi } = await load("conversationTextDraft");
const { ConversationSession } = await load("conversationState");
const { WorkspaceLayoutStore, WorkspacePreferenceSync, workspacePreferencesBody } = await load("workspaceLayout");
const { TaskResultsReader, parseTaskResultsPage } = await load("taskResults");
const { parseProjectEnvironment, qualificationCommand } = await load("projectEnvironment");
const { ProviderQualificationAction } = await load("components/ProjectEnvironment");
const { reframeViewport, fitViewport, zoomViewport, zoomViewportBy, viewportZoom, minimumMapZoom, wheelPixels } = await load("mapViewport");
const { parseWorkRoute, workRouteHref } = await load("appRouteState");
const { filterTrackerTasks } = await load("taskPresentation");
const { TaskGraph } = await load("components/TaskGraph");
const { buildTaskGraph } = await load("taskGraph");
const { buildHierarchyGraph } = await load("taskHierarchy");
const { newestTaskRuns } = await load("components/TaskInspector");
const { parseOutputList, parseTextResultContent, canViewImage, filterOutputs } = await load("outputsTypes");
const { OutputsApi } = await load("outputsApi");
const { TextReportCard } = await load("components/OutputsPanel");
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b; }); return {promise, resolve, reject}; };
const id = (kind, n=0) => `${kind}.${String(n).padStart(26,"0")}`;
const scope = {kind:"task",id:id("task")}, revision=id("evt");
const signal = () => new AbortController().signal;
const preference = (revision, patch={}) => ({schema:"agent_commons.workspace-preferences.v1", revision, sidebar_width:208, chat_width:320, sidebar_collapsed:false, chat_collapsed:false, panels_swapped:false, ...patch});

test("preferences wait for authenticated connect and preserve gestures made before a late read", async () => {
  const store=new WorkspaceLayoutStore(), sync=new WorkspacePreferenceSync(store), read=deferred(), writes=[];
  await sync.request(store.apply({chatCollapsed:true}));
  assert.equal(store.snapshot().revision,null);
  const opening=sync.connect({readWorkspacePreferences:()=>read.promise,writeWorkspacePreferences:async(body)=>{writes.push(body);return preference(900,{...body});}});
  await sync.request(store.apply({sidebarWidth:248}));
  assert.equal(writes.length,0);
  read.resolve(preference(700));await opening;
  assert.equal(store.snapshot().layout.chatCollapsed,true);assert.equal(store.snapshot().layout.sidebarWidth,248);
  assert.equal(writes[0].expected_revision,700);assert.equal(store.snapshot().revision,900);
});
test("preferences serialize rapid gestures and uncertain retry retains the original CAS body", async()=>{
  const store=new WorkspaceLayoutStore(),sync=new WorkspacePreferenceSync(store),first=deferred(),calls=[];
  await sync.connect({readWorkspacePreferences:async()=>preference(10),writeWorkspacePreferences:async(body)=>{calls.push(structuredClone(body));if(calls.length===1)return first.promise;return preference(calls.length===2?100:500,body);}});
  const original=sync.request(store.apply({sidebarCollapsed:true}));
  await tick();void sync.request(store.apply({chatWidth:400}));
  assert.equal(calls.length,1);first.reject(new TypeError("lost response"));await original;
  assert.equal(store.snapshot().save.kind,"unavailable");
  await sync.retry();assert.deepEqual(calls[0],calls[1]);assert.equal(calls[2].expected_revision,100);assert.equal(calls[2].chat_width,400);assert.equal(store.snapshot().save.kind,"saved");
  const other=new WorkspaceLayoutStore(), otherSync=new WorkspacePreferenceSync(other);await otherSync.connect({readWorkspacePreferences:async()=>preference(20),writeWorkspacePreferences:async()=>assert.fail("no write")});assert.equal(other.snapshot().layout.chatWidth,320);
});
test("an external preference conflict keeps local layout until explicit reload",async()=>{
 const store=new WorkspaceLayoutStore(),sync=new WorkspacePreferenceSync(store);let reads=0,writes=0;
 await sync.connect({readWorkspacePreferences:async()=>preference(++reads===1?10:99,{chat_width:480}),writeWorkspacePreferences:async()=>{writes++;throw new ApiProblem(409,{code:"workspace_preferences_conflict"});}});
 await sync.request(store.apply({chatWidth:360}));assert.equal(store.snapshot().save.kind,"conflict");assert.equal(store.snapshot().layout.chatWidth,360);await sync.acceptConflict();assert.equal(store.snapshot().layout.chatWidth,480);assert.equal(writes,1);
});
test("draft retains a positive empty revision and accepts arbitrary monotonic revisions", async()=>{
 const calls=[];const api=new ConversationTextDraftApi({requestConversation:async(path,options)=>{
   if(options.method==="GET")return {schema:"agent_commons.conversation-text-draft.v1",scope,revision:173001,text:"",reply_to_message_id:null,updated_at:null};
   calls.push(options.body);return {schema:"agent_commons.conversation-text-draft.v1",scope,revision:190099,text:options.body.text,reply_to_message_id:null,updated_at:null};
 }});const owner=new SavedTextDraftController(api,scope);await owner.read();assert.equal(await owner.write("Saved",null),true);assert.equal(calls[0].expected_revision,173001);assert.equal(owner.snapshot().status.draft.revision,190099);
});
test("draft exact retries survive new RAM text, 413 preserves the known revision, and late scope A cannot replace B",async()=>{
 const calls=[];let mode="lost";const api={read:async()=>({scope,revision:500,text:"Old",replyToMessageId:null,updatedAt:null}),write:async(_scope,revision,text,reply)=>{calls.push({revision,text,reply});if(mode==="lost")throw new TypeError("lost");if(mode==="large")throw new ApiProblem(413,null);return {scope,revision:900,text,replyToMessageId:reply,updatedAt:null};}};
 const a=new SavedTextDraftController(api,scope);await a.read();await a.write("Original",null);assert.equal(a.snapshot().uncertain,true);mode="ok";await a.write("New RAM text",null);assert.deepEqual(calls[0],calls[1]);assert.equal(a.snapshot().status.draft.text,"Original");
 mode="large";await a.write("Keep in RAM",null);assert.equal(a.snapshot().uncertain,false);assert.equal(a.snapshot().status.kind,"unavailable");mode="ok";await a.write("Smaller",null);assert.equal(calls.at(-1).revision,900);
 const late=deferred(),b=new SavedTextDraftController({...api,read:()=>late.promise},scope),c=new SavedTextDraftController(api,{kind:"project"});const pending=b.read();await c.read();late.resolve({scope,revision:777,text:"A only",replyToMessageId:null,updatedAt:null});await pending;assert.equal(b.snapshot().status.draft.text,"A only");assert.equal(c.snapshot().status.draft.text,"Old");
});
test("release/reacquire during an in-flight poll creates exactly one timer and no thread",async()=>{
 const originalSet=globalThis.setTimeout,originalClear=globalThis.clearTimeout,timers=new Map();let next=0,ensures=0;const read=deferred();
 globalThis.setTimeout=(callback)=>{timers.set(++next,callback);return next;};globalThis.clearTimeout=(id)=>timers.delete(id);
 try {const session=new ConversationSession({list:()=>read.promise,ensure:async()=>{ensures++;throw Error();}},scope);const release=session.startPolling();release();const second=session.startPolling();read.resolve([]);await tick();assert.equal(timers.size,1);assert.equal(ensures,0);second();assert.equal(timers.size,0);}
 finally {globalThis.setTimeout=originalSet;globalThis.clearTimeout=originalClear;}
});
test("a first send concurrent with read-only discovery still ensures one addressed thread",async()=>{
 const read=deferred();let reads=0,ensures=0,sends=0;const conversation={scope,thread_id:id("thread"),revision,state:"open"};
 const session=new ConversationSession({list:()=>++reads===1?read.promise:Promise.resolve([]),ensure:async()=>{ensures++;return conversation;},messages:async()=>({conversation,messages:[]}),send:async()=>{sends++;return {revision:id("evt",2)};}},scope);
 const viewing=session.connect(false);session.setText("First message");const sending=session.sendMessage();read.resolve([]);await viewing;await sending;assert.equal(ensures,1);assert.equal(sends,1);
});
const evidence=(n)=>({ref:{kind:"artifact",id:id("artifact",n)},revision,kind:"artifact",title:`Evidence ${n}`,summary:null,checks:[],media_type:null,content_revision:null,retained:false,stale:true});
const page=(offset=0,overrides={})=>({schema:"agent_commons.task-results.v1",task_id:scope.id,task_revision:revision,title:"Full task",description:"Long goal ".repeat(1200),acceptance_criteria:["Complete criterion".repeat(500)],summary:"Full summary",evidence:Array.from({length:Math.min(32,52-offset)},(_,i)=>evidence(offset+i)),total:52,offset,limit:32,next_offset:offset===0?32:null,...overrides});
test("52 evidence refs paginate exactly once, preserving full prose and the immutable task revision",async()=>{
 const calls=[];const reader=new TaskResultsReader({readTaskResults:async(task,rev,offset,limit)=>{calls.push({task,rev,offset,limit});return page(offset);}},scope.id,revision);await reader.loadMore();assert.equal(reader.snapshot().page.evidence.length,32);await reader.loadMore();await reader.loadMore();assert.equal(reader.snapshot().page.evidence.length,52);assert.equal(reader.snapshot().page.description.length,12000);assert.equal(reader.snapshot().page.acceptanceCriteria[0].length,9000);assert.deepEqual(calls.map(x=>x.offset),[0,32]);
 assert.throws(()=>parseTaskResultsPage(page(0,{task_revision:id("evt",1)}),scope.id,revision,0,32));assert.throws(()=>parseTaskResultsPage(page(0,{offset:1}),scope.id,revision,0,32));
});
test("task revision conflict is explicit and a StrictMode dispose/restart cannot merge a late page",async()=>{
 let calls=0;const reader=new TaskResultsReader({readTaskResults:async()=>{calls++;throw new ApiProblem(409,{code:"results_task_revision_changed"});}},scope.id,revision);await reader.loadMore();await reader.loadMore();assert.equal(reader.snapshot().error,"changed");assert.equal(calls,1);
 const late=deferred();let reads=0;const live=new TaskResultsReader({readTaskResults:()=>++reads===1?late.promise:Promise.resolve(page(0))},scope.id,revision);const old=live.loadMore();live.dispose();await live.loadMore();late.resolve(page(0,{title:"stale"}));await old;assert.equal(live.snapshot().page.title,"Full task");assert.equal(live.snapshot().loading,false);
});
test("resize maintains scale and centre; zoom stays under the pointer at a changed aspect ratio",()=>{
 const content={width:2000,height:1400},frame={width:800,height:600},newFrame={width:420,height:700};const old=fitViewport(content,frame),next=reframeViewport(old,newFrame);
 assert.equal(next.width/next.height,newFrame.width/newFrame.height);assert.equal(viewportZoom(old,frame),viewportZoom(next,newFrame));assert.equal(old.x+old.width/2,next.x+next.width/2);assert.equal(old.y+old.height/2,next.y+next.height/2);
 const anchor={x:.3,y:.7},zoomed=zoomViewport(next,newFrame,viewportZoom(next,newFrame)*1.2,anchor,content);assert.ok(Math.abs(next.x+next.width*anchor.x-zoomed.x-zoomed.width*anchor.x)<1e-8);assert.ok(Math.abs(next.y+next.height*anchor.y-zoomed.y-zoomed.height*anchor.y)<1e-8);
 assert.deepEqual(wheelPixels({deltaX:2,deltaY:3,deltaMode:1},newFrame),{x:32,y:48});assert.deepEqual(wheelPixels({deltaX:1,deltaY:1,deltaMode:2},newFrame),{x:420,y:700});
});
test("mature 315-task home shows all 70 working tasks, preserves history and legacy detail links",()=>{
 const counts={accepted:176,cancelled:69,completed:54,ready:8,active:6,review:2};let index=0;const tasks=Object.entries(counts).flatMap(([taskState,count])=>Array.from({length:count},()=>({taskId:id("task",index++),title:`${taskState} ${index}`,taskState,readiness:"ready",dependencyTaskIds:[],blockingDependencyIds:[],freshness:"fresh",evidenceState:"complete",awaitsHuman:false})));
 const snapshot={tasks,attention:[],runs:[]};assert.equal(parseWorkRoute("").filter,"working");const visible=filterTrackerTasks(snapshot,"working","");assert.equal(visible.length,70);assert.equal(visible.filter(t=>t.taskState==="completed").length,54);assert.equal(filterTrackerTasks(snapshot,"all","").length,315);
 const render=(selectedTaskId)=>renderToStaticMarkup(createElement(TaskGraph,{tasks:visible,edges:[],selectedTaskId,onSelectTask(){},locale:"en"}));const before=render(null),after=render(visible[0].taskId);assert.equal((before.match(/class="task-graph-node"/g)||[]).length,70);assert.equal((after.match(/class="task-graph-node"/g)||[]).length,70);
 const route=parseWorkRoute(`?task=${tasks[0].taskId}`);assert.equal(route.panel,"detail");assert.equal(parseWorkRoute(new URL(workRouteHref({...route,panel:null}),"http://test").search).panel,null);
});
test("newest run order uses start time rather than an old run's later repair; ties are deterministic",()=>{
 const run=(n,startedAt,updatedAt)=>({taskId:scope.id,delegationId:id("delegation",n),startedAt,updatedAt,finishedAt:null});
 const old=run(1,"2026-10-08T23:50:00Z","2026-10-10T01:00:00Z"),fresh=run(2,"2026-10-09T00:10:00Z",null),tie=run(3,fresh.startedAt,null);assert.deepEqual(newestTaskRuns([old,fresh,tie],scope.id).map(x=>x.delegationId),[tie.delegationId,fresh.delegationId,old.delegationId]);
});
test("environment is advisory and qualification is fixed-profile one-attempt CLI only",()=>{
 const dto={schema:"agent_commons.project-environment.v1",state:"observed",build_verified:false,manifests:[{name:"package.json",state:"present"}],lockfiles:[],dependencies:[{ecosystem:"node",state:"present"}],tools:[{name:"node",availability:"available",required_version:"24",observed_version:"24.4.0",version_matches:true}],limitations:["presence_only","root_manifests_only"]};
 assert.equal(parseProjectEnvironment(dto).buildVerified,false);assert.throws(()=>parseProjectEnvironment({...dto,build_verified:true}));
 const availability={profileId:"claude-builder",refusal:{remediation:["run_provider_canary"]}};assert.equal(qualificationCommand(availability),'uv run agent-commons --state-root "/absolute/path/to/panel-state-root" broker canary --profile claude-builder --profile-config "/absolute/path/to/panel-runtime.yaml" --confirm-provider-run --wall-time-seconds 300');assert.equal(qualificationCommand({...availability,profileId:"claude-builder; bad"}),null);assert.equal(qualificationCommand({...availability,refusal:{remediation:["authenticate_provider"]}}),null);
 for (const locale of ["en", "ru"]) { const markup=renderToStaticMarkup(createElement(ProviderQualificationAction,{availability,locale}));assert.match(markup,/--profile-config/);assert.match(markup,/--state-base/);assert.match(markup,/--state-root/);assert.match(markup,locale==="en"?/Replace both/:/замените оба заполнителя/);assert.doesNotMatch(markup,/<button/); }
});
test("new read routes and preference writes retain the immutable project scope",async()=>{
 const old=globalThis.fetch,calls=[];globalThis.fetch=async(url,options)=>{calls.push({url,options});return new Response("{}",{headers:{"Content-Type":"application/json"}});};
 try{const api=new WorkApi(`project.${"a".repeat(32)}`,`/api/${"b".repeat(32)}`);await api.readWorkspacePreferences(signal());await api.writeWorkspacePreferences(workspacePreferencesBody(0,new WorkspaceLayoutStore().snapshot().layout),signal());await api.readProjectEnvironment(signal());await api.readTaskResults(scope.id,revision,0,32,signal());await api.readOutputText(id("artifact"),revision,"task",scope.id,signal());assert.equal(calls.length,5);for(const call of calls)assert.ok(call.url.includes(`/projects/project.${"a".repeat(32)}/`));assert.match(calls[3].url,/task_revision=evt\./);assert.match(calls[4].url,/scope_kind=task&scope_id=task\./);}
 finally{globalThis.fetch=old;}
});

test("text_result accepts the real UTF-8 MIME, exact revision/scope/digest, and safe escaped prose",async()=>{
 const content="<script>alert('synthetic')</script>\n# Plain report", digest=`sha256:${Buffer.from(await crypto.subtle.digest("SHA-256",new TextEncoder().encode(content))).toString("hex")}`;
 const wire={kind:"text_result",output_id:`${id("artifact")}@${revision}`,series_id:`generated.${"a".repeat(64)}`,title:"Report",artifact_id:id("artifact"),artifact_revision:revision,content_revision:digest,task_id:scope.id,task_revision:revision,producer_session_id:`session.${"b".repeat(32)}`,producer_agent_id:id("agent"),producer_delegation_id:id("delegation"),delegation_revision:revision,recorded_at:"2026-10-09T00:00:00Z",media_type:"text/plain; charset=utf-8",classification:"internal",state:"ready",reason:null,latest:true,version_count:1,width:null,height:null,historical_preview_verified:false,retained:true,review_state:"awaiting",result_review_state:null,summary:"<img src=x> summary",checks:["Unit checks passed"]};
 const list={schema:"agent_commons.outputs.v1",scope,versions:"latest",items:[wire],truncated:false};const item=parseOutputList(list,scope,"latest").items[0];assert.equal(canViewImage(item),false);assert.equal(filterOutputs([item],"reports").length,1);assert.equal(filterOutputs([item],"images").length,0);
 const dto={schema:"agent_commons.text-result-content.v1",artifact_id:wire.artifact_id,artifact_revision:revision,content_revision:digest,title:wire.title,summary:wire.summary,checks:wire.checks,content,task_id:scope.id,task_revision:revision};
 let reads=0;const api=new OutputsApi({readOutputText:async()=>{reads++;return dto;},readOutputImage:()=>assert.fail("text is never image bytes"),readOutputs:()=>assert.fail("text route already binds exact scope")});assert.equal((await api.text(scope,item,signal())).content,content);assert.equal(reads,1);
 for(const patch of [{artifact_revision:id("evt",1)},{task_id:id("task",1)},{content_revision:`sha256:${"0".repeat(64)}`},{summary:"mismatch"}])assert.throws(()=>parseTextResultContent({...dto,...patch},scope,item));
 const tampered=new OutputsApi({readOutputText:async()=>({...dto,content:"altered"})});await assert.rejects(tampered.text(scope,item,signal()));
 const aborted=new AbortController();aborted.abort();await assert.rejects(api.text(scope,item,aborted.signal));
 const markup=renderToStaticMarkup(createElement(TextReportCard,{api,scope,item,locale:"en"}));assert.match(markup,/&lt;img src=x&gt;/);assert.doesNotMatch(markup,/<img/);
 assert.equal(item.reviewState,"awaiting");assert.equal(item.resultReviewState,null);
 const historical=parseOutputList({...list,versions:"all",items:[{...wire,state:"stale",reason:"producer_task_revision_changed",latest:false,historical_preview_verified:true}]},scope,"all").items[0];assert.equal((await api.text(scope,historical,signal())).content,content);
});

test("typed budget exhaustion survives the real 409 envelope without raw errors or invented counts",async()=>{
 const {launchBudgetFromError,budgetExhaustedRefusal}=await load("launchBudget");const budget={schema:"agent_commons.launch-budget.v1",profile_id:"codex-builder",provider:"codex",unit:"provider_units",limit:1,used:1,remaining:0,exhausted:true};
 const old=globalThis.fetch;globalThis.fetch=async()=>new Response(JSON.stringify({error:{code:"operator_budget_exhausted",message:"operator provider_units budget is exhausted",safe_next_actions:["review_operator_budget"],budget}}),{status:409,headers:{"Content-Type":"application/json"}});
 try{const api=new WorkApi(null,`/api/${"c".repeat(32)}`);await assert.rejects(api.requestData("/work/launch",{method:"POST",body:{},signal:signal()}),(error)=>{assert.equal(budgetExhaustedRefusal(error),true);assert.equal(launchBudgetFromError(error.apiError).remaining,0);assert.deepEqual(error.apiError.safeNextActions,["review_operator_budget"]);return true;});assert.equal(launchBudgetFromError({code:"operator_budget_exhausted",budget:{...budget,remaining:10}}),null);}
 finally{globalThis.fetch=old;}
});

test("save reply arriving after Send clears only the confirmed sent draft, never different newer text",async()=>{
 for(const savedText of ["Sent text","New draft"]){const saving=deferred(),calls=[];const owner=new SavedTextDraftController({read:async()=>({scope,revision:100,text:"",replyToMessageId:null,updatedAt:null}),write:async(_scope,expected,text,reply)=>{calls.push({expected,text,reply});if(calls.length===1)return saving.promise;return {scope,revision:900,text:"",replyToMessageId:null,updatedAt:null};}},scope);await owner.read();const pending=owner.write(savedText,null);await owner.clearAfterSend({text:"Sent text",reply:null});saving.resolve({scope,revision:700,text:savedText,replyToMessageId:null,updatedAt:null});await pending;
  if(savedText==="Sent text"){assert.equal(calls.length,2);assert.deepEqual(calls[1],{expected:700,text:"",reply:null});assert.equal(owner.snapshot().status.draft.text,"");}
  else {assert.equal(calls.length,1);assert.equal(owner.snapshot().status.draft.text,"New draft");}
 }
});
test("send confirmation belongs to its POST even when new text is typed during follow-up refresh",async()=>{
 const refresh=deferred();let reads=0;const conversation={scope,thread_id:id("thread"),revision,state:"open"};const session=new ConversationSession({list:async()=>[conversation],messages:()=>++reads===1?Promise.resolve({conversation,messages:[]}):refresh.promise,send:async()=>({revision:id("evt",1)})},scope);await session.connect(false);session.setText("Sent text");const sending=session.sendMessage();await tick();session.setText("New unsent text");assert.equal(session.snapshot().sendState,"idle");refresh.resolve({conversation:{...conversation,revision:id("evt",1)},messages:[]});assert.equal(await sending,true);assert.equal(session.snapshot().text,"New unsent text");
});
test("map chrome groups search/filter/navigation and keeps branch controls and help in closed disclosures",()=>{
 const source=(path)=>readFileSync(resolve(root,"src",path),"utf8");
 const canvas=source("components/MapCanvas.tsx"),views=source("components/TaskViews.tsx"),tracker=source("components/TrackerSection.tsx"),main=source("main.tsx");
 assert.match(canvas,/<details className="map-options"><summary>/);assert.match(canvas,/<details className="map-options map-help"><summary>/);assert.doesNotMatch(canvas,/<p className="small-copy map-canvas-gestures"/);
 assert.match(views,/className="task-view-navigation">\{filterControl\}/);assert.doesNotMatch(views,/className="button-row map-kind-row"/);assert.match(tracker,/tracker-compact-notice"><summary>/);
 assert.doesNotMatch(main,/className="work-panel-strip"/);assert.match(main,/<Modal title=\{text\("shell_new_task"\)\}/);assert.match(source("styles.css"),/\.work-shell \.map-canvas-frame \{ min-height: 0; \}/);
 assert.match(source("components/useCompactPane.ts"),/useEffect\(\(\) => \{ setPane\(null\); \}, \[projectId\]\)/);
});


test("a draft read arriving after Send clears matching bytes and reply only", async () => {
  for (const reply of [null, id("message", 1)]) {
    const reading = deferred(), writes = [];
    const owner = new SavedTextDraftController({
      read: () => reading.promise,
      write: async (_scope, expected, text, target) => {
        writes.push({ expected, text, target });
        return { scope, revision: 800, text, replyToMessageId: target, updatedAt: null };
      }
    }, scope);
    const pending = owner.read();
    await owner.clearAfterSend({ text: "Sent text", reply: null });
    reading.resolve({ scope, revision: 600, text: "Sent text", replyToMessageId: reply, updatedAt: null });
    await pending;
    if (reply === null) {
      assert.deepEqual(writes, [{ expected: 600, text: "", target: null }]);
      assert.equal(owner.snapshot().status.draft.text, "");
    } else {
      assert.deepEqual(writes, []);
      assert.equal(owner.snapshot().status.draft.replyToMessageId, reply);
    }
  }
});


test("two confirmed sends cannot overwrite the draft cleanup waiting on Save A", async () => {
  const saving = deferred(), writes = [];
  const owner = new SavedTextDraftController({
    read: async () => ({ scope, revision: 100, text: "", replyToMessageId: null, updatedAt: null }),
    write: async (_scope, expected, text, reply) => {
      writes.push({ expected, text, reply });
      return writes.length === 1 ? saving.promise : { scope, revision: 700 + writes.length * 100, text, replyToMessageId: reply, updatedAt: null };
    }
  }, scope);
  await owner.read();
  const pending = owner.write("Sent A", null);
  await owner.clearAfterSend({ text: "Sent A", reply: null });
  await owner.clearAfterSend({ text: "Sent B", reply: null });
  saving.resolve({ scope, revision: 700, text: "Sent A", replyToMessageId: null, updatedAt: null });
  await pending;
  assert.deepEqual(writes, [{ expected: 100, text: "Sent A", reply: null }, { expected: 700, text: "", reply: null }]);
  assert.equal(owner.snapshot().status.draft.text, "");
  // A later deliberate save of identical text is a new unsent intent.
  await owner.write("Sent B", null);
  assert.equal(writes.length, 3);
  assert.equal(owner.snapshot().status.draft.text, "Sent B");
});

test("an unresolved draft read retains all send confirmations and preserves a different draft", async () => {
  for (const [text, reply] of [["Sent A", null], ["Sent B", null], ["New unsent draft", null], ["Sent A", id("message", 2)]]) {
    const reading = deferred(), writes = [];
    const owner = new SavedTextDraftController({
      read: () => reading.promise,
      write: async (_scope, expected, text, reply) => {
        writes.push({ expected, text, reply });
        return { scope, revision: 900, text, replyToMessageId: reply, updatedAt: null };
      }
    }, scope);
    const pending = owner.read();
    await owner.clearAfterSend({ text: "Sent A", reply: null });
    await owner.clearAfterSend({ text: "Sent B", reply: null });
    reading.resolve({ scope, revision: 700, text, replyToMessageId: reply, updatedAt: null });
    await pending;
    const shouldClear = reply === null && text !== "New unsent draft";
    assert.equal(writes.length, shouldClear ? 1 : 0);
    assert.equal(owner.snapshot().status.draft.text, shouldClear ? "" : text);
    if (shouldClear) assert.deepEqual(writes[0], { expected: 700, text: "", reply: null });
  }
});


test("short workspace overrides global 40px summaries while retaining 24px targets and one attachment action", () => {
  const css = readFileSync(resolve(root, "src/styles.css"), "utf8");
  const conversation = readFileSync(resolve(root, "src/conversation.css"), "utf8");
  for (const selector of [".work-shell .tracker-compact-notice > summary", ".work-shell .tracker-diagnostics > summary"]) {
    const declaration = css.slice(css.lastIndexOf(selector)).split("}")[0];
    assert.match(declaration, /min-height: 24px/);
    assert.match(declaration, /padding: 2px 0/);
  }
  for (const selector of [".work-shell .layout-menu > summary", ".map-options > summary"]) {
    const declaration = css.slice(css.lastIndexOf(selector)).split("}")[0];
    assert.match(declaration, /min-height: 28px/);
    assert.match(declaration, /padding: 3px 8px/);
  }
  assert.match(css, /\.work-shell \.tracker-diagnostics \{[^}]*padding-top: 0/);
  assert.match(css, /\.work-shell \.map-canvas-toolbar \{[^}]*padding: 0/);
  assert.match(conversation, /\.conversation-compose-actions > input\[type="file"\] \{ display:none; \}/);
  assert.match(conversation, /\.conversation-content-panel \.conversation-draft-menu > summary \{ min-height:24px; padding:2px 0; \}/);
});


test("Fit covers the actual 71-task topology below 20% with accurate scale and smooth anchored zoom", () => {
  // Anonymized canonical working-set topology observed 2026-10-09: 71 tasks,
  // no parents and 24 internal dependencies. Titles/IDs are deliberately synthetic.
  const pairs = [[38,0],[55,0],[40,1],[4,2],[46,2],[66,2],[22,4],[15,7],[57,12],[12,15],[9,23],[69,24],[4,30],[39,31],[18,32],[24,37],[24,39],[57,40],[22,46],[4,46],[39,57],[16,64],[22,66],[4,69]];
  const tasks = Array.from({length:71}, (_, n) => ({taskId:id("task",n),title:`Task ${n}`,taskState:"ready",parentTaskId:null,dependencyTaskIds:pairs.filter(([,to])=>to===n).map(([from])=>id("task",from)),blockingDependencyIds:[],freshness:"fresh",readiness:"ready"}));
  const edges = pairs.map(([from,to])=>({prerequisiteTaskId:id("task",from),dependentTaskId:id("task",to),prerequisiteMissing:false}));
  const layouts = [buildHierarchyGraph(tasks,null,false),buildTaskGraph(tasks,edges,null,"all")];
  assert.deepEqual(layouts.map(layout=>[layout.width,layout.height]),[[19612,446],[14092,2056]]);
  for (const layout of layouts) for (const frame of [{width:800,height:535},{width:1010,height:248}]) {
    const fit=fitViewport(layout,frame),scale=Math.min(frame.width/layout.width,frame.height/layout.height);
    assert.ok(scale<.2);assert.equal(viewportZoom(fit,frame),scale);assert.equal(minimumMapZoom(layout,frame),scale);
    assert.ok(layout.nodes.every(node=>node.x>=fit.x-1e-8&&node.y>=fit.y-1e-8&&node.x+244<=fit.x+fit.width+1e-8&&node.y+168<=fit.y+fit.height+1e-8));
    const anchor={x:.3,y:.5},closer=zoomViewportBy(fit,frame,1.25,anchor,layout);
    assert.ok(Math.abs(viewportZoom(closer,frame)-scale*1.25)<1e-12);
    assert.ok(Math.abs((fit.x+anchor.x*fit.width)-(closer.x+anchor.x*closer.width))<1e-8);
    assert.ok(Math.abs((fit.y+anchor.y*fit.height)-(closer.y+anchor.y*closer.height))<1e-8);
    const larger={width:1600,height:900},resized=reframeViewport(fit,larger);
    assert.ok(Math.abs(viewportZoom(resized,larger)-scale)<1e-12);
    assert.ok(Math.abs(viewportZoom(zoomViewportBy(resized,larger,.8,anchor,layout),larger)-scale)<1e-12,"zoom-out below a resized fit floor does not zoom in");
    assert.ok(Math.abs(viewportZoom(zoomViewportBy(resized,larger,1.25,anchor,layout),larger)-scale*1.25)<1e-12,"zoom-in after resize remains one proportional step");
  }
});

test("Fit and zoom-out cover the maximum 128-node maps while the 129-node guard stays intact", () => {
  const tasks=Array.from({length:129},(_,n)=>({taskId:id("task",n),title:`Task ${n}`,taskState:"ready",parentTaskId:null,dependencyTaskIds:[],blockingDependencyIds:[],freshness:"fresh",readiness:"ready"}));
  const frame={width:320,height:240};
  for (const make of [(values)=>buildTaskGraph(values,[],null,"all"),(values)=>buildHierarchyGraph(values,null,false)]) {
    const layout=make(tasks.slice(0,128));assert.equal(layout.tooLarge,false);
    const fit=fitViewport(layout,frame);assert.ok(viewportZoom(fit,frame)<.01);
    assert.ok(fit.width>=layout.width&&fit.height>=layout.height);
    const out=zoomViewportBy(fit,frame,.1,{x:.5,y:.5},layout);assert.equal(viewportZoom(out,frame),viewportZoom(fit,frame));
    assert.equal(make(tasks).tooLarge,true);
  }
});
