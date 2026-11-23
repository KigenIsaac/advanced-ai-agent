//! Persistent project notes, tasks, architecture and conventions.
use super::workspace::Workspace;
use anyhow::{bail,Result};
use serde_json::{json,Value};
use std::{fs,path::PathBuf,sync::Arc};
use tokio::sync::Mutex;
use uuid::Uuid;

#[derive(Clone)]pub struct ProjectMemory {ws:Arc<Workspace>,lock:Arc<Mutex<()>>}
impl ProjectMemory {
 pub fn new(ws:Arc<Workspace>)->Result<Self>{let p=ws.resolve(".codeagent")?;fs::create_dir_all(p)?;
    Ok(Self{ws,lock:Arc::new(Mutex::new(()))})}
 fn path(&self,key:&str)->Result<PathBuf>{self.ws.resolve(&format!(".codeagent/{key}.json"))}
 fn read(&self,key:&str)->Result<Value>{let path=self.path(key)?;
    if !path.exists(){return Ok(json!({}))}Ok(serde_json::from_slice(&fs::read(path)?)?)}
 fn save(&self,key:&str,value:&Value)->Result<()> {let path=self.path(key)?;
    let temp=path.with_extension("tmp");fs::write(&temp,serde_json::to_vec_pretty(value)?)?;
    fs::rename(temp,path)?;Ok(())}
 pub async fn dispatch(&self,action:&str,kw:&Value)->Result<Value>{let _guard=self.lock.lock().await;
    let a=|k:&str|kw[k].as_str().unwrap_or("");let mut mem=self.read("memory")?;
    let mut tasks=self.read("tasks")?;
    match action{
      "context"=>Ok(json!({"root":self.ws.root,"structure":self.ws.structure(3)?,"notes":mem["notes"]})),
      "instructions"=>{let files=["AGENTS.md","CLAUDE.md",".cursorrules","CONTRIBUTING.md","README.md"];
          let mut instr=None;for name in files{let p=self.ws.resolve(name)?;if p.exists(){instr=Some(fs::read_to_string(p)?);break}}
          Ok(json!({"instructions":instr}))},
      "architecture"=>Ok(mem["notes"]["architecture"].clone()),
      "conventions"=>{let notes=mem["notes"].as_object();Ok(json!({"conventions":notes.into_iter().flat_map(|m|m.values()).filter(|n|n["kind"]=="convention").cloned().collect::<Vec<_>>()}))},
      "recent_changes"=>Ok(json!({"changes":[]})),
      "note_save"|"note_update"=>{
          if !mem["notes"].is_object(){mem["notes"]=json!({})};
          let key=a("key");if key.is_empty(){bail!("key required")}
          let kind=if a("kind").is_empty(){"note"}else{a("kind")};
          mem["notes"][key]=json!({"key":key,"value":a("value"),"kind":kind,"updated":chrono::Utc::now().timestamp()});
          self.save("memory",&mem)?;Ok(json!({"key":key,"kind":kind}))},
      "note_read"=>Ok(mem["notes"][a("key")].clone()),
      "note_delete"=>{let removed=mem["notes"].as_object_mut().and_then(|o|o.remove(a("key"))).is_some();
          self.save("memory",&mem)?;Ok(json!({"deleted":removed}))},
      "note_list"=>{let kind=kw["kind"].as_str();Ok(json!({"notes":mem["notes"].as_object().into_iter().flat_map(|o|o.values())
          .filter(|v|kind.is_none_or(|k|v["kind"]==k)).collect::<Vec<_>>()}))},
      "task_create"=>{let id=Uuid::new_v4().simple().to_string()[..12].to_string();
          if !tasks["tasks"].is_object(){tasks["tasks"]=json!({})};
          tasks["tasks"][&id]=json!({"id":id,"title":a("title"),"description":a("description"),"status":"pending","parent":kw["parent"],"result":null});
          self.save("tasks",&tasks)?;Ok(tasks["tasks"][&id].clone())},
      "task_get"=>Ok(tasks["tasks"][a("task_id")].clone()),
      "task_list"=>{let status=kw["status"].as_str();Ok(json!({"tasks":tasks["tasks"].as_object().into_iter().flat_map(|o|o.values())
          .filter(|v|status.is_none_or(|k|v["status"]==k)).collect::<Vec<_>>()}))},
      "task_update"|"task_complete"|"task_cancel"=>{let id=a("task_id");
          if tasks["tasks"].get(id).is_none(){bail!("task not found: {id}")}
          if action=="task_complete"{tasks["tasks"][id]["status"]=json!("done");tasks["tasks"][id]["result"]=kw["result"].clone();}
          else if action=="task_cancel"{tasks["tasks"][id]["status"]=json!("cancelled");}
          else{for key in ["title","description","status","result"]{if let Some(v)=kw.get(key){tasks["tasks"][id][key]=v.clone()}}}
          let result=tasks["tasks"][id].clone();self.save("tasks",&tasks)?;Ok(result)},
      _=>bail!("unknown project action {action}")
    }
 }
}
