use axum::{extract::State,routing::{get,post},Json,Router};
use gug_neo4rs_adapter::{Contract,Request,Timings,bind,execute};
use neo4rs::{ConfigBuilder,Graph};
use serde_json::{json,Value};
use std::{sync::{Arc,atomic::{AtomicU64,Ordering}},time::{Duration,Instant}};
use tokio::sync::{Semaphore,oneshot};
#[derive(Clone)]struct App{graph:Graph,contract:Arc<Contract>,admission:Arc<Semaphore>,deadline:Duration,ids:Arc<AtomicU64>,metadata:Arc<Value>,l4_gate:Option<Arc<Semaphore>>}
async fn run(State(app):State<App>,Json(req):Json<Request>)->Json<Value>{
 let id=app.ids.fetch_add(1,Ordering::Relaxed);
 let (q,tool)=match bind(&app.contract,&req){Ok(v)=>v,Err(e)=>return Json(json!({"status":"invalid","error":e,"rows":[],"request_id":id}))};
 let (mut sender,receiver)=oneshot::channel();
 tokio::spawn(async move {
  let start=Instant::now();let mut spans=Timings::default();
  let result=tokio::select! {
   _=sender.closed()=>Err("caller_cancelled".to_string()),
   r=tokio::time::timeout(app.deadline,async {
    let _tool_permit=if req.tool=="find_fan_in_accounts" {
     if let Some(gate)=&app.l4_gate {Some(gate.acquire().await.map_err(|e|e.to_string())?)}else{None}
    } else {None};
    spans.tool_admission_ms=start.elapsed().as_secs_f64()*1000.;
    let admission_start=Instant::now();
    let _permit=app.admission.acquire().await.map_err(|e|e.to_string())?;
    spans.admission_ms=admission_start.elapsed().as_secs_f64()*1000.;
    execute(&app.graph,q,&tool,&mut spans).await
   })=>r.unwrap_or_else(|_|Err("deadline_exceeded".into()))
  };
  spans.total_ms=start.elapsed().as_secs_f64()*1000.;
  let (status,rows,error)=match result{Ok(rows)=>("ok",rows,None),Err(e)=>("failed",vec![],Some(e))};
  let bytes=serde_json::to_vec(&rows).unwrap().len();
  let report=json!({"request_id":id,"tool":req.tool,"status":status,"rows":rows,"error":error,"timings":spans,"result_bytes":bytes,"retry_count":0,"instance_id":app.metadata["instance_id"]});
  println!("{}",json!({"event":"completed","request_id":id,"tool":req.tool,"status":status,"error":report["error"],"timings":report["timings"]}));
  let _=sender.send(report);
 });
 Json(receiver.await.unwrap_or_else(|_|json!({"status":"failed","error":"worker_lost","rows":[]})))
}
async fn serve()->Result<(),Box<dyn std::error::Error>>{
 let contract:Contract=serde_json::from_slice(&std::fs::read(std::env::args().nth(1).ok_or("contract path required")?)?)?;
 assert_eq!(contract.version,"gug-neo4rs-contract-v1");
 let env=|k:&str,d:&str|std::env::var(k).unwrap_or(d.into());
 let pool:usize=env("GUG_POOL","16").parse()?;assert!(pool>0);
 let admission:usize=env("GUG_ADMISSION",&pool.to_string()).parse()?;assert!(admission>0);
 let fetch:usize=env("GUG_FETCH","1000").parse()?;assert!(fetch>0);
 let l4_cap:usize=env("GUG_L4_CAP","0").parse()?;
 let timeout:u64=env("GUG_TIMEOUT_MS","15000").parse()?;assert!(timeout>0);
 let metadata=Arc::new(json!({"instance_id":format!("{}-{}",std::process::id(),std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)?.as_nanos()),"contract":contract,"runtime":{"l4_cap":l4_cap,"pool":pool,"fetch_size":fetch,"admission":admission,"deadline_ms":timeout,"runtime_threads":env("GUG_RUNTIME_THREADS","1").parse::<usize>()?,"bolt_uri":env("GUG_BOLT","127.0.0.1:17690")}}));
 let graph=Graph::connect(ConfigBuilder::default().uri(env("GUG_BOLT","127.0.0.1:17690")).user(env("GUG_DB_USER","neo4j")).password(env("GUG_DB_PASSWORD","unused-local-auth-disabled")).max_connections(pool).fetch_size(fetch).build()?).await?;
 let app=App{graph,contract:Arc::new(contract),admission:Arc::new(Semaphore::new(admission)),deadline:Duration::from_millis(timeout),ids:Arc::new(AtomicU64::new(1)),metadata,l4_gate:if l4_cap>0 {Some(Arc::new(Semaphore::new(l4_cap)))}else{None}};
 let router=Router::new().route("/health",get(||async {"process_ready_not_db_readiness"})).route("/metadata",get(|State(app):State<App>|async move {Json((*app.metadata).clone())})).route("/tool",post(run)).with_state(app);
 let listener=tokio::net::TcpListener::bind(env("GUG_LISTEN","127.0.0.1:18103")).await?;
 axum::serve(listener,router).await?;Ok(())
}

fn main()->Result<(),Box<dyn std::error::Error>>{
 let n:usize=std::env::var("GUG_RUNTIME_THREADS").unwrap_or("1".into()).parse()?;assert!(n>0);
 let rt=if n==1 {tokio::runtime::Builder::new_current_thread().enable_all().build()?}else{tokio::runtime::Builder::new_multi_thread().worker_threads(n).enable_all().build()?};
 rt.block_on(serve())
}
