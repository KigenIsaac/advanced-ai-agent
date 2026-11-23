//! Plan/dependency graph, parallel workers, acceptance verification, execution
//! persistence, and final summary. Avoids Python scheduler's false-done/deadlock paths.
use crate::{agent::{Agent,AgentOutcome,extract_json},config::{Config,MIN_TODOS,MAX_TODOS,MIN_SUBTASKS,TARGET_TODOS,TARGET_SUBTASKS,PLANNER_TOKENS,REVIEWER_TOKENS,WORKER_ROUNDS},llm::LlmClient,tools::ToolRegistry};
use anyhow::{bail,Result};
use serde::{Deserialize,Serialize};
use serde_json::{json,Value};
use std::{collections::{HashMap,HashSet},fs,path::PathBuf,sync::Arc,time::{Instant,SystemTime}};
use tokio::sync::Mutex;
use uuid::Uuid;

#[derive(Debug,Clone,Default,Serialize,Deserialize)]pub struct Subtask{
 pub title:String,#[serde(default)]pub description:String,
 #[serde(default="pending")]pub status:String,#[serde(default)]pub result:String}
fn pending()->String{"pending".into()}
#[derive(Debug,Clone,Default,Serialize,Deserialize)]pub struct Todo {
 pub id:String,pub title:String,#[serde(default)]pub description:String,
 #[serde(default)]pub acceptance:String,#[serde(default)]pub worker_prompt:String,
 #[serde(default)]pub depends_on:Vec<String>,#[serde(default)]pub subtasks:Vec<Subtask>,
 #[serde(default="pending")]pub status:String,#[serde(default)]pub assigned_to:Option<String>,
 #[serde(default)]pub result:String,#[serde(default)]pub error:String,
 #[serde(default)]pub rounds:u64,#[serde(default)]pub elapsed:f64,
 #[serde(default)]pub verified:bool
}
#[derive(Debug,Clone,Default,Serialize,Deserialize)]pub struct TodoList{
 pub summary:String,pub todos:Vec<Todo>,#[serde(default)]pub original_task:String,
 #[serde(default)]pub report:String,#[serde(default)]pub created_at:i64,
 #[serde(default)]pub finished_at:Option<i64>
}
impl TodoList{pub fn counts(&self)->HashMap<String,usize>{let mut out=HashMap::new();for t in &self.todos{
    *out.entry(t.status.clone()).or_insert(0)+=1;}out}}

pub struct Planner{config:Arc<Config>,client:LlmClient,tools:ToolRegistry}
impl Planner{
 pub fn new(config:Arc<Config>,client:LlmClient,tools:ToolRegistry)->Self{Self{config,client,tools}}
 pub async fn plan(&self,task:&str)->Result<TodoList>{
    let prompt=planner_prompt(&self.config);
    let mut agent=Agent::new(self.config.clone(),self.client.clone(),self.tools.clone(),prompt,30,PLANNER_TOKENS);
    let mut output=agent.send(task).await?;
    let mut result=extract_json(&output.content)?;
    for attempt in 0..2{
       let current=result["todos"].as_array().cloned().unwrap_or_default();
       if current.len()>=MIN_TODOS && current.iter().all(|v|v["subtasks"].as_array().is_some_and(|s|s.len()>=MIN_SUBTASKS)){break}
       output=agent.send(&format!("Expand your previous plan to at least {TARGET_TODOS} top-level todos each with at least {TARGET_SUBTASKS} substantive subtasks. Respond with ONLY complete JSON. Revision {} of 2.",attempt+1)).await?;
       let parsed=extract_json(&output.content)?;
       if parsed["todos"].as_array().is_some_and(|a|a.len()>=current.len()){result=parsed}
    }
    let mut todos:Vec<Todo>=serde_json::from_value(result["todos"].clone())?;
    todos.truncate(MAX_TODOS);
    let plan=TodoList{summary:result["summary"].as_str().unwrap_or("").into(),todos,
       original_task:task.into(),report:String::new(),created_at:chrono::Utc::now().timestamp(),finished_at:None};
    validate_graph(&plan.todos)?;
    Ok(plan)
 }
}
fn validate_graph(todos:&[Todo])->Result<()> {
    if todos.is_empty(){bail!("planner returned empty tasks")}
    let mut ids=HashSet::new();for task in todos{if task.id.is_empty()||!ids.insert(task.id.clone()){bail!("duplicate or empty task ID: {}",task.id)}}
    for task in todos {for dep in &task.depends_on {
        if !ids.contains(dep){bail!("unknown dependency {} in {}",dep,task.id)}
        if dep==&task.id{bail!("task {} depends on itself",task.id)}
    }}
    let map=todos.iter().map(|t|(t.id.clone(),t.depends_on.clone())).collect::<HashMap<_,_>>();
    fn dfs(id:&str,map:&HashMap<String,Vec<String>>,visiting:&mut HashSet<String>,visited:&mut HashSet<String>)->Result<()> {
        if visited.contains(id){return Ok(())}
        if !visiting.insert(id.to_owned()){bail!("cycle detected at {id}")}
        if let Some(deps)=map.get(id){for dep in deps{dfs(dep,map,visiting,visited)?}}
        visiting.remove(id);visited.insert(id.to_owned());Ok(())
    }
    let mut visiting=HashSet::new();let mut visited=HashSet::new();
    for task in todos{dfs(&task.id,&map,&mut visiting,&mut visited)?}Ok(())
}

