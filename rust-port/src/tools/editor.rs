//! Exact-text editing, undo and conservative language refactoring.
use super::workspace::Workspace;
use anyhow::{bail,Result};
use regex::Regex;
use serde_json::{json,Value};
use similar::TextDiff;
use std::{collections::VecDeque,fs,sync::Arc};
use tokio::sync::Mutex;

#[derive(Clone)]pub struct Editor {ws:Arc<Workspace>,undos:Arc<Mutex<VecDeque<(String,String)>>>}
impl Editor {
    pub fn new(ws:Arc<Workspace>)->Self{Self{ws,undos:Arc::new(Mutex::new(VecDeque::new()))}}
    fn read(&self,path:&str)->Result<String>{Ok(fs::read_to_string(self.ws.resolve(path)?)?)}
    async fn save(&self,path:&str,new:&str)->Result<Value>{let old=self.read(path).unwrap_or_default();
        self.undos.lock().await.push_back((path.to_owned(),old));
        self.ws.write(path,new,false)?;Ok(json!({"file":path,"applied":true,"bytes":new.len()}))}
    pub async fn dispatch(&self,action:&str,kw:&Value)->Result<Value>{let arg=|k:&str|kw[k].as_str().unwrap_or("");
        let file=arg("file");
        match action {
            "preview"|"apply"=>{let original=self.read(file)?;let old=arg("old_text");if !original.contains(old){bail!("old_text not found")}
                let count=kw["count"].as_i64().unwrap_or(1);if count==1&&original.matches(old).count()>1{bail!("patch context ambiguous")}
                let changed=if count<0{original.replace(old,arg("new_text"))}else{original.replacen(old,arg("new_text"),count as usize)};
                if action=="preview"{Ok(json!({"diff":TextDiff::from_lines(&original,&changed).unified_diff().header(file,file).to_string()}))}
                else{self.save(file,&changed).await}
            },
            "replace_range"|"delete_range"|"insert"=>{let src=self.read(file)?;let lines:Vec<&str>=src.lines().collect();
                let start=kw["start_line"].as_u64().unwrap_or(1) as usize;
                let end=if action=="insert"{start.saturating_sub(1)}else{kw["end_line"].as_u64().unwrap_or(start as u64) as usize};
                if start<1||start>lines.len()+1||end>lines.len()||start>end+1 {bail!("invalid line range")}
                let new_content=if action=="delete_range"{""}else{arg("content")};
                let mut changed=lines[..start-1].join("\n");if !changed.is_empty(){changed.push('\n')}
                if !new_content.is_empty(){changed.push_str(new_content);if !new_content.ends_with('\n'){changed.push('\n')}}
                changed.push_str(&lines[end..].join("\n"));if src.ends_with('\n')&&!changed.ends_with('\n'){changed.push('\n')}
                self.save(file,&changed).await
            },
            "rewrite"=>self.save(file,arg("content")).await,
            "revert_last"=>{if let Some((file,src))=self.undos.lock().await.pop_back(){self.ws.write(&file,&src,false)?;
                Ok(json!({"reverted":file}))}else{Ok(json!({"reverted":null}))}},
            "revert_index"=>{let index=kw["index"].as_u64().unwrap_or(u64::MAX) as usize;
                let mut lock=self.undos.lock().await;let (file,src)=lock.remove(index).ok_or_else(||anyhow::anyhow!("invalid undo index"))?;
                drop(lock);self.ws.write(&file,&src,false)?;Ok(json!({"reverted":file}))},
            "history"=>Ok(json!({"history":self.undos.lock().await.iter().map(|(file,_)|json!({"file":file})).collect::<Vec<_>>()})),
            "rename_symbol"=>{let old=arg("name");let new=arg("new_name");
                if !Regex::new(r"^[A-Za-z_][A-Za-z_0-9]*$")?.is_match(new){bail!("invalid symbol name")}
                let escaped=regex::escape(old);let rx=Regex::new(&format!(r"\b{escaped}\b"))?;
                let mut changes=vec![];for f in self.ws.find_files("*.py",if file.is_empty(){"."}else{file})?{
                    let src=self.read(&f)?;let n=rx.find_iter(&src).count();if n>0{changes.push(json!({"file":f,"replacements":n}));
                        if kw["dry_run"]!=true{self.save(&f,rx.replace_all(&src,new).as_ref()).await?;}}}
                Ok(json!({"symbol":old,"to":new,"files":changes.len(),"changes":changes,"dry_run":kw["dry_run"]==true}))
            },
            "add_import"=>{let src=self.read(file)?;let stmt=if arg("name").is_empty(){format!("import {}",arg("module"))}else{format!("from {} import {}",arg("module"),arg("name"))};
                if src.lines().any(|l|l.trim()==stmt){return Ok(json!({"added":false}))}
                self.save(file,&format!("{stmt}\n{src}")).await?;Ok(json!({"added":true,"statement":stmt}))},
            "remove_import"=>{let src=self.read(file)?;let target=arg("module");
                let new=src.lines().filter(|l|!l.starts_with(&format!("import {target}"))&&!l.starts_with(&format!("from {target} import")))
                    .collect::<Vec<_>>().join("\n");let removed=src.lines().count().saturating_sub(new.lines().count());
                if removed>0{self.save(file,&new).await?;}Ok(json!({"removed":removed>0,"lines_removed":removed}))},
            "extract_function"=>{let src=self.read(file)?;let lines:Vec<&str>=src.lines().collect();
                let start=kw["start_line"].as_u64().unwrap_or(1) as usize;let end=kw["end_line"].as_u64().unwrap_or(start as u64) as usize;
                if start==0||start>end||end>lines.len(){bail!("invalid extraction range")}
                let name=arg("name");let body=lines[start-1..end].iter().map(|l|format!("    {}",l.trim_start())).collect::<Vec<_>>().join("\n");
                let func=format!("def {name}():\n{body}\n\n{name}()");let mut result=lines[..start-1].join("\n");
                result.push_str(&format!("\n{func}\n{}",lines[end..].join("\n")));self.save(file,&result).await
            },
            "inline_function"=>{let src=self.read(file)?;let name=arg("name");let rx=Regex::new(&format!(r"(?m)^def\s+{}\(\):\s*\n\s+return\s+(.+)$",regex::escape(name)))?;
                if let Some(caps)=rx.captures(&src){let expr=caps.get(1).unwrap().as_str().to_owned();let call=Regex::new(&format!(r"\b{}\(\)",regex::escape(name)))?;
                    self.save(file,call.replace_all(&src,format!("({expr})")).as_ref()).await?;Ok(json!({"inlined":true}))}
                else{Ok(json!({"inlined":false,"reason":"Function too complex to inline safely"}))}},
            "split_file"=>{let src=self.read(file)?;let names=kw["symbols"].as_array().cloned().unwrap_or_default();
                let mut blocks=Vec::new();let mut rest=src.clone();
                for name in names.iter().filter_map(Value::as_str){let rx=Regex::new(&format!(r"(?ms)^(?:def|class|async def)\s+{}\b.*?(?=\n(?:def|class|async def)\s+|\z)",regex::escape(name)))?;
                    if let Some(m)=rx.find(&rest){blocks.push(m.as_str().to_owned());rest.replace_range(m.range(),"");}}
                if blocks.is_empty(){return Ok(json!({"moved":0}))}
                self.save(arg("target"),&blocks.join("\n\n")).await?;self.save(file,&rest).await?;
                Ok(json!({"moved":blocks.len(),"to":arg("target")}))
            },
            "remove_dead_code"=>{let symbol=arg("name");let hits=self.ws.search(symbol,".",false,1000)?;
                if hits.len()>1{return Ok(json!({"removed":false,"references":hits.len()}))}
                if kw["dry_run"]==false{bail!("requires structural AST verification before deleting code")}
                Ok(json!({"removed":false,"dry_run":true,"candidates":hits.len()}))},
            "simplify"=>Ok(json!({"simplified":false,"reason":"Use build.lint with Ruff and explicit edits; no automatic language rewrite"})),
            _=>bail!("unknown editor action: {action}")
        }
    }
}
