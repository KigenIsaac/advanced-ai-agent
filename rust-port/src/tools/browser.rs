//! Native Chromium DevTools Protocol browser automation (no Python/Node
//! sidecars). A Chrome/Chromium installation is needed at runtime.
use anyhow::{anyhow,bail,Context,Result};
use base64::{engine::general_purpose::STANDARD,Engine};
use futures_util::{SinkExt,StreamExt};
use serde_json::{json,Value};
use std::{collections::{BTreeMap,HashMap},path::PathBuf,sync::Arc};
use tokio::{net::TcpStream,process::{Child,Command},sync::Mutex};
use tokio_tungstenite::{connect_async,tungstenite::Message,MaybeTlsStream,WebSocketStream};
use url::Url;
use uuid::Uuid;

type Socket=WebSocketStream<MaybeTlsStream<TcpStream>>;
struct BrowserSession {socket:Socket,seq:u64,events:Vec<Value>,target:String}
#[derive(Clone)]pub struct Browser {session:Arc<Mutex<Option<BrowserSession>>>,child:Arc<Mutex<Option<Child>>>,
    port:u16,download_dir:PathBuf,headless:bool,profile:PathBuf,active_url:Arc<Mutex<String>>}
impl Browser {
 pub fn new(download_dir:PathBuf,headless:bool)->Self{
    let port=35000+(std::process::id()%25000) as u16;
    let profile=std::env::temp_dir().join(format!("advanced-ai-agent-chrome-{}",Uuid::new_v4()));
    Self{session:Arc::new(Mutex::new(None)),child:Arc::new(Mutex::new(None)),port,download_dir,headless,profile,
        active_url:Arc::new(Mutex::new("about:blank".into()))}
 }
 async fn start(&self)->Result<()> {
    if self.session.lock().await.is_some(){return Ok(())}
    let binary=["chromium","chromium-browser","google-chrome","google-chrome-stable","chrome","msedge"]
        .iter().find_map(|b|which::which(b).ok()).ok_or_else(||anyhow!("Chromium/Chrome executable not found"))?;
    let mut cmd=Command::new(binary);
    cmd.arg(format!("--remote-debugging-port={}",self.port))
        .arg(format!("--user-data-dir={}",self.profile.display())).arg("--no-first-run")
        .arg("--no-default-browser-check").arg("--disable-background-networking")
        .arg("about:blank").stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null());
    if self.headless{cmd.arg("--headless=new");}
    let child=cmd.spawn()?;*self.child.lock().await=Some(child);
    let http=reqwest::Client::builder().timeout(std::time::Duration::from_secs(2)).build()?;
    let mut target=None;
    for _ in 0..50{
        if let Ok(response)=http.get(format!("http://127.0.0.1:{}/json/list",self.port)).send().await {
            if let Ok(entries)=response.json::<Vec<Value>>().await {
                if let Some(page)=entries.iter().find(|v|v["type"]=="page" && v["webSocketDebuggerUrl"].as_str().is_some()){
                    target=Some((page["id"].as_str().unwrap_or("").to_owned(),page["webSocketDebuggerUrl"].as_str().unwrap_or("").to_owned()));break;}
            }
        }
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
    }
    let (id,url)=target.ok_or_else(||anyhow!("Chromium CDP debugging endpoint not available"))?;
    let (socket,_)=connect_async(url).await.context("connecting to Chrome DevTools")?;
    let mut session=BrowserSession{socket,seq:0,events:Vec::new(),target:id};
    let _=request(&mut session,"Page.enable",json!({})).await;
    let _=request(&mut session,"Runtime.enable",json!({})).await;
    let _=request(&mut session,"Network.enable",json!({})).await;
    *self.session.lock().await=Some(session);Ok(())
 }
 pub async fn close(&self)->Result<()> {
    *self.session.lock().await=None;
    if let Some(mut child)=self.child.lock().await.take(){let _=child.kill().await;}
    let _=std::fs::remove_dir_all(&self.profile);Ok(())
 }
 async fn cdp(&self,method:&str,params:Value)->Result<Value>{self.start().await?;
    let mut lock=self.session.lock().await;
    request(lock.as_mut().unwrap(),method,params).await
 }
 async fn eval(&self,expression:&str)->Result<Value>{
    let response=self.cdp("Runtime.evaluate",json!({"expression":expression,"returnByValue":true,"awaitPromise":true})).await?;
    if !response["exceptionDetails"].is_null(){bail!("JavaScript exception: {}",response["exceptionDetails"])}
    Ok(response["result"]["value"].clone())
 }
 async fn js(&self,script:&str)->Result<Value>{self.eval(&format!("(async()=>{{{script}}})() ")).await}
 async fn navigate(&self,url:&str)->Result<Value>{self.cdp("Page.navigate",json!({"url":url})).await?;
    *self.active_url.lock().await=url.into();
    tokio::time::sleep(std::time::Duration::from_millis(300)).await;
    Ok(json!({"url":url,"title":self.eval("document.title").await.unwrap_or(Value::Null)}))}
 fn jsarg(text:&str)->String{serde_json::to_string(text).unwrap_or_else(|_|"\"\"".to_string())}
 async fn selector_action(&self,selector:&str,expr:&str)->Result<Value>{let s=Self::jsarg(selector);
    self.js(&format!("const el=document.querySelector({s});if(!el)throw Error('Element not found');{expr}")).await}
 pub async fn dispatch(&self,action:&str,kw:&Value)->Result<Value>{
    let a=|key:&str|kw[key].as_str().unwrap_or("");let sel=a("selector");let url=a("url");
    match action{
       "open"=>self.navigate(url).await,
       "back"=>self.js("history.back();return {ok:true}").await,
       "forward"=>self.js("history.forward();return {ok:true}").await,
       "reload"=>self.js("location.reload();return {ok:true}").await,
       "wait"=>{if let Some(selector)=kw["wait_for"].as_str(){let timeout=kw["timeout"].as_u64().unwrap_or(15_000);
            let deadline=tokio::time::Instant::now()+std::time::Duration::from_millis(timeout);
            loop{let value=self.eval(&format!("Boolean(document.querySelector({}))",Self::jsarg(selector))).await?;
                if value==true{break}if tokio::time::Instant::now()>=deadline{bail!("wait_for selector timed out")}
                tokio::time::sleep(std::time::Duration::from_millis(200)).await;}}
            else{tokio::time::sleep(std::time::Duration::from_millis(kw["timeout"].as_u64().unwrap_or(1000))).await;}Ok(json!({"ok":true}))},
       "get_text"=>Ok(json!({"text":self.eval("document.body?.innerText || ''").await?})),
       "get_html"|"get_dom"=>{let data=self.eval("document.documentElement.outerHTML").await?;
           if action=="get_dom"{Ok(json!({"dom":data}))}else{Ok(json!({"html":data}))}},
       "get_url"=>Ok(json!({"url":self.eval("location.href").await?})),
       "get_title"=>Ok(json!({"title":self.eval("document.title").await?})),
       "get_metadata"=>self.eval("({title:document.title,url:location.href,description:document.querySelector('meta[name=description]')?.content,lang:document.documentElement.lang})").await,
       "get_links"=>Ok(json!({"links":self.eval("Array.from(document.querySelectorAll('a[href]')).slice(0,1000).map(a=>({text:a.innerText,href:a.href}))").await?})),
       "get_headings"=>Ok(json!({"headings":self.eval("Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6')).map(h=>({level:h.tagName,text:h.innerText}))").await?})),
       "get_interactive"|"get_accessibility"|"semantic_snapshot"=>{
            let value=self.eval("Array.from(document.querySelectorAll('a,button,input,select,textarea,[role=button]')).slice(0,1000).map((e,i)=>({index:i,kind:e.tagName.toLowerCase(),name:e.getAttribute('aria-label')||e.innerText||e.getAttribute('placeholder')||'',id:e.id,outer:e.outerHTML.slice(0,400)}))").await?;
            Ok(match action{"semantic_snapshot"=>json!({"snapshot":value.to_string()}),
                "get_accessibility"=>json!({"nodes":value}),_=>json!({"elements":value})})
       },
       "find_main"=>Ok(json!({"text":self.eval("document.querySelector('main,article,[role=main]')?.innerText||document.body.innerText").await?})),
       "click"=>self.selector_action(sel,"el.click();return {ok:true}").await,
       "click_text"=>{let text=Self::jsarg(a("text"));self.js(&format!("const el=Array.from(document.querySelectorAll('button,a,[role=button]')).find(e=>e.innerText.includes({text}));if(!el)throw Error('not found');el.click();return {{ok:true}}")).await},
       "fill"|"type"=>{let txt=Self::jsarg(kw["text"].as_str().or_else(||kw["value"].as_str()).unwrap_or(""));
            self.selector_action(sel,&format!("el.value={txt};el.dispatchEvent(new Event('input',{{bubbles:true}}));el.dispatchEvent(new Event('change',{{bubbles:true}}));return {{ok:true}}")).await},
       "press"=>self.cdp("Input.dispatchKeyEvent",json!({"type":"keyDown","key":a("key")})).await,
       "scroll"=>{let amount=kw["amount"].as_i64().unwrap_or(800);
            let (x,y)=match a("direction"){"up"=>(0,-amount),"left"=>(-amount,0),"right"=>(amount,0),_=>(0,amount)};
            self.eval(&format!("window.scrollBy({x},{y});true")).await},
       "hover"=>self.selector_action(sel,"const r=el.getBoundingClientRect();el.dispatchEvent(new MouseEvent('mouseover',{bubbles:true,clientX:r.x+r.width/2,clientY:r.y+r.height/2}));return {ok:true}").await,
       "select"=>{let val=Self::jsarg(a("value"));self.selector_action(sel,&format!("el.value={val};el.dispatchEvent(new Event('change',{{bubbles:true}}));return {{ok:true}}")).await},
       "check"|"uncheck"=>self.selector_action(sel,&format!("el.checked={};el.dispatchEvent(new Event('change',{{bubbles:true}}));return {{ok:true}}",action=="check")).await,
       "upload"=>{
           let file=std::fs::canonicalize(a("file_path"))?;
           let root=std::fs::canonicalize(self.download_dir.parent().ok_or_else(||anyhow!("missing workspace root"))?)?;
           if !file.starts_with(&root){bail!("upload path escapes the authorized workspace")}
           let doc=self.cdp("DOM.getDocument",json!({})).await?;
           let queried=self.cdp("DOM.querySelector",json!({"nodeId":doc["root"]["nodeId"],"selector":sel})).await?;
           let node=queried["nodeId"].as_i64().unwrap_or(0);if node==0{bail!("upload element not found")}
           self.cdp("DOM.setFileInputFiles",json!({"nodeId":node,"files":[file.to_string_lossy().to_string()]})).await
       },
       "execute_js"=>self.js(a("script")).await,
       "evaluate"=>self.eval(a("script")).await,
       "screenshot"|"screenshot_element"=>{
           let mut settings=json!({"format":"png","captureBeyondViewport":kw["full_page"]==true});
           if action=="screenshot_element"{
               let bounds=self.eval(&format!("(()=>{{const r=document.querySelector({})?.getBoundingClientRect();return r?{{x:r.x,y:r.y,width:r.width,height:r.height}}:null}})()",Self::jsarg(sel))).await?;
               if bounds.is_null(){bail!("element not found")}
               settings["clip"]=json!({"x":bounds["x"],"y":bounds["y"],"width":bounds["width"],"height":bounds["height"],"scale":1});
           }
           let result=self.cdp("Page.captureScreenshot",settings).await?;
           let data=result["data"].as_str().unwrap_or("");Ok(json!({"format":"png","base64":data,"bytes":STANDARD.decode(data).map(|v|v.len()).unwrap_or(0)}))},
       "list_tabs"=>{let entries=reqwest::get(format!("http://127.0.0.1:{}/json/list",self.port)).await?.json::<Value>().await?;Ok(json!({"tabs":entries}))},
       "new_tab"=>{let target=self.cdp("Target.createTarget",json!({"url":if url.is_empty(){"about:blank"}else{url}})).await?;
           Ok(json!({"tab_id":target["targetId"]}))},
       "switch_tab"=>{self.start().await?;let client=reqwest::Client::new();let list:Vec<Value>=client.get(format!("http://127.0.0.1:{}/json/list",self.port)).send().await?.json().await?;
            let id=a("tab_id");let page=list.iter().find(|v|v["id"]==id).ok_or_else(||anyhow!("tab not found"))?;
            let (socket,_)=connect_async(page["webSocketDebuggerUrl"].as_str().ok_or_else(||anyhow!("missing WS URL"))?).await?;
            let mut lock=self.session.lock().await;*lock=Some(BrowserSession{socket,seq:0,events:Vec::new(),target:id.to_owned()});
            drop(lock);self.cdp("Page.enable",json!({})).await?;Ok(json!({"ok":true}))},
       "close_tab"=>self.cdp("Target.closeTarget",json!({"targetId":a("tab_id")})).await,
       "get_frames"=>Ok(json!({"frames":self.cdp("Page.getFrameTree",json!({})).await?})),
       "get_frame_content"=>{
           let frames=self.cdp("Page.getFrameTree",json!({})).await?;
           fn gather(tree:&Value,out:&mut Vec<Value>){if tree["frame"].is_object(){out.push(tree["frame"].clone())}
               if let Some(children)=tree["childFrames"].as_array(){for child in children{gather(child,out)}}}
           let mut all=vec![];gather(&frames["frameTree"],&mut all);
           let frame=kw["form_index"].as_u64().and_then(|n|all.get(n as usize)).or_else(||all.iter().find(|f|{
               let key=kw["text"].as_str().unwrap_or("");!key.is_empty()&&f["url"].as_str().unwrap_or("").contains(key)}))
               .ok_or_else(||anyhow!("frame not found"))?;
           let world=self.cdp("Page.createIsolatedWorld",json!({"frameId":frame["id"],"worldName":"advanced-agent-inspection"})).await?;
           let result=self.cdp("Runtime.evaluate",json!({"expression":"document.documentElement.outerHTML","returnByValue":true,"contextId":world["executionContextId"]})).await?;
           Ok(json!({"html":result["result"]["value"]}))
       },
       "get_network"|"get_api_calls"=>{self.start().await?;let lock=self.session.lock().await;
            let events=&lock.as_ref().unwrap().events;
            Ok(json!({"entries":if action=="get_api_calls"{events.iter().filter(|v|v["method"]=="Network.requestWillBeSent").take(250).cloned().collect::<Vec<_>>()}else{events.iter().take(500).cloned().collect::<Vec<_>>()}}))},
       "get_response_body"=>{
           let request_id=if !a("request_id").is_empty(){a("request_id").to_owned()}
           else{let needle=url.to_string();let _=self.cdp("Runtime.evaluate",json!({"expression":"0"})).await?;
               let lock=self.session.lock().await;
               lock.as_ref().unwrap().events.iter().rev().find(|v|v["method"]=="Network.requestWillBeSent"&&v["params"]["request"]["url"]==needle)
                   .and_then(|v|v["params"]["requestId"].as_str()).ok_or_else(||anyhow!("request URL not seen by network recorder"))?.to_owned()};
           self.cdp("Network.getResponseBody",json!({"requestId":request_id})).await
       },
       "download"=>{let target=PathBuf::from(a("save_as"));let name=if a("save_as").is_empty(){format!("download-{}",Uuid::new_v4())}else{target.file_name().unwrap_or_default().to_string_lossy().into_owned()};
            std::fs::create_dir_all(&self.download_dir)?;
            let content=reqwest::get(url).await?.error_for_status()?.bytes().await?;
            let path=self.download_dir.join(name);std::fs::write(&path,&content)?;Ok(json!({"path":path,"bytes":content.len()}))},
       "download_link"=>{let target=self.eval(&format!("document.querySelector({})?.href||null",Self::jsarg(sel))).await?;
           let link=target.as_str().ok_or_else(||anyhow!("download link not found"))?;
           self.dispatch_download(link,kw["save_as"].as_str()).await
       },
       "get_downloads"=>Ok(json!({"downloads":std::fs::read_dir(&self.download_dir).ok().into_iter().flatten().filter_map(|x|x.ok()).map(|x|x.path().display().to_string()).collect::<Vec<_>>()})),
       "get_state"=>Ok(json!({"active_url":self.active_url.lock().await.clone(),"debug_port":self.port})),
       "save_session"=>{let result=self.cdp("Network.getAllCookies",json!({})).await?;
           std::fs::write(a("file_path"),serde_json::to_vec_pretty(&result)?)?;Ok(json!({"path":a("file_path")}))},
       "restore_session"=>{let data:Value=serde_json::from_slice(&std::fs::read(a("file_path"))?)?;
           self.cdp("Network.setCookies",json!({"cookies":data["cookies"]})).await},
       "get_cookies"=>self.cdp("Network.getAllCookies",json!({})).await,
       "get_local_storage"=>self.eval("Object.assign({},localStorage)").await,
       "find_element"|"find_button"|"find_link"=>{
           let text=Self::jsarg(a("text"));let role=match action{"find_button"=>"button,[role=button]","find_link"=>"a",_=>"a,button,input,select,textarea,[role=button]"};
           self.eval(&format!("Array.from(document.querySelectorAll('{}')).find(e=>(e.innerText||e.ariaLabel||e.placeholder||'').toLowerCase().includes({text}.toLowerCase()))?.outerHTML||null",role)).await},
       "detect_challenge"=>{let text=self.eval("document.body?.innerText.toLowerCase()||''").await?.as_str().unwrap_or("").to_string();
           Ok(json!({"captcha":text.contains("captcha")||text.contains("verify you are human"),
               "login_wall":text.contains("sign in to continue"),"access_denied":text.contains("access denied"),"rate_limit":text.contains("too many requests")}))},
       "recover_navigation"=>{self.navigate("about:blank").await?;self.navigate(url).await},
       _=>bail!("unknown browser action: {action}")
    }
 }
}
async fn request(session:&mut BrowserSession,method:&str,params:Value)->Result<Value>{
    session.seq+=1;let id=session.seq;let message=json!({"id":id,"method":method,"params":params});
    session.socket.send(Message::Text(message.to_string().into())).await?;
    let deadline=tokio::time::Instant::now()+std::time::Duration::from_secs(30);
    loop{let remaining=deadline.saturating_duration_since(tokio::time::Instant::now());
        let incoming=tokio::time::timeout(remaining,session.socket.next()).await.context("CDP request timeout")?
            .ok_or_else(||anyhow!("Chrome DevTools disconnected"))??;
        if let Message::Text(raw)=incoming{let v:Value=serde_json::from_str(raw.as_str())?;
            if v["id"].as_u64()==Some(id){if !v["error"].is_null(){bail!("CDP {method}: {}",v["error"])}return Ok(v["result"].clone())}
            if !v["method"].is_null(){if session.events.len()>=2000{session.events.remove(0);}session.events.push(v);}
        }
    }
}
