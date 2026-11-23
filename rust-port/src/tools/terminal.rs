//! Process execution with workspace containment, background lifecycle and
//! risk-aware command approval policy; no command is silently privileged.
use crate::tools::workspace::Workspace;
use anyhow::{bail,Result};
use serde_json::{json,Value};
use std::{collections::BTreeMap,process::Stdio,sync::Arc,time::Instant};
use tokio::{io::AsyncReadExt,process::{Child,Command},sync::Mutex};
use uuid::Uuid;

#[derive(Clone)]
pub struct Terminal {ws:Arc<Workspace>, processes:Arc<Mutex<BTreeMap<String,Process>>>,
    allow_destructive:bool,max_output:usize}
#[derive(Clone)]
struct Process {child:Arc<Mutex<Child>>, output:Arc<Mutex<Vec<u8>>>,error:Arc<Mutex<Vec<u8>>>,command:String}
impl Terminal {
    pub fn new(ws:Arc<Workspace>,allow_destructive:bool)->Self{Self{ws,processes:Arc::new(Mutex::new(BTreeMap::new())),allow_destructive,max_output:100_000}}
    pub fn guard(&self,cmd:&str)->Result<()> {
        if self.allow_destructive{return Ok(())}
        let lowered=cmd.to_lowercase();
        let risky=["rm -rf ","rm -fr ","del /s","rmdir /s","format ","mkfs.","dd if=", "shutdown ",
            "git reset --hard","git clean -f","git push --force","git push -f", "git branch -d",
            "drop database","drop schema","drop table","truncate table","docker system prune",
            "kubectl delete", "terraform destroy", "vercel deploy", "flyctl deploy",
            "netlify deploy","npm publish","cargo publish", "curl | sh", "wget | sh"];
        if risky.iter().any(|s|lowered.contains(s)){
            bail!("action requires explicit host authorization (set AGENT_ALLOW_DESTRUCTIVE=1 at startup): {cmd}");
        }Ok(())
    }
    fn command(&self,cmd:&str,cwd:Option<&str>)->Result<Command>{self.guard(cmd)?;
        let path=self.ws.resolve(cwd.unwrap_or("."))?;let (bin,arg)=if cfg!(windows){("cmd","/C")}else{("sh","-c")};
        let mut child=Command::new(bin);child.arg(arg).arg(cmd).current_dir(path);
        child.env("AGENT_WORKSPACE_ROOT",&self.ws.root);
        Ok(child)
    }
    pub async fn run(&self,cmd:&str,cwd:Option<&str>,timeout:u64,stdin:Option<&str>)->Result<Value>{
        let mut command=self.command(cmd,cwd)?;
        command.stdout(Stdio::piped()).stderr(Stdio::piped()).stdin(if stdin.is_some(){Stdio::piped()}else{Stdio::null()});
        let mut child=command.spawn()?;
        if let Some(data)=stdin{if let Some(mut sin)=child.stdin.take(){use tokio::io::AsyncWriteExt;sin.write_all(data.as_bytes()).await?;}}
        let started=Instant::now();
        let output=tokio::time::timeout(std::time::Duration::from_secs(timeout.max(1)),child.wait_with_output()).await
            .map_err(|_|anyhow::anyhow!("command timed out after {timeout}s"))??;
        let stdout=String::from_utf8_lossy(&output.stdout);
        let stderr=String::from_utf8_lossy(&output.stderr);
        Ok(json!({"command":cmd,"cwd":cwd.unwrap_or("."),"exit_code":output.status.code(),
            "stdout":stdout.chars().take(self.max_output).collect::<String>(),
            "stderr":stderr.chars().take(self.max_output).collect::<String>(),
            "duration":started.elapsed().as_secs_f64(),"truncated":stdout.len()>self.max_output||stderr.len()>self.max_output}))
    }
    pub async fn background(&self,cmd:&str,cwd:Option<&str>)->Result<Value>{
        let mut command=self.command(cmd,cwd)?;
        command.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped());
        let mut child=command.spawn()?;
        let stdout=child.stdout.take();let stderr=child.stderr.take();
        let out=Arc::new(Mutex::new(Vec::new()));let err=Arc::new(Mutex::new(Vec::new()));
        if let Some(mut src)=stdout{let buf=out.clone();tokio::spawn(async move{let mut chunk=[0;4096];while let Ok(n)=src.read(&mut chunk).await{
            if n==0{break}let mut lock=buf.lock().await;if lock.len()<100_000{lock.extend_from_slice(&chunk[..n]);}}});}
        if let Some(mut src)=stderr{let buf=err.clone();tokio::spawn(async move{let mut chunk=[0;4096];while let Ok(n)=src.read(&mut chunk).await{
            if n==0{break}let mut lock=buf.lock().await;if lock.len()<100_000{lock.extend_from_slice(&chunk[..n]);}}});}
        let id=Uuid::new_v4().simple().to_string()[..12].to_string();
        self.processes.lock().await.insert(id.clone(),Process{child:Arc::new(Mutex::new(child)),output:out,error:err,command:cmd.to_string()});
        Ok(json!({"process_id":id}))
    }
    async fn get(&self,id:&str)->Result<Value>{
        let proc=self.processes.lock().await.get(id).cloned().ok_or_else(||anyhow::anyhow!("no process {id}"))?;
        let status=proc.child.lock().await.try_wait()?;
        let out=proc.output.lock().await;let err=proc.error.lock().await;
        Ok(json!({"process_id":id,"command":proc.command,"status":if status.is_some(){"exited"}else{"running"},
            "exit_code":status.and_then(|s|s.code()),"stdout":String::from_utf8_lossy(&out),"stderr":String::from_utf8_lossy(&err)}))
    }
    pub async fn dispatch(&self,action:&str,kw:&Value)->Result<Value>{
        let id=kw["process_id"].as_str().unwrap_or("");
        match action{
            "run"=>self.run(kw["command"].as_str().unwrap_or(""),kw["cwd"].as_str(),kw["timeout"].as_u64().unwrap_or(120),kw["stdin"].as_str()).await,
            "background"=>self.background(kw["command"].as_str().unwrap_or(""),kw["cwd"].as_str()).await,
            "get"=>self.get(id).await,
            "list"=>{let ids=self.processes.lock().await.keys().cloned().collect::<Vec<_>>();let mut out=vec![];
                for i in ids{out.push(self.get(&i).await?);}Ok(json!({"processes":out}))},
            "kill"=>{let p=self.processes.lock().await.get(id).cloned().ok_or_else(||anyhow::anyhow!("unknown process"))?;
                p.child.lock().await.kill().await?;Ok(json!({"killed":id}))},
            "wait"=>{let deadline=tokio::time::Instant::now()+std::time::Duration::from_secs(kw["timeout"].as_u64().unwrap_or(60));
                loop{let status=self.get(id).await?;if status["status"]=="exited"{return Ok(status)}
                    if tokio::time::Instant::now()>=deadline{bail!("wait timed out")}
                    tokio::time::sleep(std::time::Duration::from_millis(200)).await;}
            },
            _=>bail!("unknown terminal action: {action}")
        }
    }
}
