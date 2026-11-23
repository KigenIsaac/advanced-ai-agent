//! Model HTTP APIs. Supports all original provider names with native Anthropic,
//! Gemini and OpenAI-compatible request/response and SSE protocol translation.
use crate::{config::{Config,Provider},rate::KeyPool};
use anyhow::{Context,Result,bail};
use reqwest::{Client,StatusCode};
use serde::{Deserialize,Serialize};
use serde_json::{json,Value};
use std::{collections::BTreeMap,sync::Arc,time::Duration};
use uuid::Uuid;

#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct ToolCall {pub id:String,pub name:String,pub arguments:Value}
#[derive(Debug,Clone,Default)]
pub struct Usage {pub input_tokens:u64,pub output_tokens:u64,pub cached_tokens:u64}
#[derive(Debug,Clone,Default)]
pub struct ModelResponse {pub content:String,pub reasoning:String,pub tool_calls:Vec<ToolCall>,pub usage:Usage,pub finish_reason:Option<String>}
#[derive(Clone)]
pub struct LlmClient {pub config:Arc<Config>,client:Client,pool:KeyPool}
impl LlmClient {
    pub fn new(config:Arc<Config>)->Result<Self>{
        let pool=KeyPool::new(config.api_keys.clone(),config.rpm)?;
        let client=Client::builder().timeout(Duration::from_secs(300)).connect_timeout(Duration::from_secs(20)).build()?;
        Ok(Self{config,client,pool})
    }
    pub fn pool(&self)->&KeyPool{&self.pool}
    pub async fn chat(&self,messages:&[Value],tools:&[Value],max_tokens:u32)->Result<ModelResponse>{
        let mut attempt=0usize;
        loop {
            let key=self.pool.acquire().await;
            let (url,payload)=self.request(messages,tools,max_tokens,&key)?;
            let mut req=self.client.post(&url).json(&payload);
            req=req.header("User-Agent","advanced-ai-agent-rust/0.1");
            match self.config.provider {
                Provider::Anthropic=>req=req.header("x-api-key",&key).header("anthropic-version","2023-06-01"),
                Provider::Gemini=>{},
                _=>req=req.bearer_auth(&key),
            }
            match req.send().await {
                Ok(response)=>{
                    let status=response.status();
                    if status==StatusCode::TOO_MANY_REQUESTS {
                        let wait=response.headers().get("retry-after").and_then(|v|v.to_str().ok()).and_then(|v|v.parse::<u64>().ok())
                            .unwrap_or(2u64.saturating_pow((attempt+1).min(6) as u32));
                        self.pool.cooldown(&key,Duration::from_secs(wait.min(3600))).await;
                        if attempt>=self.config.max_retries {bail!("provider rate limit exhausted after {} attempts",attempt+1)}
                        attempt+=1;continue;
                    }
                    if !status.is_success() {
                        let txt=response.text().await.unwrap_or_default();
                        if status.is_server_error() && attempt<self.config.max_retries {
                            attempt+=1;tokio::time::sleep(Duration::from_secs(2u64.saturating_pow(attempt.min(6) as u32))).await;continue;
                        }
                        bail!("provider HTTP {status}: {}",txt.chars().take(1000).collect::<String>());
                    }
                    let content_type=response.headers().get("content-type").and_then(|v|v.to_str().ok()).unwrap_or("").to_string();
                    let body=response.text().await.context("reading model response")?;
                    return if content_type.contains("text/event-stream") || body.trim_start().starts_with("data:") {
                        self.parse_sse(&body)
                    } else {
                        let data:Value=serde_json::from_str(&body).context("malformed provider response")?;
                        self.parse_response(&data)
                    }
                }
                Err(error)=>{
                    if attempt>=self.config.max_retries {return Err(error.into())}
                    attempt+=1;tokio::time::sleep(Duration::from_secs(2u64.saturating_pow(attempt.min(6) as u32))).await;
                }
            }
        }
    }
    fn request(&self,messages:&[Value],tools:&[Value],max_tokens:u32,key:&str)->Result<(String,Value)> {
        match self.config.provider {
            Provider::Gemini=>{
                let mut system=String::new();let mut contents=Vec::new();
                for msg in messages {
                    let role=msg["role"].as_str().unwrap_or("");
                    if role=="system"{system.push_str(msg["content"].as_str().unwrap_or(""));continue}
                    let mut parts=Vec::new();
                    if let Some(txt)=msg["content"].as_str(){if !txt.is_empty(){parts.push(json!({"text":txt}));}}
                    if role=="assistant" {
                        if let Some(calls)=msg["tool_calls"].as_array(){for c in calls {
                            let name=c["function"]["name"].as_str().unwrap_or("tool");
                            let raw=c["function"]["arguments"].as_str().unwrap_or("{}");
                            let args:Value=serde_json::from_str(raw).unwrap_or(json!({}));
                            parts.push(json!({"functionCall":{"name":name,"args":args}}));
                        }}
                    }
                    if role=="tool" {
                        let name=msg["name"].as_str().unwrap_or("tool");
                        let value=msg["content"].as_str().unwrap_or("");
                        parts.push(json!({"functionResponse":{"name":name,"response":{"content":value}}}));
                    }
                    if !parts.is_empty(){contents.push(json!({"role":if role=="assistant"{"model"}else{"user"},"parts":parts}));}
                }
                let declarations:Vec<Value>=tools.iter().map(|tool|json!({
                    "name":tool["function"]["name"],"description":tool["function"]["description"],
                    "parameters":tool["function"]["parameters"]})).collect();
                let mut body=json!({"contents":contents,"generationConfig":{"temperature":self.config.temperature,"maxOutputTokens":max_tokens}});
                if !system.is_empty(){body["systemInstruction"]=json!({"parts":[{"text":system}]});}
                if !declarations.is_empty(){body["tools"]=json!([{"functionDeclarations":declarations}]);}
                if self.config.thinking{body["generationConfig"]["thinkingConfig"]=json!({"includeThoughts":true});}
                Ok((format!("{}/{}:generateContent?key={}",self.config.base_url.trim_end_matches('/'),self.config.model,key),body))
            }
            Provider::Anthropic=>{
                let mut system=String::new();let mut msgs=Vec::new();
                let mut names=std::collections::HashMap::new();
                for msg in messages {
                    if msg["role"]=="system"{system.push_str(msg["content"].as_str().unwrap_or(""));system.push('\n');continue}
                    if msg["role"]=="assistant" {
                        let mut blocks=Vec::new();if let Some(text)=msg["content"].as_str(){if !text.is_empty(){blocks.push(json!({"type":"text","text":text}));}}
                        if let Some(calls)=msg["tool_calls"].as_array(){for call in calls {
                            let id=call["id"].as_str().unwrap_or("").to_string();
                            let name=call["function"]["name"].as_str().unwrap_or("").to_string();names.insert(id.clone(),name.clone());
                            let input:Value=serde_json::from_str(call["function"]["arguments"].as_str().unwrap_or("{}"))?;
                            blocks.push(json!({"type":"tool_use","id":id,"name":name,"input":input}));
                        }}
                        if !blocks.is_empty(){msgs.push(json!({"role":"assistant","content":blocks}));}
                    }else if msg["role"]=="tool"{
                        let id=msg["tool_call_id"].as_str().unwrap_or("");
                        msgs.push(json!({"role":"user","content":[{"type":"tool_result","tool_use_id":id,"content":msg["content"]}]}));
                    }else{msgs.push(json!({"role":"user","content":msg["content"]}));}
                }
                let atools:Vec<Value>=tools.iter().map(|t|json!({"name":t["function"]["name"],
                    "description":t["function"]["description"],"input_schema":t["function"]["parameters"]})).collect();
                let mut body=json!({"model":self.config.model,"messages":msgs,"max_tokens":max_tokens,
                    "temperature":self.config.temperature,"stream":self.config.stream});
                if !system.is_empty(){body["system"]=json!(system);}
                if !atools.is_empty(){body["tools"]=json!(atools)}
                Ok((format!("{}/messages",self.config.base_url.trim_end_matches('/')),body))
            }
            _=>{
                let mut body=json!({"model":self.config.model,"messages":messages,"stream":self.config.stream,
                    "temperature":self.config.temperature,"max_tokens":max_tokens});
                if !tools.is_empty(){body["tools"]=json!(tools);body["tool_choice"]=json!("auto");}
                if self.config.thinking{body["chat_template_kwargs"]=json!({"enable_thinking":true});}
                Ok((format!("{}/chat/completions",self.config.base_url.trim_end_matches('/')),body))
            }
        }
    }
    fn parse_response(&self,data:&Value)->Result<ModelResponse>{
        match self.config.provider {
            Provider::Gemini=>{
                let mut out=ModelResponse::default();
                if let Some(candidates)=data["candidates"].as_array(){for candidate in candidates{
                    if let Some(parts)=candidate["content"]["parts"].as_array(){for part in parts {
                        if let Some(s)=part["text"].as_str(){out.content.push_str(s)}
                        if let Some(call)=part.get("functionCall"){
                            out.tool_calls.push(ToolCall{id:format!("call_{}",Uuid::new_v4().simple()),name:call["name"].as_str().unwrap_or("").to_string(),arguments:call["args"].clone()});
                        }
                    }}
                    out.finish_reason=candidate["finishReason"].as_str().map(str::to_owned);
                }}
                out.usage.input_tokens=data["usageMetadata"]["promptTokenCount"].as_u64().unwrap_or(0);
                out.usage.output_tokens=data["usageMetadata"]["candidatesTokenCount"].as_u64().unwrap_or(0);
                Ok(out)
            }
            Provider::Anthropic=>{
                let mut out=ModelResponse::default();
                if let Some(blocks)=data["content"].as_array(){for block in blocks{
                    if block["type"]=="text" {out.content.push_str(block["text"].as_str().unwrap_or(""));}
                    if block["type"]=="tool_use"{out.tool_calls.push(ToolCall{id:block["id"].as_str().unwrap_or("").to_string(),name:block["name"].as_str().unwrap_or("").to_string(),arguments:block["input"].clone()});}
                }}
                out.usage.input_tokens=data["usage"]["input_tokens"].as_u64().unwrap_or(0);
                out.usage.output_tokens=data["usage"]["output_tokens"].as_u64().unwrap_or(0);
                out.finish_reason=data["stop_reason"].as_str().map(str::to_owned);Ok(out)
            }
            _=>{
                let mut out=ModelResponse::default();let msg=&data["choices"][0]["message"];
                out.content=msg["content"].as_str().unwrap_or("").to_string();
                out.reasoning=msg["reasoning_content"].as_str().unwrap_or("").to_string();
                if let Some(calls)=msg["tool_calls"].as_array(){for call in calls{
                    out.tool_calls.push(ToolCall{id:call["id"].as_str().unwrap_or("").to_string(),
                        name:call["function"]["name"].as_str().unwrap_or("").to_string(),
                        arguments:decode_arguments(&call["function"]["arguments"])?});
                }}
                out.usage.input_tokens=data["usage"]["prompt_tokens"].as_u64().unwrap_or(0);
                out.usage.output_tokens=data["usage"]["completion_tokens"].as_u64().unwrap_or(0);
                out.usage.cached_tokens=data["usage"]["prompt_tokens_details"]["cached_tokens"].as_u64().unwrap_or(0);
                out.finish_reason=data["choices"][0]["finish_reason"].as_str().map(str::to_owned);Ok(out)
            }
        }
    }
    fn parse_sse(&self,body:&str)->Result<ModelResponse>{
        if matches!(self.config.provider,Provider::Anthropic){return parse_anthropic_sse(body)}
        let mut out=ModelResponse::default();
        let mut calls:BTreeMap<usize,(String,String,String)>=BTreeMap::new();
        for line in body.lines(){let line=line.trim();let Some(data)=line.strip_prefix("data:") else {continue};let data=data.trim();if data=="[DONE]"{break}
            let Ok(payload)=serde_json::from_str::<Value>(data)else{continue};
            if matches!(self.config.provider,Provider::Gemini){return self.parse_response(&payload)}
            let delta=&payload["choices"][0]["delta"];
            if let Some(s)=delta["content"].as_str(){out.content.push_str(s)}
            if let Some(s)=delta["reasoning_content"].as_str(){out.reasoning.push_str(s)}
            if let Some(cs)=delta["tool_calls"].as_array(){for call in cs{
                let idx=call["index"].as_u64().unwrap_or(0) as usize;
                let slot=calls.entry(idx).or_default();
                if let Some(id)=call["id"].as_str(){slot.0=id.to_owned()}
                if let Some(name)=call["function"]["name"].as_str(){slot.1=name.to_owned()}
                if let Some(args)=call["function"]["arguments"].as_str(){slot.2.push_str(args)}
            }}
            if let Some(u)=payload.get("usage"){
                out.usage.input_tokens=u["prompt_tokens"].as_u64().unwrap_or(out.usage.input_tokens);
                out.usage.output_tokens=u["completion_tokens"].as_u64().unwrap_or(out.usage.output_tokens);
            }
            out.finish_reason=payload["choices"][0]["finish_reason"].as_str().map(str::to_owned).or(out.finish_reason);
        }
        for (_, (id,name,raw)) in calls {
            let arguments=serde_json::from_str(&raw).with_context(||format!("malformed tool arguments for {name}"))?;
            out.tool_calls.push(ToolCall{id,name,arguments});
        }
        Ok(out)
    }
}
fn decode_arguments(value:&Value)->Result<Value>{match value {Value::String(s)=>Ok(serde_json::from_str(s).context("invalid tool JSON")?),Value::Object(_)=>Ok(value.clone()),_=>Ok(json!({}))}}
fn parse_anthropic_sse(body:&str)->Result<ModelResponse>{
    let mut out=ModelResponse::default();let mut slots:BTreeMap<usize,(String,String,String)>=BTreeMap::new();
    for line in body.lines(){let Some(raw)=line.trim().strip_prefix("data:")else{continue};let Ok(v)=serde_json::from_str::<Value>(raw.trim())else{continue};
        match v["type"].as_str().unwrap_or(""){
            "message_start"=>{out.usage.input_tokens=v["message"]["usage"]["input_tokens"].as_u64().unwrap_or(0)}
            "message_delta"=>{out.usage.output_tokens=v["usage"]["output_tokens"].as_u64().unwrap_or(out.usage.output_tokens);out.finish_reason=v["delta"]["stop_reason"].as_str().map(str::to_owned)}
            "content_block_start"=>{if v["content_block"]["type"]=="tool_use"{let i=v["index"].as_u64().unwrap_or(0) as usize;
                slots.insert(i,(v["content_block"]["id"].as_str().unwrap_or("").to_owned(),v["content_block"]["name"].as_str().unwrap_or("").to_owned(),String::new()));}}
            "content_block_delta"=>{if let Some(s)=v["delta"]["text"].as_str(){out.content.push_str(s)}
                if let Some(s)=v["delta"]["partial_json"].as_str(){let i=v["index"].as_u64().unwrap_or(0) as usize;
                    if let Some(slot)=slots.get_mut(&i){slot.2.push_str(s);}}}
            _=>{}
        }
    }
    for (_, (id,name,raw)) in slots {out.tool_calls.push(ToolCall{id,name,arguments:serde_json::from_str(&raw).unwrap_or(json!({}))});}
    Ok(out)
}
