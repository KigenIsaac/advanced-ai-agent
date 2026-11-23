//! Per-key rolling-window rate limits and provider retry-after cooldown.
use std::{collections::VecDeque, sync::Arc, time::{Duration,Instant}};
use tokio::sync::Mutex;
use anyhow::{Result,bail};

#[derive(Debug)]
struct Key { secret:String, issued:VecDeque<Instant>, cooldown:Instant, requests:u64 }

#[derive(Debug,Clone)]
pub struct KeyPool { keys:Arc<Mutex<Vec<Key>>>, rpm:usize }

impl KeyPool {
    pub fn new(keys:Vec<String>,rpm:usize)->Result<Self>{
        if keys.is_empty() || rpm==0 {bail!("must provide keys and positive rpm")}
        Ok(Self{keys:Arc::new(Mutex::new(keys.into_iter().map(|secret|Key{
            secret,issued:VecDeque::new(),cooldown:Instant::now(),requests:0}).collect())),rpm})
    }
    pub async fn acquire(&self)->String {
        loop {
            let mut keys=self.keys.lock().await;let now=Instant::now();
            for k in keys.iter_mut(){while k.issued.front().is_some_and(|t|now.duration_since(*t)>=Duration::from_secs(60)){k.issued.pop_front();}}
            let choice=keys.iter().enumerate().filter(|(_,k)|k.cooldown<=now && k.issued.len()<self.rpm)
                .max_by_key(|(_,k)|self.rpm-k.issued.len()).map(|(i,_)|i);
            if let Some(i)=choice {let k=&mut keys[i];k.issued.push_back(now);k.requests+=1;return k.secret.clone()}
            drop(keys);tokio::time::sleep(Duration::from_millis(250)).await;
        }
    }
    pub async fn cooldown(&self,key:&str,duration:Duration) {
        if let Some(k)=self.keys.lock().await.iter_mut().find(|k|k.secret==key){k.cooldown=Instant::now()+duration;}
    }
    pub async fn status(&self)->Vec<serde_json::Value> {
        self.keys.lock().await.iter().map(|k|serde_json::json!({"key":format!("...{}",k.secret.chars().rev().take(6).collect::<String>().chars().rev().collect::<String>()),"used_in_window":k.issued.len(),"rpm":self.rpm,"requests":k.requests})).collect()
    }
}
