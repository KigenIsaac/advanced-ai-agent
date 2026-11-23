//! Subprocess and constrained Docker workspaces. Subprocess mode is explicitly
//! NOT a security isolation boundary; Docker mode drops capabilities.
use super::{git::quote,terminal::Terminal,workspace::Workspace};
use anyhow::{bail,Result};
use serde_json::{json,Value};
use std::{collections::BTreeMap,fs,path::PathBuf,sync::Arc};
use tokio::sync::Mutex;
use uuid::Uuid;
#[derive(Debug,Clone)]struct Sandbox{root:PathBuf,backend:String,container_id:Option<String>,image:Option<String>}
#[derive(Clone)]pub struct Sandboxes{ws:Arc<Workspace>,term:Terminal,sandboxes:Arc<Mutex<BTreeMap<String,Sandbox>>>}
impl Sandboxes {
 pub fn new(ws:Arc<Workspace>,term:Terminal)->Self{Self{ws,term,sandboxes:Arc::new(Mutex::new(BTreeMap::new()))}}
 pub async fn dispatch(&self,action:&str,kw:&Value)->Result<Value>{let id=kw["sandbox_id"].as_str().unwrap_or("");
    match action{
      "create"=>{
          let backend=kw["backend"].as_str().unwrap_or("subprocess");let id=Uuid::new_v4().simple().to_string()[..8].to_string();
          let root=std::env::temp_dir().join(format!("codeagent-{id}"));fs::create_dir_all(&root)?;
          if backend=="docker"{
             if which::which("docker").is_err(){bail!("Docker not installed")}
             let image=kw["image"].as_str().unwrap_or("python:3.12-slim");
             let command=format!("docker run -d --rm --network none --memory 1g --cpus 1 --pids-limit 128 --cap-drop ALL --security-opt no-new-privileges --read-only --tmpfs /tmp:rw,nosuid,noexec,size=128m -v {}:/workspace:rw -w /workspace {} sleep infinity",
                 quote(&self.ws.root.to_string_lossy()),quote(image));
             let result=self.term.run(&command,None,120,None).await?;
             if result["exit_code"]!=0{bail!("Docker sandbox creation failed: {}",result["stderr"])}
             let cid=result["stdout"].as_str().unwrap_or("").trim().to_string();
             self.sandboxes.lock().await.insert(id.clone(),Sandbox{root,backend:backend.into(),image:Some(image.into()),container_id:Some(cid.clone())});
             Ok(json!({"id":id,"backend":backend,"container_id":cid,"image":image,"network":"none","privileges":"dropped"}))
          }else if backend=="subprocess"{
             self.sandboxes.lock().await.insert(id.clone(),Sandbox{root:root.clone(),backend:backend.into(),container_id:None,image:None});
             Ok(json!({"id":id,"backend":backend,"root":root,"warning":"Subprocess execution is not isolated from host"}))
          }else{bail!("unsupported sandbox backend {backend}")}
      },
      "list"=>{let items=self.sandboxes.lock().await.iter().map(|(id,s)|json!({"id":id,"backend":s.backend,"root":s.root,"container_id":s.container_id,"image":s.image})).collect::<Vec<_>>();
         Ok(json!({"sandboxes":items}))},
      "destroy"=>{let item=self.sandboxes.lock().await.remove(id);
         if let Some(s)=item{if let Some(cid)=s.container_id{let _=self.term.run(&format!("docker rm -f {}",quote(&cid)),None,30,None).await;}
             fs::remove_dir_all(s.root).ok();Ok(json!({"destroyed":true}))}else{Ok(json!({"destroyed":false}))}},
      "exec"=>{let sandbox=self.sandboxes.lock().await.get(id).cloned().ok_or_else(||anyhow::anyhow!("sandbox not found"))?;
         let command=kw["command"].as_str().unwrap_or("");
         if let Some(cid)=sandbox.container_id{
            self.term.run(&format!("docker exec {} sh -c {}",quote(&cid),quote(command)),None,kw["timeout"].as_u64().unwrap_or(120),None).await
         }else{
            let local=sandbox.root.to_string_lossy().into_owned();
            // The workspace restriction on Terminal is by design. Subprocess sandbox roots live outside it;
            // execute with a restricted shell child in its temporary working directory.
            let mut cmd=tokio::process::Command::new(if cfg!(windows){"cmd"}else{"sh"});
            cmd.arg(if cfg!(windows){"/C"}else{"-c"}).arg(command).current_dir(&sandbox.root)
               .env_clear().env("PATH",std::env::var("PATH").unwrap_or_default()).env("HOME",&sandbox.root)
               .stdout(std::process::Stdio::piped()).stderr(std::process::Stdio::piped());
            let out=tokio::time::timeout(std::time::Duration::from_secs(kw["timeout"].as_u64().unwrap_or(120)),cmd.output()).await??;
            Ok(json!({"cwd":local,"exit_code":out.status.code(),"stdout":String::from_utf8_lossy(&out.stdout),
                "stderr":String::from_utf8_lossy(&out.stderr),"warning":"not a host isolation boundary"}))
         }
      },
      "snapshot"=>{let s=self.sandboxes.lock().await.get(id).cloned().ok_or_else(||anyhow::anyhow!("sandbox not found"))?;
         if s.backend!="subprocess"{bail!("snapshots supported for subprocess backend only")}
         let target=s.root.with_extension("snapshot.tar");let file=fs::File::create(&target)?;
         let mut tar=tar::Builder::new(file);tar.append_dir_all(".",&s.root)?;tar.finish()?;
         Ok(json!({"snapshot":target}))},
      "restore"=>{let s=self.sandboxes.lock().await.get(id).cloned().ok_or_else(||anyhow::anyhow!("sandbox not found"))?;
         if s.backend!="subprocess"{bail!("snapshots supported for subprocess backend only")}
         let source=kw["snapshot"].as_str().unwrap_or("");let tar=fs::File::open(source)?;
         fs::remove_dir_all(&s.root)?;fs::create_dir_all(&s.root)?;
         let mut archive=tar::Archive::new(tar);
         for entry in archive.entries()?{let mut entry=entry?;let path=entry.path()?.to_path_buf();
            if path.is_absolute() || path.components().any(|c|matches!(c,std::path::Component::ParentDir)){
                bail!("unsafe archive entry: {}",path.display());}
            entry.unpack_in(&s.root)?;
         }Ok(json!({"restored":source}))},
      _=>bail!("unknown sandbox action: {action}")
    }
 }
}
