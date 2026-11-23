use anyhow::{Context, Result};
use clap::Parser;
use serde::{Deserialize, Serialize};
use std::{collections::HashMap, env, fs, path::PathBuf};

pub const MAX_ROUNDS: usize = 5000;
pub const WORKER_ROUNDS: usize = 40;
pub const PLANNER_TOKENS: u32 = 65_536;
pub const REVIEWER_TOKENS: u32 = 8_192;
pub const MAX_OUTPUT_TOKENS: u32 = 16_384;
pub const MAX_TOOL_RESULT_CHARS: usize = 60_000;
pub const MAX_HISTORY_CHARS: usize = 400_000;
pub const MIN_TODOS: usize = 20;
pub const TARGET_TODOS: usize = 35;
pub const MAX_TODOS: usize = 200;
pub const MIN_SUBTASKS: usize = 5;
pub const TARGET_SUBTASKS: usize = 9;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Provider {
    Agnes, Openai, Anthropic, Gemini, Local, Openrouter, Deepseek, Groq,
    Mistral, Together, Cohere, Ollama, Perplexity, Fireworks, Xai,
}
impl Provider {
    pub fn name(&self) -> &'static str { match self {
        Self::Agnes=>"agnes",Self::Openai=>"openai",Self::Anthropic=>"anthropic",
        Self::Gemini=>"gemini",Self::Local=>"local",Self::Openrouter=>"openrouter",
        Self::Deepseek=>"deepseek",Self::Groq=>"groq",Self::Mistral=>"mistral",
        Self::Together=>"together",Self::Cohere=>"cohere",Self::Ollama=>"ollama",
        Self::Perplexity=>"perplexity",Self::Fireworks=>"fireworks",Self::Xai=>"xai"}}
    pub fn from_name(name: &str) -> Result<Self> {
        serde_json::from_value(serde_json::Value::String(name.to_lowercase()))
            .with_context(|| format!("unsupported provider: {name}"))
    }
    pub fn defaults(&self)->(&'static str,&'static str) { match self {
        Self::Agnes=>("https://apihub.agnes-ai.com/v1","agnes-3.0-flash"),
        Self::Openai=>("https://api.openai.com/v1","gpt-4o-mini"),
        Self::Anthropic=>("https://api.anthropic.com","claude-3-5-sonnet-latest"),
        Self::Gemini=>("https://generativelanguage.googleapis.com/v1beta/models","gemini-2.0-flash"),
        Self::Local=>("http://localhost:11434/v1","llama3.1"),
        Self::Openrouter=>("https://openrouter.ai/api/v1","openai/gpt-4o-mini"),
        Self::Deepseek=>("https://api.deepseek.com","deepseek-chat"),
        Self::Groq=>("https://api.groq.com/openai/v1","llama-3.1-70b-versatile"),
        Self::Mistral=>("https://api.mistral.ai/v1","mistral-small-latest"),
        Self::Together=>("https://api.together.xyz/v1","meta-llama/Llama-3.1-70B-Instruct-Turbo"),
        Self::Cohere=>("https://api.cohere.ai/compatibility/v1","command-r-plus"),
        Self::Ollama=>("http://localhost:11434/v1","llama3.1"),
        Self::Perplexity=>("https://api.perplexity.ai","sonar"),
        Self::Fireworks=>("https://api.fireworks.ai/inference/v1","accounts/fireworks/models/llama-v3p1-70b-instruct"),
        Self::Xai=>("https://api.x.ai/v1","grok-2-latest")
    }}
}

#[derive(Parser,Debug,Clone)]
#[command(name="advanced-ai-agent", about="Autonomous terminal software engineering agent")]
pub struct Cli {
    #[arg(long)] pub config: Option<PathBuf>,
    #[arg(long)] pub provider: Option<String>,
    #[arg(long)] pub model: Option<String>,
    #[arg(long)] pub base_url: Option<String>,
    #[arg(long)] pub agent_name: Option<String>,
    #[arg(long)] pub agent_tagline: Option<String>,
    #[arg(long)] pub custom_instructions: Option<String>,
    #[arg(long)] pub project: Option<String>,
    #[arg(long)] pub home: Option<PathBuf>,
    #[arg(long="api-key")] pub api_keys: Vec<String>,
    #[arg(long)] pub api_keys_csv: Option<String>,
    #[arg(long)] pub max_tokens: Option<u32>,
    #[arg(long)] pub max_rounds: Option<usize>,
    #[arg(long)] pub temperature: Option<f32>,
    #[arg(long)] pub tier: Option<String>,
    #[arg(long)] pub rpm: Option<usize>,
    #[arg(long)] pub max_retries: Option<usize>,
    #[arg(long)] pub swarm_workers: Option<usize>,
    #[arg(long)] pub swarm: bool,
    #[arg(long)] pub thinking: bool,
    #[arg(long)] pub no_stream: bool,
    #[arg(long)] pub verbose: bool,
    #[arg(long)] pub show_browser: bool,
    #[arg(long)] pub no_web: bool,
    #[arg(long)] pub no_code: bool,
    #[arg(long)] pub strict_approval: bool,
    #[arg(long)] pub task: Option<String>,
}

