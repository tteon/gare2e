// Historical defaults remain reproducible. High reasoning is an explicit new profile.
export const legacy=Object.freeze({max_requests:8,max_output_tokens:512,max_total_tokens:65536,context_window:32768,deadline_ms:120000,thinking:'off',temperature:0,compaction:false});
export function validateProfile(value=legacy){
 const p={...value};
 if(Object.keys(p).filter(k=>k!=='seed').sort().join()!==Object.keys(legacy).sort().join())throw Error('profile_keys');
 if('seed' in p&&(!Number.isSafeInteger(p.seed)||p.seed<0))throw Error('seed');
 for(const k of ['max_requests','max_output_tokens','max_total_tokens','context_window','deadline_ms'])if(!Number.isSafeInteger(p[k])||p[k]<=0)throw Error(k);
 if(p.max_output_tokens>=p.context_window||p.max_total_tokens<p.max_output_tokens)throw Error('token_limits');
 if(p.context_window>262144)throw Error('extended_context_unvalidated');
 if(!['off','low','medium','high'].includes(p.thinking))throw Error('thinking');
 if(!Number.isFinite(p.temperature)||p.temperature<0||p.temperature>2)throw Error('temperature');
 if(p.compaction!==false)throw Error('native_compaction_accounting_unverified');
 return p;
}
export function profileEnv(p){return {...Object.fromEntries(['max_requests','max_output_tokens','max_total_tokens','context_window','deadline_ms'].map(k=>['PI_'+k.toUpperCase(),String(p[k])])),PI_THINKING_LEVEL:p.thinking};}
export function applyPayload(payload,p){
 if(payload.previous_response_id)throw Error('stateful_responses');
 const result={...payload,temperature:p.temperature,store:false};
 if('seed' in p)result.seed=p.seed;
 // Do not override a smaller allowance reserved by the original extension.
 if(!Number.isSafeInteger(result.max_output_tokens)||result.max_output_tokens<1||result.max_output_tokens>p.max_output_tokens)throw Error('extension_output_allowance');
 if(p.thinking==='off'){
  if(result.reasoning||result.chat_template_kwargs?.enable_thinking)throw Error('unexpected_thinking');
 }else{
  const effort=p.thinking==='high'?'xhigh':p.thinking;
  result.reasoning={...result.reasoning,effort};
  result.chat_template_kwargs={...result.chat_template_kwargs,enable_thinking:true,reasoning_effort:effort,preserve_thinking:true};
 }
 return result;
}
