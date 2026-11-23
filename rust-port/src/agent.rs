//! Long-horizon LLM/tool execution with persistent conversation context.
use crate::{config::{Config,MAX_HISTORY_CHARS,MAX_TOOL_RESULT_CHARS},llm::{LlmClient,ModelResponse,Usage},tools::ToolRegistry};
use anyhow::{bail,Result};
use serde::{Deserialize,Serialize};
use serde_json::{json,Value};
use std::sync::Arc;
use uuid::Uuid;

#[derive(Debug,Clone,Default,Serialize,Deserialize)]pub struct Conversation{
 pub messages:Vec<Value>,pub prompt_tokens:u64,pub completion_tokens:u64,pub cached_tokens:u64,pub rounds:u64,
}
impl Conversation{
 pub fn cost_estimate(&self)->f64{let uncached=self.prompt_tokens.saturating_sub(self.cached_tokens);
    uncached as f64/1_000_000.0*0.05+self.cached_tokens as f64/1_000_000.0*0.005+self.completion_tokens as f64/1_000_000.0*0.15}
 pub fn append(&mut self,msg:Value){self.messages.push(msg)}
 pub fn repair(&mut self){
    // Remove detached tool outputs and orphaned assistant calls after truncation.
    let mut out=Vec::new();let mut pending=std::collections::HashMap::<String,usize>::new();
    for msg in &self.messages {if msg["role"]=="assistant" {
        if let Some(calls)=msg["tool_calls"].as_array(){for c in calls{
            if let Some(id)=c["id"].as_str(){pending.insert(id.to_owned(),out.len());}}}
        out.push(msg.clone());
    }else if msg["role"]=="tool" {if let Some(id)=msg["tool_call_id"].as_str(){if pending.contains_key(id){out.push(msg.clone());}}}
    else{out.push(msg.clone());}}
    self.messages=out;
 }
 pub fn truncate(&mut self){let size=self.messages.iter().map(|v|v.to_string().len()).sum::<usize>();
    if self.messages.len()<24||size<MAX_HISTORY_CHARS{return}
    let head=self.messages.iter().take(2).cloned().collect::<Vec<_>>();
    let tail=self.messages.iter().rev().take(16).cloned().collect::<Vec<_>>().into_iter().rev().collect::<Vec<_>>();
    let omitted=self.messages.len().saturating_sub(head.len()+tail.len());
    self.messages=head;self.messages.push(json!({"role":"system","content":format!("Earlier conversation context was shortened to fit the budget; {omitted} messages omitted. Reread project notes and repository before making assumptions.")}));
    self.messages.extend(tail);self.repair();
 }
}
#[derive(Debug,Clone,Default)]pub struct AgentOutcome {pub content:String,pub stopped_by_limit:bool,pub tool_calls:u64,pub successful_tests:u64,pub failed_tests:u64,pub tool_errors:u64,pub usage:Usage}
#[derive(Clone)]pub struct Agent{config:Arc<Config>,client:LlmClient,tools:ToolRegistry,
    pub conversation:Conversation,system_prompt:String,max_rounds:usize,max_tokens:u32}
