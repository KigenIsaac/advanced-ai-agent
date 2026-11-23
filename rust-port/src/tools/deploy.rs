//! Detect, deploy, observe and rollback with a host-side authorization policy.
use super::{terminal::Terminal,workspace::Workspace};
use anyhow::{bail,Result};
use serde_json::{json,Value};
use std::sync::Arc;
#[derive(Clone)]pub struct Deployment{term:Terminal,ws:Arc<Workspace>,allow_destructive:bool,client:reqwest::Client}
impl Deployment {
 pub fn new(term:Terminal,ws:Arc<Workspace>,allow_destructive:bool)->Self{
    Self{term,ws,allow_destructive,client:reqwest::Client::new()}}
 fn detect(&self)->Vec<&'static str>{let r=&self.ws.root;let mut out=vec![];
    if r.join("vercel.json").exists()||r.join(".vercel").is_dir(){out.push("vercel")}
    if r.join("netlify.toml").exists(){out.push("netlify")}
    if r.join("fly.toml").exists(){out.push("fly")}
    if r.join("render.yaml").exists(){out.push("render")}
    if r.join("Dockerfile").exists()||r.join("docker-compose.yml").exists(){out.push("docker")}
    if r.join(".github/workflows").is_dir(){out.push("github-actions")}
    if r.join("Procfile").exists(){out.push("heroku")}
    if r.join("k8s").is_dir(){out.push("kubernetes")}
    out
 }
 pub async fn dispatch(&self,action:&str,kw:&Value)->Result<Value>{
    let detected=self.detect();let provider=kw["provider"].as_str().or_else(||detected.first().copied()).unwrap_or("");
    match action{
       "detect"=>Ok(json!({"providers":detected})),
       "build"=>{let cmd=if self.ws.root.join("package.json").exists(){"npm run build"}
           else if self.ws.root.join("Cargo.toml").exists(){"cargo build --release"}
           else if self.ws.root.join("go.mod").exists(){"go build ./..."}
           else{bail!("no recognized deployment build command")};self.term.run(cmd,None,600,None).await},
       "health"|"metrics"=>{let url=kw["url"].as_str().unwrap_or("");let response=self.client.get(url).timeout(std::time::Duration::from_secs(10)).send().await?;
           let status=response.status();let body=response.text().await?;
           Ok(json!({"status":status.as_u16(),"ok":status.is_success(),"body":body.chars().take(if action=="health"{500}else{5000}).collect::<String>()}))},
       "status"|"logs"=>{let cmd=match (provider,action){("vercel","status")=>"vercel ls",("vercel","logs")=>"vercel logs",
           ("fly","status")=>"flyctl status",("fly","logs")=>"flyctl logs",_=>bail!("status/logs unsupported for {provider}")};
           self.term.run(cmd,None,120,None).await},
       "deploy"|"rollback"=>{
           if !self.allow_destructive{bail!("{action} requires explicit authorization via AGENT_ALLOW_DESTRUCTIVE=1 set by the host")}
           let env=kw["environment"].as_str().unwrap_or("staging");
           let cmd=match (provider,action){
             ("vercel","deploy")=>if env=="production"{"vercel deploy --prod --yes"}else{"vercel deploy --yes"},
             ("netlify","deploy")=>if env=="production"{"netlify deploy --build --prod"}else{"netlify deploy --build"},
             ("fly","deploy")=>"flyctl deploy",
             ("heroku","deploy")=>"git push heroku HEAD:main",
             ("github-actions","deploy")=>"gh workflow run deploy.yml",
             ("docker","deploy")=>"docker build -t app .",
             ("vercel","rollback")=>"vercel rollback",
             ("fly","rollback")=>"flyctl releases rollback",
             _=>bail!("{action} unsupported for {provider}")};
           self.term.run(cmd,None,600,None).await
       },
       _=>bail!("unknown deployment action: {action}")
    }
 }
}