pub struct Swarm{config:Arc<Config>,client:LlmClient,tools:ToolRegistry}
impl Swarm{
 pub fn new(config:Arc<Config>,client:LlmClient,tools:ToolRegistry)->Self{Self{config,client,tools}}
 pub async fn run(&self,task:&str)->Result<TodoList>{
    let planner=Planner::new(self.config.clone(),self.client.clone(),self.tools.clone());
    let plan=planner.plan(task).await?;self.execute(plan).await
 }
 pub async fn execute(&self,plan:TodoList)->Result<TodoList>{
    validate_graph(&plan.todos)?;
    let shared=Arc::new(Mutex::new(plan));
    let workers=self.config.swarm_workers.max(1);
    let mut slots=Vec::new();
    for index in 0..workers {
      let state=shared.clone();let cfg=self.config.clone();let client=self.client.clone();let tools=self.tools.clone();
      slots.push(tokio::spawn(async move{
        loop{
           let next={let mut plan=state.lock().await;
               let done=plan.todos.iter().filter(|t|t.status=="done").map(|t|t.id.clone()).collect::<HashSet<_>>();
               let failed=plan.todos.iter().filter(|t|t.status=="failed"||t.status=="skipped"||t.status=="incomplete").map(|t|t.id.clone()).collect::<HashSet<_>>();
               for task in plan.todos.iter_mut().filter(|t|t.status=="pending"){
                   if task.depends_on.iter().any(|dep|failed.contains(dep)){
                       task.status="skipped".into();task.error="dependency failed or incomplete".into();}}
               if let Some(todo)=plan.todos.iter_mut().find(|t|t.status=="pending"&&t.depends_on.iter().all(|d|done.contains(d))){
                   todo.status="in_progress".into();todo.assigned_to=Some(format!("W{:02}",index+1));
                   Some(todo.clone())
               }else{None}
           };
           if let Some(todo)=next{
              let started=Instant::now();
              let prompt=worker_prompt(&cfg,&todo);
              let mut worker=Agent::new(cfg.clone(),client.clone(),tools.clone(),prompt,WORKER_ROUNDS,cfg.max_tokens);
              let result=worker.send(&format!("Execute todo {}. Begin now.",todo.id)).await;
              let mut plan=state.lock().await;
              if let Some(target)=plan.todos.iter_mut().find(|t|t.id==todo.id){
                  target.elapsed=started.elapsed().as_secs_f64();target.rounds=worker.conversation.rounds;
                  match result{
                    Ok(out)=>{
                        target.result=out.content.clone();let verified=verify_completion(&todo,&out);
                        target.verified=verified;
                        target.status=if out.content.trim_start().to_uppercase().starts_with("BLOCKED:"){"failed"}
                            else if verified{"done"}else{"incomplete"}.into();
                        if !verified{target.error=if out.stopped_by_limit{"worker round limit reached".into()}
                            else{"missing verified completion/evidence".into()};}
                    },
                    Err(error)=>{target.status="failed".into();target.error=error.to_string();}
                  }
              }
           }else{
              let finished={let plan=state.lock().await;plan.todos.iter().all(|t|["done","failed","skipped","incomplete"].contains(&t.status.as_str()))};
              if finished{break}
              tokio::time::sleep(std::time::Duration::from_millis(200)).await;
           }
        }
        Ok::<(),anyhow::Error>(())
      }));
    }
    for slot in slots {slot.await??;}
    let mut out=Arc::try_unwrap(shared).map_err(|_|anyhow::anyhow!("swarm state still shared"))?.into_inner();
    out.finished_at=Some(chrono::Utc::now().timestamp());
    out.report=self.summarize(&out).await.unwrap_or_else(|e|format!("Summary generation unavailable: {e}; results persisted."));
    self.save(&out)?;Ok(out)
 }
 async fn summarize(&self,plan:&TodoList)->Result<String>{
    let prompt="You are the Summarizer for an autonomous agent swarm. Clearly distinguish verified done, incomplete, failed and skipped items. Do not claim the original objective completed unless all acceptance checks passed. Include evidence for each completed todo.";
    let mut agent=Agent::new(self.config.clone(),self.client.clone(),self.tools.clone(),prompt.into(),3,REVIEWER_TOKENS);
    let compact=plan.todos.iter().map(|t|json!({"id":t.id,"title":t.title,"status":t.status,"verified":t.verified,
        "result":t.result.chars().take(1500).collect::<String>(),"error":t.error})).collect::<Vec<_>>();
    Ok(agent.send(&json!({"task":plan.original_task,"plan_summary":plan.summary,"todo_results":compact}).to_string()).await?.content)
 }
 fn save(&self,plan:&TodoList)->Result<PathBuf>{let root=self.config.home.join("swarm");fs::create_dir_all(&root)?;
    let name=format!("{}-{}.json",chrono::Utc::now().format("%Y%m%d-%H%M%S"),&Uuid::new_v4().simple().to_string()[..6]);
    let path=root.join(name);fs::write(&path,serde_json::to_vec_pretty(plan)?)?;Ok(path)
 }
}
fn verify_completion(todo:&Todo,out:&AgentOutcome)->bool{
    if out.stopped_by_limit||!out.content.trim_start().starts_with("DONE:"){return false}
    let acceptance=todo.acceptance.to_lowercase();
    if (acceptance.contains("test")||acceptance.contains("build")||acceptance.contains("lint"))&&out.successful_tests==0{return false}
    if out.failed_tests>out.successful_tests{return false}
    true
}
fn planner_prompt(config:&Config)->String{format!(r#"You are the Planner for a swarm of autonomous coding agents.
# Task decomposition rules
1. Produce at least {MIN_TODOS} top-level todos, targeting {TARGET_TODOS} for complex tasks, maximum {MAX_TODOS}.
2. Each todo has at least {MIN_SUBTASKS} substantial subtasks, targeting {TARGET_SUBTASKS}.
3. Each todo MUST have independently verifiable acceptance criteria and an exhaustive worker_prompt.
4. Order dependencies first and use depends_on to serialize changes to the same files.
5. Cover setup, architecture, backend, frontend, tests, docs, verification and requested integrations.
6. Preserve every user requirement; do not replace the requested system with an MVP.
7. Respond with ONLY valid JSON:
{{"summary":"...","todos":[{{"id":"T01","title":"...","description":"...","acceptance":"...","worker_prompt":"...","depends_on":[],"subtasks":[{{"title":"...","description":"..."}}]}}]}}
# Project
{}
"#,config.project_root.display())}
fn worker_prompt(config:&Config,todo:&Todo)->String{format!(r#"You are one worker in the autonomous engineering swarm.
# Task
ID: {}
Title: {}
Description: {}
Acceptance: {}
Dependencies: {:?}
Mission: {}
Subtasks:
{}
Original project root: {}
# Rules
1. Inspect relevant files before changing them. Preserve user requirements and project conventions.
2. Implement your assigned changes completely; test with executable commands, check results and inspect diffs.
3. After verified completion respond exactly 'DONE:' followed by file/test evidence.
4. Otherwise respond exactly 'BLOCKED:' with a detailed blocker. Never claim success without test output.
5. Keep secrets secure and use host-authorized actions only.
"#,todo.id,todo.title,todo.description,todo.acceptance,todo.depends_on,todo.worker_prompt,
    todo.subtasks.iter().enumerate().map(|(i,s)|format!("{}. {}: {}",i+1,s.title,s.description)).collect::<Vec<_>>().join("\n"),
    config.project_root.display())}
#[cfg(test)]mod tests {use super::*;
 #[test]fn catches_missing_dependency(){let tasks=vec![Todo{id:"T01".into(),depends_on:vec!["missing".into()],..Default::default()}];assert!(validate_graph(&tasks).is_err());}
 #[test]fn catches_cycle(){let tasks=vec![Todo{id:"T01".into(),depends_on:vec!["T02".into()],..Default::default()},
      Todo{id:"T02".into(),depends_on:vec!["T01".into()],..Default::default()}];assert!(validate_graph(&tasks).is_err());}
 #[test]fn completion_requires_done_prefix(){let task=Todo::default();let out=AgentOutcome{content:"Maybe done".into(),..Default::default()};assert!(!verify_completion(&task,&out));}
}
