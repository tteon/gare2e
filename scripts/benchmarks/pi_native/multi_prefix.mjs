import fs from 'node:fs';import path from 'node:path';
export function emitCollector(kind,data){const raw=Buffer.from('GRAPH_COLLECTOR '+JSON.stringify({kind,...data})+'\n');if(raw.length>8*1024*1024)throw new Error('collector_frame_bound');let offset=0;while(offset<raw.length){const n=fs.writeSync(1,raw,offset,raw.length-offset);if(n<=0)throw new Error('collector_short_write');offset+=n;}}
// Complete IRI only. Never repair partial identifiers or read a future query tail.
export function semanticEntity(query){
 for(let i=0;i<query.length;i++){
  const c=query[i];if(c==='#'){while(i<query.length&&query[i]!=="\n")i++;continue;}
  if(c==='"'||c==="'"){const delimiter=query.slice(i,i+3)===c.repeat(3)?c.repeat(3):c;i+=delimiter.length;let closed=false;for(;i<query.length;i++){if(query[i]==="\\"){i++;continue;}if(query.slice(i,i+delimiter.length)===delimiter){i+=delimiter.length-1;closed=true;break;}}if(!closed)return null;continue;}
  if(c==='<'){const end=query.indexOf('>',i+1);if(end<0)return null;const iri=query.slice(i,end+1);const m=/^<urn:seocho:gtsqa:(entity|predicate):([^<>\s]+)>$/.exec(iri);if(m)return {kind:m[1],identifier:m[2],complete_iri:iri};i=end;}
 }return null;
}
export function registerLandmarks(pi,{out,episode,session}){
 const fd=fs.openSync(path.join(out,'multi-prefix.jsonl'),'wx',0o600);let seq=0,turn=0,seen=new Set(),failed=null,bytes=0,cost=0;
 const emit=(kind,data={})=>{if(failed)return;try{const row={schema:'multi_prefix.v2',kind,episode,session,pid:process.pid,clock:'node.hrtime.bigint',seq:++seq,...data};const raw=JSON.stringify(row)+'\n';const n=Buffer.byteLength(raw);if(bytes+n>2*1024*1024)throw new Error('bound');if(fs.writeSync(fd,raw)!==n)throw new Error('short');bytes+=n;if(kind==='landmark')emitCollector('landmark',row);}catch(e){failed=String(e);}};
 emit('bound',{criteria:['fixed64','first_complete_iri','query_complete'],unit:'JavaScript UTF16 code units',snapshot_cap:4096});pi.on('message_start',e=>{if(e.message?.role==='assistant'){turn++;seen=new Set();}});
 pi.on('message_update',e=>{const entered=process.hrtime.bigint();try{const a=e.assistantMessageEvent;if(!['toolcall_delta','toolcall_end'].includes(a?.type))return;const block=a.toolCall??e.message?.content?.[a.contentIndex];if(block?.type!=='toolCall'||block.name!=='execute_graph_query'||typeof block.arguments?.query!=='string')return;const query=block.arguments.query;const prefix=query.slice(0,4096),semantic=semanticEntity(prefix);
 for(const criterion of (a.type==='toolcall_end'?['query_complete']:['fixed64','first_complete_iri'])){const key=turn+':'+a.contentIndex+':'+criterion;if(seen.has(key)||(criterion==='fixed64'?query.length<64:criterion==='first_complete_iri'?!semantic:false))continue;seen.add(key);emit('landmark',{turn,content_index:a.contentIndex,tool_call_id:block.id??null,criterion,callback_entered_ns:entered.toString(),observation_ready_ns:process.hrtime.bigint().toString(),actual_chars:query.length,query_prefix:prefix,truncated:query.length>4096,complete_identifier:semantic});}
 }catch(e){failed=String(e);}finally{cost+=Number(process.hrtime.bigint()-entered);}});
 process.once('exit',code=>{emit('end',{code,callback_cost_ns:cost,failed});try{fs.closeSync(fd);fs.writeFileSync(path.join(out,'multi-prefix-summary.json'),JSON.stringify({episode,session,pid:process.pid,seq,bytes,failed,callback_cost_ns:cost}),{flag:'wx',mode:0o600});}catch{}});
}
