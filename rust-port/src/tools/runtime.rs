//! Application lifecycle, HTTP endpoint testing, and read-only SQLite access.
use super::{terminal::Terminal,workspace::Workspace};
use anyhow::{bail,Context,Result};
use reqwest::{Client,Method};
use rusqlite::{Connection,OpenFlags,types::ValueRef};
use serde_json::{json,Value};
use std::{collections::BTreeSet,fs,sync::Arc};
use tokio::sync::Mutex;

#[derive(Clone)]pub struct Runtime {ws:Arc<Workspace>,term:Terminal,client:Client,
    db:Arc<Mutex<Option<String>>>,process:Arc<Mutex<Option<String>>>}
impl Runtime {
 pub fn new(ws:Arc<Workspace>,term:Terminal)->Result<Self>{Ok(Self{ws,term,client:Client::builder().timeout(std::time::Duration::from_secs(30)).build()?,
    db:Arc::new(Mutex::new(None)),process:Arc::new(Mutex::new(None))})}
 pub async fn http(&self,action:&str,kw:&Value)->Result<Value>{
    let a=|k:&str|kw[k].as_str().unwrap_or("");let method=a("method");let method=Method::from_bytes(if method.is_empty(){b"GET"}else{method.as_bytes()})?;
    let send=|url:&str|self.client.request(method.clone(),url);
    match action{
       "request"|"test"=>{
            let mut req=send(a("url"));if let Some(headers)=kw["headers"].as_object(){for(k,v) in headers{if let Some(s)=v.as_str(){req=req.header(k,s);}}}
            if let Some(body)=kw.get("json_body"){if !body.is_null(){req=req.json(body)}}
            else if let Some(body)=kw["body"].as_str(){req=req.body(body.to_owned())}
            let started=std::time::Instant::now();let response=req.send().await?;
            let status=response.status().as_u16();let headers=response.headers().iter().map(|(k,v)|(k.to_string(),v.to_str().unwrap_or("").to_string())).collect::<std::collections::BTreeMap<_,_>>();
            let text=response.text().await?;let parsed=serde_json::from_str::<Value>(&text).ok();
            Ok(json!({"status":status,"headers":headers,"text":text.chars().take(50_000).collect::<String>(),"json":parsed,
              "latency":started.elapsed().as_secs_f64(),"ok":kw["expected_status"].as_u64().is_none_or(|expected|expected==status as u64)}))
       },
       "compare"=>{let a=self.client.get(kw["url"].as_str().unwrap_or("")).send().await?;
           let b=self.client.get(kw["url_b"].as_str().unwrap_or("")).send().await?;
           let status_match=a.status()==b.status();let a_status=a.status().as_u16();let b_status=b.status().as_u16();
           let a_body=a.text().await?;let b_body=b.text().await?;
           Ok(json!({"status_match":status_match,"a_status":a_status,"b_status":b_status,"body_equal":a_body==b_body}))},
       _=>bail!("unsupported http action: {action}")
    }
 }
 pub async fn database(&self,action:&str,kw:&Value)->Result<Value>{let arg=|s:&str|kw[s].as_str().unwrap_or("");
    match action{
      "detect"=>{let mut engines=BTreeSet::new();let structure=self.ws.structure(5)?;
          for entry in structure["tree"].as_array().into_iter().flatten(){let p=entry["path"].as_str().unwrap_or("");
              if p.ends_with(".sqlite")||p.ends_with(".db"){engines.insert("sqlite");}
              if p.ends_with("schema.prisma")||p.contains("postgres"){engines.insert("postgres");}
              if p.contains("mysql"){engines.insert("mysql");}
          }Ok(json!({"engines":engines}))},
      "connect_sqlite"=>{let p=self.ws.resolve(arg("path"))?;if !p.exists(){bail!("database does not exist: {}",p.display())}
          let c=Connection::open_with_flags(&p,OpenFlags::SQLITE_OPEN_READ_ONLY)?;
          let _:i64=c.query_row("PRAGMA schema_version",[],|r|r.get(0))?;
          *self.db.lock().await=Some(arg("path").to_owned());Ok(json!({"ok":true,"readonly":true}))},
      "migrations"=>{let files=self.ws.find_files("*.sql",".")?;Ok(json!({"migrations":files.into_iter().filter(|p|p.contains("migration")||p.contains("schema")).collect::<Vec<_>>()}))},
      "find_usage"=>Ok(json!({"hits":self.ws.search(arg("model"),".",false,500)?})),
      _=>{
          let path=self.db.lock().await.clone().ok_or_else(||anyhow::anyhow!("no SQLite database connected"))?;
          let file=self.ws.resolve(&path)?;
          let connection=Connection::open_with_flags(file,OpenFlags::SQLITE_OPEN_READ_ONLY|OpenFlags::SQLITE_OPEN_URI)?;
          connection.pragma_update(None,"query_only",true)?;
          match action{
              "tables"=>{let mut stmt=connection.prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")?;
                  let rows=stmt.query_map([],|r|r.get::<_,String>(0))?.collect::<rusqlite::Result<Vec<_>>>()?;
                  Ok(json!({"tables":rows}))},
              "describe"=>{let table=arg("table");let safe=table.replace('"',"\"\"");
                  let mut stmt=connection.prepare(&format!("PRAGMA table_info(\"{safe}\")"))?;
                  let rows=stmt.query_map([],|r|Ok(json!({"cid":r.get::<_,i64>(0)?,"name":r.get::<_,String>(1)?,
                    "type":r.get::<_,String>(2)?,"notnull":r.get::<_,i64>(3)?!=0,"default":r.get::<_,Option<String>>(4)?,"pk":r.get::<_,i64>(5)?!=0})))?.collect::<rusqlite::Result<Vec<_>>>()?;
                  Ok(json!({"columns":rows}))},
              "query"=>{if kw["readonly"]==false {bail!("database tool enforces read-only; use reviewed migrations through terminal")}
                  let query=arg("query");let mut stmt=connection.prepare(query)?;
                  if !stmt.readonly(){bail!("refusing SQL that modifies database")}
                  let cols=stmt.column_names().iter().map(|s|s.to_string()).collect::<Vec<_>>();
                  let limit=kw["limit"].as_u64().unwrap_or(1000).min(10_000) as usize;
                  let mut rows=Vec::new();let mut cursor=stmt.query([])?;
                  while rows.len()<limit {let Some(row)=cursor.next()? else{break};let mut values=Vec::new();
                      for i in 0..cols.len(){values.push(match row.get_ref(i)?{
                          ValueRef::Null=>Value::Null,ValueRef::Integer(n)=>json!(n),ValueRef::Real(n)=>json!(n),
                          ValueRef::Text(s)=>json!(String::from_utf8_lossy(s).to_string()),
                          ValueRef::Blob(b)=>json!(format!("<{} bytes>",b.len()))});}
                      rows.push(values);
                  }Ok(json!({"columns":cols,"rows":rows,"count":rows.len()}))
              },
              "schema"=>{let mut stmt=connection.prepare("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name")?;
                  let sql=stmt.query_map([],|r|r.get::<_,String>(0))?.collect::<rusqlite::Result<Vec<_>>>()?;
                  Ok(json!({"schema":sql}))},
              _=>bail!("unknown database action {action}")
          }
      }
    }
 }
 pub async fn app(&self,action:&str,kw:&Value)->Result<Value>{
    match action{
       "start"=>{let command=kw["command"].as_str().map(str::to_owned).or_else(||self.detect_start()).ok_or_else(||anyhow::anyhow!("cannot detect app start command"))?;
           let status=self.term.background(&command,None).await?;
           *self.process.lock().await=status["process_id"].as_str().map(str::to_owned);Ok(status)},
       "status"=>{let id=self.process.lock().await.clone();
           if let Some(pid)=id{self.term.dispatch("get",&json!({"process_id":pid})).await}else{Ok(json!({"status":"stopped"}))}},
       "logs"=>{let id=self.process.lock().await.clone();if let Some(pid)=id{self.term.dispatch("get",&json!({"process_id":pid})).await}
           else{Ok(json!({"logs":"","status":"stopped"}))}},
       "stop"=>{let id=self.process.lock().await.take();if let Some(pid)=id{self.term.dispatch("kill",&json!({"process_id":pid})).await}else{Ok(json!({"status":"stopped"}))}},
       _=>bail!("unknown app action {action}")
    }
 }
 fn detect_start(&self)->Option<String>{let root=&self.ws.root;if root.join("package.json").exists(){Some("npm run dev".into())}
    else if root.join("main.py").exists(){Some("python main.py".into())}else if root.join("Cargo.toml").exists(){Some("cargo run".into())}
    else if root.join("go.mod").exists(){Some("go run .".into())}else{None}}
}
