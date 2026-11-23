//! Interactive terminal / one-shot command interface; Python CLI parity.
use advanced_ai_agent::{Agent,Config,LlmClient,Swarm,ToolRegistry};
use advanced_ai_agent::config::Cli;
use anyhow::{Context,Result};
use clap::Parser;
use serde_json::{json,Value};
use std::{fs,io::{self,Write},path::{Path,PathBuf},sync::Arc};

fn print_help(){println!(r#"Commands:
  /help               Show help
  /exit, /quit         Exit
  /clear              Clear terminal
  /new                Archive and start a new conversation
  /retry              Retry last user message
  /swarm [task|on|off] Run or toggle planner + worker swarm
  /swarm-status       Show latest swarm execution results
  /projects           List managed projects
  /new-project NAME   Create a managed project workspace
  /import-project DIR Import an existing workspace
  /project            Show current project
  /open NAME          Open a managed project
  /history            Show archived conversations
  /resume ID          Resume an archived conversation
  /archive            Archive current conversation
  /notes              Show project notes
  /tools              Show tool names
  /tools enable NAME  Enable a tool
  /tools disable NAME Disable a tool
  /thinking           Toggle thinking mode
  /stream             Toggle streaming request mode
  /verbose            Toggle verbose tool output
  /tokens             Show session usage and estimated model cost
  /limits             Show configured rate and execution limits
  /model NAME         Change current model
  /save PATH          Save conversation JSON
  /load PATH          Load conversation JSON
  /export PATH        Export markdown transcript
"#);}
fn slugify(input:&str)->String{let mut slug=input.to_lowercase().chars().map(|c|if c.is_ascii_alphanumeric()||c=='-'{c}else{'-'})
    .collect::<String>();while slug.contains("--"){slug=slug.replace("--","-")}slug.trim_matches('-').to_string()}
fn write_atomic(path:&Path,value:&Value)->Result<()>{if let Some(parent)=path.parent(){fs::create_dir_all(parent)?}
    let temp=path.with_extension("tmp");fs::write(&temp,serde_json::to_vec_pretty(value)?)?;
    fs::rename(temp,path)?;Ok(())}
fn conversation_path(cfg:&Config)->PathBuf {if cfg.project_root.starts_with(cfg.home.join("projects")){
        cfg.project_root.parent().unwrap_or(&cfg.home).join("conversations").join("current.json")
    }else{cfg.home.join("chats/current.json")}}
fn archive(cfg:&Config,agent:&Agent)->Result<Option<PathBuf>> {
    let src=conversation_path(cfg);if !src.exists(){return Ok(None)}
    let dest=src.with_file_name(format!("{}-{}.json",chrono::Utc::now().format("%Y%m%d%H%M%S"),uuid::Uuid::new_v4().simple()));
    let _=agent;fs::rename(src,&dest)?;Ok(Some(dest))
}
fn save_conversation(cfg:&Config,agent:&Agent)->Result<()>{write_atomic(&conversation_path(cfg),&serde_json::to_value(&agent.conversation)?)}
fn input(prompt:&str)->Result<String>{print!("{prompt}");io::stdout().flush()?;
    let mut buffer=String::new();let bytes=io::stdin().read_line(&mut buffer)?;
    if bytes==0{return Ok("/exit".into())}
    let mut text=buffer.trim_end().to_string();
    if text.starts_with("'''")||text.starts_with("\"\"\"") {
        let marker=if text.starts_with("'''"){ "'''" }else{ "\"\"\""};text.clear();
        loop{let mut line=String::new();let n=io::stdin().read_line(&mut line)?;if n==0||line.trim_end()==marker{break}text.push_str(&line);}
    }
    Ok(text.trim().to_owned())
}
fn new_agent(cfg:Arc<Config>,tools:ToolRegistry)->Result<(LlmClient,Agent)>{let client=LlmClient::new(cfg.clone())?;
    Ok((client.clone(),Agent::session(cfg,client,tools)))}

#[tokio::main]
async fn main()->Result<()> {
    let cli=Cli::parse();let mut config=Arc::new(Config::from_cli(&cli)?);
    fs::create_dir_all(&config.home)?;fs::create_dir_all(&config.project_root)?;
    let mut registry=ToolRegistry::new(&config)?;
    let (mut client,mut session)=new_agent(config.clone(),registry.clone())?;
    if let Ok(data)=fs::read(conversation_path(&config)) {
        if let Ok(parsed)=serde_json::from_slice(&data){session.conversation=parsed;session.conversation.repair();}}
    let mut swarm_default=config.swarm;let mut last_swarm:Option<advanced_ai_agent::TodoList>=None;
    println!("\nAdvanced AI Agent (Rust) | provider={} model={} | workspace={} | tools={}\n",
        config.provider.name(),config.model,config.project_root.display(),registry.names().await.len());
    if let Some(task)=&cli.task {
        if swarm_default {let result=Swarm::new(config.clone(),client.clone(),registry.clone()).run(task).await?;
            println!("{}\n{}",result.report,serde_json::to_string_pretty(&result.counts())?);}
        else{let result=session.send(task).await?;println!("{}",result.content);save_conversation(&config,&session)?;}
        return Ok(())
    }
    println!("Type /help for commands. Multiline input: start with triple quotes and end on a matching line.");
    loop{
        let line=input("agent> ")?;if line.is_empty(){continue}
        if !line.starts_with('/'){
            if swarm_default {
                match Swarm::new(config.clone(),client.clone(),registry.clone()).run(&line).await{
                  Ok(result)=>{println!("{}\n{}",result.report,serde_json::to_string_pretty(&result.counts())?);last_swarm=Some(result)}
                  Err(error)=>eprintln!("Swarm error: {error:#}")}
            }else{
                match session.send(&line).await{Ok(out)=>{println!("\n{}",out.content);
                    if out.stopped_by_limit{eprintln!("INCOMPLETE: round limit reached")}},Err(error)=>eprintln!("Agent error: {error:#}")}
                save_conversation(&config,&session)?;
            }
            continue
        }
        let (command,rest)=line.split_once(' ').unwrap_or((line.as_str(),""));
        match command.to_lowercase().as_str(){
          "/exit"|"/quit"|"/q"=>break,
          "/help"=>print_help(),
          "/clear"=>print!("\x1b[2J\x1b[H"),
          "/new"=>{let path=archive(&config,&session)?;session.reset();println!("New conversation; archived: {path:?}");},
          "/archive"=>{save_conversation(&config,&session)?;let path=archive(&config,&session)?;session.reset();println!("Archived: {path:?}")},
          "/retry"=>{
              let text=session.conversation.messages.iter().rev().find(|m|m["role"]=="user").and_then(|m|m["content"].as_str()).map(str::to_owned);
              if let Some(text)=text{let out=session.send(&text).await?;println!("{}",out.content);save_conversation(&config,&session)?;}},
          "/swarm"=>{if rest=="on"{swarm_default=true;println!("Swarm enabled")}
              else if rest=="off"{swarm_default=false;println!("Swarm disabled")}
              else if !rest.is_empty(){let result=Swarm::new(config.clone(),client.clone(),registry.clone()).run(rest).await?;
                  println!("{}",result.report);last_swarm=Some(result)}
              else{println!("swarm is {}",if swarm_default{"on"}else{"off"})}},
          "/swarm-status"=>{if let Some(ref run)=last_swarm{println!("{}",serde_json::to_string_pretty(&run.counts())?)}else{println!("No swarm run in this session")}},
          "/tools"=>{let parts=rest.split_whitespace().collect::<Vec<_>>();match parts.as_slice(){
              ["enable",name]=>println!("enabled: {}",registry.enable(name).await),
              ["disable",name]=>println!("disabled: {}",registry.disable(name).await),
              _=>println!("Tools: {}",registry.names().await.join(", "))}},
          "/tokens"=>println!("prompt={} completion={} cached={} rounds={} estimated USD ${:.5}",
                 session.conversation.prompt_tokens,session.conversation.completion_tokens,
                 session.conversation.cached_tokens,session.conversation.rounds,session.conversation.cost_estimate()),
          "/limits"=>println!("rpm/key={} keys={} max rounds={} worker rounds=40 max tokens={} pool={:?}",
             config.rpm,config.api_keys.len(),config.max_rounds,config.max_tokens,client.pool().status().await),
          "/thinking"|"/stream"|"/verbose"|"/model"=>{
             let mut updated=(*config).clone();match command{
               "/thinking"=>updated.thinking=!updated.thinking,
               "/stream"=>updated.stream=!updated.stream,
               "/verbose"=>updated.verbose=!updated.verbose,
               "/model"=>{if rest.is_empty(){println!("model: {}",updated.model);continue}updated.model=rest.to_owned()},_=>{}}
             let old=session.conversation.clone();config=Arc::new(updated);
             (client,session)=new_agent(config.clone(),registry.clone())?;session.conversation=old;
             println!("updated: model={},thinking={},stream={},verbose={}",config.model,config.thinking,config.stream,config.verbose);
          },
          "/project"=>println!("workspace: {}",config.project_root.display()),
          "/notes"=>println!("{}",registry.project.dispatch("note_list",&json!({})).await?),
          "/projects"=>{let root=config.home.join("projects");fs::create_dir_all(&root)?;
              for entry in fs::read_dir(root)?.flatten(){println!("{}",entry.file_name().to_string_lossy())}},
          "/new-project"|"/open"|"/import-project"=>{
              if rest.trim().is_empty(){println!("project name/path required");continue}
              let mut updated=(*config).clone();
              if command=="/import-project"{updated.project_root=PathBuf::from(rest).canonicalize().context("opening imported workspace")?;}
              else{let slug=slugify(rest);if slug.is_empty(){println!("invalid project name");continue}
                  let project_dir=config.home.join("projects").join(&slug);
                  if command=="/open"&&!project_dir.exists(){println!("project not found");continue}
                  fs::create_dir_all(project_dir.join("workspace"))?;
                  updated.project_root=project_dir.join("workspace");
                  write_atomic(&project_dir.join("project.json"),&json!({"slug":slug,"name":rest,"kind":"other","language":"python","created":chrono::Utc::now()}))?;
              }
              save_conversation(&config,&session)?;
              config=Arc::new(updated);registry=ToolRegistry::new(&config)?;
              (client,session)=new_agent(config.clone(),registry.clone())?;
              println!("opened: {}",config.project_root.display());
          },
          "/history"=>{let dir=conversation_path(&config).parent().unwrap().to_path_buf();
              for f in fs::read_dir(dir).into_iter().flatten().flatten(){println!("{}",f.path().display())}},
          "/resume"=>{let path=conversation_path(&config).with_file_name(format!("{rest}.json"));
              if path.exists(){session.conversation=serde_json::from_slice(&fs::read(path)?)?;println!("resumed")}
              else{println!("conversation not found")}},
          "/save"=>{let path=if rest.is_empty(){conversation_path(&config)}else{PathBuf::from(rest)};
              write_atomic(&path,&serde_json::to_value(&session.conversation)?)?;println!("saved {}",path.display())},
          "/load"=>{session.conversation=serde_json::from_slice(&fs::read(rest)?)?;session.conversation.repair();println!("loaded")},
          "/export"=>{
              let mut transcript=String::new();for msg in &session.conversation.messages{transcript.push_str(&format!("\n## {}\n\n{}\n",msg["role"].as_str().unwrap_or("message"),
                  msg["content"].as_str().unwrap_or("")));}
              let path=if rest.is_empty(){PathBuf::from("conversation.md")}else{PathBuf::from(rest)};
              fs::write(&path,transcript)?;println!("exported {}",path.display());
          },
          _=>println!("Unknown command. Use /help.")
        }
    }
    save_conversation(&config,&session)?;
    registry.browser.close().await.ok();Ok(())
}