#[derive(Debug,Clone)]
pub struct Config {
    pub provider: Provider, pub base_url:String, pub model:String,
    pub api_keys:Vec<String>, pub agent_name:String, pub agent_tagline:String,
    pub custom_instructions:String, pub project_root:PathBuf, pub home:PathBuf,
    pub max_rounds:usize, pub max_tokens:u32, pub temperature:f32,
    pub rpm:usize, pub max_retries:usize, pub swarm_workers:usize,
    pub swarm:bool, pub thinking:bool, pub stream:bool, pub verbose:bool,
    pub browser_headless:bool, pub web_enabled:bool, pub code_enabled:bool,
    pub strict_approval:bool,
}
fn load_env() {
    // Treat .env as local-only; never print or persist parsed secret values.
    if let Ok(src)=fs::read_to_string(".env") {
        for line in src.lines() {
            let line=line.trim();
            if line.is_empty() || line.starts_with('#') {continue}
            if let Some((key,value))=line.strip_prefix("export ").unwrap_or(line).split_once('=') {
                let key=key.trim(); let value=value.trim().trim_matches('"').trim_matches('\'');
                if env::var_os(key).is_none() && !key.is_empty() {env::set_var(key,value);}
            }
        }
    }
}
impl Config {
    pub fn from_cli(cli:&Cli)->Result<Self> {
        load_env();
        let profile:HashMap<String,serde_json::Value>=cli.config.as_ref()
            .map(|p| fs::read_to_string(p).with_context(||format!("reading {}",p.display())))
            .transpose()?.and_then(|s|serde_json::from_str(&s).ok()).unwrap_or_default();
        let get=|key:&str|->Option<String> {profile.get(key).and_then(|v|v.as_str().map(str::to_owned))};
        let provider=Provider::from_name(&cli.provider.clone().or_else(||env::var("AGNES_PROVIDER").ok())
           .or_else(||get("provider")).unwrap_or_else(||"agnes".into()))?;
        let (base,model)=provider.defaults();
        let base_url=cli.base_url.clone().or_else(||env::var("AGNES_BASE_URL").ok())
            .or_else(||get("base_url")).filter(|s|!s.trim().is_empty()).unwrap_or_else(||base.into());
        let model=cli.model.clone().or_else(||env::var("AGNES_MODEL").ok())
            .or_else(||get("model")).unwrap_or_else(||model.into());
        let mut keys=cli.api_keys.clone();
        for list in [cli.api_keys_csv.clone(),env::var("AGNES_API_KEYS").ok(),env::var("AGNES_API_KEY").ok()].into_iter().flatten(){
            keys.extend(list.split(',').map(str::trim).filter(|s|!s.is_empty()).map(str::to_owned));
        }
        keys.sort();keys.dedup();
        if keys.is_empty() && !matches!(provider,Provider::Local|Provider::Ollama) {
            anyhow::bail!("no API key: set AGNES_API_KEY or pass --api-key");
        }
        if keys.is_empty(){keys.push("local".into());}
        let rpm=cli.rpm.unwrap_or_else(||match cli.tier.as_deref().unwrap_or("default") {
            "enterprise"=>20,"token-plan"=>1000,_=>10});
        let home=cli.home.clone().or_else(||env::var("AGNES_HOME").ok().map(PathBuf::from))
            .unwrap_or_else(||env::var("HOME").map(|v|PathBuf::from(v).join(".agnes"))
            .unwrap_or_else(|_|PathBuf::from(".agnes")));
        let project_root= if let Some(project)=cli.project.as_ref() {home.join("projects").join(project).join("workspace")}
            else {env::current_dir()?};
        let swarm_workers=cli.swarm_workers.unwrap_or(keys.len()).max(1).min(keys.len());
        Ok(Self{provider,base_url,model,api_keys:keys,agent_name:cli.agent_name.clone().or_else(||get("agent_name"))
            .unwrap_or_else(||"Agnes Agent".into()),agent_tagline:cli.agent_tagline.clone().or_else(||get("agent_tagline"))
            .unwrap_or_else(||"Autonomous terminal engineering agent".into()),
            custom_instructions:cli.custom_instructions.clone().or_else(||get("custom_instructions")).unwrap_or_default(),
            project_root,home,max_rounds:cli.max_rounds.unwrap_or(MAX_ROUNDS),max_tokens:cli.max_tokens.unwrap_or(MAX_OUTPUT_TOKENS),
            temperature:cli.temperature.unwrap_or(0.3),rpm,max_retries:cli.max_retries.unwrap_or(5),swarm_workers,
            swarm:cli.swarm,thinking:cli.thinking,stream:!cli.no_stream,verbose:cli.verbose,
            browser_headless:!cli.show_browser,web_enabled:!cli.no_web,code_enabled:!cli.no_code,
            strict_approval:cli.strict_approval})
    }
}
