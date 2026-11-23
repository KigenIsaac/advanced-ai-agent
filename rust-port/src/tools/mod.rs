//! LLM-facing tool registry. The 16 original code tool names and web_search
//! keep their Python names and action strings for prompt compatibility.
use crate::config::Config;
use anyhow::{bail,Result};
use serde_json::{json,Value};
use std::{collections::BTreeSet,sync::Arc};
use tokio::sync::{Mutex,RwLock};

pub mod browser;pub mod documents;pub mod debug;pub mod deploy;pub mod editor;pub mod git;
pub mod lsp;pub mod packages;pub mod project;pub mod review;pub mod runtime;
pub mod sandbox;pub mod terminal;pub mod testing;pub mod web;pub mod workspace;

use browser::Browser;use debug::Debugger;use deploy::Deployment;use editor::Editor;
use git::Git;use lsp::Lsp;use packages::Packages;use project::ProjectMemory;
use review::Reviewer;use runtime::Runtime;use sandbox::Sandboxes;
use terminal::Terminal;use testing::Testing;use web::WebSearch;use workspace::Workspace;

pub type ToolResult=Value;
#[derive(Clone)]pub struct ToolRegistry{
 pub workspace:Arc<Workspace>,pub terminal:Terminal,pub editor:Editor,pub code:Lsp,
 pub git:Git,pub testing:Testing,pub packages:Packages,pub runtime:Runtime,
 pub browser:Browser,pub web:WebSearch,pub project:ProjectMemory,pub review:Reviewer,
 pub debug:Debugger,pub sandbox:Sandboxes,pub deploy:Deployment,
 enabled:Arc<RwLock<BTreeSet<String>>>,write_lock:Arc<Mutex<()>>,web_enabled:bool,code_enabled:bool,
}
impl ToolRegistry{
 pub fn new(cfg:&Config)->Result<Self>{let ws=Arc::new(Workspace::new(&cfg.project_root)?);
    let allow=std::env::var("AGENT_ALLOW_DESTRUCTIVE").ok().as_deref()==Some("1");
    let term=Terminal::new(ws.clone(),allow);let editor=Editor::new(ws.clone());
    let code=Lsp::new(ws.clone());let git=Git::new(term.clone(),ws.clone());
    let testing=Testing::new(ws.clone(),term.clone());let packages=Packages::new(term.clone(),ws.clone());
    let runtime=Runtime::new(ws.clone(),term.clone())?;let browser=Browser::new(cfg.project_root.join("downloads"),cfg.browser_headless);
    let web=WebSearch::new()?;let project=ProjectMemory::new(ws.clone())?;
    let review=Reviewer::new(ws.clone(),term.clone());let debug=Debugger::new(ws.clone(),term.clone());
    let sandbox=Sandboxes::new(ws.clone(),term.clone());let deploy=Deployment::new(term.clone(),ws.clone(),allow);
    let enabled=TOOL_ACTIONS.iter().filter(|(name,_)|{
        if *name=="web_search"||*name=="browser" {cfg.web_enabled}
        else{cfg.code_enabled}
    }).map(|(name,_)|name.to_string()).collect();
    Ok(Self{workspace:ws,terminal:term,editor,code,git,testing,packages,runtime,browser,web,project,review,debug,
       sandbox,deploy,enabled:Arc::new(RwLock::new(enabled)),write_lock:Arc::new(Mutex::new(())),
       web_enabled:cfg.web_enabled,code_enabled:cfg.code_enabled})
 }
 pub async fn schemas(&self)->Vec<Value>{let enabled=self.enabled.read().await;
    TOOL_ACTIONS.iter().filter(|(n,_)|enabled.contains(*n)).map(|(name,actions)|{
        let props=json!({
         "action":{"type":"string","enum":actions},"query":{"type":"string"},
         "path":{"type":"string"},"file":{"type":"string"},"content":{"type":"string"},
         "command":{"type":"string"},"cwd":{"type":"string"},"timeout":{"type":"integer"},
         "start_line":{"type":"integer"},"end_line":{"type":"integer"},"line":{"type":"integer"},"col":{"type":"integer"},
         "old_text":{"type":"string"},"new_text":{"type":"string"},"source":{"type":"string"},"destination":{"type":"string"},
         "pattern":{"type":"string"},"symbol":{"type":"string"},"name":{"type":"string"},"new_name":{"type":"string"},
         "recursive":{"type":"boolean"},"dry_run":{"type":"boolean"},"count":{"type":"integer"},
         "url":{"type":"string"},"url_b":{"type":"string"},"method":{"type":"string"},"headers":{"type":"object"},
         "body":{"type":"string"},"json_body":{"type":"object"},"expected_status":{"type":"integer"},
         "selector":{"type":"string"},"text":{"type":"string"},"value":{"type":"string"},"key":{"type":"string"},
         "kind":{"type":"string"},"max_results":{"type":"integer"},"page":{"type":"integer"},
         "domains":{"type":"array","items":{"type":"string"}},"exclude_domains":{"type":"array","items":{"type":"string"}},
         "date_from":{"type":"string"},"date_to":{"type":"string"},"language":{"type":"string"},"country":{"type":"string"},
         "process_id":{"type":"string"},"sandbox_id":{"type":"string"},"backend":{"type":"string"},"image":{"type":"string"},
         "snapshot":{"type":"string"},"test_name":{"type":"string"},"target_file":{"type":"string"},"output_file":{"type":"string"},
         "scope":{"type":"string"},"target":{"type":"string"},"checks":{"type":"array","items":{"type":"string"}},
         "ref":{"type":"string"},"provider":{"type":"string"},"environment":{"type":"string"},"package":{"type":"string"},
         "manager":{"type":"string"},"dev":{"type":"boolean"},"table":{"type":"string"},"readonly":{"type":"boolean"},
         "limit":{"type":"integer"},"title":{"type":"string"},"description":{"type":"string"},"task_id":{"type":"string"},
         "status":{"type":"string"},"result":{"type":"string"},"parent":{"type":"string"},"file_path":{"type":"string"},
         "tab_id":{"type":"string"},"script":{"type":"string"},"full_page":{"type":"boolean"},
         "wait_for":{"type":"string"},"direction":{"type":"string"},"amount":{"type":"integer"},
         "branch":{"type":"string"},"base":{"type":"string"},"message":{"type":"string"},"commit":{"type":"string"},
         "number":{"type":"integer"},"files":{"type":"array","items":{"type":"string"}},"force":{"type":"boolean"},
         "symbols":{"type":"array","items":{"type":"string"}},"index":{"type":"integer"},"args":{"type":"array","items":{"type":"string"}},
         "glob":{"type":"string"},"regex":{"type":"boolean"},"name_pattern":{"type":"string"},"note":{"type":"string"}
        });
        json!({"type":"function","function":{"name":name,"description":format!("{} tool operations",name),
            "parameters":{"type":"object","properties":props,"required":if *name=="web_search"{vec!["query"]}else{vec!["action"]}}}})
    }).collect()
 }
 pub async fn names(&self)->Vec<String>{self.enabled.read().await.iter().cloned().collect()}
 pub async fn enable(&self,name:&str)->bool{if TOOL_ACTIONS.iter().any(|(n,_)|*n==name){self.enabled.write().await.insert(name.to_string());true}else{false}}
 pub async fn disable(&self,name:&str)->bool{self.enabled.write().await.remove(name)}
 pub async fn dispatch(&self,name:&str,kw:&Value)->Value{
    if !self.enabled.read().await.contains(name){return json!({"error":"tool_disabled","tool":name})}
    let action=kw["action"].as_str().unwrap_or("");
    // Serialize mutating tools across workers to prevent writes racing through different tool types.
    let mutating=match name{"workspace"=>["write","append","edit","delete","move","copy","mkdir","rmdir","replace_all"].contains(&action),
       "editor"|"git"|"packages"|"deploy"|"sandbox"=>true,"terminal"=>true,"database"=>action=="query"&&kw["readonly"]==false,
       "project"=>action.contains("save")||action.contains("create")||action.contains("update")||action.contains("delete")||action.contains("complete"),
       _=>false};
    let _lock=if mutating{Some(self.write_lock.lock().await)}else{None};
    match self.execute(name,action,kw).await{Ok(value)=>value,Err(err)=>json!({"error":"tool_failed","tool":name,"action":action,"message":err.to_string()})}
 }
 async fn execute(&self,name:&str,action:&str,kw:&Value)->Result<Value>{match name{
   "workspace"=>self.workspace.dispatch(action,kw),
   "terminal"=>self.terminal.dispatch(action,kw).await,
   "code"=>self.code.dispatch(action,kw).await,
   "editor"=>self.editor.dispatch(action,kw).await,
   "test"=>self.testing.test(action,kw).await,
   "build"=>self.testing.build(action,kw).await,
   "git"=>self.git.dispatch(action,kw).await,
   "packages"=>self.packages.dispatch(action,kw).await,
   "debug"=>self.debug.dispatch(action,kw).await,
   "browser"=>self.browser.dispatch(action,kw).await,
   "http"=>self.runtime.http(action,kw).await,
   "database"=>self.runtime.database(action,kw).await,
   "project"=>self.project.dispatch(action,kw).await,
   "review"=>self.review.dispatch(kw["scope"].as_str().unwrap_or(if action.is_empty(){"diff"}else{action}),kw).await,
   "sandbox"=>self.sandbox.dispatch(action,kw).await,
   "deploy"=>self.deploy.dispatch(action,kw).await,
   "web_search"=>self.web.dispatch(kw).await,
   _=>bail!("unknown tool {name}")
 }}
}

