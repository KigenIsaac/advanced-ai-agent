use advanced_ai_agent::{Config,Provider,ToolRegistry};
use advanced_ai_agent::tools::{workspace::Workspace,testing::Testing};
use serde_json::json;
use tempfile::tempdir;

fn config(root:&std::path::Path)->Config{
    Config{provider:Provider::Local,base_url:"http://127.0.0.1:11434/v1".into(),model:"llama3.1".into(),
        api_keys:vec!["local".into()],agent_name:"Test".into(),agent_tagline:"test".into(),
        custom_instructions:String::new(),project_root:root.into(),home:root.join(".agnes"),
        max_rounds:40,max_tokens:1024,temperature:0.3,rpm:10,max_retries:1,swarm_workers:1,
        swarm:false,thinking:false,stream:false,verbose:false,browser_headless:true,
        web_enabled:false,code_enabled:true,strict_approval:true}
}
#[test]
fn workspace_round_trip_and_protection(){
    let dir=tempdir().unwrap();let ws=Workspace::new(dir.path()).unwrap();
    ws.write("src/example.py","class Demo:\n    def run(self):\n        return 42\n",false).unwrap();
    assert!(ws.read("src/example.py",None,None).unwrap()["content"].as_str().unwrap().contains("return 42"));
    assert!(ws.resolve("../outside.py").is_err());
    assert_eq!(ws.symbols(".",Some("run")).unwrap().len(),1);
    assert_eq!(ws.search("return 42",".",false,100).unwrap().len(),1);
}
#[test]
fn replacing_ambiguous_text_requires_explicit_intent(){
    let dir=tempdir().unwrap();let ws=Workspace::new(dir.path()).unwrap();
    ws.write("f.txt","x\nx\n",false).unwrap();
    assert!(ws.edit("f.txt","x","y",1).is_err());
    assert!(ws.edit("f.txt","x","y",-1).is_ok());
}
#[test]
fn provider_names_and_endpoints_preserved(){
    let p=Provider::from_name("deepseek").unwrap();assert_eq!(p.name(),"deepseek");
    assert_eq!(p.defaults().0,"https://api.deepseek.com");
    assert!(Provider::from_name("madeup").is_err());
}
#[test]
fn test_command_compatibility(){
    assert_eq!(Testing::command("pytest",None,None),"pytest -v");
    assert_eq!(Testing::command("pytest",Some("tests/test_api.py"),None),"pytest -v tests/test_api.py");
    assert_eq!(Testing::command("cargo",None,Some("unit_test")),"cargo test unit_test");
    assert_eq!(Testing::command("go",None,None),"go test ./...");
}
#[tokio::test]
async fn registry_exports_all_coding_tool_categories(){
    let dir=tempdir().unwrap();let cfg=config(dir.path());let registry=ToolRegistry::new(&cfg).unwrap();
    let names=registry.names().await;
    for tool in ["workspace","terminal","code","editor","test","build","git","packages",
      "debug","http","database","project","review","sandbox","deploy"]{
        assert!(names.contains(&tool.to_string()),"missing {tool}");
    }
    assert!(!names.contains(&"web_search".to_string()));
}
#[tokio::test]
async fn database_is_readonly_even_for_with_clauses(){
    let dir=tempdir().unwrap();let db=dir.path().join("fixture.db");
    {let conn=rusqlite::Connection::open(&db).unwrap();
        conn.execute_batch("CREATE TABLE sample(id INTEGER PRIMARY KEY, value TEXT); INSERT INTO sample(value) VALUES ('hello');").unwrap();}
    let cfg=config(dir.path());let registry=ToolRegistry::new(&cfg).unwrap();
    let connected=registry.dispatch("database",&json!({"action":"connect_sqlite","path":"fixture.db"})).await;
    assert_eq!(connected["ok"],true);
    let rows=registry.dispatch("database",&json!({"action":"query","query":"SELECT id,value FROM sample"})).await;
    assert_eq!(rows["count"],1);
    let write=registry.dispatch("database",&json!({"action":"query","query":"WITH x AS (SELECT 1) DELETE FROM sample"})).await;
    assert!(write.get("error").is_some());
}
#[tokio::test]
async fn unsafe_terminal_operations_denied_without_host_approval(){
    let dir=tempdir().unwrap();let ws=std::sync::Arc::new(Workspace::new(dir.path()).unwrap());
    let term=advanced_ai_agent::tools::terminal::Terminal::new(ws,false);
    assert!(term.guard("rm -rf /somewhere").is_err());
    assert!(term.guard("git push --force origin master").is_err());
    assert!(term.guard("cargo test").is_ok());
}

#[tokio::test]
async fn agent_performs_real_tool_round_trip_against_mock_model() {
    use advanced_ai_agent::{Agent,LlmClient};
    use tokio::io::{AsyncReadExt,AsyncWriteExt};
    use std::sync::Arc;
    let listener=tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr=listener.local_addr().unwrap();
    let server=tokio::spawn(async move {
        for i in 0..2 {
            let (mut stream,_)=listener.accept().await.unwrap();
            let mut received=[0u8;16000];
            let n=tokio::time::timeout(std::time::Duration::from_secs(5),stream.read(&mut received)).await.unwrap().unwrap();
            assert!(String::from_utf8_lossy(&received[..n]).contains("/v1/chat/completions"));
            let reply=if i==0 {
                json!({"id":"test1","choices":[{"index":0,"message":{"role":"assistant","content":null,
                    "tool_calls":[{"id":"call_one","type":"function","function":{"name":"workspace",
                    "arguments":"{\"action\":\"write\",\"path\":\"test-output.txt\",\"content\":\"native-rust\"}"}}]},
                    "finish_reason":"tool_calls"}]})
            } else {
                json!({"id":"test2","choices":[{"index":0,"message":{"role":"assistant",
                    "content":"DONE: created test-output.txt"},"finish_reason":"stop"}]})
            };
            let content=reply.to_string();
            let header=format!("HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n",content.len());
            stream.write_all(format!("{header}{content}").as_bytes()).await.unwrap();
        }
    });
    let root=tempdir().unwrap();
    let mut cfg=config(root.path());
    cfg.base_url=format!("http://{addr}/v1");
    let cfg=Arc::new(cfg);
    let client=LlmClient::new(cfg.clone()).unwrap();
    let tools=ToolRegistry::new(&cfg).unwrap();
    let mut agent=Agent::session(cfg.clone(),client,tools);
    let outcome=agent.send("Create test-output.txt with native-rust").await.unwrap();
    assert_eq!(outcome.tool_calls,1);
    assert!(!outcome.stopped_by_limit);
    assert!(outcome.content.starts_with("DONE:"));
    assert_eq!(std::fs::read_to_string(root.path().join("test-output.txt")).unwrap(),"native-rust");
    server.await.unwrap();
}
