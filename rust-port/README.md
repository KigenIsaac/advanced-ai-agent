# Advanced AI Agent — Native Rust Port

This is a source-level Rust migration of [KigenIsaac/advanced-ai-agent](https://github.com/KigenIsaac/advanced-ai-agent), pinned to the Python `master` tree `bb4abd49a22fa34972b400ea7318a23eb875e823` (October 6, 2026). The Python source is preserved independently.

## Coverage

The Rust project implements the original 15 provider names and defaults, session and swarm modes, 16 coding tool categories, web search, user/project storage, CLIs and slash-command families, tool schemas/actions, worker limits, task plans with dependencies, and the central LLM → tools → results loop. Native Rust modules implement file operations, process control, Git, tests, build, packages, HTTP, read-only SQLite, LSP, Chrome CDP automation, web search, project memory/tasks, review, sandboxing, deployment and model HTTP adapters.

**Important:** A source port is not identical to full feature/behavior parity. Several high-complexity behaviors require end-to-end validation against the original Python client. See `PARITY.md` for exact differences and the verification matrix. This crate has not been compiled in the artifact-generation environment because `cargo`/`rustc` were absent; CI is provided for compilation and testing.

## Build

```bash
rustup toolchain install stable
cargo fmt
cargo check --all-targets
cargo test --all-targets
cargo run -- --provider deepseek --project demo
```

Pass a single task with `--task "Implement the complete project specification..."` or use `--swarm --task ...`. The CLI supports `/help`, `/swarm`, `/tools`, `/tokens`, `/project`, `/new`, `/save`, `/load`, `/export` and other commands. Use a Chrome/Chromium installation to enable browser automation.

Set `AGNES_API_KEY` securely in `.env` (ignored by Git). `AGNES_PROVIDER` selects one of the original provider integrations. For local Ollama, no API key is required. User-supplied credentials should never be committed.

## Security

Known high-risk host operations and deployment are gated unless the host sets `AGENT_ALLOW_DESTRUCTIVE=1` before launching the program. Browser automation is driven using Rust and Chrome DevTools Protocol. The Docker sandbox drops privileges, disables networking by default and places resource limits. The subprocess backend is **not** a security boundary. The database tool enforces SQLite read-only access.

## Directory structure

- `src/agent.rs`: tool-use loop, history/usage, prompts
- `src/swarm.rs`: planner/worker DAG scheduling, verification and run artifacts
- `src/llm.rs`: all model-provider HTTP protocols
- `src/rate.rs`: rate limiting/cooldowns/key pool
- `src/config.rs`: provider defaults and CLI configuration
- `src/main.rs`: interactive terminal
- `src/tools/*.rs`: all code and web tool families

## Credits

Faithful migration target: `KigenIsaac/advanced-ai-agent` (Python). Original file/action names and defaults are preserved as much as possible. Changes that improve security/correctness are documented instead of silently substituted.

## Native document readers

The Rust `tools::documents::read_any(path)` function supports text, CSV, Excel, PDF and DOCX using Rust-native parsers, corresponding to the Python `webagent/documents.py` convenience method. Parsed data remains a Rust JSON value and follows the original document-reader result keys. Limits prevent oversized documents. See `PARITY.md` for the verification matrix and remaining differences.
