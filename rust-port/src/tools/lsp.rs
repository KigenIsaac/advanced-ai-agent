//! Language Server Protocol JSON-RPC client, with correct async initialization.
use super::workspace::Workspace;
use anyhow::{bail,Result};
use serde_json::{json,Value};
use std::{collections::HashMap,sync::{Arc,atomic::{AtomicU64,Ordering}}};
use tokio::{io::{AsyncBufReadExt,AsyncReadExt,AsyncWriteExt,BufReader},process::{ChildStdin,Command},sync::{Mutex,oneshot}};

#[derive(Clone)]pub struct Lsp {ws:Arc<Workspace>,inner:Arc<Mutex<Option<Connection>>>}
struct Connection {stdin:Arc<Mutex<ChildStdin>>,pending:Arc<Mutex<HashMap<u64,oneshot::Sender<Result<Value,String>>>>>,seq:Arc<AtomicU64>,language:String}
impl Lsp {
 pub fn new(ws:Arc<Workspace>)->Self{Self{ws,inner:Arc::new(Mutex::new(None))}}
 async fn ensure(&self)->Result<()> {
    let mut lock=self.inner.lock().await;if lock.is_some(){return Ok(())}
    let servers=[("pylsp",vec!["pylsp"],"python"),("pyright-langserver",vec!["pyright-langserver","--stdio"],"python"),
        ("typescript-language-server",vec!["typescript-language-server","--stdio"],"typescript"),
        ("gopls",vec!["gopls"],"go"),("rust-analyzer",vec!["rust-analyzer"],"rust")];
    let Some((_,cmd,language))=servers.iter().find(|(name,_,_)|which::which(name).is_ok())else{bail!("no language server available")};
    let mut process=Command::new(cmd[0]);process.args(&cmd[1..]).current_dir(&self.ws.root).stdin(std::process::Stdio::piped()).stdout(std::process::Stdio::piped()).stderr(std::process::Stdio::null());
    let mut child=process.spawn()?;let stdin=Arc::new(Mutex::new(child.stdin.take().unwrap()));
    let stdout=child.stdout.take().unwrap();let pending:Arc<Mutex<HashMap<u64,oneshot::Sender<Result<Value,String>>>>>=Arc::new(Mutex::new(HashMap::new()));
    let receivers=pending.clone();tokio::spawn(async move{
        let mut reader=BufReader::new(stdout);
        loop{let mut length=0usize;
            loop{let mut header=String::new();let n=reader.read_line(&mut header).await.unwrap_or(0);if n==0{return}
                if header.trim().is_empty(){break}
                if let Some(value)=header.to_lowercase().strip_prefix("content-length:"){
                    length=value.trim().parse().unwrap_or(0);}}
            if length==0||length>20_000_000{return}
            let mut payload=vec![0;length];if reader.read_exact(&mut payload).await.is_err(){return}
            let Ok(data)=serde_json::from_slice::<Value>(&payload)else{continue};
            if let Some(id)=data["id"].as_u64(){if let Some(tx)=receivers.lock().await.remove(&id){let response=if data.get("error").is_some(){Err(data["error"].to_string())}else{Ok(data["result"].clone())};let _=tx.send(response);}}
        }
    });
    let conn=Connection{stdin,pending,seq:Arc::new(AtomicU64::new(0)),language:(*language).into()};
    let root_uri=format!("file://{}",self.ws.root.display());
    let init=rpc_request(&conn,"initialize",json!({"processId":std::process::id(),"rootUri":root_uri,
       "capabilities":{"textDocument":{"definition":{},"references":{},"hover":{},"documentSymbol":{},"rename":{},"diagnostic":{}},"workspace":{"symbol":{}}}})).await?;
    let _=init;rpc_notify(&conn,"initialized",json!({})).await?;
    *lock=Some(conn);Ok(())
 }
 pub async fn dispatch(&self,action:&str,kw:&Value)->Result<Value>{
    if self.ensure().await.is_err(){return match action {
       "symbols"=>Ok(json!({"symbols":self.ws.symbols(kw["file"].as_str().unwrap_or("."),None)?})),
       "definition"=>Ok(json!({"definitions":self.ws.symbols(".",kw["query"].as_str())?})),
       _=>bail!("no language server available for action {action}")};}
    let lock=self.inner.lock().await;let conn=lock.as_ref().unwrap();let file=kw["file"].as_str().unwrap_or("");
    let uri=format!("file://{}",self.ws.resolve(file)?.display());
    if action!="workspace_symbols" {
        let text=self.ws.read(file,None,None)?["content"].as_str().unwrap_or("").to_string();
        rpc_notify(conn,"textDocument/didOpen",json!({"textDocument":{"uri":uri,"languageId":conn.language,"version":1,"text":text}})).await?;
    }
    let position=json!({"line":kw["line"].as_u64().unwrap_or(1).saturating_sub(1),"character":kw["col"].as_u64().unwrap_or(1).saturating_sub(1)});
    let result=match action {
        "definition"=>rpc_request(conn,"textDocument/definition",json!({"textDocument":{"uri":uri},"position":position})).await?,
        "references"=>rpc_request(conn,"textDocument/references",json!({"textDocument":{"uri":uri},"position":position,"context":{"includeDeclaration":true}})).await?,
        "hover"=>rpc_request(conn,"textDocument/hover",json!({"textDocument":{"uri":uri},"position":position})).await?,
        "rename"=>rpc_request(conn,"textDocument/rename",json!({"textDocument":{"uri":uri},"position":position,"newName":kw["new_name"]})).await?,
        "symbols"=>rpc_request(conn,"textDocument/documentSymbol",json!({"textDocument":{"uri":uri}})).await?,
        "workspace_symbols"=>rpc_request(conn,"workspace/symbol",json!({"query":kw["query"]})).await?,
        "diagnostics"=>rpc_request(conn,"textDocument/diagnostic",json!({"textDocument":{"uri":uri}})).await?,
        _=>bail!("unknown code action: {action}")};
    Ok(match action {"definition"=>json!({"definitions":result}),"references"=>json!({"references":result}),
        "symbols"|"workspace_symbols"=>json!({"symbols":result}),"diagnostics"=>json!({"diagnostics":result}),_=>result})
 }
}
async fn rpc_notify(conn:&Connection,method:&str,params:Value)->Result<()> {let s=json!({"jsonrpc":"2.0","method":method,"params":params});send(conn,&s).await}
async fn rpc_request(conn:&Connection,method:&str,params:Value)->Result<Value>{let id=conn.seq.fetch_add(1,Ordering::SeqCst)+1;
    let (tx,rx)=oneshot::channel();conn.pending.lock().await.insert(id,tx);
    send(conn,&json!({"jsonrpc":"2.0","id":id,"method":method,"params":params})).await?;
    let reply=tokio::time::timeout(std::time::Duration::from_secs(30),rx).await??;
    reply.map_err(anyhow::Error::msg)
}
async fn send(conn:&Connection,value:&Value)->Result<()> {let data=serde_json::to_vec(value)?;
    let mut stdin=conn.stdin.lock().await;
    stdin.write_all(format!("Content-Length: {}\r\n\r\n",data.len()).as_bytes()).await?;
    stdin.write_all(&data).await?;stdin.flush().await?;Ok(())}