impl Agent{
 pub fn new(config:Arc<Config>,client:LlmClient,tools:ToolRegistry,system_prompt:String,max_rounds:usize,max_tokens:u32)->Self{
    Self{config,client,tools,conversation:Conversation::default(),system_prompt,max_rounds,max_tokens}
 }
 pub fn session(config:Arc<Config>,client:LlmClient,tools:ToolRegistry)->Self{
    let prompt=system_prompt(&config);let max_rounds=config.max_rounds;let max_tokens=config.max_tokens;
    Self::new(config,client,tools,prompt,max_rounds,max_tokens)
 }
 pub async fn send(&mut self,user_text:&str)->Result<AgentOutcome>{
    self.conversation.append(json!({"role":"user","content":user_text}));self.conversation.truncate();
    let schemas=self.tools.schemas().await;let mut out=AgentOutcome::default();
    for _ in 0..self.max_rounds{
        let mut messages=vec![json!({"role":"system","content":self.system_prompt})];
        messages.extend(self.conversation.messages.iter().cloned());
        let response:ModelResponse=self.client.chat(&messages,&schemas,self.max_tokens).await?;
        self.conversation.rounds+=1;
        self.conversation.prompt_tokens+=response.usage.input_tokens;
        self.conversation.completion_tokens+=response.usage.output_tokens;
        self.conversation.cached_tokens+=response.usage.cached_tokens;
        out.usage.input_tokens+=response.usage.input_tokens;
        out.usage.output_tokens+=response.usage.output_tokens;
        out.usage.cached_tokens+=response.usage.cached_tokens;
        out.content=response.content.clone();
        let calls=response.tool_calls;
        let as_calls=calls.iter().map(|call|json!({"id":call.id,"type":"function",
             "function":{"name":call.name,"arguments":call.arguments.to_string()}})).collect::<Vec<_>>();
        let mut assistant=json!({"role":"assistant","content":response.content});
        if !as_calls.is_empty(){assistant["tool_calls"]=json!(as_calls)}
        self.conversation.append(assistant);
        if calls.is_empty(){return Ok(out)}
        for call in calls {
            out.tool_calls+=1;
            let result=self.tools.dispatch(&call.name,&call.arguments).await;
            if result.get("error").is_some(){out.tool_errors+=1;}
            if call.name=="test"||call.name=="build"{
                if result["exit_code"].as_i64()==Some(0){out.successful_tests+=1}
                if result["exit_code"].as_i64().is_some_and(|n|n!=0){out.failed_tests+=1}
            }
            let mut value=result.to_string();
            if value.chars().count()>MAX_TOOL_RESULT_CHARS {value=value.chars().take(MAX_TOOL_RESULT_CHARS).collect();value.push_str("\n… [truncated]");}
            self.conversation.append(json!({"role":"tool","tool_call_id":call.id,"name":call.name,"content":value}));
            if self.config.verbose{eprintln!("[tool {}] {}",call.name,result);}
        }
        self.conversation.truncate();
    }
    out.stopped_by_limit=true;
    if out.content.is_empty(){out.content=format!("INCOMPLETE: maximum rounds ({}) reached",self.max_rounds)}
    Ok(out)
 }
 pub fn reset(&mut self){self.conversation=Conversation::default()}
}

pub fn system_prompt(config:&Config)->String{
 format!(r#"You are {}, a terminal-based autonomous agent that can search the web, browse pages, and operate on a software project.
# Environment
- Model: {}
- Agent: {}
- Working directory: {}
# Operating principles
1. Use tools to inspect or modify projects; never invent file contents, URLs, tool outputs, success, or verification evidence.
2. Read before you write. Prefer previewed structured edits rather than whole-file replacement.
3. For multi-step tasks, plan briefly and act. Preserve the user's complete specification.
4. Return tool results to the conversation before the next action.
5. Never claim success without evidence; cite file paths, test output and actual results.
6. When a tool call fails, inspect errors, adjust and avoid repeating failing calls.
7. Preserve constraints and essential requirements across long tasks. Use project memory and repository instructions.
8. Destructive actions require explicit host authorization; do not attempt to bypass safeguards via terminal or shell.
# Long-running commands
Use terminal.background for servers/watchers, inspect their output, and terminate when finished. Never block a foreground command on an indefinite server.
# Tool arguments
Pass a single valid JSON object, with escaped paths. Do not invent action names.
# Custom instructions
{}
"#,config.agent_name,config.model,config.agent_name,config.project_root.display(),config.custom_instructions)
}

pub fn extract_json(src:&str)->Result<Value>{
    if let Ok(v)=serde_json::from_str(src.trim()){return Ok(v)}
    let mut start=None;let mut depth=0usize;let mut quoted=false;let mut escaped=false;
    for (i,ch) in src.char_indices(){
        if start.is_none(){if ch=='{'{start=Some(i);depth=1;}continue}
        if quoted{if escaped{escaped=false}else if ch=='\\'{escaped=true}else if ch=='"'{quoted=false}continue}
        if ch=='"'{quoted=true}else if ch=='{'{depth+=1}else if ch=='}'{depth=depth.saturating_sub(1);
            if depth==0{return Ok(serde_json::from_str(&src[start.unwrap()..i+1])?);}}
    }
    bail!("no valid JSON object found")
}
#[cfg(test)]mod tests{use super::*;
 #[test]fn json_extraction(){let value=extract_json("```json\n{\"x\":1}\n```").unwrap();assert_eq!(value["x"],1);}
 #[test]fn history_repair(){let mut c=Conversation::default();c.messages=vec![json!({"role":"tool","tool_call_id":"missing","content":"x"})];c.repair();assert!(c.messages.is_empty())}
}
