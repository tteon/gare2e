import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {mkdtempSync,writeFileSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {legacy,validateProfile,applyPayload} from '../profile.mjs';
const root=fileURLToPath(new URL('../',import.meta.url));
function sse(item,n,inputTokens=10){
 const events=[];const add=(type,x)=>events.push(`event: ${type}\ndata: ${JSON.stringify({type,sequence_number:events.length,...x})}\n\n`);
 add('response.created',{response:{id:`r${n}`,object:'response',status:'in_progress',output:[]}});
 add('response.output_item.added',{output_index:0,item:{...item,arguments:'',status:'in_progress'}});
 if(item.type==='function_call'){
  add('response.function_call_arguments.delta',{output_index:0,item_id:item.id,delta:item.arguments});
  add('response.function_call_arguments.done',{output_index:0,item_id:item.id,arguments:item.arguments});
 }
 if(item.type==='message'){add('response.content_part.added',{output_index:0,item_id:item.id,content_index:0,part:{type:'output_text',text:'',annotations:[]}});add('response.output_text.delta',{output_index:0,item_id:item.id,content_index:0,delta:item.content[0].text});add('response.output_text.done',{output_index:0,item_id:item.id,content_index:0,text:item.content[0].text});}
 add('response.output_item.done',{output_index:0,item});
 add('response.completed',{response:{id:`r${n}`,object:'response',status:'completed',output:[item],usage:{input_tokens:inputTokens,output_tokens:20,total_tokens:inputTokens+20}}});
 return events.join('');
}

test('profile bounds and explicit unsupported compaction',()=>{
 assert.throws(()=>validateProfile({...legacy,compaction:true}),/compaction/);
 assert.throws(()=>validateProfile({...legacy,context_window:1000000}),/extended/);
 assert.throws(()=>applyPayload({max_output_tokens:513},legacy),/allowance/);
 assert.equal(applyPayload({max_output_tokens:32},legacy).max_output_tokens,32);
});
for(const thinking of ['off','high'])test(`retained native session tool roundtrip: ${thinking}`,{timeout:30000},async()=>{
 const dir=mkdtempSync(join(tmpdir(),'retained-pi-'));const requests=[];
 const server=http.createServer(async(req,res)=>{
  let text='';for await(const b of req)text+=b;
  const p=JSON.parse(text);requests.push(p);
  const n=requests.length;const outputs=p.input.filter(x=>x.type==='function_call_output');
  const name=outputs.length===0?'inspect_graph_schema':outputs.length===1?'execute_graph_query':'finish_task';
  const args=name==='execute_graph_query'?{query:'SELECT Q1'}:name==='finish_task'?{answer_ids:['Q1']}:{};
  res.writeHead(200,{'content-type':'text/event-stream'});
  res.end(sse({type:'function_call',id:`fc${n}`,call_id:`call${n}`,name,arguments:JSON.stringify(args),status:'completed'},n));
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const extension=join(dir,'fixture.mjs');
 // Scripted tool extension is only a fixture; production loads the retained owner extension.
 writeFileSync(extension,`export default function(pi){
 pi.registerProvider('pi-qwen-local',{baseUrl:'http://127.0.0.1:${server.address().port}/v1',apiKey:'fixture',api:'openai-responses',models:[{id:'fixture',name:'fixture',reasoning:${thinking!=='off'},input:['text'],cost:{input:0,output:0,cacheRead:0,cacheWrite:0},contextWindow:32768,maxTokens:512}]});
 pi.on('before_provider_request',e=>({...e.payload,max_output_tokens:512}));
 for(const name of ['inspect_graph_schema','execute_graph_query','finish_task'])pi.registerTool({name,label:name,description:name,parameters:{type:'object',properties:name==='execute_graph_query'?{query:{type:'string'}}:name==='finish_task'?{answer_ids:{type:'array',items:{type:'string'}}}:{},required:[],additionalProperties:false},async execute(id,args){return {content:[{type:'text',text:JSON.stringify({rows:['Q1']})}],details:{status:'ok',answer_ids:['Q1']}};}});
 pi.on('tool_execution_end',async(e,ctx)=>{if(e.toolName==='finish_task')await ctx.abort();});
 }`);
 const config=join(dir,'config.json');const out=join(dir,'run');
 writeFileSync(config,JSON.stringify({output:out,cwd:dir,extension,question:'fixture question',system_prompt:'Use the three tools.',profile:{...legacy,thinking,...(thinking==='high'?{seed:42}:{})},env:{PI_MODEL_ID:'fixture',PI_TOOL_EPISODE_ID:'e1'}}));
 try{
  let stderr='';const child=spawn(process.execPath,[join(root,'native_case.mjs'),config],{stdio:['ignore','pipe','pipe']});
  child.stdout.resume();child.stderr.on('data',b=>stderr+=b);
  const timer=setTimeout(()=>child.kill('SIGKILL'),25000);
  const code=await new Promise(r=>child.on('close',r));clearTimeout(timer);
  assert.equal(code,0,stderr);
  assert.equal(requests.length,3);
  assert.equal(requests[1].input.find(x=>x.type==='function_call_output').call_id,'call1');
  assert.equal(requests[2].input.filter(x=>x.type==='function_call_output').length,2);
  for(const p of requests){assert.equal(p.store,false);assert.equal(p.previous_response_id,undefined);assert.equal(p.max_output_tokens,512);if(thinking==='high'){assert.equal(p.reasoning.effort,'xhigh');assert.equal(p.seed,42);}else assert.equal(p.seed,undefined);}
  const result=JSON.parse(readFileSync(join(out,'native-result.json')));
  assert.equal(result.shutdown,true);assert.equal(result.abortSettled,true);assert.equal(result.error,null);
  const identities=JSON.parse(readFileSync(join(out,'identity.json')));assert.ok(identities.session_id);
 }finally{await new Promise(r=>server.close(r));rmSync(dir,{recursive:true,force:true});}
});
