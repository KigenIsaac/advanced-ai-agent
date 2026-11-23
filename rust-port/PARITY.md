# Python → Rust behavioral parity contract

**Original:** `KigenIsaac/advanced-ai-agent` Python commit `bb4abd49a22fa34972b400ea7318a23eb875e823`  
**Rust:** `rust-port/` on repository branch `rust-port`.

This matrix separates **port coverage** from **proof of equivalent behavior**. All entries describe real code, not an intended future design. Production readiness and precise output parity must be measured with the original Python implementation side by side using identical tasks, credentials, and sandboxed workspaces.

## Status vocabulary

- **Implemented:** Native Rust code exists for this functionality.
- **Partial:** A Rust operation exists but lacks some of the Python semantics or integrations.
- **Unverified:** Source exists but requires external services or reproducible e2e tests.

## Exact structure / execution rules carried over

- 15 model-provider identities and original provider endpoint/model defaults (`config.rs`).
- Single-agent model-to-tool feedback loop; streamed and non-streamed response parsing (`agent.rs`, `llm.rs`).
- Tool call names: the original 16 code categories (`workspace`, `terminal`, `code`, `editor`, `test`, `build`, `git`, `packages`, `debug`, `browser`, `http`, `database`, `project`, `review`, `sandbox`, `deploy`) plus `web_search` (`tools/mod.rs`).
- Legacy executor constants: normal maximum 5,000 rounds; worker maximum 40 rounds; planner 20 minimum tasks and target 35; minimum 5 subtasks and target 9; 200 todo maximum; 60,000-character tool result cap.
- Planner/worker result persistence and dependency scheduling (`swarm.rs`).
- Project-specific workspace, chat history, provider settings, CLI and slash commands (`main.rs`, `config.rs`).
- Browser automation architecture, rewritten from Python Playwright to native Chromium DevTools Protocol (`browser.rs`).
- Test detection, shell/build interfaces, repository tooling, simple memory, LSP requests, and optional Docker sandbox.

## Module mapping

| Python original | Rust location | Coverage | Deviation or caveat |
|---|---|---|---|
| `main.py` agent/send, prompts, tool dispatch | `agent.rs`, `tools/mod.rs` | Implemented | Terminal interface differs in presentation; requests are buffered in HTTP rather than progressively displaying every streamed token. |
| `main.py` planner/worker/swarm | `swarm.rs` | Implemented | Explicit DAG validation and stricter completion; workers share tool lock for mutating actions; verification still heuristic, not fully independent. |
| `main.py` configuration/chat/project store | `config.rs`, `main.rs`, `tools/project.rs` | Partial | Project menu, import/export, and transcript/notes layout differ; not every slash-command output is identical. |
| `main.py` key pool | `rate.rs` | Implemented | Rate limits and cooldowns; cost telemetry should be checked against actual model billing. |
| `llm_providers/*.py` + duplicated Python client | `llm.rs` | Partial / unverified | 15 providers implemented, but individual model features, Anthropic/Gemini tool-call replay, SSE edge cases, retries and quotas need real API contract tests. |
| `codeagent/workspace.py` | `tools/workspace.rs` | Partial | Common CRUD/grep/symbol/structure operations present; Python AST-rich source transformations and exact ignore matching differ. |
| `codeagent/editor.py` | `tools/editor.rs` | Implemented / partial | Patches and undo present; undo is in-memory, no durable transaction log. |
| `codeagent/refactor.py` | `tools/editor.rs` | Partial | Python-aware rename/import/extract/inline/split exist to varying depth; exact libcst-style scope and formatting safety cannot be guaranteed. |
| `codeagent/shell.py` | `tools/terminal.rs` | Implemented / partial | Cross-platform process handling, output and timeout controls; Unix descendant cleanup and shell authorization require hardening. |
| `codeagent/testing.py` | `tools/testing.rs` | Implemented / partial | Framework and test build commands; placeholder test generation intentionally does not silently pass as an assertion. |
| `codeagent/vcs.py` | `tools/git.rs` | Partial | Core local/remote operations and command forms; requires Git/gh installed/configured for remote features. |
| `codeagent/lsp.py` | `tools/lsp.rs` | Partial / unverified | Native JSON-RPC/LSP requests; server-specific behavior and refactoring document synchronization require e2e tests. |
| `codeagent/debugger.py` | `tools/debug.rs` | Partial | Original Python debugpy path also only executes scripts; full interactive debugging protocol is not implemented. |
| `codeagent/packages.py` | `tools/packages.rs` | Partial | Main managers and build commands present; not all niche package managers/OS flavors validated. |
| `codeagent/runtime.py` | `tools/runtime.rs` | Partial | HTTP, SQLite, runtime workflows; SQLite read-only is stricter; PostgreSQL/MySQL are detected, not directly connected via the SQLite interface. |
| `codeagent/knowledge.py` | `tools/project.rs` | Partial | Notes, tasks and project context; file formats and specialized docs lookup differ. |
| `codeagent/review.py` | `tools/review.rs` | Partial | Regex-based checks, not semantic code verification. |
| `codeagent/sandbox.py` | `tools/sandbox.rs` | Implemented / partial | Restrictive Docker flags, subprocess temp-root; subprocess is not a security boundary; writable workspace volume remains. |
| `codeagent/deploy.py` | `tools/deploy.rs` | Partial | Deploy/status/log/rollback commands and host authorization flag; cloud environments need real credentials and tests. |
| `webagent/browser.py` + `agent.py` | `tools/browser.rs` | Partial / unverified | Browser operations via CDP instead of Playwright; DOM/form/frames, network timing, multiple tabs, challenges and downloads may differ materially. |
| `webagent/search.py` + `parser.py` | `tools/web.rs` | Partial | DDG / SerpAPI and HTML parsing; index/relevance and result shapes differ. |
| `webagent/documents.py` | `tools/documents.rs` | Implemented / partial | Native TXT/CSV/XLSX/PDF/DOCX support, including pages and Word tables. PDF table extraction is absent, matching original's pypdf fallback rather than pdfplumber. |
| Python `test_*.py` + `.github/workflows/ci.yml` | `tests/core.rs` + GitHub Rust CI | Implemented | Python test suite not translated verbatim; Rust behavior and regression tests currently cover core logic only. |

