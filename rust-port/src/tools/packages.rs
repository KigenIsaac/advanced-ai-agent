//! Package manager selection, dependency installation, builds and audits.
use super::{git::quote,terminal::Terminal,workspace::Workspace};
use anyhow::{bail,Result};
use serde_json::{json,Value};
use std::sync::Arc;
#[derive(Clone)]pub struct Packages{term:Terminal,ws:Arc<Workspace>}
impl Packages {
 pub fn new(term:Terminal,ws:Arc<Workspace>)->Self{Self{term,ws}}
 fn detect(&self)->Vec<&'static str>{let root=&self.ws.root;let mut out=vec![];
    if root.join("package.json").exists(){out.push(if root.join("pnpm-lock.yaml").exists(){"pnpm"}else if root.join("yarn.lock").exists(){"yarn"}else{"npm"});}
    if root.join("pyproject.toml").exists()||root.join("requirements.txt").exists(){out.push(if root.join("poetry.lock").exists(){"poetry"}else if root.join("uv.lock").exists(){"uv"}else{"pip"})}
    for (file,manager) in [("Cargo.toml","cargo"),("go.mod","go"),("composer.json","composer"),("Gemfile","bundler"),("pom.xml","maven"),("build.gradle","gradle")]{if root.join(file).exists(){out.push(manager)}}
    if root.read_dir().ok().into_iter().flatten().flatten().any(|e|e.path().extension().is_some_and(|x|x=="csproj")){out.push("dotnet")}
    out
 }
 pub async fn dispatch(&self,action:&str,kw:&Value)->Result<Value>{
    let managers=self.detect();let requested=kw["manager"].as_str();let manager=requested.or_else(||managers.first().copied()).unwrap_or("unknown");
    let pkg=kw["package"].as_str().unwrap_or("");let package=quote(pkg);let dev=kw["dev"].as_bool().unwrap_or(false);
    let cmd=match action {
       "detect"=>return Ok(json!({"managers":managers,"build_system":managers.first()})),
       "list"=>match manager{"pip"=>"pip list --format=json".into(),"npm"=>"npm ls --json".into(),"cargo"=>"cargo tree".into(),"go"=>"go list -m all".into(),"dotnet"=>"dotnet list package".into(),_=>bail!("unsupported manager {manager}")},
       "install"=>{
           if pkg.is_empty(){bail!("package required")}
           match manager{"npm"=>format!("npm install {}{package}",if dev{"--save-dev "}else{""}),"yarn"=>format!("yarn add {}{package}",if dev{"--dev "}else{""}),
             "pnpm"=>format!("pnpm add {}{package}",if dev{"--save-dev "}else{""}),"pip"=>format!("pip install {package}"),
             "poetry"=>format!("poetry add {}{package}",if dev{"--group dev "}else{""}),"uv"=>format!("uv add {}{package}",if dev{"--dev "}else{""}),
             "cargo"=>format!("cargo add {package}"),"go"=>format!("go get {package}"),"composer"=>format!("composer require {package}"),
             "dotnet"=>format!("dotnet add package {package}"),_=>bail!("unsupported install manager")}
       },
       "remove"=>match manager{"npm"=>format!("npm uninstall {package}"),"yarn"=>format!("yarn remove {package}"),"pnpm"=>format!("pnpm remove {package}"),
           "pip"=>format!("pip uninstall -y {package}"),"poetry"=>format!("poetry remove {package}"),"uv"=>format!("uv remove {package}"),
           "cargo"=>format!("cargo remove {package}"),"go"=>format!("go get {package}@none"),"dotnet"=>format!("dotnet remove package {package}"),
           _=>bail!("unsupported removal manager")},
       "update"=>match manager{"npm"=>format!("npm update {package}"),"yarn"=>format!("yarn upgrade {package}"),"pnpm"=>format!("pnpm update {package}"),
           "pip"=>if pkg.is_empty(){"pip list --outdated".into()}else{format!("pip install --upgrade {package}")},
           "poetry"=>"poetry update".into(),"uv"=>"uv lock --upgrade".into(),"cargo"=>"cargo update".into(),"go"=>"go get -u ./...".into(),
           _=>bail!("unsupported update manager")},
       "audit"=>match manager{"npm"=>"npm audit --json".into(),"pnpm"=>"pnpm audit --json".into(),"yarn"=>"yarn audit --json".into(),
           "pip"=>"pip-audit --format=json".into(),"cargo"=>"cargo audit --json".into(),"go"=>"govulncheck ./...".into(),
           _=>bail!("no audit command for {manager}")},
       _=>bail!("unknown packages action: {action}")
    };
    self.term.run(&cmd,None,600,None).await
 }
}
