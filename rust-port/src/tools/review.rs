//! Deterministic review heuristics for bugs, security, tests, style, performance.
use super::{terminal::Terminal,workspace::Workspace};
use anyhow::{Result,bail};
use regex::Regex;
use serde_json::{json,Value};
use std::{fs,sync::Arc};
#[derive(Clone)]pub struct Reviewer{ws:Arc<Workspace>,term:Terminal}
impl Reviewer {
 pub fn new(ws:Arc<Workspace>,term:Terminal)->Self{Self{ws,term}}
 pub async fn dispatch(&self,scope:&str,kw:&Value)->Result<Value>{
   let mut files:Vec<String>=Vec::new();
   if scope=="file"{files.push(kw["target"].as_str().unwrap_or("").into())}
   else if scope=="diff"{
       let base=kw["ref"].as_str().unwrap_or("HEAD");let r=self.term.run(&format!("git diff --name-only {}",super::git::quote(base)),None,30,None).await?;
       files=r["stdout"].as_str().unwrap_or("").lines().map(str::to_owned).collect();
   }else if scope=="pr"{let n=kw["number"].as_u64().unwrap_or(0);let r=self.term.run(&format!("gh pr diff {n} --name-only"),None,30,None).await?;
       files=r["stdout"].as_str().unwrap_or("").lines().map(str::to_owned).collect();
   }else{bail!("unknown review scope {scope}")}
   let checks=kw["checks"].as_array().map(|v|v.iter().filter_map(Value::as_str).collect::<Vec<_>>())
       .unwrap_or_else(||vec!["bugs","security","performance","style","tests"]);
   let mut findings=Vec::new();let mut counts=[0usize;3];
   for file in &files {
       let p=match self.ws.resolve(file){Ok(p)=>p,Err(_)=>continue};if !p.is_file(){continue}
       let content=fs::read_to_string(p).unwrap_or_default();
       for (i,line) in content.lines().enumerate(){let l=line.trim();
           for check in &checks {
               let patterns=match *check{
                  "bugs"=>vec![(r"\bexcept\s*:","Bare except swallows critical errors","warning"),
                      (r"\bdef\s+\w+\([^)]*=\s*\[\]","Mutable default argument","warning")],
                  "security"=>vec![(r"\beval\s*\(","Dynamic eval call","error"),
                     (r"\bexec\s*\(","Dynamic exec call","error"),
                     (r"\bos\.system\s*\(","OS command execution","warning"),
                     (r"shell\s*=\s*True","Shell-mode subprocess","warning"),
                     (r"verify\s*=\s*False","TLS verification disabled","error"),
                     (r#"(?i)password\s*=\s*['"][^'"]+['"]"#,"Possible embedded password","error")],
                  "performance"=>vec![(r"\.objects\.all\(\)","Possible N+1 database query","warning")],
                  "style"=>vec![(r"TODO|FIXME","Unresolved TODO/FIXME","info")],
                  "tests"=>vec![(r"\bassert\s+True\b","Unconditional test assertion","warning"),
                     (r"\bexpect\(true\)\.toBe\(true\)","Unconditional test assertion","warning")],
                  _=>vec![]};
               for (pattern,message,severity) in patterns{
                   if Regex::new(pattern)?.is_match(l){let index=match severity{"error"=>0,"warning"=>1,_=>2};counts[index]+=1;
                       findings.push(json!({"file":file,"line":i+1,"severity":severity,"category":check,
                        "message":message,"evidence":line}));}
               }
           }
       }
   }
   Ok(json!({"scope":scope,"files":files,"findings":findings,"counts":{"error":counts[0],"warning":counts[1],"info":counts[2]}}))
 }
}
