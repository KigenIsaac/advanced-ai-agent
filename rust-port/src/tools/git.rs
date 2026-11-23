//! Git and GitHub CLI operations: parity with Python codeagent/vcs.py.
use super::{terminal::Terminal,workspace::Workspace};
use anyhow::{bail,Result};
use serde_json::{json,Value};
use std::sync::Arc;

#[derive(Clone)]pub struct Git {term:Terminal,ws:Arc<Workspace>}
impl Git {
 pub fn new(term:Terminal,ws:Arc<Workspace>)->Self{Self{term,ws}}
 async fn git(&self,args:&[&str])->Result<Value>{
    // Git args pass through a token-escaped shell string for cross-platform compatibility.
    let cmd=format!("git {}",args.iter().map(|s|quote(s)).collect::<Vec<_>>().join(" "));
    let result=self.term.run(&cmd,None,120,None).await?;
    if result["exit_code"].as_i64().unwrap_or(1)!=0{bail!("git failed: {}",result["stderr"])}Ok(result)
 }
 pub async fn dispatch(&self,action:&str,kw:&Value)->Result<Value>{
    let arg=|k:&str|kw[k].as_str().unwrap_or("");
    let path=arg("path");let branch=arg("branch");
    let response=match action{
       "status"=>self.git(&["status","--porcelain=v2","--branch"]).await?,
       "diff"=>{let mut a=vec!["diff"];if kw["staged"].as_bool().unwrap_or(false){a.push("--cached")}
          if !path.is_empty(){a.extend(["--",path]);}self.git(&a).await?},
       "log"=>self.git(&["log","--pretty=format:%H%x09%an%x09%ae%x09%aI%x09%s","-20"]).await?,
       "show"=>self.git(&["show",arg("commit")]).await?,
       "branch_current"=>self.git(&["rev-parse","--abbrev-ref","HEAD"]).await?,
       "branch_list"=>self.git(&["branch"]).await?,
       "branch_create"=>{let mut a=vec!["checkout","-b",branch];if !arg("base").is_empty(){a.push(arg("base"))}self.git(&a).await?},
       "branch_switch"=>self.git(&["checkout",branch]).await?,
       "branch_delete"=>self.git(&["branch",if kw["force"]==true{"-D"}else{"-d"},branch]).await?,
       "add"=>{let files=kw["files"].as_array().map(|a|a.iter().filter_map(Value::as_str).collect::<Vec<_>>())
            .unwrap_or_else(||vec!["."]);let mut a=vec!["add"];a.extend(files);self.git(&a).await?},
       "reset"=>self.git(&["reset"]).await?,
       "commit"=>{self.git(&["add","."]).await?;self.git(&["commit","-m",arg("message")]).await?},
       "amend"=>{let mut a=vec!["commit","--amend"];if arg("message").is_empty(){a.push("--no-edit")}
            else{a.extend(["-m",arg("message")]);}self.git(&a).await?},
       "stash"=>{if arg("message").is_empty(){self.git(&["stash"]).await?}else{self.git(&["stash","push","-m",arg("message")]).await?}},
       "stash_pop"=>self.git(&["stash","pop"]).await?,
       "stash_list"=>self.git(&["stash","list"]).await?,
       "revert"=>self.git(&["revert","--no-edit",arg("commit")]).await?,
       "reset_hard"=>self.git(&["reset","--hard",if arg("commit").is_empty(){"HEAD"}else{arg("commit")}]).await?,
       "clean"=>self.git(&["clean",if kw["force"]==true{"-fdx"}else{"-fd"}]).await?,
       "merge"=>self.git(&["merge",branch]).await?,
       "rebase"=>self.git(&["rebase",branch]).await?,
       "cherry_pick"=>self.git(&["cherry-pick",arg("commit")]).await?,
       "blame"=>self.git(&["blame","--line-porcelain",path]).await?,
       "file_history"=>self.git(&["log","--oneline","--",path]).await?,
       "clone"=>{let url=arg("url");let dest=arg("destination");self.term.run(&format!("git clone {} {}",quote(url),quote(dest)),None,300,None).await?},
       "pull"=>self.git(&["pull"]).await?,"push"=>self.git(&["push"]).await?,
       "pr_list"=>self.gh(&["pr","list","--json","number,title,state"]).await?,
       "pr_get"=>self.gh(&["pr","view",arg("number"),"--json","number,title,body,state"]).await?,
       "pr_diff"=>self.gh(&["pr","diff",arg("number")]).await?,
       "pr_create"=>self.gh(&["pr","create","--title",arg("title"),"--body",arg("body")]).await?,
       "pr_update"=>self.gh(&["pr","edit",arg("number"),"--title",arg("title"),"--body",arg("body")]).await?,
       "issue_list"=>self.gh(&["issue","list","--json","number,title,state"]).await?,
       "issue_get"=>self.gh(&["issue","view",arg("number"),"--json","number,title,body,state"]).await?,
       "issue_create"=>self.gh(&["issue","create","--title",arg("title"),"--body",arg("body")]).await?,
       "issue_comment"=>self.gh(&["issue","comment",arg("number"),"--body",arg("body")]).await?,
       _=>bail!("unknown git action: {action}")};
    let text=response["stdout"].as_str().unwrap_or("");
    Ok(match action{
        "status"=>json!({"raw":text}),"diff"=>json!({"diff":text}),"show"=>json!({"show":text}),
        "branch_current"=>json!({"branch":text.trim()}),"branch_list"=>json!({"branches":text.lines().collect::<Vec<_>>()}),
        "log"|"file_history"=>json!({"commits":text.lines().collect::<Vec<_>>()}),
        "pr_list"=>json!({"prs":text}),"issue_list"=>json!({"issues":text}),_=>response})
 }
 async fn gh(&self,parts:&[&str])->Result<Value>{let cmd=format!("gh {}",parts.iter().map(|s|quote(s)).collect::<Vec<_>>().join(" "));
    self.term.run(&cmd,None,120,None).await}
}
pub fn quote(input:&str)->String{
    if cfg!(windows){format!("\"{}\"",input.replace('"',"\\\""))}
    else{format!("'{}'",input.replace('\'',"'\\''"))}
}
