//! Exception/traceback inspection and debugger orchestration.
use super::{terminal::Terminal,workspace::Workspace};
use anyhow::{bail,Result};
use regex::Regex;
use serde_json::{json,Value};
use std::{collections::BTreeMap,sync::Arc};
use tokio::sync::Mutex;
#[derive(Clone)]pub struct Debugger{ws:Arc<Workspace>,term:Terminal,
    breakpoints:Arc<Mutex<BTreeMap<String,Vec<usize>>>>,last:Arc<Mutex<Option<Value>>>}
impl Debugger {
 pub fn new(ws:Arc<Workspace>,term:Terminal)->Self{Self{ws,term,breakpoints:Arc::new(Mutex::new(BTreeMap::new())),last:Arc::new(Mutex::new(None))}}
 pub fn parse(&self,output:&str)->Value{
    let mut frames=Vec::new();let mut language="unknown";
    let py=Regex::new(r#"File "([^"]+)", line (\d+), in (\S+)"#).unwrap();
    let ts=Regex::new(r"([^\s:(]+)\((\d+),(\d+)\): error TS(\d+)").unwrap();
    let go=Regex::new(r"([^\s:]+):(\d+):(\d+):\s*(.*)").unwrap();
    if output.contains("Traceback (most recent call last)"){language="python";
        for c in py.captures_iter(output){frames.push(json!({"file":c[1],"line":c[2].parse::<u64>().unwrap_or(0),"function":c[3]}));}}
    else if output.contains("error TS"){language="typescript";
        for c in ts.captures_iter(output){frames.push(json!({"file":c[1],"line":c[2].parse::<u64>().unwrap_or(0),"col":c[3].parse::<u64>().unwrap_or(0),"code":c[4]}));}}
    else{for c in go.captures_iter(output){frames.push(json!({"file":c[1],"line":c[2].parse::<u64>().unwrap_or(0),"col":c[3].parse::<u64>().unwrap_or(0),"message":c[4]}));}
        if !frames.is_empty(){language="go";}}
    let last=output.lines().last().unwrap_or("");json!({"language":language,"frames":frames,"exception":last})
 }
 pub async fn dispatch(&self,action:&str,kw:&Value)->Result<Value>{let file=kw["file"].as_str().unwrap_or("");
    let line=kw["line"].as_u64().unwrap_or(0) as usize;
    match action{
      "start"=>{let script=kw["script"].as_str().unwrap_or("");let p=self.ws.resolve(script)?;
          let command=format!("python {}",super::git::quote(&p.to_string_lossy()));
          let r=self.term.run(&command,None,kw["timeout"].as_u64().unwrap_or(60),None).await?;
          *self.last.lock().await=Some(self.parse(r["stderr"].as_str().unwrap_or("")));Ok(r)},
      "set_breakpoint"=>{self.breakpoints.lock().await.entry(file.into()).or_default().push(line);
          Ok(json!({"file":file,"line":line}))},
      "remove_breakpoint"=>{if let Some(v)=self.breakpoints.lock().await.get_mut(file){v.retain(|n|*n!=line);}
          Ok(json!({"file":file,"line":line}))},
      "list_breakpoints"=>Ok(json!({"breakpoints":*self.breakpoints.lock().await})),
      "parse_trace"=>Ok(self.parse(kw["output"].as_str().unwrap_or(""))),
      "last_error"=>Ok(self.last.lock().await.clone().unwrap_or(Value::Null)),
      "find_source"|"explain_error"=>{let parsed=self.parse(kw["output"].as_str().unwrap_or(""));
          let frame=parsed["frames"].as_array().and_then(|v|v.last()).cloned().unwrap_or(Value::Null);
          let source=frame["file"].as_str().and_then(|f|self.ws.read(f,None,None).ok());
          Ok(json!({"parsed":parsed,"source":frame,"context":source}))},
      "context"=>self.ws.read(file,Some(line.saturating_sub(10).max(1)),Some(line+10)),
      _=>bail!("unknown debug action: {action}")
    }
 }
}
