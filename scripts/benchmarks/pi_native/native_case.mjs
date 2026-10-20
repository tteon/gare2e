// Retained native pi session path. Extension and owner endpoint are supplied by the deployment.
import assert from 'node:assert/strict';
import {registerLandmarks,emitCollector} from './multi_prefix.mjs';
import {createHash} from 'node:crypto';
import {readFileSync, writeFileSync, appendFileSync, mkdirSync} from 'node:fs';
import {join} from 'node:path';
import {pathToFileURL,fileURLToPath} from 'node:url';

const [file, check] = process.argv.slice(2);
if (!file || file === '--help') {
  console.log('node native_case.mjs CONFIG.json [--check-imports]');
  process.exit(0);
}
const c = JSON.parse(readFileSync(file, 'utf8'));
const {validateProfile,profileEnv,applyPayload}=await import('./profile.mjs');
const profile=validateProfile(c.profile);
const toolNames=c.tool_names??['inspect_graph_schema','execute_graph_query','finish_task'];
assert.ok(Array.isArray(toolNames)&&toolNames.length>0&&toolNames.every(x=>typeof x==='string')&&new Set(toolNames).size===toolNames.length);
c.sdk??=fileURLToPath(new URL('./node_modules/@earendil-works/pi-coding-agent/',import.meta.url));
assert.equal(JSON.parse(readFileSync(join(c.sdk,'package.json'),'utf8')).version,'0.80.10','SDK pin mismatch');
const imports = ['sdk.js','session-manager.js','settings-manager.js','model-runtime.js',
  'auth-storage.js','extensions/loader.js','event-bus.js','extensions/runner.js'];
const [{createAgentSession},{SessionManager},{SettingsManager},{ModelRuntime},
  {AuthStorage},{loadExtensions,loadExtensionFromFactory},{createEventBus},runner] =
  await Promise.all(imports.map(p => import(pathToFileURL(join(c.sdk,'dist/core',p)).href)));
if (check === '--check-imports') { console.log('SDK imports OK; no session/model/owner started'); process.exit(0); }
process.umask(0o077);
mkdirSync(c.output,{recursive:true,mode:0o700});
writeFileSync(join(c.output,'.native-session-lock'),String(process.pid),{flag:'wx',mode:0o600});
const out=c.output, write=(n,v)=>writeFileSync(join(out,n),JSON.stringify(v,null,2)+'\n');
const sm=SessionManager.create(c.cwd,join(out,'sessions'));
Object.assign(process.env,c.env,profileEnv(profile),{PI_SESSION_ID:sm.getSessionId()});
const bus=createEventBus(), loaded=await loadExtensions([c.extension],c.cwd,bus);
assert.deepEqual(loaded.errors,[], 'original extension load failed');
// Identical policy hook in both observer arms; original tools and prompt untouched.
let readySequence=0;
const stable=x=>Array.isArray(x)?x.map(stable):x&&typeof x==='object'?Object.fromEntries(Object.keys(x).sort().map(k=>[k,stable(x[k])])):x;
const policy=await loadExtensionFromFactory(pi=>{
  registerLandmarks(pi,{out,episode:process.env.PI_TOOL_EPISODE_ID,session:sm.getSessionId()});
  pi.on('before_provider_request',e=>{
    // SDK hook exceptions can be swallowed: invalid state must terminate.
    if(e.payload.previous_response_id) { process.stderr.write('stateful Responses rejected\n'); process.exit(2); }
    let payload;
    try {payload=applyPayload(e.payload,profile);} catch(error) {process.stderr.write(String(error)+'\n');process.exit(2);}
    const readyNs=process.hrtime.bigint().toString();
    try {
    const clean=JSON.parse(JSON.stringify(payload));
    const canonical=JSON.stringify(stable(clean));
    const row={schema:'provider_payload_ready.v1',episode_id:process.env.PI_TOOL_EPISODE_ID,session_id:sm.getSessionId(),pid:process.pid,sequence:++readySequence,clock:'node.hrtime.bigint',payload_ready_ns:readyNs,payload_sha256:createHash('sha256').update(canonical).digest('hex'),logical_input_bytes:Buffer.byteLength(JSON.stringify(clean.input)),logical_payload_bytes:Buffer.byteLength(JSON.stringify(clean)),encoding_finished_ns:process.hrtime.bigint().toString(),scope:'complete fixed-policy provider payload callback; operational readiness boundary, not earliest possible object construction or controller receipt'};
    appendFileSync(join(out,'provider-ready.jsonl'),JSON.stringify(row)+'\n');emitCollector('payload_ready',row);}
    catch(error){process.stderr.write('READY_OBSERVER_WRITE_FAILED\n');process.exit(2);}
    return payload;
  });
},c.cwd,bus,loaded.runtime,'<cuda-fixed-generation-policy>');
loaded.extensions.push(policy);
const resourceLoader={getExtensions:()=>loaded,getSkills:()=>({skills:[],diagnostics:[]}),
  getPrompts:()=>({prompts:[],diagnostics:[]}),getThemes:()=>({themes:[],diagnostics:[]}),
  getAgentsFiles:()=>({agentsFiles:[]}),getSystemPrompt:()=>c.system_prompt,
  getAppendSystemPrompt:()=>[],extendResources:()=>{},reload:async()=>{}};
