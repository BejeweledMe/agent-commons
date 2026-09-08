import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, symlinkSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { setImmediate as tick } from "node:timers/promises";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const compiled = mkdtempSync(resolve(tmpdir(), "agent-commons-conversations-"));
execFileSync(resolve(root, "node_modules/.bin/tsc"), ["--ignoreConfig", "--target", "ES2022", "--module", "ESNext", "--moduleResolution", "Bundler", "--lib", "ES2022,DOM,DOM.Iterable", "--jsx", "react-jsx", "--outDir", compiled, resolve(root, "src/components/ConversationPanel.tsx"), resolve(root, "src/api.ts")], { cwd: root });
symlinkSync(resolve(root, "node_modules"), resolve(compiled, "node_modules"), "dir");
const load = (file) => import(pathToFileURL(resolve(compiled, file)).href);
const { ConversationApi, parseAttachment, parseConversation, parseMessage, parseDraft, parseDelivery, validateFiles } = await load("conversationApi.js");
const { ConversationSession, ConversationSessions, ConversationObjectUrl } = await load("conversationState.js");
const { conversationPath, attachmentPath, boundedAttachmentBlob } = await load("conversationTransport.js");
const { WorkApi, ApiProblem } = await load("api.js");
const { ConversationButton, ConversationWorkspace } = await load("components/ConversationPanel.js");
const { conversationText } = await load("conversationStrings.js");
const id = (kind, n="0") => `${kind}.${n.repeat(26)}`;
const opaque = (kind,n="a") => `${kind}.${n.repeat(32)}`;
const scope = {kind:"task",id:id("task")};
const conversation = (extra={}) => ({thread_id:id("thread"),revision:id("evt"),scope,subject:"Task discussion",state:"open",audience:"task",message_count:0,...extra});
const attachment = (extra={}) => ({attachment_id:opaque("attachment"),display_name:"note.txt",media_type:"text/plain",category:"file",size_bytes:3,digest:`sha256:${"a".repeat(64)}`,...extra});
const draft = (extra={}) => ({draft_id:opaque("draft"),state:"ready",attachments:[],expires_at:2000000000,message_id:null,...extra});
const message = (extra={}) => ({message_id:id("message"),body:"Hello",recorded_at:"2026-09-08T00:00:00Z",author:{kind:"operator",agent_id:null,name:null},attachments:[],reply_to_message_id:null,delivery:{state:"recorded",read_confirmed:false,recipients:[]},...extra});
const result = {message_id:id("message"),revision:id("evt","1"),state:"recorded"};
const signal = () => new AbortController().signal;
const deferred = () => { let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject}; };
function fixture(overrides={}) { const calls={send:[],upload:[],draft:0,remove:[]}; const api={
 list:async()=>[conversation()],ensure:async()=>conversation(),messages:async()=>({conversation:conversation(),messages:[]}),
 draft:async()=>{calls.draft++;return draft();},upload:async(...args)=>{calls.upload.push(args);return attachment({display_name:args[2].name,size_bytes:args[2].size});},remove:async(...args)=>{calls.remove.push(args);return draft();},send:async(...args)=>{calls.send.push(args);return result;},...overrides};
 return {api,calls,session:new ConversationSession(api,scope)}; }

