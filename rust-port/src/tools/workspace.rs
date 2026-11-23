//! Workspace filesystem, code search, structure, dependency and symbol analysis.
use anyhow::{anyhow,bail,Context,Result};
use regex::Regex;
use serde_json::{json,Value};
use std::{collections::{BTreeMap,BTreeSet},fs,path::{Component,Path,PathBuf}};
use walkdir::WalkDir;

#[derive(Debug,Clone)]
pub struct Workspace {pub root:PathBuf, ignores:BTreeSet<String>}
impl Workspace {
    pub fn new(root:impl AsRef<Path>)->Result<Self>{
        fs::create_dir_all(root.as_ref())?;
        Ok(Self{root:root.as_ref().canonicalize()?, ignores:[".git","node_modules",".venv","venv","__pycache__","target","dist","build",".next",".cache"].iter().map(|s|s.to_string()).collect()})
    }
    pub fn resolve(&self,path:&str)->Result<PathBuf>{
        let given=Path::new(path);let mut candidate=if given.is_absolute(){PathBuf::new()}else{self.root.clone()};
        for part in given.components(){match part {
            Component::Prefix(_) | Component::RootDir=>{if !given.is_absolute(){bail!("invalid absolute component")}
                candidate.push(part.as_os_str());}
            Component::CurDir=>{},Component::ParentDir=>{candidate.pop();},
            Component::Normal(name)=>candidate.push(name),
        }}
        if !candidate.starts_with(&self.root){bail!("path traversal outside workspace: {path}")}
        // Check existing parents, not just the full candidate, to reject symlink escapes.
        let mut ancestor=candidate.as_path();
        while !ancestor.exists(){ancestor=ancestor.parent().ok_or_else(||anyhow!("invalid path"))?;}
        let canonical=ancestor.canonicalize()?;
        if !canonical.starts_with(&self.root){bail!("symlink escapes workspace: {path}")}
        Ok(candidate)
    }
    pub fn relative(&self,path:&Path)->String{path.strip_prefix(&self.root).unwrap_or(path).to_string_lossy().replace('\\',"/")}
    fn ignored(&self,path:&Path)->bool{path.strip_prefix(&self.root).unwrap_or(path).components().any(|c|
        self.ignores.contains(&c.as_os_str().to_string_lossy().to_string()))}
    fn iter(&self,root:&Path)->Vec<PathBuf>{WalkDir::new(root).into_iter().filter_entry(|x|!self.ignored(x.path()))
        .filter_map(|x|x.ok()).map(|x|x.path().to_path_buf()).collect()}
    pub fn list(&self,path:&str,recursive:bool)->Result<Value>{let p=self.resolve(path)?;let mut entries=vec![];
        if recursive {for x in self.iter(&p) {if x==p{continue}entries.push(self.info(&x)?);}}
        else {for x in fs::read_dir(&p)?{let x=x?;if !self.ignored(&x.path()){entries.push(self.info(&x.path())?);}}}
        Ok(json!({"entries":entries}))}
    fn info(&self,p:&Path)->Result<Value>{let metadata=fs::symlink_metadata(p)?;Ok(json!({"path":self.relative(p),
        "type":if metadata.is_dir(){"dir"}else{"file"},"size":metadata.len(),"read_only":metadata.permissions().readonly()}))}
    pub fn read(&self,path:&str,start:Option<usize>,end:Option<usize>)->Result<Value>{let p=self.resolve(path)?;let metadata=fs::metadata(&p)?;
        if metadata.len()>10*1024*1024{bail!("file too large for one read")}
        let src=fs::read_to_string(&p).with_context(||format!("reading {path}"))?;
        let lines:Vec<&str>=src.lines().collect();let first=start.unwrap_or(1).max(1)-1;let last=end.unwrap_or(lines.len()).min(lines.len());
        let selected=if first>=lines.len()||first>=last{String::new()}else{lines[first..last].join("\n")};
        Ok(json!({"path":self.relative(&p),"content":selected,"lines":last.saturating_sub(first),"total_lines":lines.len(),"encoding":"utf-8"}))
    }
    pub fn write(&self,path:&str,content:&str,append:bool)->Result<Value>{let p=self.resolve(path)?;
        if let Some(parent)=p.parent(){fs::create_dir_all(parent)?}
        if append {use std::io::Write;let mut file=fs::OpenOptions::new().create(true).append(true).open(&p)?;
            file.write_all(content.as_bytes())?;}else{fs::write(&p,content)?}
        Ok(json!({"path":self.relative(&p),"bytes":content.len(),"appended":append}))
    }
    pub fn edit(&self,path:&str,old:&str,new:&str,count:i64)->Result<Value>{if old.is_empty(){bail!("old_text must not be empty")}
        let p=self.resolve(path)?;let text=fs::read_to_string(&p)?;
        let occurrences=text.matches(old).count();if occurrences==0{bail!("old_text not found")}
        if occurrences>1&&count==1{bail!("old_text ambiguous ({occurrences} matches); use precise text or count=-1")}
        let changed=if count<0{text.replace(old,new)}else{text.replacen(old,new,count as usize)};
        fs::write(p,changed)?;Ok(json!({"path":path,"replaced":if count<0{occurrences}else{occurrences.min(count as usize)}}))
    }
    pub fn search(&self,pattern:&str,path:&str,regex:bool,limit:usize)->Result<Vec<Value>>{
        let p=self.resolve(path)?;let rx=Regex::new(&if regex{pattern.to_string()}else{regex::escape(pattern)})?;
        let mut out=Vec::new();for f in self.iter(&p){if !f.is_file()||!text_file(&f){continue}
            let content=match fs::read_to_string(&f){Ok(c)=>c,Err(_)=>continue};
            for (i,line) in content.lines().enumerate(){if rx.is_match(line){out.push(json!({"file":self.relative(&f),"line":i+1,"text":line}));
                if out.len()>=limit{return Ok(out)}}}
        }Ok(out)
    }
    pub fn find_files(&self,pattern:&str,path:&str)->Result<Vec<String>>{
        let glob=globset::Glob::new(pattern)?.compile_matcher();
        Ok(self.iter(&self.resolve(path)?).into_iter().filter(|p|p.is_file()&&glob.is_match(p.file_name().unwrap_or_default())).map(|p|self.relative(&p)).collect())
    }
    pub fn structure(&self,max_depth:usize)->Result<Value>{let mut entries=vec![];
        for p in self.iter(&self.root){if p==self.root{continue}
            let d=p.strip_prefix(&self.root)?.components().count();if d<=max_depth{entries.push(self.info(&p)?);}}
        Ok(json!({"root":self.root,"tree":entries}))
    }
    pub fn symbols(&self,path:&str,search_name:Option<&str>)->Result<Vec<Value>>{
        let p=self.resolve(path)?;let files=if p.is_file(){vec![p]}else{self.iter(&p).into_iter().filter(|p|p.is_file()).collect()};
        let python=Regex::new(r"^\s*(async\s+def|def|class)\s+([A-Za-z_][A-Za-z_0-9]*)")?;
        let other=Regex::new(r"\b(?:pub\s+)?(?:async\s+)?(?:fn|function|class|struct|interface|type)\s+([A-Za-z_][A-Za-z_0-9]*)")?;
        let mut out=vec![];
        for f in files {if !text_file(&f){continue}let src=match fs::read_to_string(&f){Ok(s)=>s,Err(_)=>continue};
            for (i,line) in src.lines().enumerate(){
                if let Some(c)=python.captures(line).filter(|_|f.extension().is_some_and(|e|e=="py")){
                    let name=c.get(2).unwrap().as_str();if search_name.is_none_or(|n|n==name){out.push(json!({"file":self.relative(&f),"name":name,"line":i+1,"kind":if c.get(1).unwrap().as_str()=="class"{"class"}else{"function"}}));}
                }else if let Some(c)=other.captures(line){let name=c.get(1).unwrap().as_str();
                    if search_name.is_none_or(|n|n==name){out.push(json!({"file":self.relative(&f),"name":name,"line":i+1,"kind":"symbol"}));}}
            }
        }Ok(out)
    }
    pub fn imports(&self,path:&str)->Result<Value>{let text=fs::read_to_string(self.resolve(path)?)?;
        let rx=Regex::new(r#"^\s*(?:from\s+([\w.]+)\s+import\s+|import\s+([\w.]+)|use\s+([\w:]+)|(?:const|var)\s+\w+\s*=\s*require\(['"]([^'"]+)|from\s+['"]([^'"]+))"#)?;
        let items:Vec<_>=text.lines().enumerate().filter_map(|(i,l)|rx.captures(l).map(|c|json!({"line":i+1,"module":(1..=5).find_map(|j|c.get(j).map(|x|x.as_str())).unwrap_or("")}))).collect();Ok(json!({"imports":items}))
    }
    pub fn module_graph(&self)->Result<Value>{let mut nodes=vec![];let mut edges=vec![];
        for f in self.iter(&self.root){if !f.is_file()||!text_file(&f){continue}
            let name=self.relative(&f);nodes.push(json!({"id":name,"path":name}));
            if let Ok(imp)=self.imports(&name){if let Some(imps)=imp["imports"].as_array(){for m in imps{
                edges.push(json!({"from":name,"to":m["module"]}));}}}
        }Ok(json!({"nodes":nodes,"edges":edges}))
    }
    pub fn dispatch(&self,action:&str,kw:&Value)->Result<Value>{let arg=|key:&str|kw[key].as_str().unwrap_or("");
        let path=if arg("path").is_empty(){"."}else{arg("path")};
        match action {
            "list"=>self.list(path,kw["recursive"].as_bool().unwrap_or(false)),
            "read"=>self.read(path,kw["start_line"].as_u64().map(|x|x as usize),kw["end_line"].as_u64().map(|x|x as usize)),
            "write"=>self.write(path,arg("content"),false),"append"=>self.write(path,arg("content"),true),
            "edit"=>self.edit(path,arg("old_text"),arg("new_text"),kw["count"].as_i64().unwrap_or(1)),
            "delete"=>{let p=self.resolve(path)?;fs::remove_file(p)?;Ok(json!({"deleted":path}))},
            "move"=>{let s=self.resolve(arg("source"))?;let d=self.resolve(arg("destination"))?;
                if let Some(p)=d.parent(){fs::create_dir_all(p)?}fs::rename(s,d)?;Ok(json!({"from":arg("source"),"to":arg("destination")}))},
            "copy"=>{let s=self.resolve(arg("source"))?;let d=self.resolve(arg("destination"))?;
                if let Some(p)=d.parent(){fs::create_dir_all(p)?}fs::copy(s,d)?;Ok(json!({"from":arg("source"),"to":arg("destination")}))},
            "mkdir"=>{fs::create_dir_all(self.resolve(path)?)?;Ok(json!({"created":path}))},
            "rmdir"=>{if path=="."{bail!("refusing workspace root deletion")}
                let p=self.resolve(path)?;if kw["recursive"].as_bool().unwrap_or(true){fs::remove_dir_all(p)?}else{fs::remove_dir(p)?}Ok(json!({"deleted":path}))},
            "exists"=>Ok(json!({"exists":self.resolve(path)?.exists()})),
            "info"=>self.info(&self.resolve(path)?),
            "find_files"=>Ok(json!({"files":self.find_files(arg("name_pattern"),path)?})),
            "grep"|"search_code"=>Ok(json!({"hits":self.search(arg("pattern"),path,true,500)?})),
            "replace_all"=>{
                let pattern=arg("pattern");let replacement=arg("new_text");let dry=kw["dry_run"].as_bool().unwrap_or(false);
                let rx=Regex::new(&if kw["regex"].as_bool().unwrap_or(false){pattern.to_owned()}else{regex::escape(pattern)})?;
                let hits=self.search(pattern,path,kw["regex"].as_bool().unwrap_or(false),10000)?;
                let mut files=BTreeSet::new();for h in hits{if let Some(s)=h["file"].as_str(){files.insert(s.to_string());}}
                let mut edits=0;for file in &files{let p=self.resolve(file)?;let src=fs::read_to_string(&p)?;
                    edits+=rx.find_iter(&src).count();if !dry{fs::write(&p,rx.replace_all(&src,replacement).as_ref())?;}}
                Ok(json!({"files":files.len(),"edits":edits,"dry_run":dry}))
            },
            "find_symbol"=>Ok(json!({"symbols":self.symbols(path,Some(arg("symbol")))?})),
            "symbols"=>Ok(json!({"symbols":self.symbols(path,None)?})),
            "function"|"class"=>{
                let name=if action=="class"{arg("class_name")}else{arg("symbol")};
                let found=self.symbols(path,Some(name))?;
                found.into_iter().next().ok_or_else(||anyhow!("symbol {name} not found"))
            },
            "imports"=>self.imports(path),
            "exports"=>Ok(json!({"exports":self.symbols(path,None)?.iter().map(|x|x["name"].clone()).collect::<Vec<_>>()})),
            "dependencies"=>self.imports(path),
            "project_structure"=>self.structure(6),"module_graph"=>self.module_graph(),
            "ast"=>{let src=fs::read_to_string(self.resolve(path)?)?;Ok(json!({"file":path,"symbols":self.symbols(path,None)?,"lines":src.lines().count(),"note":"structural symbol index; not a full language AST"}))},
            _=>bail!("unsupported workspace action: {action}")
        }
    }
}
fn text_file(path:&Path)->bool{let ext=path.extension().and_then(|e|e.to_str()).unwrap_or("").to_lowercase();
    ["rs","py","ts","tsx","js","jsx","json","toml","yaml","yml","md","txt","go","rb","php","css","html","xml","sql","java","cs","cpp","h","sh","ps1","swift","kt","gradle"].contains(&ext.as_str())}