## Compatibility and reliability concerns that remain

1. **No demonstrated 1:1 task-output parity.** The user-reported SACCO build has not been rerun under both runtimes on an identical isolated workspace.
2. **No production permission boundary for arbitrary shell commands.** A command denylist is not proof against malicious shell syntax, script files or indirect execution; never run untrusted tasks with credentials or broad filesystem access. Host authorization is not a complete solution.
3. **No hardened multi-tenant service.** This is a developer-side CLI, not a sandboxed hosted execution platform with user isolation, distributed task queues, quotas and a secret vault.
4. **Browser and provider integration require live end-to-end tests.** Passing Rust unit tests cannot validate external provider APIs or Chromium behavior.
5. **Some feature implementations are simpler than Python.** Nontrivial refactors, LSP rename, project menus, test coverage and review semantics require side-by-side compatibility tests.
6. **Provider costs and model IDs are copied from the original, not refreshed.** External model identifiers/endpoints may change independently of source parity.
7. **Document readers have safety caps** to prevent uncontrolled input sizes. PDF structured table extraction is not implemented; original Python used pdfplumber if available, otherwise pypdf text-only fallback.

## Reproducible validation

```sh
cd rust-port
cargo check --all-targets
cargo test --all-targets
cargo clippy --all-targets
cargo fmt --all -- --check
```

Then test in a **disposable isolated project**. Include fixture tasks for creating a Python program, Node frontend, DB migration, integration tests, Git commits and browser screenshots. Record the same task outputs, execution rounds, tokens, cost, exit status, file hashes and acceptance-test results for Python and Rust. Run external-service tests only with non-production credentials, and preserve audit logs.

## Source references

- Python commit: https://github.com/KigenIsaac/advanced-ai-agent/tree/bb4abd49a22fa34972b400ea7318a23eb875e823
- Rust branch: https://github.com/KigenIsaac/advanced-ai-agent/tree/rust-port/rust-port
- Rust CI: https://github.com/KigenIsaac/advanced-ai-agent/actions/workflows/rust-port-ci.yml