const modelRuntime=await ModelRuntime.create({credentials:AuthStorage.inMemory(),modelsPath:null,allowModelNetwork:false});
for(const {name,config} of loaded.runtime.pendingProviderRegistrations){
  if(name==='pi-qwen-local')config.models=config.models.map(m=>m.id===process.env.PI_MODEL_ID?
    {...m,reasoning:profile.thinking!=='off',contextWindow:profile.context_window,maxTokens:profile.max_output_tokens}:m);
  modelRuntime.registerProvider(name,config);
}
const model=modelRuntime.getModel('pi-qwen-local',process.env.PI_MODEL_ID);
assert.ok(model); assert.equal(model.reasoning,profile.thinking!=='off'); assert.equal(model.api,'openai-responses');
const settingsManager=SettingsManager.inMemory({compaction:{enabled:false},
  retry:{enabled:false,provider:{maxRetries:0,timeoutMs:120000}},transport:'sse',
  httpIdleTimeoutMs:120000,enableAnalytics:false,enableInstallTelemetry:false});
let finishSequence=0;
let session,error=null,errors=[],shutdown=false,abortSettled=false,responseAvailable=null;
try {
  ({session}=await createAgentSession({cwd:c.cwd,agentDir:join(out,'agent'),modelRuntime,model,
    thinkingLevel:profile.thinking,tools:toolNames,
    sessionManager:sm,settingsManager,resourceLoader}));
  await session.bindExtensions({onError:e=>errors.push(String(e))});
  assert.deepEqual([...session.getActiveToolNames()].sort(),
    [...toolNames].sort());
  session.subscribe(e=>{ const at=process.hrtime.bigint().toString(); appendFileSync(join(out,'agent-events.jsonl'),JSON.stringify({...e,observed_monotonic_ns:at,observed_wall_ms:Date.now()})+'\n'); if(e.type==='tool_execution_start'||e.type==='tool_execution_end'){emitCollector('tool_phase',{tool_call_id:e.toolCallId,tool_name:e.toolName,phase:e.type==='tool_execution_start'?'start':'end',is_error:e.isError===true,source_ns:at});} if(e.type==='tool_execution_end'&&e.toolName==='finish_task'){emitCollector('finish_result',{episode_id:process.env.PI_TOOL_EPISODE_ID,session_id:sm.getSessionId(),sequence:++finishSequence,source_ns:at,event:e});} if(e.type==='agent_end'){responseAvailable={monotonic_ns:at,wall_ms:Date.now(),boundary:'agent_end event before cleanup'};emitCollector('agent_end_received',{episode_id:process.env.PI_TOOL_EPISODE_ID,session_id:sm.getSessionId(),source_ns:at,event:e});} });
  write('profile.json',profile);
  write('identity.json',{episode_id:process.env.PI_TOOL_EPISODE_ID,session_id:sm.getSessionId()});
  await session.prompt(c.question);
} catch(e) { error=String(e); }
finally {
  if(session) {
    try { await session.abort(); abortSettled=true; } catch(e) { error??=String(e); }
    try { await runner.emitSessionShutdownEvent(session.extensionRunner,{type:'session_shutdown',reason:'quit'}); shutdown=true; }
    catch(e) { error??=String(e); }
    session.dispose();
  }
  emitCollector('finish_stream_end',{episode_id:process.env.PI_TOOL_EPISODE_ID,session_id:sm.getSessionId(),count:finishSequence});
  write('native-result.json',{error,errors,shutdown,abortSettled,response_available:responseAvailable,cleanup_finished_monotonic_ns:process.hrtime.bigint().toString(),session_id:sm.getSessionId(),answer_score_valid:false});
}
if(error||errors.length) process.exitCode=1;