test("strict conversations and attachments bind exact scope and bounded typed metadata",()=>{
 assert.equal(parseConversation(conversation(),scope).thread_id,id("thread"));assert.equal(parseAttachment(attachment()).size_bytes,3);
 for(const patch of [{thread_id:"thread/else"},{revision:id("task")},{scope:{kind:"agent",id:id("agent")}},{state:"hidden"},{audience:"all_project_agents"},{message_count:NaN}])assert.throws(()=>parseConversation(conversation(patch),scope));
 for(const patch of [{attachment_id:"attachment.../secret"},{display_name:"../secret"},{display_name:"name\u202efile"},{size_bytes:0},{size_bytes:15_000_001},{size_bytes:1.1},{digest:"wrong"},{category:"image",media_type:"image/svg+xml"},{category:"file",media_type:"image/png"}])assert.throws(()=>parseAttachment(attachment(patch)));
 assert.deepEqual(parseDraft(draft()).attachments,[]);assert.throws(()=>parseDraft(draft({state:"bound"})));assert.throws(()=>parseDraft(draft({attachments:[attachment(),attachment()]})));
});
test("read acknowledgement is independent of fetch and reply",()=>{
 for(const state of ["recorded","queued","fetched","answered"])assert.equal(parseDelivery({state,read_confirmed:false,recipients:[]}).read_confirmed,false);
 assert.throws(()=>parseDelivery({state:"fetched",read_confirmed:true,recipients:[]}));assert.equal(parseDelivery({state:"acknowledged",read_confirmed:true,recipients:[]}).read_confirmed,true);
 const recipient={agent_id:id("agent"),state:"acknowledged",fetched_at:"2026-09-08T00:00:00Z",acknowledged_at:"2026-09-08T00:01:00Z"};
 assert.equal(parseDelivery({state:"answered",read_confirmed:true,recipients:[recipient]}).state,"answered");
 assert.equal(parseMessage(message()).body,"Hello");assert.throws(()=>parseMessage(message({author:{kind:"agent",name:null,agent_id:null}})));assert.throws(()=>parseMessage(message({body:"",attachments:[]})));assert.throws(()=>parseMessage(message({body:"Ж".repeat(32001)})));
});
test("file limits are per category and size and names are UTF-8 bounded",()=>{
 const files=Array.from({length:10},(_,i)=>new File(["abc"],`file${i}.txt`,{type:"text/plain"}));const images=Array.from({length:10},(_,i)=>new File(["abc"],`img${i}.png`,{type:"image/png"}));
 assert.equal(validateFiles([], [...files,...images]),null);assert.equal(validateFiles([], [...files,...images,new File(["x"],"extra.txt")]),"count");
 assert.equal(validateFiles([], [new File([],"empty")]),"size");assert.equal(validateFiles([], [new File([new Uint8Array(15_000_001)],"large")]),"size");assert.equal(validateFiles([], [new File(["x"],"evil.svg",{type:"image/svg+xml"})]),"format");assert.equal(validateFiles([], [new File(["x"],"Ж".repeat(91))]),"filename");
});
test("messages reject repeated cursors and mismatched conversations",async()=>{
 let calls=0;const api=new ConversationApi({requestConversation:async()=>{calls++;return {schema:"agent_commons.conversation-messages.v1",conversation:conversation(),messages:[message()],next_cursor:id("message")};}});
 await assert.rejects(api.messages(id("thread"),signal()));assert.equal(calls,1);
 const wrong=new ConversationApi({requestConversation:async()=>({schema:"agent_commons.conversation-messages.v1",conversation:conversation({thread_id:id("thread","1")}),messages:[],next_cursor:null})});await assert.rejects(wrong.messages(id("thread"),signal()));
});
test("list parsing refuses another scope and ensure preserves its exact scope",async()=>{
 const api=new ConversationApi({requestConversation:async()=>({schema:"agent_commons.conversations.v1",scope:{kind:"project"},conversations:[]})});await assert.rejects(api.list(scope,signal()));
});
test("uncertain send freezes text revision draft and identity across close and refresh",async()=>{
 const intents=[];let fail=true;const f=fixture({send:async(_thread,intent)=>{intents.push(intent);if(fail)throw new ApiProblem(409,{code:"conversation_unavailable"});return result;}});
 await f.session.connect(true);f.session.setText("Original text");await f.session.send();const first=f.session.snapshot().intent;
 assert.equal(f.session.snapshot().sendState,"uncertain");f.session.setText("must not replace");f.session.setReply(id("message"));await f.session.refresh();assert.equal(f.session.snapshot().text,"Original text");assert.equal(f.session.snapshot().reply,null);
 fail=false;await f.session.send();assert.equal(intents[0],intents[1]);assert.equal(intents[1],first);assert.equal(f.session.snapshot().text,"");assert.equal(f.session.snapshot().intent,null);
});
test("confirmed send clears its intent before a refresh fails; refresh never repeats POST",async()=>{
 let reads=0;const f=fixture({messages:async()=>{if(reads++>0)throw Error("offline");return {conversation:conversation(),messages:[]};}});await f.session.connect(true);f.session.setText("Hello");await f.session.send();
 assert.equal(f.session.snapshot().sendState,"recorded");assert.equal(f.session.snapshot().text,"");assert.equal(f.session.snapshot().intent,null);assert.equal(f.session.snapshot().readError,true);await f.session.refresh();await f.session.send();assert.equal(f.calls.send.length,1);
});
test("late poll cannot replace authoritative send revision",async()=>{
 const late=deferred();let reads=0;const f=fixture({messages:async()=>{reads++;return reads===2?late.promise:{conversation:conversation({revision:reads>2?id("evt","1"):id("evt")}),messages:[]};}});await f.session.connect(true);const poll=f.session.refresh();f.session.setText("Hi");const sending=f.session.send();await tick();
 assert.equal(f.session.snapshot().intent,null);assert.equal(f.session.snapshot().text,"");assert.equal(f.session.snapshot().conversation.revision,id("evt","1"));late.resolve({conversation:conversation(),messages:[]});await poll;await sending;assert.equal(f.session.snapshot().conversation.revision,id("evt","1"));
});
test("only typed precommit refusals unfreeze a send and next send uses fresh identity",async()=>{
 for(const error of [new ApiProblem(409,{code:"conversation_revision_conflict"}),new ApiProblem(422,{code:"conversation_invalid_message"})]){
  let count=0;const intents=[];const f=fixture({send:async(_thread,intent)=>{intents.push(intent);if(count++===0)throw error;return result;},messages:async()=>({conversation:conversation({revision:id("evt","2")}),messages:[]})});await f.session.connect(true);f.session.setText("Hello");await f.session.send();assert.equal(f.session.snapshot().sendState,"refused");assert.equal(f.session.snapshot().intent,null);await f.session.send();assert.notEqual(intents[0].idempotency_key,intents[1].idempotency_key);assert.equal(intents[1].expected_revision,id("evt","2"));
 }
 for(const status of [409,422,500]){const f=fixture({send:async()=>{throw new ApiProblem(status,null);}});await f.session.connect(true);f.session.setText("Keep");await f.session.send();assert.equal(f.session.snapshot().sendState,"uncertain");}
});
test("RAM sessions keep drafts for project and scope separately",async()=>{
 const bank=new ConversationSessions(),a=fixture(),b=fixture();const first=bank.get("A",scope,a.api),second=bank.get("B",scope,b.api),agent=bank.get("A",{kind:"agent",id:id("agent")},a.api);first.setText("A draft");second.setText("B draft");agent.setText("Agent draft");
 assert.equal(bank.get("A",scope,a.api),first);assert.equal(first.snapshot().text,"A draft");assert.equal(second.snapshot().text,"B draft");assert.equal(agent.snapshot().text,"Agent draft");
});
test("uncertain upload retries same File and operation key, cannot disappear locally",async()=>{
 let count=0;const uploads=[];const f=fixture({upload:async(...args)=>{uploads.push(args);if(count++===0)throw Error("lost response");return attachment();}});await f.session.connect(true);const file=new File(["abc"],"note.txt",{type:"text/plain"});f.session.addFiles([file]);await tick();let item=f.session.snapshot().uploads[0];assert.equal(item.state,"uncertain");await f.session.remove(item.id);assert.equal(f.session.snapshot().uploads.length,1);assert.equal(f.session.canSend,false);
 f.session.retryUpload(item.id);await tick();item=f.session.snapshot().uploads[0];assert.equal(item.state,"ready");assert.equal(uploads[0][2],file);assert.equal(uploads[1][2],file);assert.equal(uploads[0][3],uploads[1][3]);assert.equal(f.calls.draft,1);
});
test("one draft reservation serves a serial upload queue and invalid files preserve valid choices",async()=>{
 const pending=deferred();let created=0;const f=fixture({draft:async()=>{created++;return pending.promise;}});await f.session.connect(true);f.session.addFiles([new File(["abc"],"note.txt"),new File([],"empty"),new File(["abc"],"other.txt")]);await tick();assert.equal(created,1);assert.equal(f.session.snapshot().uploads.length,2);assert.equal(f.session.snapshot().fileProblem,"size");pending.resolve(draft());await tick();assert.equal(f.calls.upload.length,2);assert.equal(f.session.snapshot().uploads.every(x=>x.state==="ready"),true);
});
test("uncertain removal blocks send until exact attachment removal is confirmed",async()=>{
 let count=0;const removals=[];const f=fixture({remove:async(...args)=>{removals.push(args);if(count++===0)throw Error("uncertain");return draft();}});await f.session.connect(true);f.session.addFiles([new File(["abc"],"note.txt")]);await tick();const item=f.session.snapshot().uploads[0];await f.session.remove(item.id);f.session.setText("Message");assert.equal(f.session.canSend,false);assert.equal(f.session.snapshot().uploads[0].state,"remove_uncertain");await f.session.remove(item.id);assert.equal(f.session.snapshot().uploads.length,0);assert.equal(f.session.canSend,true);assert.deepEqual(removals[0].slice(0,3),removals[1].slice(0,3));
});
test("readonly connect never creates a conversation and repeated open coalesces ensure",async()=>{
 let creates=0;const f=fixture({list:async()=>[],ensure:async()=>{creates++;return conversation();}});await f.session.connect(false);assert.equal(creates,0);assert.equal(f.session.snapshot().conversation,null);await Promise.all([f.session.connect(true),f.session.connect(true)]);assert.equal(creates,1);
});
test("path allowlist rejects normalization, arbitrary query and method mixing",()=>{
 const path=`/conversations/${id("thread")}/messages`;assert.equal(conversationPath(path,"GET"),path);const file=new File(["abc"],"note.txt");
 for(const value of ["/x/../conversations","//outside/conversations",`${path}?x=1`,`${path}?after=${id("message")}&after=${id("message")}`,"/conversations/%2e%2e/secrets","/conversations?scope_kind=agent&scope_id=task.bad"])assert.throws(()=>conversationPath(value,"GET"));assert.throws(()=>conversationPath(path,"GET",file));assert.throws(()=>attachmentPath(id("thread"),id("message"),"../secret"));
});
test("real WorkApi includes only approved archived query and retains immutable project upload scope",async()=>{
 const previous=globalThis.fetch,calls=[];const host=Object.assign(new WorkApi(),{apiBase:"/api/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"});const a=host.forProject("project.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),b=host.forProject("project.bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb");globalThis.fetch=async(url,init)=>{calls.push({url,init});return new Response("{}",{headers:{"Content-Type":"application/json"}});};
 try{await a.requestData("/library/blueprints?include_archived=true",{signal:signal()});await assert.rejects(a.requestData("/library/blueprints?include_archived=true",{method:"POST",body:{},signal:signal()}));for(const path of ["/library/blueprints?arbitrary=true","/library/blueprints?include_archived=true&extra=1","/library/skills?include_archived=true"])await assert.rejects(a.requestData(path,{signal:signal()}));
 const file=new File(["abc"],"note.txt",{type:"text/plain"}),path=`/conversations/${id("thread")}/drafts/${opaque("draft")}/attachments?filename=note.txt`;
 await a.requestConversation(path,{method:"POST",body:file,operationId:"fixed-key",signal:signal()});await b.requestConversation("/conversations?scope_kind=project",{method:"GET",signal:signal()});
 assert.equal(calls.length,3);assert.match(calls[0].url,/project\.a+\/library\/blueprints\?include_archived=true$/);assert.match(calls[1].url,/project\.a+\/conversations/);assert.match(calls[2].url,/project\.b+\/conversations/);assert.equal(calls[1].init.body,file);assert.equal(calls[1].init.headers["Idempotency-Key"],"fixed-key");assert.equal(calls[1].init.redirect,"error");assert.equal(calls[1].init.credentials,"same-origin");
 }finally{globalThis.fetch=previous;}
});
test("download verifies byte limits MIME digest and immutable bound URL",async()=>{
 const previous=globalThis.fetch,calls=[];const work=Object.assign(new WorkApi(),{apiBase:"/api/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}).forProject("project.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");const bytes=new TextEncoder().encode("abc");const digest=Buffer.from(await crypto.subtle.digest("SHA-256",bytes)).toString("hex");globalThis.fetch=async(url,init)=>{calls.push({url,init});return new Response(bytes,{headers:{"Content-Type":"application/octet-stream"}});};
 try{const api=new ConversationApi(work);const blob=await api.download(id("thread"),id("message"),attachment({digest:`sha256:${digest}`}),signal());assert.equal(await blob.text(),"abc");assert.match(calls[0].url,/project\.a+\/conversations\/thread\..*\/messages\/message\..*\/attachments\/attachment\./);assert.equal(calls[0].init.redirect,"error");await assert.rejects(api.download(id("thread"),id("message"),attachment(),signal()));}finally{globalThis.fetch=previous;}
 for(const response of [new Response("<svg/>",{headers:{"Content-Type":"image/svg+xml"}}),new Response(new Uint8Array(),{headers:{"Content-Type":"image/png"}}),new Response("a",{headers:{"Content-Type":"image/png","Content-Length":"2"}}),new Response("a",{headers:{"Content-Type":"image/png","Content-Length":"15000001"}}),new Response(new Uint8Array(15000001),{headers:{"Content-Type":"image/png"}})])await assert.rejects(boundedAttachmentBlob(response));
});
test("native Blob URL owner revokes every replaced and closed preview once",()=>{
 const created=[],revoked=[];const lease=new ConversationObjectUrl({createObjectURL(blob){const value=`blob:${created.length}`;created.push({blob,value});return value;},revokeObjectURL(url){revoked.push(url);}});const first=lease.create(new Blob(["first"])),second=lease.create(new Blob(["second"]));lease.release();lease.release();assert.deepEqual(revoked,[first,second]);
});
test("entry points use scoped accessible dialog labels and both locales",()=>{
 assert.equal(renderToStaticMarkup(createElement(ConversationButton,{scope,title:"Task"})),"");
 for(const locale of ["en","ru"]){const markup=renderToStaticMarkup(createElement(ConversationWorkspace,{api:{},projectId:"A",locale,writesEnabled:true,sessions:new ConversationSessions()},createElement(ConversationButton,{scope,title:"Task"})));assert.match(markup,/aria-haspopup="dialog"/);assert.ok(markup.includes(conversationText(locale).conversation));assert.ok(markup.includes("Task"));}
 const source=readFileSync(resolve(root,"src/components/ConversationPanel.tsx"),"utf8");assert.match(source,/aria-labelledby=\{heading\}/);assert.match(source,/showModal\(\)/);assert.doesNotMatch(source,/dangerouslySetInnerHTML|localStorage|sessionStorage/);
});

test("typed upload rejection may be removed; arbitrary 422 remains recoverable only by exact retry",async()=>{
 for(const typed of [true,false]){const f=fixture({upload:async()=>{throw new ApiProblem(422,{code:typed?"attachment_invalid":"unknown"});}});await f.session.connect(true);f.session.addFiles([new File(["abc"],"note.txt")]);await tick();const item=f.session.snapshot().uploads[0];assert.equal(item.state,typed?"rejected":"uncertain");await f.session.remove(item.id);assert.equal(f.session.snapshot().uploads.length,typed?0:1);}
});

test("precommit revision refusal drains an old poll before enabling a fresh send",async()=>{
 const pending=deferred();let reads=0;const f=fixture({messages:async()=>{reads++;return reads===2?pending.promise:{conversation:conversation({revision:reads>2?id("evt","2"):id("evt")}),messages:[]};},send:async()=>{throw new ApiProblem(409,{code:"conversation_revision_conflict"});}});await f.session.connect(true);const poll=f.session.refresh();f.session.setText("Hello");const sending=f.session.send();await tick();assert.equal(f.session.canSend,false);pending.resolve({conversation:conversation(),messages:[]});await poll;await sending;assert.equal(reads,3);assert.equal(f.session.snapshot().conversation.revision,id("evt","2"));assert.equal(f.session.canSend,true);
});

test("local text remains editable while connecting without enabling send or upload", async () => {
 const pending = deferred();
 const f = fixture({ list: async () => pending.promise });
 const connecting = f.session.connect(true);
 assert.equal(f.session.canEditText, true);
 f.session.setText("Draft before connecting");
 assert.equal(f.session.canSend, false);
 await f.session.send();
 assert.equal(f.calls.send.length, 0);
 assert.equal(f.calls.upload.length, 0);
 pending.resolve([conversation()]);
 await connecting;
 assert.equal(f.session.snapshot().text, "Draft before connecting");
 assert.equal(f.session.canSend, true);
 const source = readFileSync(resolve(root, "src/components/ConversationPanel.tsx"), "utf8");
 assert.match(source, /value=\{snapshot\.text\} disabled=\{!writable \|\| !session\.canEditText\}/);
 assert.match(source, /type="file" multiple disabled=\{!canWrite \|\| locked\}/);
});

test("uncertain sends and closed conversations never unlock their text", async () => {
 const f = fixture({ send: async () => { throw Error("uncertain"); } });
 await f.session.connect(true); f.session.setText("Frozen"); await f.session.send();
 assert.equal(f.session.canEditText, false);
 f.session.setText("Changed"); assert.equal(f.session.snapshot().text, "Frozen");
 const closed = fixture({ list: async () => [conversation({state:"resolved"})], messages: async () => ({conversation:conversation({state:"resolved"}), messages:[]}) });
 await closed.session.connect(false);
 assert.equal(closed.session.canEditText, false);
 assert.equal(closed.session.canSend, false);
});

const numberedMessage = (n) => message({message_id:`message.${n.toString(16).toUpperCase().padStart(26,"0")}`,body:`Message ${n}`});
test("a 2001-message history opens its tail and pages earlier without blocking send", async()=>{
 const all=Array.from({length:2001},(_,i)=>numberedMessage(i)), paths=[];
 const api=new ConversationApi({requestConversation:async(path)=>{
  paths.push(path);const query=new URL(path,"http://fixture").searchParams;
  const before=query.get("before"),end=before?all.findIndex(x=>x.message_id===before):all.length,start=Math.max(0,end-50),page=all.slice(start,end);
  return {schema:"agent_commons.conversation-messages.v1",conversation:conversation({message_count:all.length}),messages:page,previous_cursor:start?page[0].message_id:null,next_cursor:end<all.length?page.at(-1)?.message_id??null:null};
 }});
 const f=fixture({messages:api.messages.bind(api)});await f.session.connect(true);
 assert.equal(paths.length,1);assert.match(paths[0],/tail=true$/);assert.equal(f.session.snapshot().messages[0].body,"Message 1951");
 f.session.setText("A new message");assert.equal(f.session.canSend,true);
 for(let i=0;i<39;i++)await f.session.loadOlder();
 assert.equal(f.session.snapshot().messages.length,2000);assert.equal(f.session.snapshot().historyLimited,true);assert.equal(f.session.snapshot().messages[0].body,"Message 1");
 assert.equal(f.session.canSend,true);assert.equal(f.session.snapshot().readError,false);await f.session.loadOlder();assert.equal(paths.length,40);
 await f.session.refresh();assert.equal(f.session.snapshot().messages.length,2000);assert.equal(f.session.snapshot().historyLimited,true);
});
test("failed earlier-page read preserves the current history and send readiness",async()=>{
 const f=fixture({messages:async(_thread,_signal,before)=>{if(before)throw Error("offline");return {conversation:conversation({message_count:100}),messages:[numberedMessage(99)],previousCursor:numberedMessage(99).message_id};}});
 await f.session.connect(true);f.session.setText("Can send");await f.session.loadOlder();assert.equal(f.session.snapshot().olderError,true);assert.equal(f.session.snapshot().readError,false);assert.equal(f.session.canSend,true);assert.equal(f.session.snapshot().messages.length,1);
});
test("a stale earlier page cannot replace a newer tail after a gap",async()=>{
 const pending=deferred();let reads=0;
 const f=fixture({messages:async(_thread,_signal,before)=>before?pending.promise:{conversation:conversation({message_count:100}),messages:[numberedMessage(++reads)],previousCursor:numberedMessage(reads).message_id}});
 await f.session.connect(true);const older=f.session.loadOlder();await f.session.refresh();pending.resolve({conversation:conversation(),messages:[numberedMessage(0)],previousCursor:null});await older;
 assert.deepEqual(f.session.snapshot().messages.map(x=>x.body),["Message 2"]);
});
test("only proven expired-draft refusal reuploads original files and unlocks a fresh send",async()=>{
 let drafts=0,sends=0;const intents=[];
 const f=fixture({draft:async()=>draft({draft_id:opaque("draft",++drafts===1?"a":"b")}),send:async(_thread,intent)=>{intents.push(intent);if(++sends===1)throw new ApiProblem(409,{code:"conversation_draft_expired"});return result;}});
 await f.session.connect(true);const file=new File(["abc"],"note.txt");f.session.addFiles([file]);await tick();f.session.setText("Keep my text");f.session.setReply(id("message"));const original=f.session.snapshot().uploads[0];await f.session.send();await tick();
 assert.equal(f.session.snapshot().refusal,"expired");assert.equal(f.session.snapshot().intent,null);assert.equal(f.session.snapshot().text,"Keep my text");assert.equal(f.session.snapshot().reply,id("message"));assert.equal(sends,1);assert.equal(drafts,2);
 const uploaded=f.session.snapshot().uploads[0];assert.equal(uploaded.file,file);assert.notEqual(uploaded.key,original.key);assert.equal(uploaded.state,"ready");assert.equal(f.session.canSend,true);
 await f.session.send();assert.notEqual(intents[0].idempotency_key,intents[1].idempotency_key);assert.notEqual(intents[0].draft_id,intents[1].draft_id);
 const uncertain=fixture({send:async()=>{throw new ApiProblem(409,{code:"conversation_unavailable"});}});await uncertain.session.connect(true);uncertain.session.addFiles([file]);await tick();uncertain.session.setText("Frozen");await uncertain.session.send();const frozen=uncertain.session.snapshot().intent;await uncertain.session.send();assert.equal(uncertain.session.snapshot().intent,frozen);assert.equal(uncertain.calls.draft,1);assert.equal(uncertain.session.canEditText,false);
});
test("a closed conversation rotates its ensure identity once and retains closed history",async()=>{
 let current=null,fail=false;const keys=[];
 const f=fixture({list:async()=>current?[current]:[],ensure:async(_scope,key)=>{keys.push(key);if(fail)throw Error("lost");current=conversation({thread_id:id("thread",keys.length>1?"1":"0")});return current;},messages:async(thread)=>({conversation:{...current,thread_id:thread},messages:[message()],previousCursor:null})});
 await f.session.connect(true);f.session.setText("Draft survives");const closed={...current,state:"resolved"};current=closed;await f.session.refresh();fail=true;await f.session.connect(true);fail=false;await f.session.connect(true);
 assert.notEqual(keys[0],keys[1]);assert.equal(keys[1],keys[2]);assert.equal(f.session.snapshot().conversation.state,"open");assert.equal(f.session.snapshot().text,"Draft survives");assert.equal(f.session.snapshot().archives[0].thread_id,closed.thread_id);
 const archive=new ConversationSession({...f.api,messages:async()=>({conversation:closed,messages:[message()],previousCursor:null})},scope,closed);await archive.connect(false);assert.equal(archive.snapshot().messages.length,1);assert.equal(archive.canSend,false);assert.equal(keys.length,3);
});
test("a resolved conversation with an uncertain send keeps the frozen original identity",async()=>{
 let closed=false,ensures=0;const f=fixture({ensure:async()=>{ensures++;return conversation();},messages:async()=>({conversation:conversation({state:closed?"resolved":"open"}),messages:[],previousCursor:null}),send:async()=>{throw Error("uncertain");}});
 await f.session.connect(true);f.session.setText("Frozen");await f.session.send();const intent=f.session.snapshot().intent;closed=true;await f.session.refresh();await f.session.connect(true);assert.equal(ensures,0);assert.equal(f.session.snapshot().intent,intent);assert.equal(f.session.canEditText,false);
});
test("history transport only accepts a single bounded direction and validates cursors",async()=>{
 const path=`/conversations/${id("thread")}/messages`;
 for(const suffix of ["?tail=true",`?before=${id("message")}`,`?after=${id("message")}`])assert.equal(conversationPath(path+suffix,"GET"),path+suffix);
 for(const suffix of ["?tail=false","?tail=1",`?tail=true&before=${id("message")}`,`?after=${id("message")}&before=${id("message")}`,"?before=garbage","?limit=5000"])assert.throws(()=>conversationPath(path+suffix,"GET"));
 const api=new ConversationApi({requestConversation:async()=>({schema:"agent_commons.conversation-messages.v1",conversation:conversation({message_count:100}),messages:[message()],previous_cursor:id("message","1"),next_cursor:null})});await assert.rejects(api.messages(id("thread"),signal()));
});
test("both languages explain bounded history and expired uploads without fabricated read receipts",()=>{
 for(const locale of ["en","ru"]){for(const name of ["expired","historyPartial","historyLimited","closedNotice","olderError"])assert.ok(conversationText(locale)[name].length>10);}
 const source=readFileSync(resolve(root,"src/components/ConversationPanel.tsx"),"utf8");assert.match(source,/message.delivery.recipients.length \? <span>/);assert.match(source,/canWrite = !archiveId/);
});

test("actual WorkApi carries only approved history cursors inside its immutable project",async()=>{
 const previous=globalThis.fetch,calls=[];const work=Object.assign(new WorkApi(),{apiBase:"/api/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}).forProject("project.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
 globalThis.fetch=async(url,init)=>{calls.push({url,init});return new Response("{}",{headers:{"Content-Type":"application/json"}});};
 try {
  for(const query of ["tail=true",`before=${id("message")}`])await work.requestConversation(`/conversations/${id("thread")}/messages?${query}`,{method:"GET",signal:signal()});
  await assert.rejects(work.requestConversation(`/conversations/${id("thread")}/messages?tail=true&after=${id("message")}`,{method:"GET",signal:signal()}));
  assert.equal(calls.length,2);assert.match(calls[0].url,/project\.a+\/conversations\/thread\..*\/messages\?tail=true$/);assert.match(calls[1].url,/\?before=message\./);assert.equal(calls[0].init.redirect,"error");
 }finally {globalThis.fetch=previous;}
});
