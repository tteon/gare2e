use neo4rs::{query,Graph,Query,Row};
use serde::{Deserialize,Serialize};
use serde_json::{Value,json};
use std::{collections::BTreeMap,time::Instant};
#[derive(Clone,Deserialize,Serialize)]
pub struct Contract {pub version:String,pub workspace:String,pub dataset_id:String,pub snapshot_sha256:String,pub tools:BTreeMap<String,Tool>}
#[derive(Clone,Deserialize,Serialize)]
pub struct Tool {pub properties:BTreeMap<String,Value>,pub outputs:BTreeMap<String,String>,pub queries:BTreeMap<String,String>}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {pub tool:String,pub arguments:BTreeMap<String,Value>}
#[derive(Default,Serialize)]
pub struct Timings {pub tool_admission_ms:f64,pub admission_ms:f64,pub acquire_begin_ms:f64,pub run_ms:f64,pub first_row_ms:Option<f64>,pub drain_ms:f64,pub commit_ms:f64,pub total_ms:f64}
pub fn bind(c:&Contract,r:&Request)->Result<(Query,Tool),String> {
 let tool=c.tools.get(&r.tool).ok_or("unknown_tool")?;
 if tool.properties.keys().ne(r.arguments.keys()){return Err("argument_keys".into())}
 for (k,s) in &tool.properties {
  let v=&r.arguments[k];
  let valid=if s["type"]=="integer" {
   v.as_i64().map(|n| n>=s["minimum"].as_i64().unwrap() && n<=s["maximum"].as_i64().unwrap()).unwrap_or(false)
  } else {v.as_f64().map(|n|n.is_finite() && n>=s["minimum"].as_f64().unwrap() && n<=s["maximum"].as_f64().unwrap()).unwrap_or(false)};
  if !valid{return Err(format!("argument_value:{k}"))}
 }
 if let Some(start)=r.arguments.get("start_ts") {if start.as_i64()>=r.arguments["end_ts"].as_i64(){return Err("time_order".into())}}
 if let Some(lo)=r.arguments.get("account_min") {if lo.as_i64()>r.arguments["account_max"].as_i64(){return Err("account_range".into())}}
 let variant=r.arguments.get("max_hops").map(ToString::to_string).unwrap_or("default".into());
 let mut q=query(tool.queries.get(&variant).ok_or("query_variant")?).param("workspace",c.workspace.clone());
 for (k,v) in &r.arguments {
  if k=="max_hops"{continue}
  q=if let Some(n)=v.as_i64(){q.param(k,n)}else{q.param(k,v.as_f64().ok_or("invalid_number")?)};
 }
 Ok((q,tool.clone()))
}
fn decode(row:Row,t:&Tool)->Result<Value,String>{
 let mut out=serde_json::Map::new();
 for (key,kind) in &t.outputs {
  let v=match kind.as_str(){
   "integer"=>json!(row.get::<i64>(key).map_err(|e|e.to_string())?),
   "integer_array"=>json!(row.get::<Vec<i64>>(key).map_err(|e|e.to_string())?),
   _=>return Err("unsupported_output_schema".into())};
  out.insert(key.clone(),v);
 }
 Ok(Value::Object(out))
}
// Caller owns the deadline; patched Txn discards its socket on cancellation/error.
// No automatic retries. Explicit transaction semantics must match the Python control.
pub async fn execute(g:&Graph,q:Query,t:&Tool,spans:&mut Timings)->Result<Vec<Value>,String>{
 let start=Instant::now();
 let mut tx=g.start_txn().await.map_err(|e|e.to_string())?;
 spans.acquire_begin_ms=start.elapsed().as_secs_f64()*1000.;
 let run=Instant::now();let mut stream=tx.execute(q).await.map_err(|e|e.to_string())?;
 spans.run_ms=run.elapsed().as_secs_f64()*1000.;let drain=Instant::now();let mut rows=Vec::new();
 while let Some(row)=stream.next(tx.handle()).await.map_err(|e|e.to_string())? {
  if spans.first_row_ms.is_none(){spans.first_row_ms=Some(start.elapsed().as_secs_f64()*1000.)}
  rows.push(decode(row,t)?);
 }
 spans.drain_ms=drain.elapsed().as_secs_f64()*1000.;drop(stream);
 let commit=Instant::now();tx.commit().await.map_err(|e|e.to_string())?;
 spans.commit_ms=commit.elapsed().as_secs_f64()*1000.;Ok(rows)
}
#[cfg(test)]mod tests {
 use super::*;
 fn contract()->Contract{serde_json::from_value(json!({"version":"v1","workspace":"scope","dataset_id":"d","snapshot_sha256":"s","tools":{"t":{"properties":{"account":{"type":"integer","minimum":0,"maximum":9223372036854775807i64}},"outputs":{"account":"integer"},"queries":{"default":"RETURN $account AS account"}}}})).unwrap()}
 #[test]fn validates_without_oracle(){let c=contract();for v in [json!(0),json!(9223372036854775807i64)]{assert!(bind(&c,&Request{tool:"t".into(),arguments:BTreeMap::from([("account".into(),v)])}).is_ok());}}
 #[test]fn rejects_wrong_types_and_bounds(){let c=contract();for v in [json!(true),json!(1.0),json!(-1),json!(9223372036854775808u64),json!("0")] {assert!(bind(&c,&Request{tool:"t".into(),arguments:BTreeMap::from([("account".into(),v)])}).is_err());}}
 #[test]fn rejects_scope_injection(){let c=contract();assert!(bind(&c,&Request{tool:"t".into(),arguments:BTreeMap::from([("account".into(),json!(1)),("workspace".into(),json!("other"))])}).is_err());}
}
