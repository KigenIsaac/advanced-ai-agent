//! Search and HTML extraction, matching webagent/search.py and parser.py.
use anyhow::{bail,Context,Result};
use reqwest::Client;
use scraper::{Html,Selector};
use serde_json::{json,Value};
use url::Url;
use std::{collections::HashSet,sync::Arc};

#[derive(Clone)]pub struct WebSearch {client:Client,serpapi_key:Option<String>}
impl WebSearch {
 pub fn new()->Result<Self>{Ok(Self{client:Client::builder().timeout(std::time::Duration::from_secs(20))
    .user_agent("Mozilla/5.0 (compatible; AdvancedAIAgent/1.0)").build()?,serpapi_key:std::env::var("SERPAPI_KEY").ok()})}
 pub async fn dispatch(&self,kw:&Value)->Result<Value>{
    let q=kw["query"].as_str().unwrap_or("");if q.is_empty(){bail!("query required")}
    let kind=kw["kind"].as_str().unwrap_or("web");let max=kw["max_results"].as_u64().unwrap_or(10).min(100) as usize;
    let page=kw["page"].as_u64().unwrap_or(0);let mut records=Vec::new();
    if let Some(key)=&self.serpapi_key {
        let engine=match kind{"news"=>"google_news","images"=>"google_images","shopping"=>"google_shopping",_=>"google"};
        let query=[("engine",engine.to_string()),("q",q.to_string()),("api_key",key.clone()),("start",(page*10).to_string())];
        let response=self.client.get("https://serpapi.com/search.json").query(&query).send().await?.error_for_status()?;
        let data:Value=response.json().await?;
        let key=match kind{"news"=>"news_results","images"=>"images_results","shopping"=>"shopping_results",_=>"organic_results"};
        if let Some(items)=data[key].as_array(){for item in items {
            let url=item["link"].as_str().unwrap_or("");records.push(json!({"title":item["title"],"url":url,
                "domain":Url::parse(url).ok().and_then(|u|u.host_str().map(str::to_owned)),"snippet":item["snippet"],
                "published_date":item["date"],"image_url":item["original"],"price":item["price"]}));
        }}
    }else{
        let query=match kind{"news"=>format!("{q} news"),"images"=>format!("{q} images"),
            "shopping"=>format!("{q} price buy"),_=>q.to_string()};
        let response=self.client.post("https://html.duckduckgo.com/html/").form(&[("q",query.as_str()),("b","")]).send().await?.error_for_status()?;
        let text=response.text().await?;
        let doc=Html::parse_document(&text);let result_sel=Selector::parse(".result").unwrap();
        let a_sel=Selector::parse("a.result__a").unwrap();let snippet_sel=Selector::parse(".result__snippet").unwrap();
        for node in doc.select(&result_sel) {
            if let Some(a)=node.select(&a_sel).next(){let href=a.value().attr("href").unwrap_or("");
                let url=ddg_url(href);let title=a.text().collect::<Vec<_>>().join(" ");
                let snippet=node.select(&snippet_sel).next().map(|e|e.text().collect::<Vec<_>>().join(" ")).unwrap_or_default();
                records.push(json!({"title":title,"url":url,"domain":Url::parse(&url).ok().and_then(|u|u.host_str().map(str::to_owned)),
                    "snippet":snippet,"published_date":null}));
            }
        }
    }
    let allow=kw["domains"].as_array().map(|a|a.iter().filter_map(Value::as_str).collect::<Vec<_>>()).unwrap_or_default();
    let deny=kw["exclude_domains"].as_array().map(|a|a.iter().filter_map(Value::as_str).collect::<Vec<_>>()).unwrap_or_default();
    let tokens=q.split_whitespace().map(|s|s.to_lowercase()).collect::<Vec<_>>();
    let mut seen=HashSet::new();let mut final_results=Vec::new();
    for mut r in records {
        let url=r["url"].as_str().unwrap_or("").to_owned();let dom=r["domain"].as_str().unwrap_or("").to_owned();
        if url.is_empty()||!seen.insert(url.clone())||(!allow.is_empty()&&!allow.iter().any(|s|dom.ends_with(s)))||deny.iter().any(|s|dom.ends_with(s)){continue}
        let date=r["published_date"].as_str().unwrap_or("");
        if !date.is_empty() && (kw["date_from"].as_str().is_some_and(|v|date<v)||kw["date_to"].as_str().is_some_and(|v|date>v)){continue}
        let hay=format!("{} {}",r["title"],r["snippet"]).to_lowercase();
        let overlap=tokens.iter().filter(|t|hay.contains(t.as_str())).count();
        r["relevance"]=json!(1.0/(final_results.len() as f64+1.0)+overlap as f64*0.05);
        final_results.push(r);if final_results.len()>=max{break}
    }
    Ok(json!({"query":q,"kind":kind,"count":final_results.len(),"results":final_results,
        "backend":if self.serpapi_key.is_some(){"serpapi"}else{"duckduckgo"}}))
 }
 pub async fn fetch_page(&self,url:&str)->Result<Value>{
    let text=self.client.get(url).send().await?.error_for_status()?.text().await?;
    let document=Html::parse_document(&text);
    let title=Selector::parse("title").unwrap();
    let main=Selector::parse("main,article,[role=main],body").unwrap();
    let links=Selector::parse("a[href]").unwrap();
    let body=document.select(&main).next().map(|node|node.text().collect::<Vec<_>>().join(" ")).unwrap_or_default();
    let links:Vec<_>=document.select(&links).take(250).map(|node|json!({"text":node.text().collect::<Vec<_>>().join(" "),
        "href":node.value().attr("href").unwrap_or("")})).collect();
    Ok(json!({"title":document.select(&title).next().map(|v|v.text().collect::<Vec<_>>().join(" ")),
        "text":body.chars().take(100_000).collect::<String>(),"links":links}))
 }
}
fn ddg_url(href:&str)->String{if let Ok(url)=Url::parse(href){
    if url.host_str().unwrap_or("").contains("duckduckgo.com"){
        if let Some((_,value))=url.query_pairs().find(|(k,_)|k=="uddg"){return value.into_owned();}}
    return href.to_owned();}if href.starts_with("//"){format!("https:{href}")}else{href.to_owned()}}
