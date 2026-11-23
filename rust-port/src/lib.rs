#![recursion_limit = "512"]
//! Native Rust implementation of the advanced AI engineering agent.
//! Keeps the Python project's tool names, actions, provider set, planning
//! model, workspace semantics and project persistence while strengthening
//! permissions, error reporting and task completion validation.

pub mod agent;
pub mod config;
pub mod llm;
pub mod rate;
pub mod swarm;
pub mod tools;

pub use agent::{Agent, Conversation};
pub use config::{Config, Provider};
pub use llm::{LlmClient, ModelResponse, ToolCall};
pub use swarm::{Planner, Swarm, Todo, TodoList};
pub use tools::{ToolRegistry, ToolResult};
