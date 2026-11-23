//! Cross-ecosystem test runner, failure parser, coverage and static checks.
use super::{terminal::Terminal,workspace::Workspace};
use anyhow::{bail,Result};
use regex::Regex;
use serde_json::{json,Value};
use std::{fs,sync::Arc};

#[derive(Clone)]pub struct Testing {ws:Arc<Workspace>,term:Terminal}
impl Testing {
 pub fn new(ws:Arc<Workspace>,term:Terminal)->Self{Self{ws,term}}
 pub fn framework(&self)->Option<&'static str>{let root=&self.ws.root;
    if root.join("pyproject.toml").exists()||root.join("pytest.ini").exists()||root.join("tests").is_dir(){
        return Some(if which::which("pytest").is_ok(){"pytest"}else{"unittest"});}
    if let Ok(pkg)=fs::read_to_string(root.join("package.json")){
        if pkg.contains("vitest"){return Some("vitest")}
        if pkg.contains("jest"){return Some("jest")}
        if pkg.contains("mocha"){return Some("mocha")}
    }
    if root.join("Cargo.toml").exists(){return Some("cargo")}
    if root.join("go.mod").exists(){return Some("go")}
    None
 }
 pub fn command(fw:&str,path:Option<&str>,test:Option<&str>)->String{
    let path=path.unwrap_or("");let test=test.unwrap_or("");
    match fw{
      "pytest"|"unittest"=>format!("pytest -v {}",if path.is_empty(){test}else{path}).trim().into(),
      "jest"=>format!("npx jest {}",if !test.is_empty(){format!("-t '{}'",test.replace('\'',"'\\''"))}else{path.to_string()}).trim().into(),
      "vitest"=>format!("npx vitest run {}",if !test.is_empty(){format!("-t '{}'",test.replace('\'',"'\\''"))}else{path.to_string()}).trim().into(),
      "mocha"=>format!("npx mocha {path}").trim().into(),
      "cargo"=>format!("cargo test {test}").trim().into(),
      "go"=>"go test ./...".into(),_=>String::new()
    }
 }
 pub fn failures(&self,stdout:&str,framework:&str)->Vec<Value>{
    let pattern=match framework{
       "pytest"|"unittest"=>r"(?m)^(FAILED|ERROR)\s+(\S+)(?:\s+-\s+([^\n]*))?",
       "cargo"=>r"(?m)^test\s+(\S+)\s+\.\.\.\s+(FAILED)",
       "go"=>r"(?m)^--- FAIL:\s+(\S+)",
       _=>r"(?m)^\s*(?:✕|×|✗)\s+([^\n]+)"};
    Regex::new(pattern).ok().map(|rx|rx.captures_iter(stdout).map(|cap|{
        if framework=="pytest"||framework=="unittest"{
            json!({"status":cap.get(1).map(|m|m.as_str()),"test":cap.get(2).map(|m|m.as_str()),"message":cap.get(3).map(|m|m.as_str()).unwrap_or("")})
        }else{json!({"status":"FAILED","test":cap.get(1).map(|m|m.as_str())})}
    }).collect()).unwrap_or_default()
 }
 pub async fn test(&self,action:&str,kw:&Value)->Result<Value>{
    let fw=self.framework().ok_or_else(||anyhow::anyhow!("no test framework detected"))?;
    match action{
       "discover"=>{let path=kw["path"].as_str().unwrap_or(".");
           let pats=match fw{"cargo"=>vec!["*.rs"],"go"=>vec!["*_test.go"],
            "jest"|"vitest"|"mocha"=>vec!["*.test.ts","*.spec.ts","*.test.js","*.spec.js","*.test.tsx"],
            _=>vec!["test_*.py","*_test.py"]};let mut files=Vec::new();for p in pats{files.extend(self.ws.find_files(p,path)?)}
            files.sort();files.dedup();Ok(json!({"tests":files}))},
       "generate"=>{
          let src=kw["target_file"].as_str().ok_or_else(||anyhow::anyhow!("target_file required"))?;
          let out=kw["output_file"].as_str().map(str::to_owned).unwrap_or_else(||format!("tests/test_{}.py",src.rsplit('/').next().unwrap_or("module").trim_end_matches(".py")));
          self.ws.write(&out,&format!("\"\"\"Tests for {src}; replace skip with real assertions.\"\"\"\nimport pytest\n\n@pytest.mark.skip(reason=\"Implement meaningful assertions before enabling\")\ndef test_{}_behavior():\n    raise NotImplementedError\n",src.rsplit('/').next().unwrap_or("module").trim_end_matches(".py")),false)?;
          Ok(json!({"created":out,"placeholder":true,"skipped":true}))
       },
       "run"|"run_file"|"run_case"|"failures"|"coverage"=>{
            let cmd=if action=="coverage"{
               match fw{"pytest"|"unittest"=>"coverage run -m pytest && coverage report -m".into(),
                  "jest"=>"npx jest --coverage".into(),"vitest"=>"npx vitest run --coverage".into(),
                  _=>bail!("coverage unsupported for {fw}")}
            }else{Self::command(fw,kw["path"].as_str(),kw["test_name"].as_str())};
            let mut result=self.term.run(&cmd,None,kw["timeout"].as_u64().unwrap_or(300),None).await?;
            let out=format!("{}\n{}",result["stdout"].as_str().unwrap_or(""),result["stderr"].as_str().unwrap_or(""));
            let failures=self.failures(&out,fw);
            if action=="failures"{return Ok(json!({"failures":failures,"exit_code":result["exit_code"]}))}
            result["framework"]=json!(fw);result["failures"]=json!(failures);Ok(result)
       },_=>bail!("unknown test action: {action}")
    }
 }
 pub async fn build(&self,action:&str,kw:&Value)->Result<Value>{
    let root=&self.ws.root;let path=kw["path"].as_str().unwrap_or(".");
    let system=if root.join("package.json").exists(){"npm"}else if root.join("Cargo.toml").exists(){"cargo"}
       else if root.join("go.mod").exists(){"go"}else if root.join("pom.xml").exists(){"maven"}
       else if root.join("build.gradle").exists(){"gradle"}else if root.join("Makefile").exists(){"make"}
       else if root.join("pyproject.toml").exists(){"python"}else{"unknown"};
    let cmd=match action{
       "detect"=>return Ok(json!({"system":system})),
       "build"=>match system{"npm"=>"npm run build","cargo"=>"cargo build","go"=>"go build ./...","maven"=>"mvn package -DskipTests",
          "gradle"=>"gradle build -x test","make"=>"make","python"=>"python -m build",_=>bail!("no build system detected")},
       "clean"=>match system{"npm"=>"npm run clean","cargo"=>"cargo clean","go"=>"go clean -cache","maven"=>"mvn clean",
          "gradle"=>"gradle clean","make"=>"make clean",_=>bail!("no clean task available")},
       "typecheck"=>if root.join("tsconfig.json").exists(){"npx --no-install tsc --noEmit"}
          else if root.join("pyproject.toml").exists(){"mypy ."}else{bail!("no typechecker detected")},
       "lint"=>if root.join("Cargo.toml").exists(){"cargo clippy --all-targets"}
          else if root.join("package.json").exists(){"npm run lint"}else{"ruff check ."},
       "security"=>if which::which("semgrep").is_ok(){"semgrep --json --config=auto ."}
          else if which::which("bandit").is_ok(){"bandit -r . -f json"}else{bail!("no security scanner installed")},
       "complexity"=>if which::which("radon").is_ok(){"radon cc -j ."}
          else if which::which("lizard").is_ok(){"lizard ."}else{bail!("no complexity scanner installed")},
       "dead_code"=>{let symbols=self.ws.symbols(path,None)?;return Ok(json!({"candidates":symbols,"warning":"heuristic; absence of textual references is not proof of dead code"}))},
       _=>bail!("unknown build action: {action}")};
    self.term.run(cmd,None,600,None).await
 }
}