pub const TOOL_ACTIONS:&[(&str,&[&str])]=&[
("workspace", &["list","read","write","append","edit","delete","move","copy","mkdir","rmdir","exists","info","find_files","grep","replace_all","search_code","find_symbol","ast","symbols","function","class","imports","exports","dependencies","project_structure","module_graph"]),
("terminal", &["run","background","list","get","kill","wait"]),
("code", &["definition","references","hover","rename","symbols","workspace_symbols","diagnostics"]),
("editor", &["preview","apply","replace_range","insert","delete_range","rewrite","revert_last","revert_index","history","rename_symbol","add_import","remove_import","extract_function","inline_function","split_file","remove_dead_code","simplify"]),
("test", &["run","run_file","run_case","discover","coverage","failures","generate"]),
("build", &["detect","build","clean","typecheck","lint","security","complexity","dead_code"]),
("git", &["status","diff","log","show","branch_current","branch_list","branch_create","branch_switch","branch_delete","add","reset","commit","amend","stash","stash_pop","stash_list","revert","reset_hard","clean","merge","rebase","cherry_pick","blame","file_history","clone","pull","push","pr_list","pr_get","pr_diff","pr_create","pr_update","issue_list","issue_get","issue_create","issue_comment"]),
("packages", &["detect","list","install","remove","update","audit"]),
("debug", &["start","set_breakpoint","remove_breakpoint","list_breakpoints","parse_trace","explain_error","last_error","find_source","context"]),
("browser", &["open","back","forward","reload","wait","get_text","get_html","get_dom","get_metadata","get_url","get_title","get_links","get_interactive","get_accessibility","semantic_snapshot","get_headings","find_main","click","click_text","type","fill","press","scroll","hover","select","check","uncheck","upload","execute_js","evaluate","screenshot","screenshot_element","new_tab","close_tab","switch_tab","list_tabs","get_frames","get_frame_content","get_network","get_api_calls","get_response_body","download","download_link","get_downloads","get_state","save_session","restore_session","get_cookies","get_local_storage","find_element","find_button","find_link","detect_challenge","recover_navigation"]),
("http", &["request","test","compare"]),
("database", &["detect","connect_sqlite","tables","describe","query","schema","migrations","find_usage"]),
("project", &["context","instructions","architecture","conventions","recent_changes","note_save","note_read","note_update","note_delete","note_list","task_create","task_get","task_list","task_update","task_complete","task_cancel"]),
("review", &["file","diff","pr"]),
("sandbox", &["create","destroy","list","exec","snapshot","restore"]),
("deploy", &["detect","build","deploy","status","logs","rollback","health","metrics"]),
("web_search", &["web","news","images","videos","shopping"])
];
