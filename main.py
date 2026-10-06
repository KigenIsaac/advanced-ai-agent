#!/usr/bin/env python3

from __future__ import annotations

import argparse
import asyncio
import ast
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

import httpx
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from rich.box import ROUNDED
from llm_providers import get_provider_adapter
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text


try:
    from webagent import WebAgent
    from webagent.tools import WEB_SEARCH_SCHEMA, WEB_BROWSER_SCHEMA
    _HAS_WEB = True
except ImportError:
    WebAgent = None
    WEB_SEARCH_SCHEMA = None
    WEB_BROWSER_SCHEMA = None
    _HAS_WEB = False

try:
    from codeagent import CodeAgent
    from codeagent.tools import TOOLS as _CODE_TOOLS
    _HAS_CODE = True
except ImportError:
    CodeAgent = None
    _CODE_TOOLS = []
    _HAS_CODE = False


DEFAULT_BASE_URL = "https://apihub.agnes-ai.com/v1"
DEFAULT_MODEL = "agnes-3.0-flash"
DEFAULT_PROVIDER = "agnes"
DEFAULT_AGENT_NAME = "Agnes Agent"
DEFAULT_AGENT_TAGLINE = "Autonomous terminal engineering agent"
DEFAULT_CUSTOM_INSTRUCTIONS = ""
DEFAULT_MAX_ROUNDS = 5000
DEFAULT_WORKER_ROUNDS = 40
DEFAULT_PLANNER_TOKENS = 65_536
DEFAULT_REVIEWER_TOKENS = 8_192
MAX_TOOL_RESULT_CHARS = 60_000
MAX_HISTORY_CHARS = 400_000
DEFAULT_MAX_TOKENS = 16_384
MIN_TODOS = 20
TARGET_TODOS = 35
MAX_TODOS = 200
MIN_SUBTASKS = 5
TARGET_SUBTASKS = 9

PRICE_INPUT = 0.05
PRICE_CACHED_INPUT = 0.005
PRICE_OUTPUT = 0.15

SCHEMA_VERSION = 1

ACCESS_TIERS: dict[str, dict[str, Any]] = {
    "default":    {"rpm": 10,   "label": "Free / default"},
    "enterprise": {"rpm": 20,   "label": "Enterprise verified"},
    "token-plan": {"rpm": 1000, "label": "Token Plan"},
}

DEFAULT_TIER = "default"
DEFAULT_MAX_RETRIES = 5
DEFAULT_RETRY_CAP = 60.0

WORKER_COLORS = [
    "cyan", "magenta", "green", "yellow", "blue",
    "bright_cyan", "bright_magenta", "bright_green",
    "bright_yellow", "bright_blue", "red", "bright_red",
]

PROJECT_KINDS = [
    "web", "cli", "library", "script", "data",
    "mobile", "api", "game", "ml", "other",
]

PROJECT_LANGUAGES = [
    "python", "javascript", "typescript", "go", "rust",
    "java", "kotlin", "swift", "cpp", "csharp",
    "ruby", "php", "elixir", "haskell", "other",
]

TEMPLATE_CHOICES = [
    "minimal", "python-cli", "python-library",
    "node-cli", "node-web", "go-cli", "rust-cli",
]

CHAT_SLUG = "_chat"

MODEL_PROVIDERS = {
    "agnes": "Agnes API",
    "openai": "OpenAI-compatible API",
    "anthropic": "Anthropic Messages API",
    "gemini": "Google Gemini API",
    "local": "Local model API",
    "openrouter": "OpenRouter",
    "deepseek": "DeepSeek",
    "groq": "Groq",
    "mistral": "Mistral",
    "together": "Together AI",
    "cohere": "Cohere",
    "ollama": "Ollama",
    "perplexity": "Perplexity",
    "fireworks": "Fireworks AI",
    "xai": "xAI",
}

PROVIDER_DEFAULT_MODELS = {
    "agnes": DEFAULT_MODEL,
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-sonnet-latest",
    "gemini": "gemini-2.0-flash",
    "local": "llama3.1",
    "openrouter": "openai/gpt-4o-mini",
    "deepseek": "deepseek-chat",
    "groq": "llama-3.1-70b-versatile",
    "mistral": "mistral-small-latest",
    "together": "meta-llama/Llama-3.1-70B-Instruct-Turbo",
    "cohere": "command-r-plus",
    "ollama": "llama3.1",
    "perplexity": "sonar",
    "fireworks": "accounts/fireworks/models/llama-v3p1-70b-instruct",
    "xai": "grok-2-latest",
}

PROVIDER_DEFAULT_BASE_URLS = {
    "agnes": DEFAULT_BASE_URL,
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models",
    "local": "http://localhost:11434/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "deepseek": "https://api.deepseek.com",
    "groq": "https://api.groq.com/openai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "together": "https://api.together.xyz/v1",
    "cohere": "https://api.cohere.ai/compatibility/v1",
    "ollama": "http://localhost:11434/v1",
    "perplexity": "https://api.perplexity.ai",
    "fireworks": "https://api.fireworks.ai/inference/v1",
    "xai": "https://api.x.ai/v1",
}


def humanize_time(ts: float) -> str:
    delta = time.time() - ts
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    if delta < 604800:
        return f"{int(delta // 86400)}d ago"
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def slugify(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s or "project"


def generate_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


_JSON_KW = {"indent": 2, "ensure_ascii": False, "default": str}


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, **_JSON_KW), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def sanitize_text(s: Any) -> str:
    if not isinstance(s, str):
        return s
    return "".join(
        ch for ch in s
        if ch not in ("\ufffd", "\ufffe", "\uffff")
        and not (0xD800 <= ord(ch) <= 0xDFFF)
        and ord(ch) != 0
    )


def sanitize_obj(o: Any) -> Any:
    if isinstance(o, str):
        return sanitize_text(o)
    if isinstance(o, dict):
        return {
            (sanitize_text(k) if isinstance(k, str) else k): sanitize_obj(v)
            for k, v in o.items()
        }
    if isinstance(o, (list, tuple)):
        return [sanitize_obj(v) for v in o]
    return o


def _one_line(s: Any, max_len: int = 100) -> str:
    if not isinstance(s, str):
        s = str(s)
    s = s.replace("\r", "").replace("\n", " ").strip()
    if len(s) > max_len:
        s = s[: max_len - 1] + "…"
    return s


def _count_lines(text: Any) -> int:
    if not isinstance(text, str) or not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _first_meaningful_line(*texts: str) -> str:
    for text in texts:
        if not text:
            continue
        for line in text.splitlines():
            line = line.strip()
            if line:
                return _one_line(line, 100)
    return ""


_JSON_VALID_SIMPLE_ESCAPES = set('"\\/bfnrt')


def _escape_bad_backslashes(text: str) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        if i + 1 >= n:
            out.append("\\\\")
            i += 1
            continue
        nxt = text[i + 1]
        if nxt in _JSON_VALID_SIMPLE_ESCAPES:
            out.append("\\")
            out.append(nxt)
            i += 2
            continue
        if nxt == "u" and i + 5 < n and all(
            text[i + 2 + j] in "0123456789abcdefABCDEF" for j in range(4)
        ):
            out.append(text[i:i + 6])
            i += 6
            continue
        out.append("\\\\")
        i += 1
    return "".join(out)


def _parse_tool_arguments(
    name: str, raw: str
) -> tuple[dict, Optional[str]]:
    raw = (raw or "").strip()
    if not raw:
        return {}, None

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed, None
        return {"_value": parsed}, None
    except json.JSONDecodeError:
        pass

    try:
        parsed = ast.literal_eval(raw)
        if isinstance(parsed, dict):
            return parsed, None
    except (ValueError, SyntaxError):
        pass

    try:
        repaired = raw
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
        repaired = re.sub(r"\bTrue\b", "true", repaired)
        repaired = re.sub(r"\bFalse\b", "false", repaired)
        repaired = re.sub(r"\bNone\b", "null", repaired)
        if "'" in repaired and '"' not in repaired:
            repaired = repaired.replace("'", '"')
        repaired = _escape_bad_backslashes(repaired)
        parsed = json.loads(repaired)
        if isinstance(parsed, dict):
            return parsed, None
    except (json.JSONDecodeError, TypeError):
        pass

    return {}, (
        f"Your tool call arguments were not valid JSON. Emit a single JSON "
        f"object with double-quoted keys, and escape literal backslashes in "
        f"Windows paths as double backslashes (C:\\\\Users\\\\...). "
        f"Received: {raw[:400]}"
    )


def _normalize_message_for_api(msg: dict) -> dict:
    if not isinstance(msg, dict):
        return msg
    if msg.get("role") != "assistant":
        return msg
    tool_calls = msg.get("tool_calls")
    if not tool_calls:
        return msg

    new_calls: list[dict] = []
    for tc in tool_calls:
        fn = tc.get("function") or {}
        raw = fn.get("arguments")
        parsed, _ = _parse_tool_arguments(fn.get("name", ""), raw or "{}")
        new_fn = dict(fn)
        new_fn["arguments"] = json.dumps(parsed, ensure_ascii=False)
        new_tc = dict(tc)
        new_tc["function"] = new_fn
        new_calls.append(new_tc)

    new_msg = dict(msg)
    new_msg["tool_calls"] = new_calls
    return new_msg


def _normalize_messages_for_api(messages: list[dict]) -> list[dict]:
    return [_normalize_message_for_api(m) for m in messages]


def _repair_tool_call_history(messages: list[dict]) -> list[dict]:
    repaired: list[dict] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if not isinstance(msg, dict):
            repaired.append(msg)
            i += 1
            continue

        role = msg.get("role")
        if role == "tool":
            i += 1
            continue

        tool_calls = msg.get("tool_calls") or []
        if role == "assistant" and tool_calls:
            expected_ids = {
                str(tc.get("id"))
                for tc in tool_calls
                if isinstance(tc, dict) and tc.get("id")
            }
            if not expected_ids:
                repaired.append(msg)
                i += 1
                continue

            matched: set[str] = set()
            block: list[dict] = []
            j = i + 1
            while j < len(messages):
                nxt = messages[j]
                if not isinstance(nxt, dict):
                    break
                if nxt.get("role") != "tool":
                    break
                call_id = nxt.get("tool_call_id")
                call_id_str = str(call_id) if call_id is not None else ""
                if call_id_str in expected_ids and call_id_str not in matched:
                    matched.add(call_id_str)
                    block.append(nxt)
                    j += 1
                    continue
                if call_id_str and call_id_str in expected_ids:
                    block.append(nxt)
                    j += 1
                    continue
                block.append(nxt)
                j += 1
                break

            if matched == expected_ids:
                repaired.append(msg)
                repaired.extend(block)
                i = j
                continue

            i += 1
            continue

        repaired.append(msg)
        i += 1
    return repaired


def suggest_template(kind: str, language: str) -> str:
    table = {
        ("cli", "python"): "python-cli",
        ("script", "python"): "python-cli",
        ("library", "python"): "python-library",
        ("cli", "javascript"): "node-cli",
        ("cli", "typescript"): "node-cli",
        ("web", "javascript"): "node-web",
        ("web", "typescript"): "node-web",
        ("api", "javascript"): "node-web",
        ("api", "typescript"): "node-web",
        ("cli", "go"): "go-cli",
        ("cli", "rust"): "rust-cli",
    }
    return table.get((kind, language), "minimal")


def _render_readme(project: "Project") -> str:
    tags = ", ".join(f"`{t}`" for t in project.tags) if project.tags else "_none_"
    return f"""# {project.name}

{project.description or "_No description yet._"}

## Meta

| Field | Value |
| --- | --- |
| Slug | `{project.slug}` |
| Kind | `{project.kind}` |
| Language | `{project.language}` |
| Template | `{project.template}` |
| Tags | {tags} |
| Created | {time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(project.created_at))} |

## Layout

| Path | Purpose |
| --- | --- |
| `workspace/` | Source code and assets |
| `conversations/` | Chat history with the agent |
| `swarm/` | Autonomous swarm runs |
| `downloads/` | Files fetched from the web |
| `logs/` | Per-project logs |
| `notes.md` | Running notes for you and the agent |
| `project.json` | Project metadata (schema v{SCHEMA_VERSION}) |

## Working with the agent

Open this project with `agnes-agent --project {project.slug}` and talk to the agent.
Use `/swarm <task>` to launch an autonomous multi-worker run.

## Notes

Add anything the agent should know to `notes.md`. The agent reads it on every turn.
"""


_GITIGNORE_BASE = """# OS
.DS_Store
Thumbs.db
desktop.ini

# Editors
.idea/
.vscode/
*.swp
*.swo
*~

# Agnes
.agnes/
.agnes-lock
logs/
conversations/
swarm/
downloads/
.env
.env.*
!.env.example
"""

_GITIGNORE_LANG = {
    "python": """
# Python
__pycache__/
*.py[cod]
.venv/
venv/
env/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/
dist/
build/
*.egg-info/
""",
    "javascript": """
# Node
node_modules/
npm-debug.log*
yarn-debug.log*
yarn-error.log*
pnpm-debug.log*
.next/
.nuxt/
dist/
coverage/
""",
    "typescript": """
# Node / TypeScript
node_modules/
dist/
*.tsbuildinfo
coverage/
""",
    "go": """
# Go
/bin/
*.test
*.out
""",
    "rust": """
# Rust
/target/
""",
    "java": """
# Java
target/
*.class
.gradle/
build/
""",
    "kotlin": """
# Kotlin
build/
.gradle/
.kotlin/
""",
    "swift": """
# Swift
.build/
.swiftpm/
*.xcodeproj
""",
    "cpp": """
# C++
build/
*.o
*.so
*.a
""",
    "csharp": """
# C#
bin/
obj/
""",
    "ruby": """
# Ruby
.bundle/
vendor/bundle/
*.gem
""",
    "php": """
# PHP
vendor/
""",
    "elixir": """
# Elixir
_build/
deps/
*.beam
""",
    "haskell": """
# Haskell
dist-newstyle/
.stack-work/
""",
}


def _render_gitignore(language: str) -> str:
    return _GITIGNORE_BASE + _GITIGNORE_LANG.get(language, "")


def _render_notes(project: "Project") -> str:
    return f"""# {project.name}

{project.description or "_No description yet._"}

## Conventions

_Record coding conventions, build commands, and known constraints here._

## Build & test

_Run `terminal.run` or edit this file with commands the agent should use._

## Known issues

_Track anything the agent should avoid or be careful with._
"""


def _render_env_example(language: str) -> str:
    if language in ("python", "javascript", "typescript"):
        return "# Optional runtime secrets.\n# AGNES_API_KEY=\n# DATABASE_URL=\n"
    return "# Optional runtime secrets.\n"


def _template_files(template: str, project: "Project") -> dict[str, str]:
    slug = project.slug
    slug_u = slug.replace("-", "_")
    desc = project.description or ""

    if template == "minimal":
        return {"workspace/.keep": ""}

    if template == "python-cli":
        return {
            "workspace/pyproject.toml": (
                f'[project]\n'
                f'name = "{slug}"\n'
                f'version = "0.1.0"\n'
                f'description = "{desc}"\n'
                f'requires-python = ">=3.11"\n'
                f'\n'
                f'[project.scripts]\n'
                f'{slug_u} = "src.main:main"\n'
                f'\n'
                f'[build-system]\n'
                f'requires = ["hatchling"]\n'
                f'build-backend = "hatchling.build"\n'
            ),
            "workspace/src/__init__.py": "",
            "workspace/src/main.py": (
                '"""Entry point."""\n'
                '\n'
                '\n'
                'def main() -> int:\n'
                '    print("hello from ' + slug + '")\n'
                '    return 0\n'
                '\n'
                '\n'
                'if __name__ == "__main__":\n'
                '    raise SystemExit(main())\n'
            ),
            "workspace/tests/__init__.py": "",
            "workspace/tests/test_main.py": (
                'from src.main import main\n'
                '\n'
                '\n'
                'def test_main_runs():\n'
                '    assert main() == 0\n'
            ),
            "workspace/.env.example": _render_env_example("python"),
        }

    if template == "python-library":
        return {
            "workspace/pyproject.toml": (
                f'[project]\n'
                f'name = "{slug}"\n'
                f'version = "0.1.0"\n'
                f'description = "{desc}"\n'
                f'requires-python = ">=3.11"\n'
                f'\n'
                f'[build-system]\n'
                f'requires = ["hatchling"]\n'
                f'build-backend = "hatchling.build"\n'
            ),
            f"workspace/src/{slug_u}/__init__.py": (
                f'"""Public package for {slug}."""\n'
                f'\n'
                f'__version__ = "0.1.0"\n'
            ),
            f"workspace/src/{slug_u}/core.py": (
                '"""Core module."""\n'
                '\n'
                '\n'
                'def greet(name: str) -> str:\n'
                '    return f"hello, {name}"\n'
            ),
            "workspace/tests/__init__.py": "",
            "workspace/tests/test_core.py": (
                f'from {slug_u}.core import greet\n'
                '\n'
                '\n'
                'def test_greet():\n'
                '    assert greet("world") == "hello, world"\n'
            ),
        }

    if template == "node-cli":
        return {
            "workspace/package.json": json.dumps({
                "name": slug,
                "version": "0.1.0",
                "description": desc,
                "type": "module",
                "bin": {slug: "./bin/cli.js"},
                "scripts": {"start": "node ./bin/cli.js"},
            }, indent=2) + "\n",
            "workspace/bin/cli.js": (
                "#!/usr/bin/env node\n"
                "\n"
                "import { run } from '../src/index.js';\n"
                "\n"
                "run(process.argv.slice(2));\n"
            ),
            "workspace/src/index.js": (
                "export function run(args) {\n"
                "  console.log('hello from " + slug + "', args);\n"
                "}\n"
            ),
            "workspace/.env.example": _render_env_example("javascript"),
        }

    if template == "node-web":
        return {
            "workspace/package.json": json.dumps({
                "name": slug,
                "version": "0.1.0",
                "description": desc,
                "type": "module",
                "scripts": {
                    "start": "node ./src/server.js",
                    "dev": "node --watch ./src/server.js",
                },
            }, indent=2) + "\n",
            "workspace/src/server.js": (
                "import http from 'node:http';\n"
                "\n"
                "const server = http.createServer((req, res) => {\n"
                "  res.writeHead(200, {'content-type': 'text/plain'});\n"
                "  res.end('hello from " + slug + "\\n');\n"
                "});\n"
                "\n"
                "const port = Number(process.env.PORT || 3000);\n"
                "server.listen(port, () => {\n"
                "  console.log(`listening on http://127.0.0.1:${port}`);\n"
                "});\n"
            ),
            "workspace/public/index.html": (
                "<!doctype html>\n"
                "<html><head><meta charset=\"utf-8\"><title>" + slug + "</title></head>\n"
                "<body><h1>" + slug + "</h1></body></html>\n"
            ),
            "workspace/.env.example": _render_env_example("javascript"),
        }

    if template == "go-cli":
        return {
            "workspace/go.mod": f"module {slug}\n\ngo 1.22\n",
            "workspace/main.go": (
                "package main\n"
                "\n"
                "import \"fmt\"\n"
                "\n"
                "func main() {\n"
                "\tfmt.Println(\"hello from " + slug + "\")\n"
                "}\n"
            ),
        }

    if template == "rust-cli":
        return {
            "workspace/Cargo.toml": (
                "[package]\n"
                f'name = "{slug}"\n'
                'version = "0.1.0"\n'
                'edition = "2021"\n'
                "\n"
                "[dependencies]\n"
            ),
            "workspace/src/main.rs": (
                "fn main() {\n"
                '    println!("hello from ' + slug + '");\n'
                "}\n"
            ),
        }

    return {"workspace/.keep": ""}


@dataclass
class Project:
    slug: str
    name: str
    description: str = ""
    kind: str = "other"
    language: str = "python"
    tags: list[str] = field(default_factory=list)
    settings: dict = field(default_factory=dict)
    template: str = "minimal"
    git_initialized: bool = False
    schema_version: int = SCHEMA_VERSION
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    root: Optional[Path] = None
    workspace: Optional[Path] = None

    def to_metadata(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "kind": self.kind,
            "language": self.language,
            "template": self.template,
            "tags": list(self.tags),
            "settings": dict(self.settings),
            "git_initialized": self.git_initialized,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_index_entry(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "kind": self.kind,
            "language": self.language,
            "template": self.template,
            "tags": list(self.tags),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def save(self) -> None:
        if self.root is None:
            raise ValueError("project has no root")
        self.updated_at = time.time()
        self.schema_version = SCHEMA_VERSION
        _write_json(self.root / "project.json", self.to_metadata())

    @classmethod
    def load(cls, root: Path) -> "Project":
        meta = _read_json(root / "project.json", {})
        version = int(meta.get("schema_version", 0))

        project = cls(
            slug=root.name,
            root=root,
            workspace=root / "workspace",
            name=meta.get("name", root.name),
            description=meta.get("description", ""),
            kind=meta.get("kind", "other"),
            language=meta.get("language", "python"),
            tags=list(meta.get("tags") or []),
            settings=dict(meta.get("settings") or {}),
            template=meta.get("template", "minimal"),
            git_initialized=bool(meta.get("git_initialized", False)),
            schema_version=version or SCHEMA_VERSION,
            created_at=float(meta.get("created_at", time.time())),
            updated_at=float(meta.get("updated_at", time.time())),
        )
        if version < SCHEMA_VERSION:
            project.save()
        return project

    def conversations_dir(self) -> Path:
        d = self.root / "conversations"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def current_conversation_path(self) -> Path:
        return self.conversations_dir() / "current.json"

    def conversations_index_path(self) -> Path:
        return self.conversations_dir() / "index.json"

    def swarm_dir(self) -> Path:
        d = self.root / "swarm"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def swarm_index_path(self) -> Path:
        return self.swarm_dir() / "index.json"

    def downloads_dir(self) -> Path:
        d = self.root / "downloads"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def logs_dir(self) -> Path:
        d = self.root / "logs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def notes_path(self) -> Path:
        return self.root / "notes.md"

    def read_notes(self) -> str:
        p = self.notes_path()
        if not p.exists():
            return ""
        try:
            return p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""

    def write_notes(self, text: str) -> None:
        self.notes_path().write_text(sanitize_text(text), encoding="utf-8")

    def load_current_conversation(self) -> list[dict]:
        data = _read_json(self.current_conversation_path(), {})
        if not isinstance(data, dict):
            return []
        return list(data.get("messages") or [])

    def save_current_conversation(
        self, messages: list[dict], title: str = ""
    ) -> None:
        if not messages:
            return
        p = self.current_conversation_path()
        created = time.time()
        if p.exists():
            existing = _read_json(p, {})
            if isinstance(existing, dict):
                created = existing.get("created_at", created)
        if not title:
            for m in messages:
                if m.get("role") == "user" and m.get("content"):
                    title = str(m["content"]).strip().splitlines()[0][:80]
                    break
        _write_json(p, {
            "id": "current",
            "title": sanitize_text(title or "untitled"),
            "created_at": created,
            "updated_at": time.time(),
            "messages": sanitize_obj(messages),
        })

    def archive_current_conversation(self) -> Optional[str]:
        p = self.current_conversation_path()
        if not p.exists():
            return None
        data = _read_json(p, {})
        if not isinstance(data, dict) or not data.get("messages"):
            try:
                p.unlink()
            except OSError:
                pass
            return None
        new_id = generate_id()
        target = self.conversations_dir() / f"{new_id}.json"
        data["id"] = new_id
        _write_json(target, sanitize_obj(data))
        try:
            p.unlink()
        except OSError:
            pass
        self._reindex_conversations()
        return new_id

    def _reindex_conversations(self) -> None:
        entries: list[dict] = []
        for f in sorted(self.conversations_dir().glob("*.json")):
            if f.name in ("index.json", "current.json"):
                continue
            data = _read_json(f, {})
            if not isinstance(data, dict):
                continue
            entries.append({
                "id": f.stem,
                "title": sanitize_text(data.get("title", "untitled")),
                "created_at": float(data.get("created_at", 0.0)),
                "updated_at": float(data.get("updated_at", 0.0)),
                "messages": len(data.get("messages") or []),
            })
        entries.sort(key=lambda e: e["updated_at"], reverse=True)
        _write_json(self.conversations_index_path(), {
            "schema_version": SCHEMA_VERSION,
            "updated_at": time.time(),
            "conversations": entries,
        })

    def list_conversations(self) -> list[dict]:
        idx = self.conversations_index_path()
        if idx.exists():
            data = _read_json(idx, {})
            if isinstance(data, dict):
                return list(data.get("conversations") or [])
        self._reindex_conversations()
        data = _read_json(idx, {})
        if isinstance(data, dict):
            return list(data.get("conversations") or [])
        return []

    def load_conversation(self, conversation_id: str) -> Optional[list[dict]]:
        if conversation_id == "current":
            return self.load_current_conversation()
        p = self.conversations_dir() / f"{conversation_id}.json"
        if not p.exists():
            return None
        data = _read_json(p, {})
        if not isinstance(data, dict):
            return None
        return list(data.get("messages") or [])

    def _reindex_swarm(self) -> None:
        entries: list[dict] = []
        for f in sorted(self.swarm_dir().glob("*.json")):
            if f.name == "index.json":
                continue
            data = _read_json(f, {})
            if not isinstance(data, dict):
                continue
            todos = data.get("todos") or []
            done = sum(1 for t in todos if t.get("status") == "done")
            failed = sum(1 for t in todos if t.get("status") == "failed")
            entries.append({
                "id": f.stem,
                "task": sanitize_text((data.get("original_task") or ""))[:120],
                "todos": len(todos),
                "done": done,
                "failed": failed,
                "created_at": float(data.get("created_at", 0.0)),
                "finished_at": float(data.get("finished_at") or 0.0),
            })
        entries.sort(key=lambda e: e["created_at"], reverse=True)
        _write_json(self.swarm_index_path(), {
            "schema_version": SCHEMA_VERSION,
            "updated_at": time.time(),
            "runs": entries,
        })

    def list_swarm_runs(self) -> list[dict]:
        idx = self.swarm_index_path()
        if idx.exists():
            data = _read_json(idx, {})
            if isinstance(data, dict):
                return list(data.get("runs") or [])
        self._reindex_swarm()
        data = _read_json(idx, {})
        if isinstance(data, dict):
            return list(data.get("runs") or [])
        return []


class ProjectIndex:
    def __init__(self, path: Path):
        self.path = path

    def _read(self) -> dict:
        data = _read_json(self.path, None)
        if not isinstance(data, dict) or "projects" not in data:
            return {
                "schema_version": SCHEMA_VERSION,
                "updated_at": time.time(),
                "projects": [],
            }
        return data

    def _write(self, data: dict) -> None:
        data["schema_version"] = SCHEMA_VERSION
        data["updated_at"] = time.time()
        _write_json(self.path, data)

    def upsert(self, project: Project) -> None:
        data = self._read()
        entry = project.to_index_entry()
        projects = [
            p for p in data["projects"] if p.get("slug") != project.slug
        ]
        projects.append(entry)
        projects.sort(key=lambda p: p.get("updated_at", 0.0), reverse=True)
        data["projects"] = projects
        self._write(data)

    def remove(self, slug: str) -> None:
        data = self._read()
        data["projects"] = [
            p for p in data["projects"] if p.get("slug") != slug
        ]
        self._write(data)

    def entries(self) -> list[dict]:
        return list(self._read().get("projects") or [])


class ProjectManager:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.index = ProjectIndex(self.root.parent / "projects.json")
        self._self_heal()

    def _self_heal(self) -> None:
        known = {p["slug"] for p in self.index.entries()}
        disk: set[str] = set()
        for entry in sorted(self.root.iterdir()):
            if not entry.is_dir() or entry.name == CHAT_SLUG:
                continue
            if not (entry / "project.json").exists():
                continue
            disk.add(entry.name)
            if entry.name not in known:
                try:
                    self.index.upsert(Project.load(entry))
                except Exception:
                    pass
        for slug in known - disk:
            self.index.remove(slug)

    def list_projects(self) -> list[Project]:
        out: list[Project] = []
        for entry in sorted(self.root.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name == CHAT_SLUG:
                continue
            if not (entry / "project.json").exists():
                continue
            try:
                out.append(Project.load(entry))
            except Exception:
                continue
        out.sort(key=lambda p: p.updated_at, reverse=True)
        return out

    def find(self, slug: str) -> Optional[Project]:
        p = self.root / slug
        if not p.is_dir() or not (p / "project.json").exists():
            return None
        try:
            return Project.load(p)
        except Exception:
            return None

    def unique_slug(self, base: str) -> str:
        slug = slugify(base)
        if slug == CHAT_SLUG:
            slug = "chat"
        candidate = slug
        i = 2
        while (self.root / candidate).exists():
            candidate = f"{slug}-{i}"
            i += 1
        return candidate

    def _git_available(self) -> bool:
        return shutil.which("git") is not None

    def _git_init(self, workspace: Path) -> bool:
        if not self._git_available():
            return False
        try:
            subprocess.run(
                ["git", "init", "-q"],
                cwd=str(workspace), check=True, timeout=30, capture_output=True,
            )
            subprocess.run(
                ["git", "add", "-A"],
                cwd=str(workspace), check=False, timeout=30, capture_output=True,
            )
            subprocess.run(
                ["git", "-c", "user.name=agnes",
                 "-c", "user.email=agnes@local",
                 "commit", "-q", "-m", "chore: initial scaffold"],
                cwd=str(workspace), check=False, timeout=30, capture_output=True,
            )
            return True
        except Exception:
            return False

    def create(
        self,
        name: str,
        description: str = "",
        kind: str = "other",
        language: str = "python",
        tags: Optional[list[str]] = None,
        template: str = "minimal",
        git: bool = False,
        starter_files: bool = True,
    ) -> Project:
        slug = self.unique_slug(name)
        root = self.root / slug
        root.mkdir(parents=True)

        workspace = root / "workspace"
        workspace.mkdir()

        (root / "conversations").mkdir()
        _write_json(root / "conversations" / "index.json", {
            "schema_version": SCHEMA_VERSION,
            "updated_at": time.time(),
            "conversations": [],
        })

        (root / "swarm").mkdir()
        _write_json(root / "swarm" / "index.json", {
            "schema_version": SCHEMA_VERSION,
            "updated_at": time.time(),
            "runs": [],
        })

        (root / "downloads").mkdir()
        (root / "logs").mkdir()

        project = Project(
            slug=slug,
            name=sanitize_text(name.strip() or slug),
            description=sanitize_text(description.strip()),
            kind=kind,
            language=language,
            tags=[sanitize_text(t) for t in (tags or [])],
            template=template,
            root=root,
            workspace=workspace,
        )

        if starter_files and template != "minimal":
            for rel, content in _template_files(template, project).items():
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(sanitize_text(content), encoding="utf-8")

        (root / ".gitignore").write_text(
            sanitize_text(_render_gitignore(language)), encoding="utf-8"
        )
        (root / "README.md").write_text(
            sanitize_text(_render_readme(project)), encoding="utf-8"
        )
        (root / "notes.md").write_text(
            sanitize_text(_render_notes(project)), encoding="utf-8"
        )
        (root / ".env.example").write_text(
            sanitize_text(_render_env_example(language)), encoding="utf-8"
        )

        if git:
            project.git_initialized = self._git_init(workspace)

        project.save()
        self.index.upsert(project)
        return project

    def import_existing(
        self,
        source: Path,
        name: Optional[str] = None,
        description: str = "",
        kind: str = "other",
        language: str = "other",
        tags: Optional[list[str]] = None,
        git: bool = False,
    ) -> Project:
        source = source.expanduser().resolve()
        if not source.is_dir():
            raise ValueError(f"not a directory: {source}")

        slug = self.unique_slug(name or source.name)
        root = self.root / slug
        root.mkdir(parents=True)

        workspace = root / "workspace"
        try:
            os.symlink(source, workspace, target_is_directory=True)
            linked = True
        except (OSError, NotImplementedError):
            shutil.copytree(source, workspace, dirs_exist_ok=True)
            linked = False

        (root / "conversations").mkdir()
        _write_json(root / "conversations" / "index.json", {
            "schema_version": SCHEMA_VERSION,
            "updated_at": time.time(),
            "conversations": [],
        })
        (root / "swarm").mkdir()
        _write_json(root / "swarm" / "index.json", {
            "schema_version": SCHEMA_VERSION,
            "updated_at": time.time(),
            "runs": [],
        })
        (root / "downloads").mkdir()
        (root / "logs").mkdir()

        project = Project(
            slug=slug,
            name=sanitize_text((name or source.name).strip() or slug),
            description=sanitize_text(
                description.strip() or f"Imported from {source}"
            ),
            kind=kind,
            language=language,
            tags=[sanitize_text(t) for t in (tags or [])],
            template="imported",
            git_initialized=git,
            root=root,
            workspace=workspace,
        )
        project.settings["imported_from"] = str(source)
        project.settings["workspace_linked"] = linked

        (root / ".gitignore").write_text(
            sanitize_text(_render_gitignore(language)), encoding="utf-8"
        )
        (root / "README.md").write_text(
            sanitize_text(_render_readme(project)), encoding="utf-8"
        )
        (root / "notes.md").write_text(
            sanitize_text(_render_notes(project)), encoding="utf-8"
        )
        (root / ".env.example").write_text(
            sanitize_text(_render_env_example(language)), encoding="utf-8"
        )

        if git and linked:
            project.git_initialized = self._git_init(source)

        project.save()
        self.index.upsert(project)
        return project

    def delete(self, slug: str) -> bool:
        if slug == CHAT_SLUG:
            return False
        p = self.root / slug
        if not p.is_dir():
            return False
        workspace = p / "workspace"
        if workspace.is_symlink():
            try:
                workspace.unlink()
            except OSError:
                pass
        shutil.rmtree(p, ignore_errors=True)
        self.index.remove(slug)
        return True

    def touch(self, project: Project) -> None:
        project.save()
        self.index.upsert(project)


class ChatStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def current_path(self) -> Path:
        return self.root / "current.json"

    def index_path(self) -> Path:
        return self.root / "index.json"

    def _read_index(self) -> dict:
        data = _read_json(self.index_path(), None)
        if not isinstance(data, dict) or "conversations" not in data:
            return {
                "schema_version": SCHEMA_VERSION,
                "updated_at": time.time(),
                "conversations": [],
            }
        return data

    def _write_index(self, data: dict) -> None:
        data["schema_version"] = SCHEMA_VERSION
        data["updated_at"] = time.time()
        _write_json(self.index_path(), data)

    def load_current(self) -> list[dict]:
        data = _read_json(self.current_path(), {})
        if not isinstance(data, dict):
            return []
        return list(data.get("messages") or [])

    def save_current(self, messages: list[dict], title: str = "") -> None:
        if not messages:
            return
        p = self.current_path()
        created = time.time()
        if p.exists():
            existing = _read_json(p, {})
            if isinstance(existing, dict):
                created = existing.get("created_at", created)
        if not title:
            for m in messages:
                if m.get("role") == "user" and m.get("content"):
                    title = str(m["content"]).strip().splitlines()[0][:80]
                    break
        _write_json(p, {
            "id": "current",
            "title": sanitize_text(title or "untitled"),
            "created_at": created,
            "updated_at": time.time(),
            "messages": sanitize_obj(messages),
        })

    def archive_current(self) -> Optional[str]:
        p = self.current_path()
        if not p.exists():
            return None
        data = _read_json(p, {})
        if not isinstance(data, dict) or not data.get("messages"):
            try:
                p.unlink()
            except OSError:
                pass
            return None
        new_id = generate_id()
        target = self.root / f"{new_id}.json"
        data["id"] = new_id
        _write_json(target, sanitize_obj(data))
        try:
            p.unlink()
        except OSError:
            pass
        self._reindex()
        return new_id

    def _reindex(self) -> None:
        entries: list[dict] = []
        for f in sorted(self.root.glob("*.json")):
            if f.name in ("index.json", "current.json"):
                continue
            data = _read_json(f, {})
            if not isinstance(data, dict):
                continue
            entries.append({
                "id": f.stem,
                "title": sanitize_text(data.get("title", "untitled")),
                "created_at": float(data.get("created_at", 0.0)),
                "updated_at": float(data.get("updated_at", 0.0)),
                "messages": len(data.get("messages") or []),
            })
        entries.sort(key=lambda e: e["updated_at"], reverse=True)
        self._write_index({
            "schema_version": SCHEMA_VERSION,
            "updated_at": time.time(),
            "conversations": entries,
        })

    def list_conversations(self) -> list[dict]:
        data = self._read_index()
        entries = list(data.get("conversations") or [])
        cur = self.current_path()
        if cur.exists():
            cd = _read_json(cur, {})
            if isinstance(cd, dict) and cd.get("messages"):
                entries = [e for e in entries if e.get("id") != "current"]
                entries.insert(0, {
                    "id": "current",
                    "title": sanitize_text(cd.get("title", "untitled")),
                    "created_at": float(cd.get("created_at", 0.0)),
                    "updated_at": float(cd.get("updated_at", 0.0)),
                    "messages": len(cd.get("messages") or []),
                })
        return entries

    def load_conversation(self, conversation_id: str) -> Optional[list[dict]]:
        if conversation_id == "current":
            return self.load_current()
        p = self.root / f"{conversation_id}.json"
        if not p.exists():
            return None
        data = _read_json(p, {})
        if not isinstance(data, dict):
            return None
        return list(data.get("messages") or [])


@dataclass
class Config:
    api_keys: list[str]
    provider: str = DEFAULT_PROVIDER
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    agent_name: str = DEFAULT_AGENT_NAME
    agent_tagline: str = DEFAULT_AGENT_TAGLINE
    custom_instructions: str = DEFAULT_CUSTOM_INSTRUCTIONS
    project: Optional[Project] = None
    fallback_root: Path = field(default_factory=Path.cwd)
    downloads_dir: Optional[Path] = None
    temperature: float = 0.3
    max_tokens: int = DEFAULT_MAX_TOKENS
    max_rounds: int = DEFAULT_MAX_ROUNDS
    thinking: bool = False
    stream: bool = True
    verbose: bool = False
    headless_browser: bool = True
    timeout: float = 300.0
    tier: str = DEFAULT_TIER
    rpm_override: Optional[int] = None
    max_retries: int = DEFAULT_MAX_RETRIES
    swarm_workers: Optional[int] = None
    swarm_default: bool = False

    @property
    def project_root(self) -> Path:
        if self.project is not None and self.project.workspace is not None:
            return self.project.workspace
        return self.fallback_root

    @property
    def effective_downloads_dir(self) -> Path:
        if self.downloads_dir is not None:
            return self.downloads_dir
        if self.project is not None:
            return self.project.downloads_dir()
        return self.fallback_root / "downloads"


class RateLimiter:
    def __init__(self, rpm: int, window: float = 60.0):
        if rpm <= 0:
            raise ValueError("rpm must be positive")
        self.rpm = rpm
        self.window = window
        self._timestamps: deque[float] = deque()
        self._lock: Optional[asyncio.Lock] = None
        self._waits = 0

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _prune(self) -> None:
        now = time.monotonic()
        while self._timestamps and now - self._timestamps[0] >= self.window:
            self._timestamps.popleft()

    def remaining(self) -> int:
        self._prune()
        return max(0, self.rpm - len(self._timestamps))

    def usage(self) -> int:
        self._prune()
        return len(self._timestamps)

    def seconds_until_slot(self) -> float:
        self._prune()
        if len(self._timestamps) < self.rpm:
            return 0.0
        now = time.monotonic()
        return max(0.0, self.window - (now - self._timestamps[0]))

    def reserve_now(self) -> None:
        self._timestamps.append(time.monotonic())

    async def acquire(self) -> float:
        async with self._get_lock():
            waited = 0.0
            while True:
                self._prune()
                if len(self._timestamps) < self.rpm:
                    self._timestamps.append(time.monotonic())
                    return waited
                wait = self.seconds_until_slot() + 0.05
                self._waits += 1
                await asyncio.sleep(wait)
                waited += wait

    def status(self) -> dict:
        self._prune()
        now = time.monotonic()
        oldest = self._timestamps[0] if self._timestamps else None
        return {
            "rpm_limit": self.rpm,
            "window_seconds": self.window,
            "used_in_window": len(self._timestamps),
            "remaining": max(0, self.rpm - len(self._timestamps)),
            "seconds_until_slot": (
                max(0.0, self.window - (now - oldest))
                if oldest is not None and len(self._timestamps) >= self.rpm
                else 0.0
            ),
            "throttle_events": self._waits,
        }


@dataclass
class KeyEntry:
    key: str
    limiter: RateLimiter
    cooldown_until: float = 0.0
    cooldown_count: int = 0
    requests: int = 0

    def is_available(self) -> bool:
        return (
            time.monotonic() >= self.cooldown_until
            and self.limiter.remaining() > 0
        )

    def set_cooldown(self, seconds: float) -> None:
        self.cooldown_until = time.monotonic() + seconds
        self.cooldown_count += 1

    def label(self) -> str:
        return f"...{self.key[-6:]}" if len(self.key) > 6 else self.key


class KeyPool:
    def __init__(self, keys: list[str], rpm_per_key: int):
        if not keys:
            raise ValueError("at least one key is required")
        self._entries: list[KeyEntry] = [
            KeyEntry(key=k, limiter=RateLimiter(rpm_per_key)) for k in keys
        ]
        self._select_lock: Optional[asyncio.Lock] = None

    def _get_lock(self) -> asyncio.Lock:
        if self._select_lock is None:
            self._select_lock = asyncio.Lock()
        return self._select_lock

    @property
    def entries(self) -> list[KeyEntry]:
        return list(self._entries)

    @property
    def rpm_per_key(self) -> int:
        return self._entries[0].limiter.rpm

    @property
    def total_rpm(self) -> int:
        return sum(e.limiter.rpm for e in self._entries)

    def total_usage(self) -> int:
        return sum(e.limiter.usage() for e in self._entries)

    async def acquire(self) -> KeyEntry:
        while True:
            async with self._get_lock():
                available = [e for e in self._entries if e.is_available()]
                if available:
                    entry = max(available, key=lambda e: e.limiter.remaining())
                    entry.limiter.reserve_now()
                    entry.requests += 1
                    return entry

                cooling = [
                    (e, e.cooldown_until - time.monotonic())
                    for e in self._entries
                    if e.cooldown_until > time.monotonic()
                ]
                if cooling:
                    soonest, wait = min(cooling, key=lambda x: x[1])
                else:
                    soonest, wait = min(
                        ((e, e.limiter.seconds_until_slot()) for e in self._entries),
                        key=lambda x: x[1],
                    )
            await asyncio.sleep(max(0.05, min(wait, 2.0)))

    def status(self) -> list[dict]:
        out: list[dict] = []
        now = time.monotonic()
        for e in self._entries:
            st = e.limiter.status()
            st["key"] = e.label()
            st["cooldown_seconds"] = max(0.0, e.cooldown_until - now)
            st["cooldown_count"] = e.cooldown_count
            st["requests"] = e.requests
            out.append(st)
        return out


class AgnesAPIError(RuntimeError):
    pass


_TRANSIENT_NET_ERRORS = (
    httpx.TransportError,
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
)


class AgnesClient:
    def __init__(
        self,
        config: Config,
        pool: KeyPool,
        on_retry: Optional[Any] = None,
    ):
        self.model = config.model
        self.provider = str(getattr(config, "provider", DEFAULT_PROVIDER) or DEFAULT_PROVIDER).lower()
        self.tier = config.tier
        self._pool = pool
        self._max_retries = max(0, config.max_retries)
        self._retry_cap = DEFAULT_RETRY_CAP
        self._on_retry = on_retry
        self._clients: dict[str, httpx.AsyncClient] = {}
        self.last_key_label: str = "—"
        self.provider_adapter = get_provider_adapter(self)

        for entry in pool.entries:
            self._clients[entry.key] = httpx.AsyncClient(
                base_url=config.base_url.rstrip("/"),
                headers=self._headers_for(entry.key, stream=True),
                timeout=httpx.Timeout(config.timeout, connect=20.0),
            )

    @property
    def rate_limiter(self) -> RateLimiter:
        return self._pool.entries[0].limiter

    def pool_status(self) -> list[dict]:
        return self._pool.status()

    def pool_totals(self) -> dict:
        return {
            "keys": len(self._pool.entries),
            "rpm_per_key": self._pool.rpm_per_key,
            "total_rpm": self._pool.total_rpm,
            "used_in_window": self._pool.total_usage(),
        }

    async def close(self) -> None:
        for client in self._clients.values():
            await client.aclose()

    @staticmethod
    def _parse_retry_after(response: httpx.Response) -> Optional[float]:
        raw = response.headers.get("retry-after")
        if not raw:
            return None
        try:
            return max(0.0, float(raw))
        except ValueError:
            return 30.0

    def _headers_for(self, api_key: str, *, stream: bool = True) -> dict[str, str]:
        provider = self.provider
        if provider == "anthropic":
            return {
                "x-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "anthropic-version": "2023-06-01",
                "User-Agent": "agnes-agent/1.0",
            }
        if provider == "gemini":
            return {
                "Content-Type": "application/json",
                "User-Agent": "agnes-agent/1.0",
            }
        if provider in {"deepseek", "perplexity", "xai"}:
            return {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "agnes-agent/1.0",
            }
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "User-Agent": "agnes-agent/1.0",
        }

    def _build_payload(
        self,
        messages: list[dict],
        *,
        tools: Optional[list[dict]],
        thinking: bool,
        temperature: float,
        max_tokens: int,
        stream: bool,
    ) -> dict:
        safe_messages = sanitize_obj(
            _normalize_messages_for_api(_repair_tool_call_history(messages))
        )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": safe_messages,
            "stream": stream,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if thinking:
            payload["chat_template_kwargs"] = {"enable_thinking": True}
        return payload

    @staticmethod
    def _message_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            pieces: list[str] = []
            for item in content:
                if isinstance(item, str):
                    pieces.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content") or ""
                    if text:
                        pieces.append(str(text))
            return "\n".join(pieces)
        if content is None:
            return ""
        return str(content)

    @staticmethod
    def _tool_schema_to_anthropic(tool: dict) -> dict:
        fn = tool.get("function") or {}
        schema = fn.get("parameters") or {}
        return {
            "name": fn.get("name", "tool"),
            "description": fn.get("description", ""),
            "input_schema": {
                "type": schema.get("type", "object"),
                "properties": schema.get("properties", {}),
                "required": schema.get("required", []),
            },
        }

    def _anthropic_messages(self, messages: list[dict]) -> list[dict]:
        out: list[dict] = []
        system_parts: list[str] = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            if role == "system":
                system_parts.append(self._message_text(content))
                continue
            if role == "user":
                out.append({"role": "user", "content": self._message_text(content)})
                continue
            if role == "assistant":
                tool_calls = msg.get("tool_calls") or []
                blocks: list[dict] = []
                if tool_calls:
                    for tc in tool_calls:
                        fn = tc.get("function") or {}
                        args = fn.get("arguments") or "{}"
                        try:
                            parsed = json.loads(args)
                        except Exception:
                            parsed = {"raw": args}
                        blocks.append({
                            "type": "tool_use",
                            "id": tc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                            "name": fn.get("name", "tool"),
                            "input": parsed,
                        })
                if content:
                    text = self._message_text(content)
                    if text:
                        blocks.append({"type": "text", "text": text})
                if blocks:
                    out.append({"role": "assistant", "content": blocks})
                continue
            if role == "tool":
                out.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.get("tool_call_id") or f"call_{uuid.uuid4().hex[:8]}",
                            "content": self._message_text(content),
                        }
                    ],
                })
        return out

    async def _stream_agnes_style(
        self,
        messages: list[dict],
        *,
        tools: Optional[list[dict]] = None,
        thinking: bool = False,
        temperature: float = 0.3,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        stream: bool = True,
    ):
        async for item in self._stream_openai_compatible(
            messages,
            tools=tools,
            thinking=thinking,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
        ):
            yield item

    async def _stream_openai_style(
        self,
        messages: list[dict],
        *,
        tools: Optional[list[dict]] = None,
        thinking: bool = False,
        temperature: float = 0.3,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        stream: bool = True,
    ):
        async for item in self._stream_openai_compatible(
            messages,
            tools=tools,
            thinking=thinking,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
        ):
            yield item

    async def _stream_local_style(
        self,
        messages: list[dict],
        *,
        tools: Optional[list[dict]] = None,
        thinking: bool = False,
        temperature: float = 0.3,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        stream: bool = True,
    ):
        async for item in self._stream_openai_compatible(
            messages,
            tools=tools,
            thinking=thinking,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
        ):
            yield item

    async def _stream_openrouter_style(
        self,
        messages: list[dict],
        *,
        tools: Optional[list[dict]] = None,
        thinking: bool = False,
        temperature: float = 0.3,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        stream: bool = True,
    ):
        async for item in self._stream_openai_compatible(
            messages,
            tools=tools,
            thinking=thinking,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
        ):
            yield item

    async def _stream_deepseek_style(
        self,
        messages: list[dict],
        *,
        tools: Optional[list[dict]] = None,
        thinking: bool = False,
        temperature: float = 0.3,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        stream: bool = True,
    ):
        async for item in self._stream_openai_compatible(
            messages,
            tools=tools,
            thinking=thinking,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
        ):
            yield item

    async def _stream_groq_style(
        self,
        messages: list[dict],
        *,
        tools: Optional[list[dict]] = None,
        thinking: bool = False,
        temperature: float = 0.3,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        stream: bool = True,
    ):
        async for item in self._stream_openai_compatible(
            messages,
            tools=tools,
            thinking=thinking,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
        ):
            yield item

    async def _stream_mistral_style(
        self,
        messages: list[dict],
        *,
        tools: Optional[list[dict]] = None,
        thinking: bool = False,
        temperature: float = 0.3,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        stream: bool = True,
    ):
        async for item in self._stream_openai_compatible(
            messages,
            tools=tools,
            thinking=thinking,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
        ):
            yield item

    async def _stream_together_style(
        self,
        messages: list[dict],
        *,
        tools: Optional[list[dict]] = None,
        thinking: bool = False,
        temperature: float = 0.3,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        stream: bool = True,
    ):
        async for item in self._stream_openai_compatible(
            messages,
            tools=tools,
            thinking=thinking,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
        ):
            yield item

    async def _stream_cohere_style(
        self,
        messages: list[dict],
        *,
        tools: Optional[list[dict]] = None,
        thinking: bool = False,
        temperature: float = 0.3,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        stream: bool = True,
    ):
        async for item in self._stream_openai_compatible(
            messages,
            tools=tools,
            thinking=thinking,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
        ):
            yield item

    async def _stream_ollama_style(
        self,
        messages: list[dict],
        *,
        tools: Optional[list[dict]] = None,
        thinking: bool = False,
        temperature: float = 0.3,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        stream: bool = True,
    ):
        async for item in self._stream_openai_compatible(
            messages,
            tools=tools,
            thinking=thinking,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
        ):
            yield item

    async def _stream_perplexity_style(
        self,
        messages: list[dict],
        *,
        tools: Optional[list[dict]] = None,
        thinking: bool = False,
        temperature: float = 0.3,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        stream: bool = True,
    ):
        async for item in self._stream_openai_compatible(
            messages,
            tools=tools,
            thinking=thinking,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
        ):
            yield item

    async def _stream_fireworks_style(
        self,
        messages: list[dict],
        *,
        tools: Optional[list[dict]] = None,
        thinking: bool = False,
        temperature: float = 0.3,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        stream: bool = True,
    ):
        async for item in self._stream_openai_compatible(
            messages,
            tools=tools,
            thinking=thinking,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
        ):
            yield item

    async def _stream_xai_style(
        self,
        messages: list[dict],
        *,
        tools: Optional[list[dict]] = None,
        thinking: bool = False,
        temperature: float = 0.3,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        stream: bool = True,
    ):
        async for item in self._stream_openai_compatible(
            messages,
            tools=tools,
            thinking=thinking,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
        ):
            yield item

    async def _stream_openai_compatible(
        self,
        messages: list[dict],
        *,
        tools: Optional[list[dict]] = None,
        thinking: bool = False,
        temperature: float = 0.3,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        stream: bool = True,
    ):
        payload = self._build_payload(
            messages,
            tools=tools,
            thinking=thinking,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
        )

        attempt = 0
        while True:
            entry = await self._pool.acquire()
            self.last_key_label = entry.label()
            client = self._clients[entry.key]
            request = client.build_request("POST", "/chat/completions", json=payload)

            try:
                response = await client.send(request, stream=True)
            except _TRANSIENT_NET_ERRORS as e:
                if attempt >= self._max_retries:
                    raise AgnesAPIError(f"network error after {attempt + 1} attempts: {type(e).__name__}: {str(e) or '(no detail)'}")
                attempt += 1
                wait = min(self._retry_cap, 2.0 ** attempt)
                if self._on_retry is not None:
                    try:
                        self._on_retry(attempt, wait, f"net: {type(e).__name__}")
                    except Exception:
                        pass
                await asyncio.sleep(wait)
                continue

            if response.status_code == 429:
                retry_after = self._parse_retry_after(response)
                await response.aread(); await response.aclose()
                wait = retry_after if retry_after is not None else min(self._retry_cap, 2.0 ** attempt)
                entry.set_cooldown(wait)
                if attempt >= self._max_retries:
                    raise AgnesAPIError(f"Rate limited after {attempt + 1} attempts (key={entry.label()}, tier={self.tier}).")
                attempt += 1
                if self._on_retry is not None:
                    try:
                        self._on_retry(attempt, wait, f"429 on {entry.label()}")
                    except Exception:
                        pass
                continue

            if response.status_code >= 400:
                body = await response.aread(); await response.aclose()
                raise AgnesAPIError(f"Provider API error {response.status_code}: {body.decode(errors='replace')[:2000]}")

            yielded_any = False
            try:
                if not stream:
                    data = await response.aread(); await response.aclose()
                    try:
                        yield json.loads(data)
                    except json.JSONDecodeError as e:
                        raise AgnesAPIError(f"Malformed non-streaming response: {e}") from e
                    return
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        return
                    try:
                        parsed = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    yielded_any = True
                    yield parsed
            except _TRANSIENT_NET_ERRORS as e:
                if yielded_any:
                    raise AgnesAPIError(f"stream broke mid-response: {type(e).__name__}: {str(e) or '(no detail)'}")
                if attempt >= self._max_retries:
                    raise AgnesAPIError(f"stream error after {attempt + 1} attempts: {type(e).__name__}: {str(e) or '(no detail)'}")
                attempt += 1
                wait = min(self._retry_cap, 2.0 ** attempt)
                if self._on_retry is not None:
                    try:
                        self._on_retry(attempt, wait, f"stream: {type(e).__name__}")
                    except Exception:
                        pass
                await asyncio.sleep(wait)
                continue
            finally:
                try:
                    await response.aclose()
                except Exception:
                    pass
            return

    async def _stream_anthropic(
        self,
        messages: list[dict],
        *,
        tools: Optional[list[dict]] = None,
        thinking: bool = False,
        temperature: float = 0.3,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        stream: bool = True,
    ):
        system_parts = []
        for m in messages:
            if m.get("role") == "system":
                system_parts.append(self._message_text(m.get("content")))
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._anthropic_messages(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }
        if system_parts:
            payload["system"] = "\n".join(system_parts)
        if tools:
            payload["tools"] = [self._tool_schema_to_anthropic(t) for t in tools]
        if thinking:
            payload["thinking"] = {"type": "enabled"}

        attempt = 0
        while True:
            entry = await self._pool.acquire(); self.last_key_label = entry.label(); client = self._clients[entry.key]
            request = client.build_request("POST", "/messages", json=payload)
            try:
                response = await client.send(request, stream=stream)
            except _TRANSIENT_NET_ERRORS as e:
                if attempt >= self._max_retries:
                    raise AgnesAPIError(f"network error after {attempt + 1} attempts: {type(e).__name__}: {str(e) or '(no detail)'}")
                attempt += 1; wait = min(self._retry_cap, 2.0 ** attempt)
                if self._on_retry is not None:
                    try: self._on_retry(attempt, wait, f"net: {type(e).__name__}")
                    except Exception: pass
                await asyncio.sleep(wait); continue

            if response.status_code == 429:
                retry_after = self._parse_retry_after(response); await response.aread(); await response.aclose()
                wait = retry_after if retry_after is not None else min(self._retry_cap, 2.0 ** attempt)
                entry.set_cooldown(wait)
                if attempt >= self._max_retries:
                    raise AgnesAPIError(f"Rate limited after {attempt + 1} attempts (key={entry.label()}, tier={self.tier}).")
                attempt += 1
                if self._on_retry is not None:
                    try: self._on_retry(attempt, wait, f"429 on {entry.label()}")
                    except Exception: pass
                continue

            if response.status_code >= 400:
                body = await response.aread(); await response.aclose(); raise AgnesAPIError(f"Anthropic API error {response.status_code}: {body.decode(errors='replace')[:2000]}")

            if not stream:
                data = await response.aread(); await response.aclose();
                try:
                    parsed = json.loads(data)
                except json.JSONDecodeError as e:
                    raise AgnesAPIError(f"Malformed non-streaming response: {e}") from e
                text = ""
                for block in parsed.get("content") or []:
                    if block.get("type") == "text":
                        text += block.get("text", "")
                yield {"id": parsed.get("id", "msg_0"), "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": parsed.get("stop_reason") or "stop"}], "usage": {"prompt_tokens": parsed.get("usage", {}).get("input_tokens", 0), "completion_tokens": parsed.get("usage", {}).get("output_tokens", 0)}}
                return

            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    return
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                event_type = parsed.get("type")
                if event_type == "content_block_delta":
                    delta = parsed.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            yield {"id": "msg_0", "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}
                elif event_type == "message_delta":
                    stop_reason = parsed.get("delta", {}).get("stop_reason")
                    if stop_reason:
                        yield {"id": "msg_0", "choices": [{"index": 0, "delta": {}, "finish_reason": stop_reason}]}
            try:
                await response.aclose()
            except Exception:
                pass
            return

    async def _stream_gemini(
        self,
        messages: list[dict],
        *,
        tools: Optional[list[dict]] = None,
        thinking: bool = False,
        temperature: float = 0.3,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        stream: bool = True,
    ):
        contents: list[dict[str, Any]] = []
        system_prompt = "\n".join(
            self._message_text(m.get("content"))
            for m in messages if m.get("role") == "system"
        )
        for msg in messages:
            role = msg.get("role")
            if role == "system":
                continue
            text = self._message_text(msg.get("content"))
            if not text:
                continue
            contents.append({
                "role": "model" if role == "assistant" else "user",
                "parts": [{"text": text}],
            })

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}
        if tools:
            payload["tools"] = [{
                "functionDeclarations": [
                    {
                        "name": (t.get("function") or {}).get("name", "tool"),
                        "description": (t.get("function") or {}).get("description", ""),
                        "parameters": (t.get("function") or {}).get("parameters") or {"type": "object", "properties": {}},
                    }
                    for t in tools
                ]
            }]
        if thinking:
            payload["generationConfig"]["thinkingConfig"] = {"includeThoughts": True}

        attempt = 0
        while True:
            entry = await self._pool.acquire(); self.last_key_label = entry.label(); client = self._clients[entry.key]
            request = client.build_request("POST", f"/{self.model}:generateContent?key={entry.key}", json=payload)
            try:
                response = await client.send(request)
            except _TRANSIENT_NET_ERRORS as e:
                if attempt >= self._max_retries:
                    raise AgnesAPIError(f"network error after {attempt + 1} attempts: {type(e).__name__}: {str(e) or '(no detail)'}")
                attempt += 1; wait = min(self._retry_cap, 2.0 ** attempt)
                if self._on_retry is not None:
                    try: self._on_retry(attempt, wait, f"net: {type(e).__name__}")
                    except Exception: pass
                await asyncio.sleep(wait); continue
            if response.status_code == 429:
                retry_after = self._parse_retry_after(response); await response.aread(); await response.aclose()
                wait = retry_after if retry_after is not None else min(self._retry_cap, 2.0 ** attempt)
                entry.set_cooldown(wait)
                if attempt >= self._max_retries:
                    raise AgnesAPIError(f"Rate limited after {attempt + 1} attempts (key={entry.label()}, tier={self.tier}).")
                attempt += 1
                if self._on_retry is not None:
                    try: self._on_retry(attempt, wait, f"429 on {entry.label()}")
                    except Exception: pass
                continue
            if response.status_code >= 400:
                body = await response.aread(); await response.aclose(); raise AgnesAPIError(f"Gemini API error {response.status_code}: {body.decode(errors='replace')[:2000]}")
            data = await response.aread(); await response.aclose()
            try:
                parsed = json.loads(data)
            except json.JSONDecodeError as e:
                raise AgnesAPIError(f"Malformed Gemini response: {e}") from e
            pieces: list[str] = []
            for cand in parsed.get("candidates") or []:
                for part in (cand.get("content") or {}).get("parts") or []:
                    if isinstance(part, dict):
                        pieces.append(part.get("text", ""))
            text = "".join(pieces)
            usage = parsed.get("usageMetadata") or {}
            yield {
                "id": f"gemini-{uuid.uuid4().hex[:12]}",
                "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": usage.get("promptTokenCount", 0),
                    "completion_tokens": usage.get("candidatesTokenCount", 0),
                },
            }
            return

    async def stream_chat(
        self,
        messages: list[dict],
        *,
        tools: Optional[list[dict]] = None,
        thinking: bool = False,
        temperature: float = 0.3,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        stream: bool = True,
    ):
        handlers = {
            "agnes": self._stream_agnes_style,
            "openai": self._stream_openai_style,
            "anthropic": self._stream_anthropic,
            "gemini": self._stream_gemini,
            "local": self._stream_local_style,
            "openrouter": self._stream_openrouter_style,
            "deepseek": self._stream_deepseek_style,
            "groq": self._stream_groq_style,
            "mistral": self._stream_mistral_style,
            "together": self._stream_together_style,
            "cohere": self._stream_cohere_style,
            "ollama": self._stream_ollama_style,
            "perplexity": self._stream_perplexity_style,
            "fireworks": self._stream_fireworks_style,
            "xai": self._stream_xai_style,
        }

        handler = handlers.get(self.provider, self._stream_openai_compatible)
        async for item in handler(
            messages,
            tools=tools,
            thinking=thinking,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
        ):
            yield item


_PARALLEL_SAFE_TOOLS = {"web_search", "http"}


class ToolRegistry:
    def __init__(self, web_agent: Optional[Any], code_agent: Optional[Any]):
        self.web = web_agent
        self.code = code_agent
        self._schemas: list[dict] = []
        self._enabled: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        self._build()

    def _build(self) -> None:
        if self.web is not None and WEB_SEARCH_SCHEMA is not None:
            self._schemas.append(WEB_SEARCH_SCHEMA)

        if self.code is not None:
            self._schemas.extend(_CODE_TOOLS)
        elif self.web is not None and WEB_BROWSER_SCHEMA is not None:
            self._schemas.append(WEB_BROWSER_SCHEMA)

        for s in self._schemas:
            self._enabled.add(s["function"]["name"])

    def _lock_for(self, name: str) -> asyncio.Lock:
        lock = self._locks.get(name)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[name] = lock
        return lock

    def all_schemas(self) -> list[dict]:
        return list(self._schemas)

    def schemas(self) -> list[dict]:
        return [s for s in self._schemas if s["function"]["name"] in self._enabled]

    def names(self) -> list[str]:
        return [s["function"]["name"] for s in self._schemas]

    def is_enabled(self, name: str) -> bool:
        return name in self._enabled

    def enable(self, name: str) -> bool:
        if name in self.names():
            self._enabled.add(name)
            return True
        return False

    def disable(self, name: str) -> bool:
        if name in self._enabled:
            self._enabled.discard(name)
            return True
        return False

    async def _do_dispatch(self, name: str, arguments: dict) -> Any:
        if name == "web_search":
            if self.web is None:
                return {"error": "web agent is not attached"}
            return await self.web.web_search(**arguments)

        if name == "web_browser":
            if self.web is None:
                return {"error": "web agent is not attached"}
            return await self.web.web_browser(**arguments)

        if self.code is None:
            return {"error": f"tool '{name}' is not available in this session"}

        return await self.code.dispatch(name, **arguments)

    async def dispatch(self, name: str, arguments: dict) -> Any:
        if name in _PARALLEL_SAFE_TOOLS:
            return await self._do_dispatch(name, arguments)
        async with self._lock_for(name):
            return await self._do_dispatch(name, arguments)


def _summarize_error(r: dict) -> str:
    err = r.get("error", "error")
    msg = r.get("message") or r.get("detail") or ""

    if not msg:
        raw = r.get("raw_arguments")
        if isinstance(raw, str) and raw.strip():
            msg = f"args: {_one_line(raw, 120)}"

    err_s = str(err) if err is not None else "error"
    if msg:
        return f"error · {err_s} — {_one_line(msg, 160)}"
    return f"error · {err_s}"


def _summarize_dict(r: dict) -> str:
    if len(r) == 1:
        k, v = next(iter(r.items()))
        if isinstance(v, list):
            return f"{k}: {len(v)} item{'s' if len(v) != 1 else ''}"
        if isinstance(v, str):
            return f"{k}: {_one_line(v, 120)}"
        if isinstance(v, bool):
            return f"{k}: {'yes' if v else 'no'}"
        return f"{k}: {_one_line(v, 120)}"
    keys = list(r.keys())[:6]
    return "{" + ", ".join(keys) + "}"


def _summarize_terminal(r: dict) -> Optional[str]:
    if "process_id" in r:
        return f"started background process {r['process_id']}"
    if "processes" in r and isinstance(r["processes"], list):
        return f"{len(r['processes'])} processes"

    code = r.get("exit_code")
    dur = r.get("duration")
    stdout = r.get("stdout") or ""
    stderr = r.get("stderr") or ""

    parts: list[str] = []
    if code is not None:
        if code == 0:
            parts.append("exit 0")
        else:
            parts.append(f"exit {code}")
    if dur is not None:
        try:
            parts.append(f"{float(dur):.2f}s")
        except (TypeError, ValueError):
            pass
    out_lines = _count_lines(stdout)
    err_lines = _count_lines(stderr)
    if out_lines:
        parts.append(f"{out_lines} out")
    if err_lines:
        parts.append(f"{err_lines} err")

    summary = " · ".join(parts) if parts else "ok"

    first = _first_meaningful_line(stderr, stdout)
    if first:
        summary += f"  ·  {first}"

    return summary


def _summarize_workspace(r: dict) -> Optional[str]:
    if "content" in r and "path" in r:
        lines = r.get("lines", _count_lines(r.get("content", "")))
        total = r.get("total_lines", lines)
        return f"read {r['path']} · {lines}/{total} lines"
    if "bytes" in r and "path" in r:
        return f"wrote {r['path']} · {r['bytes']} bytes"
    if "entries" in r and isinstance(r["entries"], list):
        return f"{len(r['entries'])} entries"
    if "hits" in r and isinstance(r["hits"], list):
        return f"{len(r['hits'])} hits"
    if "files" in r and isinstance(r["files"], list):
        return f"{len(r['files'])} files"
    if "created" in r:
        return f"created {r['created']}"
    if "deleted" in r:
        return f"deleted {r['deleted']}"
    if "exists" in r:
        return f"exists: {'yes' if r['exists'] else 'no'}"
    if "from" in r and "to" in r:
        return f"{r['from']} → {r['to']}"
    if "imports" in r and isinstance(r["imports"], list):
        return f"{len(r['imports'])} imports"
    if "symbols" in r and isinstance(r["symbols"], list):
        return f"{len(r['symbols'])} symbols"
    if "tree" in r:
        return "project tree"
    if "nodes" in r and "edges" in r:
        return f"{len(r['nodes'])} nodes · {len(r['edges'])} edges"
    return None


def _summarize_editor(r: dict) -> Optional[str]:
    if "diff" in r:
        text = r["diff"]
        if not text.strip():
            return "no changes"
        return f"preview diff · {_count_lines(text)} lines"
    if r.get("applied"):
        return f"applied {r.get('file', '?')}"
    if "reverted" in r and r["reverted"]:
        return f"reverted {r['reverted']}"
    if "history" in r and isinstance(r["history"], list):
        return f"{len(r['history'])} patches"
    if r.get("added") and "statement" in r:
        return _one_line(r["statement"], 100)
    if r.get("removed"):
        return f"removed {r.get('lines_removed', '?')} lines"
    if "replaced_lines" in r:
        return f"replaced {r['replaced_lines']} lines"
    if "extracted" in r:
        return f"extracted {r['extracted']}"
    if "inlined" in r:
        return f"inlined: {'yes' if r['inlined'] else 'no'}"
    if "moved" in r and isinstance(r["moved"], int):
        return f"moved {r['moved']} symbols to {r.get('to', '?')}"
    if "simplified" in r:
        return f"simplify: {'ok' if r['simplified'] else 'no change'}"
    return None


def _summarize_git(r: dict) -> Optional[str]:
    if "diff" in r and isinstance(r["diff"], str):
        text = r["diff"]
        if not text.strip():
            return "no diff"
        files = text.count("diff --git ")
        if files == 0:
            files = text.count("--- a/")
        return f"diff · {files} files · {_count_lines(text)} lines"
    if "commits" in r and isinstance(r["commits"], list):
        if not r["commits"]:
            return "no commits"
        return f"{len(r['commits'])} commits"
    if "branch" in r:
        return f"branch {r['branch']}"
    if "branches" in r and isinstance(r["branches"], list):
        return f"{len(r['branches'])} branches"
    if "raw" in r and isinstance(r["raw"], str):
        text = _one_line(r["raw"], 120)
        return text or "clean"
    if "stashes" in r and isinstance(r["stashes"], list):
        return f"{len(r['stashes'])} stashes"
    if "show" in r:
        return f"show · {_count_lines(r['show'])} lines"
    if "blame" in r and isinstance(r["blame"], list):
        return f"{len(r['blame'])} blame lines"
    if "prs" in r and isinstance(r["prs"], list):
        return f"{len(r['prs'])} PRs"
    if "issues" in r and isinstance(r["issues"], list):
        return f"{len(r['issues'])} issues"
    if "url" in r:
        return _one_line(r["url"], 100)
    return None


def _summarize_test(r: dict) -> Optional[str]:
    if "framework" in r and "exit_code" in r:
        fw = r.get("framework") or "?"
        code = r.get("exit_code")
        dur = r.get("duration")
        failures = r.get("failures") or []
        parts = [fw]
        parts.append("passed" if code == 0 else f"exit {code}")
        if dur is not None:
            try:
                parts.append(f"{float(dur):.2f}s")
            except (TypeError, ValueError):
                pass
        if failures:
            first = failures[0]
            name = first.get("test", "?") if isinstance(first, dict) else str(first)
            parts.append(f"{len(failures)} failing · {_one_line(name, 80)}")
        return " · ".join(parts)
    if "failures" in r and isinstance(r["failures"], list):
        if not r["failures"]:
            return "no failures"
        return f"{len(r['failures'])} failures"
    if "tests" in r and isinstance(r["tests"], list):
        return f"{len(r['tests'])} tests discovered"
    if "created" in r:
        return f"created {r['created']}"
    return None


def _summarize_build(r: dict) -> Optional[str]:
    if "system" in r or "managers" in r:
        sys_ = r.get("system") or "?"
        mgrs = r.get("managers") or []
        return f"system={sys_} · managers={', '.join(mgrs) or 'none'}"
    return _summarize_terminal(r)


def _summarize_http(r: dict) -> Optional[str]:
    if "status" in r:
        status = r["status"]
        parts = [f"HTTP {status}"]
        if r.get("ok") is True:
            parts.append("✓")
        elif r.get("ok") is False:
            parts.append("✗")
        lat = r.get("latency")
        if lat is not None:
            try:
                parts.append(f"{float(lat) * 1000:.0f}ms")
            except (TypeError, ValueError):
                pass
        return " · ".join(parts)
    if "status_match" in r:
        return f"match: {'yes' if r['status_match'] else 'no'}"
    return None


def _summarize_database(r: dict) -> Optional[str]:
    if "engines" in r and isinstance(r["engines"], list):
        return ", ".join(r["engines"]) or "none detected"
    if "tables" in r and isinstance(r["tables"], list):
        return f"{len(r['tables'])} tables"
    if "rows" in r and "columns" in r:
        return f"{len(r['rows'])} rows × {len(r['columns'])} cols"
    if "columns" in r and isinstance(r["columns"], list):
        return f"{len(r['columns'])} columns"
    if "migrations" in r and isinstance(r["migrations"], list):
        return f"{len(r['migrations'])} migration files"
    return None


def _summarize_project(r: dict) -> Optional[str]:
    if "todos" in r and isinstance(r["todos"], list):
        return f"{len(r['todos'])} todos"
    if "notes" in r and isinstance(r["notes"], list):
        return f"{len(r['notes'])} notes"
    if "tasks" in r and isinstance(r["tasks"], list):
        return f"{len(r['tasks'])} tasks"
    if "changes" in r and isinstance(r["changes"], list):
        return f"{len(r['changes'])} recent changes"
    if "tree" in r:
        return "project tree"
    if "key" in r:
        return f"note saved: {r['key']}"
    return None


def _summarize_review(r: dict) -> Optional[str]:
    s = r.get("summary")
    if isinstance(s, dict):
        total = s.get("total", 0)
        errors = s.get("errors", 0)
        warnings = s.get("warnings", 0)
        return f"{total} findings · {errors}E {warnings}W"
    if "findings" in r and isinstance(r["findings"], list):
        return f"{len(r['findings'])} findings"
    return None


def _summarize_browser(r: dict) -> Optional[str]:
    if "url" in r and isinstance(r["url"], str):
        return _one_line(r["url"], 100)
    if "title" in r and isinstance(r["title"], str):
        return f"title: {_one_line(r['title'], 100)}"
    if "text" in r and isinstance(r["text"], str):
        return f"{len(r['text'])} chars of text"
    if "snapshot" in r and isinstance(r["snapshot"], str):
        return f"{_count_lines(r['snapshot'])} interactive elements"
    if "elements" in r and isinstance(r["elements"], list):
        return f"{len(r['elements'])} elements"
    if "links" in r and isinstance(r["links"], list):
        return f"{len(r['links'])} links"
    if "base64" in r:
        return f"screenshot · {r.get('bytes', 0)} bytes"
    if "path" in r and "bytes" in r:
        return f"file · {r['path']} · {r['bytes']} bytes"
    return None


def _summarize_debug(r: dict) -> Optional[str]:
    if "exception" in r and isinstance(r["exception"], dict):
        exc = r["exception"]
        return f"{exc.get('type', '?')}: {_one_line(exc.get('message', ''), 100)}"
    if "source" in r and isinstance(r["source"], dict):
        s = r["source"]
        return f"{s.get('file', '?')}:{s.get('line', '?')}"
    if "frames" in r and isinstance(r["frames"], list):
        return f"{len(r['frames'])} frames"
    if "parsed" in r:
        p = r["parsed"]
        exc = p.get("exception") or {}
        if exc:
            return f"{exc.get('type', '?')}: {_one_line(exc.get('message', ''), 100)}"
    if "exit_code" in r:
        return _summarize_terminal(r) or "ran"
    return None


def _summarize_web_search(r: dict) -> Optional[str]:
    if "results" in r and isinstance(r["results"], list):
        n = len(r["results"])
        q = r.get("query", "")
        return f"{n} results for '{_one_line(q, 60)}'"
    return None


def _summarize_packages(r: dict) -> Optional[str]:
    if "managers" in r and isinstance(r["managers"], list):
        return ", ".join(r["managers"]) or "none detected"
    if "build_system" in r and r.get("build_system"):
        return f"system {r['build_system']}"
    if "raw" in r and isinstance(r["raw"], str):
        return f"{_count_lines(r['raw'])} lines"
    return None


def _summarize_sandbox(r: dict) -> Optional[str]:
    if "id" in r and "backend" in r:
        return f"{r['backend']} sandbox {r['id']}"
    if "sandboxes" in r and isinstance(r["sandboxes"], list):
        return f"{len(r['sandboxes'])} sandboxes"
    if "exit_code" in r:
        return _summarize_terminal(r)
    return None


def _summarize_deploy(r: dict) -> Optional[str]:
    if "providers" in r and isinstance(r["providers"], list):
        return ", ".join(r["providers"]) or "none detected"
    if "status" in r and isinstance(r["status"], dict):
        return str(r["status"].get("status", "unknown"))
    if "exit_code" in r:
        return _summarize_terminal(r)
    return None


_TOOL_SUMMARIZERS = {
    "terminal": _summarize_terminal,
    "workspace": _summarize_workspace,
    "editor": _summarize_editor,
    "git": _summarize_git,
    "test": _summarize_test,
    "build": _summarize_build,
    "http": _summarize_http,
    "database": _summarize_database,
    "project": _summarize_project,
    "review": _summarize_review,
    "browser": _summarize_browser,
    "web_browser": _summarize_browser,
    "debug": _summarize_debug,
    "web_search": _summarize_web_search,
    "packages": _summarize_packages,
    "sandbox": _summarize_sandbox,
    "deploy": _summarize_deploy,
}


def summarize_result(name: str, result: Any) -> str:
    if isinstance(result, dict):
        if "error" in result:
            return _summarize_error(result)
        handler = _TOOL_SUMMARIZERS.get(name)
        if handler is not None:
            try:
                summary = handler(result)
            except Exception:
                summary = None
            if summary:
                return summary
        return _summarize_dict(result)

    if isinstance(result, list):
        return f"{len(result)} item{'s' if len(result) != 1 else ''}"

    s = str(result).replace("\n", " ").strip()
    if not s:
        return "(empty)"
    return s[:160] + ("…" if len(s) > 160 else "")


def is_error_result(result: Any) -> bool:
    return isinstance(result, dict) and "error" in result


class TerminalUI:
    def __init__(self) -> None:
        self.console = Console(highlight=False, soft_wrap=False)
        self._content_buffer: str = ""
        self._content_started_at: float = 0.0
        self._header_printed: bool = False
        self._header_key: str = "—"
        self._agent_name: str = DEFAULT_AGENT_NAME
        self._session: Optional[PromptSession] = None

    # ------------------------------------------------------------------ #
    # prompt_toolkit wiring
    # ------------------------------------------------------------------ #

    def _get_session(self) -> PromptSession:
        if self._session is not None:
            return self._session

        bindings = KeyBindings()

        @bindings.add("enter")
        def _submit(event):
            buf = event.current_buffer
            if buf.text.strip():
                buf.validate_and_handle()
            else:
                buf.insert_text("\n")

        @bindings.add("escape", "enter")
        def _newline_alt(event):
            event.current_buffer.insert_text("\n")

        @bindings.add("c-j")
        def _newline_ctrl(event):
            event.current_buffer.insert_text("\n")

        style = Style.from_dict({
            "prompt": "ansicyan bold",
        })

        self._session = PromptSession(
            multiline=True,
            key_bindings=bindings,
            style=style,
            mouse_support=False,
            enable_history_search=False,
            complete_while_typing=False,
        )
        return self._session

    def _prompt_message(self) -> HTML:
        return HTML("<prompt>❯ </prompt>")

    def _prompt_message_continuation(
        self, width: int, line_number: int, wrap_count: int
    ) -> HTML:
        return HTML("<prompt>  </prompt>")

    async def _read_multiline(self) -> str:
        session = self._get_session()
        try:
            text = await session.prompt_async(
                message=self._prompt_message,
                prompt_continuation=self._prompt_message_continuation,
            )
        except (EOFError, KeyboardInterrupt):
            raise KeyboardInterrupt
        return text.strip()

    async def _read_single_line(self, label: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        session = self._get_session()
        try:
            text = await session.prompt_async(
                message=HTML(f"<prompt>{label}{suffix}: </prompt>"),
                multiline=False,
            )
        except (EOFError, KeyboardInterrupt):
            return ""
        text = text.strip()
        return text or default

    # ------------------------------------------------------------------ #
    # Public input API
    # ------------------------------------------------------------------ #

    async def ask(self) -> str:
        self.console.print()
        self.console.print(Rule(style="grey35"))
        text = await self._read_multiline()
        return text

    async def prompt(self, label: str, default: str = "") -> str:
        return await self._read_single_line(label, default)

    async def confirm(self, label: str) -> bool:
        raw = await self._read_single_line(f"{label} [y/N]")
        return raw.strip().lower() in ("y", "yes")

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #

    def banner(
        self,
        config: Config,
        tools_count: int,
        web_ok: bool,
        code_ok: bool,
        client: Optional[AgnesClient] = None,
    ) -> None:
        title = Text()
        title.append("◆ ", style="bold cyan")
        title.append(config.agent_name or DEFAULT_AGENT_NAME, style="bold white")
        title.append("  ·  ", style="dim")
        title.append(config.model, style="cyan")

        tier = ACCESS_TIERS.get(config.tier, ACCESS_TIERS[DEFAULT_TIER])

        info = Table.grid(padding=(0, 2))
        info.add_column(style="dim", justify="right")
        info.add_column(style="white")

        if config.project is not None:
            p = config.project
            info.add_row("Project", f"[bold]{p.name}[/]  [dim]({p.slug})[/]")
            if p.description:
                desc = p.description.replace("\n", " ")[:120]
                info.add_row("About", desc)
            info.add_row("Kind", f"{p.kind}  ·  {p.language}  ·  {p.template}")
            info.add_row("Workspace", str(p.workspace))
            info.add_row("Created", humanize_time(p.created_at))
        else:
            info.add_row("Mode", "[bold]Chat[/]  [dim](ephemeral)[/]")
            info.add_row("Workspace", str(config.project_root))

        info.add_row("Tools", f"{tools_count} available")
        info.add_row("Web", "[green]✓[/]" if web_ok else "[dim]—[/]")
        info.add_row("Code", "[green]✓[/]" if code_ok else "[dim]—[/]")
        info.add_row("Thinking", "[green]on[/]" if config.thinking else "[dim]off[/]")
        info.add_row("Streaming", "[green]on[/]" if config.stream else "[dim]off[/]")

        if client is not None:
            totals = client.pool_totals()
            info.add_row(
                "Keys",
                f"{totals['keys']} × {totals['rpm_per_key']} RPM "
                f"[dim](= {totals['total_rpm']} RPM total)[/]",
            )
        info.add_row("Tier", tier["label"])

        self.console.print()
        self.console.print(
            Panel(
                info,
                title=title,
                border_style="cyan",
                box=ROUNDED,
                padding=(1, 2),
            )
        )
        self.console.print(
            "[dim]Type [bold]/help[/] for commands, [bold]/exit[/] to quit.[/]"
        )
        self.console.print(
            "[dim]Multi-line: [bold]Enter[/] submits · "
            "[bold]Alt+Enter[/] or [bold]Ctrl+J[/] adds a newline.[/]"
        )
        self.console.print()

    def begin_round(self, key_label: str = "—") -> None:
        self._header_printed = False
        self._header_key = key_label

    def _ensure_header(self) -> None:
        if self._header_printed:
            return
        head = Text()
        head.append("◆ " + (self._agent_name or DEFAULT_AGENT_NAME), style="bold cyan")
        if self._header_key and self._header_key != "—":
            head.append("  ·  ", style="dim")
            head.append(f"key {self._header_key}", style="dim")
        self.console.print()
        self.console.print(head)
        self._header_printed = True

    def content_start(self) -> None:
        self._ensure_header()
        self._content_buffer = ""
        self._content_started_at = time.time()

    def content_delta(self, text: str) -> None:
        self._content_buffer += text

    def content_end(self) -> None:
        try:
            buf = sanitize_text(self._content_buffer)
            if not buf.strip():
                return
            elapsed = time.time() - self._content_started_at
            self.console.print(Markdown(buf))
            if elapsed > 0:
                approx_tokens = max(1, len(buf) // 4)
                rate = approx_tokens / elapsed
                self.console.print(
                    f"  [dim]· {approx_tokens} tok · {rate:.0f} tok/s[/]"
                )
        finally:
            self._content_buffer = ""

    def reasoning_start(self) -> None:
        self._ensure_header()
        self.console.print(Text("  · thinking…", style="dim italic"))

    def reasoning_delta(self, text: str) -> None:
        self.console.print(
            sanitize_text(text),
            end="",
            style="dim italic",
            markup=False,
            highlight=False,
            soft_wrap=True,
        )

    def reasoning_end(self) -> None:
        self.console.print()

    def tool_call(self, name: str, arguments: dict) -> None:
        self.console.print()
        head = Text()
        head.append("  ⚙ ", style="cyan")
        head.append(name, style="bold cyan")
        if arguments:
            head.append("  ", style="")
            parts: list[str] = []
            for k, v in list(arguments.items())[:5]:
                if isinstance(v, str):
                    vs = v
                elif isinstance(v, bool):
                    vs = "true" if v else "false"
                else:
                    try:
                        vs = json.dumps(v, ensure_ascii=False, default=str)
                    except Exception:
                        vs = str(v)
                if len(vs) > 140:
                    vs = vs[:139] + "…"
                parts.append(f"{k}={vs}")
            head.append("  ".join(parts), style="dim")
        self.console.print(head)

    def tool_result(
        self,
        name: str,
        result: Any,
        elapsed: float,
        verbose: bool = False,
        key_label: str = "—",
    ) -> None:
        err = is_error_result(result)
        summary = summarize_result(name, result)

        line = Text()
        line.append("  ↳ ", style="red" if err else "green")
        line.append(summary, style="red" if err else "white")
        line.append(f"  ({elapsed:.2f}s", style="dim")
        if key_label and key_label != "—":
            line.append(f" · {key_label}", style="dim")
        line.append(")", style="dim")
        self.console.print(line)

        if verbose:
            self._print_preview(result)

    def _print_preview(self, result: Any) -> None:
        if isinstance(result, dict) and (
            "stdout" in result or "stderr" in result
        ):
            parts: list[str] = []
            out = result.get("stdout") or ""
            err = result.get("stderr") or ""
            if out:
                body = sanitize_text(out)
                if len(body) > 4000:
                    body = body[:4000] + "\n…"
                parts.append("[bold]stdout[/]\n" + body.rstrip())
            if err:
                body = sanitize_text(err)
                if len(body) > 4000:
                    body = body[:4000] + "\n…"
                parts.append("[bold red]stderr[/]\n" + body.rstrip())
            if parts:
                self.console.print(
                    Panel(
                        "\n\n".join(parts),
                        border_style="grey35",
                        box=ROUNDED,
                        padding=(0, 1),
                    )
                )
                return

        try:
            text = json.dumps(
                sanitize_obj(result), ensure_ascii=False, default=str, indent=2
            )
        except Exception:
            text = sanitize_text(str(result))
        if len(text) > 2400:
            text = text[:2400] + "\n…"
        self.console.print(
            Panel(
                text,
                border_style="grey35",
                box=ROUNDED,
                padding=(0, 1),
            )
        )

    def info(self, msg: str) -> None:
        self.console.print(f"[dim]{msg}[/]")

    def warning(self, msg: str) -> None:
        self.console.print(f"[yellow]⚠[/] {msg}")

    def error(self, msg: str) -> None:
        self.console.print(f"[red]✗[/] {msg}")

    def success(self, msg: str) -> None:
        self.console.print(f"[green]✓[/] {msg}")

    def retry_notice(self, attempt: int, wait: float, reason: str) -> None:
        self.console.print(
            f"  [yellow]↻[/] retry {attempt} in {wait:.1f}s [dim]({reason})[/]"
        )

    def planner_start(self) -> None:
        self.console.print()
        self.console.print(
            Text("[Planner] ", style="bold magenta")
            + Text("analyzing task…", style="dim")
        )

    def planner_plan(self, plan: "TodoList") -> None:
        self.console.print(
            Text("[Planner] ", style="bold magenta")
            + Text(
                f"plan created: {len(plan.todos)} todos, "
                f"{sum(len(t.subtasks) for t in plan.todos)} subtasks",
                style="white",
            )
        )
        if plan.summary:
            self.console.print()
            self.console.print(
                Panel(
                    plan.summary,
                    title="[bold magenta]Plan summary[/]",
                    border_style="magenta",
                    box=ROUNDED,
                    padding=(0, 1),
                )
            )

    def planner_table(self, plan: "TodoList") -> None:
        table = Table(
            show_header=True,
            header_style="bold",
            box=ROUNDED,
            border_style="grey35",
            padding=(0, 1),
        )
        table.add_column("#", style="dim", no_wrap=True)
        table.add_column("Todo", style="cyan", no_wrap=False, max_width=64)
        table.add_column("Sub", justify="right", no_wrap=True)
        table.add_column("Depends", style="dim", no_wrap=True)

        for i, t in enumerate(plan.todos, 1):
            deps = ", ".join(t.depends_on) if t.depends_on else "—"
            table.add_row(
                f"{i:>3}",
                t.title[:64],
                str(len(t.subtasks)),
                deps,
            )
        self.console.print(
            Panel(
                table,
                title="[bold]Todo list[/]",
                border_style="grey35",
                box=ROUNDED,
                padding=(1, 1),
            )
        )

    def swarm_start(self, workers: int, total_rpm: int) -> None:
        self.console.print()
        self.console.print(
            Text("[Swarm] ", style="bold green")
            + Text(
                f"launching {workers} workers · budget {total_rpm} RPM",
                style="white",
            )
        )
        self.console.print()

    def swarm_done(self, plan: "TodoList") -> None:
        done = sum(1 for t in plan.todos if t.status == "done")
        failed = sum(1 for t in plan.todos if t.status == "failed")
        total = len(plan.todos)
        self.console.print()
        self.console.print(Rule(style="grey35"))
        self.console.print(
            Text("[Swarm] ", style="bold green")
            + Text(
                f"finished · {done}/{total} done"
                + (f" · {failed} failed" if failed else ""),
                style="white",
            )
        )
        self.console.print(Rule(style="grey35"))
        self.console.print()


class WorkerUI:
    def __init__(self, parent: TerminalUI, label: str, color: str):
        self.parent = parent
        self.label = label
        self.color = color

    def _line(self, text: Text) -> None:
        self.parent.console.print(text)

    def begin_round(self, key_label: str = "—") -> None:
        pass

    def assistant_start(self) -> None:
        pass

    def content_start(self) -> None:
        pass

    def content_delta(self, text: str) -> None:
        pass

    def content_end(self) -> None:
        pass

    def reasoning_start(self) -> None:
        pass

    def reasoning_delta(self, text: str) -> None:
        pass

    def reasoning_end(self) -> None:
        pass

    def tool_call(self, name: str, arguments: dict) -> None:
        parts: list[str] = []
        for k, v in list(arguments.items())[:3]:
            if isinstance(v, str):
                vs = v
            elif isinstance(v, bool):
                vs = "true" if v else "false"
            else:
                try:
                    vs = json.dumps(v, ensure_ascii=False, default=str)
                except Exception:
                    vs = str(v)
            if len(vs) > 90:
                vs = vs[:89] + "…"
            parts.append(f"{k}={vs}")
        args = "  ".join(parts)

        line = Text()
        line.append(f"  [{self.label}] ", style=f"bold {self.color}")
        line.append("⚙ ", style=self.color)
        line.append(name, style="white")
        if args:
            line.append(f"  {args}", style="dim")
        self._line(line)

    def tool_result(
        self,
        name: str,
        result: Any,
        elapsed: float,
        verbose: bool = False,
        key_label: str = "—",
    ) -> None:
        err = is_error_result(result)
        summary = summarize_result(name, result)

        line = Text()
        line.append(f"  [{self.label}] ", style=f"bold {self.color}")
        line.append("↳ ", style="red" if err else "green")
        line.append(summary, style="red" if err else "dim")
        line.append(f"  ({elapsed:.2f}s)", style="dim")
        if key_label and key_label != "—":
            line.append(f" · {key_label}", style="dim")
        self._line(line)

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        line = Text()
        line.append(f"  [{self.label}] ", style=f"bold {self.color}")
        line.append("⚠ ", style="yellow")
        line.append(msg, style="yellow")
        self._line(line)

    def error(self, msg: str) -> None:
        line = Text()
        line.append(f"  [{self.label}] ", style=f"bold {self.color}")
        line.append("✗ ", style="red")
        line.append(msg, style="red")
        self._line(line)

    def success(self, msg: str) -> None:
        line = Text()
        line.append(f"  [{self.label}] ", style=f"bold {self.color}")
        line.append("✓ ", style="green")
        line.append(msg, style="white")
        self._line(line)

    def retry_notice(self, attempt: int, wait: float, reason: str) -> None:
        line = Text()
        line.append(f"  [{self.label}] ", style=f"bold {self.color}")
        line.append(f"↻ retry {attempt} in {wait:.1f}s ", style="yellow")
        line.append(f"({reason})", style="dim")
        self._line(line)


def _project_context(config: Config) -> str:
    if config.project is not None:
        p = config.project
        notes = p.read_notes()
        notes_block = ""
        if notes.strip():
            trimmed = notes.strip()
            if len(trimmed) > 3000:
                trimmed = trimmed[:3000] + "\n…"
            notes_block = f"\n# Project notes\n{trimmed}\n"
        return (
            f"# Project\n"
            f"- Name: {p.name}\n"
            f"- Slug: {p.slug}\n"
            f"- Kind: {p.kind}\n"
            f"- Language: {p.language}\n"
            f"- Template: {p.template}\n"
            f"- Description: {p.description or '(none)'}\n"
            f"- Created: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(p.created_at))}\n"
            f"- Workspace: {p.workspace}\n"
            f"{notes_block}"
        )
    return (
        "# Mode\n"
        "- Chat mode: ephemeral conversation, no persistent project.\n"
    )


def build_system_prompt(config: Config, tool_count: int) -> str:
    custom_instructions = (config.custom_instructions or "").strip()
    custom_block = f"\n# Custom instructions\n{custom_instructions}\n" if custom_instructions else ""
    return f"""You are {config.agent_name}, a terminal-based autonomous agent that can search the web, browse pages, and operate on a software project.

# Environment
- Agent: {config.agent_name}
- Tagline: {config.agent_tagline}
- Model: {config.model}
- Platform: {platform.system()} {platform.release()} ({platform.machine()})
- Python: {platform.python_version()}
- Working directory: {os.getcwd()}
- Available tools: {tool_count}

{_project_context(config)}

# Operating principles
1. Use tools when you need real-world information, or must inspect or modify the project. Do not invent file contents, URLs, or tool output.
2. Read before you write. Inspect a file before editing it. Prefer structured edits (`editor.apply` with `preview`) over whole-file rewrites.
3. When a task has multiple steps, plan briefly, then act. Do not over-plan.
4. Return tool results to the conversation before requesting the next action.
5. Never claim success without evidence. Cite the file path, command output, or URL that supports the claim.
6. If a tool call fails, inspect the error, adjust, and try a different approach. Do not repeat the same failing call.
7. Keep the objective, constraints, and runtime context stable across long tasks.
8. Ask before destructive operations (deleting files, force-pushing, deploying).
{custom_block}
# Long-running commands
- If a command starts a server, watcher, or any process that does not exit on its own (e.g. `npm run dev`, `go run ./cmd/server`, `uvicorn`, `flask run`, `python -m http.server`, `docker compose up`, `node server.js`, `cargo run`), use `terminal` with `action=background` — NOT `action=run`.
- After starting a background process, wait 1–2 seconds, then check its stdout with `terminal` `action=get` on the returned `process_id`, and confirm the port is listening with `terminal` `action=run` and a short `netstat` / `ss` / `Get-NetTCPConnection` probe (or use the `http` tool to hit the service).
- Never send a foreground `terminal.run` for a command you expect to keep running. It will block until the timeout and leave an orphan process on the port.
- When you are done with a background process, kill it with `terminal` `action=kill` and the `process_id`.
- On Windows, avoid POSIX-only helpers like `head`, `tail`, `grep`, `sed`, `awk`, `wc`, `&&` chains that mix shells. Use PowerShell equivalents (`Select-Object -First`, `Select-String`) or plain commands that work in `cmd.exe`. If a pipeline fails with "'X' is not recognized", rewrite without it.

# Tool argument rules
- Every tool call argument must be a single valid JSON object with double-quoted keys and values.
- Windows paths must use escaped backslashes: `"C:\\\\Users\\\\me"`. Do not emit raw single backslashes inside JSON strings.
- Do not use Python dict syntax. Do not use single quotes. Do not add trailing commas.

# Output style
- Be concise; this is a terminal.
- Use short paragraphs and flat bullet lists.
- Show file paths and commands in backticks.
- Do not echo raw tool JSON in your reply. The UI already renders it.
"""


def build_planner_prompt(config: Config, tool_count: int) -> str:
    return f"""You are the Planner for a swarm of autonomous coding agents. Your job is to decompose the user's task into an exhaustive, ordered todo list that will be executed by parallel worker agents.

# Environment
- Model: {config.model}
- Platform: {platform.system()} {platform.release()} ({platform.machine()})
- Python: {platform.python_version()}
- Working directory: {os.getcwd()}
- Available tools: {tool_count}

{_project_context(config)}

# Decomposition rules
1. Produce a MINIMUM of {MIN_TODOS} top-level todos. Target {TARGET_TODOS} todos. More is better. The longer the plan, the more thorough the swarm will be. Never fewer than {MIN_TODOS}.
2. Each todo MUST have at least {MIN_SUBTASKS} subtasks, targeting {TARGET_SUBTASKS} subtasks per todo. Subtasks must be substantive units of work with detailed descriptions, not single trivial steps. Expand aggressively: if you think a todo needs 4 subtasks, write 10.
3. Each todo must be a concrete, verifiable unit of work, not a vague category like "improve code quality". Prefer narrow, well-scoped todos over broad ones — a long list of narrow todos is always better than a short list of broad ones.
4. Order the todos so that dependencies come first, and populate `depends_on` with the ids of prerequisite todos.
5. No two todos should modify the same file if it can be avoided. If they must, use `depends_on` to serialize them.
6. Each todo must be independently executable by a worker agent that has access to the shared project and tools, given only the todo text.
7. Populate `worker_prompt` with a detailed, self-contained mission brief for the worker that will execute this todo. Include context, exact file paths when knowable, constraints, required steps, and any project-specific conventions to follow. Make it specific, thorough, and actionable.
8. Populate `acceptance` with the exact evidence that will prove the todo is done (a test passing, a file existing, a command output matching).
9. The `summary` field must be one paragraph explaining the plan and the ordering rationale.
10. Preserve breadth: cover setup, implementation, tests, docs, cleanup, verification, and any project-specific concerns as separate todos.

# Output
Respond with ONLY valid JSON. No prose before or after. Use this schema:

{{
  "summary": "one paragraph describing the plan and its ordering",
  "todos": [
    {{
      "id": "T01",
      "title": "Short imperative title",
      "description": "Detailed description of what must be done.",
      "acceptance": "Exact evidence that proves completion.",
      "worker_prompt": "You are worker assigned to T01. Your mission: ...",
      "depends_on": [],
      "subtasks": [
        {{"title": "Short title", "description": "Detailed description."}}
      ]
    }}
  ]
}}
"""


def build_worker_prompt(config: Config, todo: "Todo", original_task: str) -> str:
    subtasks_text = "\n".join(
        f"{i}. {s.title}\n   {s.description}"
        for i, s in enumerate(todo.subtasks, 1)
    )
    deps_text = ", ".join(todo.depends_on) if todo.depends_on else "none"

    return f"""You are a worker agent in an autonomous swarm. You have been assigned exactly one todo from a larger plan. Complete it, verify it, and report.

# Environment
- Model: {config.model}
- Platform: {platform.system()} {platform.release()} ({platform.machine()})
- Python: {platform.python_version()}
- Working directory: {os.getcwd()}

{_project_context(config)}

# Your todo
ID: {todo.id}
Title: {todo.title}
Description: {todo.description}
Acceptance: {todo.acceptance}
Depends on: {deps_text}

# Mission brief
{todo.worker_prompt or "Complete the todo above, using tools to inspect and modify the project as needed."}

# Subtasks
{subtasks_text}

# Original task context
{original_task}

# Instructions
1. Focus only on your todo. Do not attempt work outside it.
2. Read before you write. Inspect files before editing them.
3. Verify your work with tools (run the test, read the file back, check the command output).
4. When complete, respond with a final message beginning with "DONE:" followed by a concise summary of what you did and the evidence (file paths, command output, test results).
5. If you cannot complete the todo, respond with "BLOCKED:" followed by the specific reason and what would unblock you.
6. Be concise. The terminal is shared with other workers.
7. Do not echo raw tool JSON in your reply. The UI already renders it.

# Long-running commands
- If a command starts a server, watcher, or any process that does not exit on its own, use `terminal` `action=background`, not `action=run`.
- Kill background processes you started when you are done with them.
- On Windows, avoid POSIX-only helpers like `head`, `tail`, `grep`, `sed`. Use PowerShell equivalents or plain commands.

# Tool argument rules
- Every tool call argument must be a single valid JSON object with double-quoted keys and values.
- Windows paths must use escaped backslashes: `"C:\\\\Users\\\\me"`.
- Do not use Python dict syntax. Do not use single quotes. Do not add trailing commas.
"""


def build_summarizer_prompt() -> str:
    return """You are the Summarizer for an autonomous agent swarm. You will receive the original task, the plan that was created, and the results of each todo. Produce a final report for the user.

The report must include:
1. A one-paragraph overview of what was accomplished.
2. A bullet list of completed todos with evidence drawn from their results.
3. A bullet list of any failed or blocked todos with the reason.
4. Any follow-up work the user should consider.

Be concise, factual, and cite specific files or command output. Do not invent results."""


def extract_json(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    if start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
    raise ValueError("No valid JSON object found in the response")


class Agent:
    def __init__(
        self,
        config: Config,
        client: AgnesClient,
        tools: ToolRegistry,
        ui: Any,
        system_prompt: str,
        max_rounds: Optional[int] = None,
        max_tokens: Optional[int] = None,
    ):
        self.config = config
        self.client = client
        self.tools = tools
        self.ui = ui
        self.system_prompt = system_prompt
        self.messages: list[dict] = []
        self.max_rounds = max_rounds or config.max_rounds
        self.max_tokens = max_tokens or config.max_tokens
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cached_tokens = 0
        self.rounds = 0
        self._last_user_message: Optional[str] = None

    def reset(self) -> None:
        self.messages.clear()
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cached_tokens = 0
        self.rounds = 0
        self._last_user_message = None

    def cost_estimate(self) -> float:
        uncached = max(0, self.prompt_tokens - self.cached_tokens)
        return (
            uncached / 1_000_000 * PRICE_INPUT
            + self.cached_tokens / 1_000_000 * PRICE_CACHED_INPUT
            + self.completion_tokens / 1_000_000 * PRICE_OUTPUT
        )

    async def send(self, user_text: str) -> None:
        self._last_user_message = user_text
        self.messages.append({
            "role": "user",
            "content": sanitize_text(user_text),
        })
        self._truncate_history()

        for _ in range(self.max_rounds):
            try:
                state = await self._stream_round()
            except asyncio.CancelledError:
                self.ui.warning("Turn cancelled.")
                raise

            self.rounds += 1

            parsed_calls: list[dict] = []
            for i, tc in enumerate(state["tool_calls"]):
                args, err = _parse_tool_arguments(
                    tc["name"], tc["arguments"] or "{}"
                )
                parsed_calls.append({
                    "raw": tc,
                    "id": tc["id"] or f"call_{i}",
                    "name": tc["name"],
                    "args": args,
                    "error": err,
                })

            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": state["content"] or None,
            }
            if parsed_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": pc["id"],
                        "type": "function",
                        "function": {
                            "name": pc["name"],
                            "arguments": json.dumps(
                                pc["args"], ensure_ascii=False
                            ),
                        },
                    }
                    for pc in parsed_calls
                ]
            self.messages.append(assistant_msg)

            if not parsed_calls:
                return

            for pc in parsed_calls:
                result = await self._execute_parsed_tool(pc)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": pc["id"],
                    "content": self._serialize_tool_result(result),
                })

        self.ui.warning(
            f"Reached maximum tool rounds ({self.max_rounds}). Turn stopped."
        )

    async def _stream_round(self) -> dict[str, Any]:
        full_messages = [
            {"role": "system", "content": self.system_prompt}
        ] + _repair_tool_call_history(self.messages)

        state: dict[str, Any] = {
            "content": "",
            "reasoning": "",
            "tool_calls": [],
            "finish_reason": None,
        }
        tc_acc: dict[int, dict[str, str]] = {}

        if hasattr(self.ui, "begin_round"):
            self.ui.begin_round(getattr(self.client, "last_key_label", "—"))

        in_content = False
        in_reasoning = False

        async for chunk in self.client.stream_chat(
            full_messages,
            tools=self.tools.schemas() or None,
            thinking=self.config.thinking,
            temperature=self.config.temperature,
            max_tokens=self.max_tokens,
            stream=self.config.stream,
        ):
            usage = chunk.get("usage")
            if usage:
                self.prompt_tokens += usage.get("prompt_tokens", 0) or 0
                self.completion_tokens += usage.get("completion_tokens", 0) or 0
                details = usage.get("prompt_tokens_details") or {}
                self.cached_tokens += details.get("cached_tokens", 0) or 0

            choices = chunk.get("choices") or []
            if not choices:
                continue
            choice = choices[0]

            delta = choice.get("delta")
            if delta is None:
                msg = choice.get("message") or {}
                delta = {
                    "content": msg.get("content"),
                    "reasoning_content": msg.get("reasoning_content")
                    or msg.get("reasoning"),
                    "tool_calls": msg.get("tool_calls"),
                }

            fr = choice.get("finish_reason")
            if fr:
                state["finish_reason"] = fr

            rc = delta.get("reasoning_content") or delta.get("reasoning")
            if rc:
                if not in_reasoning:
                    if in_content:
                        self.ui.content_end()
                        in_content = False
                    self.ui.reasoning_start()
                    in_reasoning = True
                self.ui.reasoning_delta(rc)
                state["reasoning"] += rc

            content = delta.get("content")
            if content:
                if not in_content:
                    if in_reasoning:
                        self.ui.reasoning_end()
                        in_reasoning = False
                    self.ui.content_start()
                    in_content = True
                self.ui.content_delta(content)
                state["content"] += content

            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = tc_acc.setdefault(
                    idx, {"id": "", "name": "", "arguments": ""}
                )
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]

        if in_content:
            self.ui.content_end()
        if in_reasoning:
            self.ui.reasoning_end()

        state["content"] = sanitize_text(state["content"])
        state["reasoning"] = sanitize_text(state["reasoning"])
        state["tool_calls"] = [tc_acc[i] for i in sorted(tc_acc.keys())]
        return state

    async def _execute_parsed_tool(self, parsed: dict) -> Any:
        name = parsed["name"]

        if parsed["error"]:
            raw = (parsed["raw"].get("arguments") or "")[:2000]
            return {
                "error": "invalid_tool_arguments",
                "message": parsed["error"],
                "raw_arguments": raw,
            }

        arguments = parsed["args"]
        self.ui.tool_call(name, arguments)
        started = time.time()
        try:
            result = await self.tools.dispatch(name, arguments)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            detail = str(e).strip() or repr(e) or "(no detail)"
            result = {"error": type(e).__name__, "message": detail}
        elapsed = time.time() - started
        key_label = getattr(self.client, "last_key_label", "—")
        self.ui.tool_result(
            name,
            result,
            elapsed,
            verbose=self.config.verbose,
            key_label=key_label,
        )
        return result

    @staticmethod
    def _serialize_tool_result(result: Any) -> str:
        try:
            text = json.dumps(
                sanitize_obj(result), ensure_ascii=False, default=str, indent=2
            )
        except Exception:
            text = sanitize_text(str(result))
        if len(text) > MAX_TOOL_RESULT_CHARS:
            text = text[:MAX_TOOL_RESULT_CHARS] + "\n… [truncated]"
        return text

    def _truncate_history(self) -> None:
        if len(self.messages) < 24:
            return
        total = 0
        for m in self.messages:
            total += len(json.dumps(m, default=str, ensure_ascii=False))
        if total < MAX_HISTORY_CHARS:
            return
        keep_head = self.messages[:2]
        keep_tail = self.messages[-16:]
        self.messages = _repair_tool_call_history(keep_head + keep_tail)
        self.ui.info("Conversation history truncated to stay within budget.")


class Session(Agent):
    def __init__(
        self,
        config: Config,
        client: AgnesClient,
        tools: ToolRegistry,
        ui: TerminalUI,
    ):
        super().__init__(
            config=config,
            client=client,
            tools=tools,
            ui=ui,
            system_prompt=build_system_prompt(config, len(tools.schemas())),
            max_rounds=config.max_rounds,
            max_tokens=config.max_tokens,
        )


@dataclass
class Subtask:
    title: str
    description: str = ""
    status: str = "pending"
    result: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Todo:
    id: str
    title: str
    description: str = ""
    acceptance: str = ""
    worker_prompt: str = ""
    depends_on: list[str] = field(default_factory=list)
    subtasks: list[Subtask] = field(default_factory=list)
    status: str = "pending"
    assigned_to: Optional[str] = None
    result: str = ""
    error: str = ""
    rounds: int = 0
    elapsed: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TodoList:
    summary: str
    todos: list[Todo]
    original_task: str = ""
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    report: str = ""

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "original_task": self.original_task,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "report": self.report,
            "todos": [t.to_dict() for t in self.todos],
        }

    def counts(self) -> dict:
        out = {"pending": 0, "in_progress": 0, "done": 0, "failed": 0, "skipped": 0}
        for t in self.todos:
            out[t.status] = out.get(t.status, 0) + 1
        return out


class Planner(Agent):
    def __init__(
        self,
        config: Config,
        client: AgnesClient,
        tools: ToolRegistry,
        ui: TerminalUI,
    ):
        super().__init__(
            config=config,
            client=client,
            tools=tools,
            ui=ui,
            system_prompt=build_planner_prompt(config, len(tools.schemas())),
            max_rounds=30,
            max_tokens=DEFAULT_PLANNER_TOKENS,
        )

    async def plan(self, task: str) -> TodoList:
        self.ui.planner_start()
        await self.send(task)

        data = self._extract_plan()
        todos = self._parse_todos(data)

        for attempt in range(2):
            if len(todos) >= MIN_TODOS and self._min_subtasks_ok(todos):
                break
            self.ui.info(
                f"Plan too small (todos={len(todos)}, min subtasks="
                f"{min((len(t.subtasks) for t in todos), default=0)}). "
                f"Requesting expansion ({attempt + 1}/2)…"
            )
            await self.send(
                f"Your previous plan was too small. It had {len(todos)} todos. "
                f"Expand it to at least {TARGET_TODOS} top-level todos, each "
                f"with at least {TARGET_SUBTASKS} substantive subtasks. "
                f"Keep every todo narrow and verifiable. Respond with ONLY "
                f"valid JSON in the same schema."
            )
            try:
                data = self._extract_plan()
                new_todos = self._parse_todos(data)
                if len(new_todos) >= len(todos):
                    todos = new_todos
            except Exception:
                continue

        plan = TodoList(
            summary=data.get("summary", ""),
            todos=todos,
            original_task=task,
        )
        return plan

    @staticmethod
    def _min_subtasks_ok(todos: list[Todo]) -> bool:
        if not todos:
            return False
        return all(len(t.subtasks) >= MIN_SUBTASKS for t in todos)

    def _extract_plan(self) -> dict:
        text = ""
        for msg in reversed(self.messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                text = msg["content"]
                break
        if not text:
            raise ValueError("Planner produced no output")
        return extract_json(text)

    def _parse_todos(self, data: dict) -> list[Todo]:
        raw_todos = data.get("todos") or []
        if not isinstance(raw_todos, list):
            return []

        todos: list[Todo] = []
        seen_ids: set[str] = set()
        for i, raw in enumerate(raw_todos[:MAX_TODOS]):
            if not isinstance(raw, dict):
                continue
            tid = str(raw.get("id") or f"T{i + 1:02d}")
            if tid in seen_ids:
                tid = f"{tid}_{i}"
            seen_ids.add(tid)

            subs_raw = raw.get("subtasks") or []
            subs: list[Subtask] = []
            if isinstance(subs_raw, list):
                for s in subs_raw:
                    if isinstance(s, dict):
                        subs.append(
                            Subtask(
                                title=sanitize_text(
                                    str(s.get("title", "")).strip()[:200]
                                ),
                                description=sanitize_text(
                                    str(s.get("description", "")).strip()
                                ),
                            )
                        )
                    elif isinstance(s, str):
                        subs.append(Subtask(title=sanitize_text(s.strip()[:200])))

            deps_raw = raw.get("depends_on") or []
            if isinstance(deps_raw, str):
                deps_raw = [deps_raw]
            deps = [str(d) for d in deps_raw if isinstance(d, (str, int))]

            todos.append(
                Todo(
                    id=tid,
                    title=sanitize_text(
                        str(raw.get("title", "")).strip()[:200]
                    ) or f"Todo {tid}",
                    description=sanitize_text(
                        str(raw.get("description", "")).strip()
                    ),
                    acceptance=sanitize_text(
                        str(raw.get("acceptance", "")).strip()
                    ),
                    worker_prompt=sanitize_text(
                        str(raw.get("worker_prompt", "")).strip()
                    ),
                    depends_on=deps,
                    subtasks=subs,
                )
            )

        return todos


class Worker(Agent):
    def __init__(
        self,
        config: Config,
        client: AgnesClient,
        tools: ToolRegistry,
        ui: WorkerUI,
        todo: Todo,
        original_task: str,
    ):
        super().__init__(
            config=config,
            client=client,
            tools=tools,
            ui=ui,
            system_prompt=build_worker_prompt(config, todo, original_task),
            max_rounds=DEFAULT_WORKER_ROUNDS,
            max_tokens=config.max_tokens,
        )
        self.todo = todo

    def final_message(self) -> str:
        for msg in reversed(self.messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                return msg["content"]
        return ""


class Swarm:
    def __init__(
        self,
        config: Config,
        client: AgnesClient,
        tools: ToolRegistry,
        ui: TerminalUI,
    ):
        self.config = config
        self.client = client
        self.tools = tools
        self.ui = ui
        self.workers = config.swarm_workers or len(config.api_keys)
        self.workers = max(1, min(self.workers, len(config.api_keys)))

    async def run(self, task: str) -> TodoList:
        planner = Planner(self.config, self.client, self.tools, self.ui)
        try:
            plan = await planner.plan(task)
        except Exception as e:
            self.ui.error(f"Planning failed: {e}")
            raise

        if not plan.todos:
            self.ui.error("Planner produced an empty todo list.")
            raise ValueError("empty plan")

        self.ui.planner_plan(plan)
        self.ui.planner_table(plan)

        self.ui.swarm_start(self.workers, self.client.pool_totals()["total_rpm"])

        await self._execute(plan)
        plan.finished_at = time.time()

        self.ui.swarm_done(plan)
        await self._summarize(plan)
        return plan

    async def _execute(self, plan: TodoList) -> None:
        done_ids: set[str] = set()
        failed_ids: set[str] = set()
        skipped_ids: set[str] = set()
        queue_lock = asyncio.Lock()

        async def next_ready() -> Optional[Todo]:
            async with queue_lock:
                for t in plan.todos:
                    if t.status != "pending":
                        continue
                    failed_deps = [d for d in t.depends_on if d in failed_ids]
                    skipped_deps = [d for d in t.depends_on if d in skipped_ids]
                    if failed_deps or skipped_deps:
                        t.status = "skipped"
                        t.error = (
                            "dependency failed: "
                            + ", ".join(failed_deps + skipped_deps)
                        )
                        skipped_ids.add(t.id)
                        continue
                    if all(d in done_ids for d in t.depends_on):
                        return t
                return None

        async def slot(slot_index: int) -> None:
            while True:
                todo = await next_ready()
                if todo is None:
                    if all(
                        t.status in ("done", "failed", "skipped")
                        for t in plan.todos
                    ):
                        return
                    await asyncio.sleep(0.4)
                    continue

                todo.status = "in_progress"
                label = f"W{slot_index + 1:02d}"
                todo.assigned_to = label
                color = WORKER_COLORS[slot_index % len(WORKER_COLORS)]
                worker_ui = WorkerUI(self.ui, label, color)

                header = Text()
                header.append(f"  [{label}] ", style=f"bold {color}")
                header.append(todo.id, style="bold white")
                header.append(f"  {todo.title}", style="white")
                self.ui.console.print(header)

                worker = Worker(
                    config=self.config,
                    client=self.client,
                    tools=self.tools,
                    ui=worker_ui,
                    todo=todo,
                    original_task=plan.original_task,
                )

                started = time.time()
                try:
                    await worker.send(
                        f"Execute todo {todo.id}. Begin now."
                    )
                    final = worker.final_message()
                    todo.result = sanitize_text(final)
                    todo.rounds = worker.rounds
                    todo.elapsed = time.time() - started

                    if final.strip().upper().startswith("BLOCKED"):
                        todo.status = "failed"
                        todo.error = sanitize_text(final.strip()[:500])
                        failed_ids.add(todo.id)
                        worker_ui.error(f"{todo.id} blocked")
                    else:
                        todo.status = "done"
                        done_ids.add(todo.id)
                        worker_ui.success(
                            f"{todo.id} done "
                            f"({todo.rounds} rounds, {todo.elapsed:.1f}s)"
                        )
                except asyncio.CancelledError:
                    todo.status = "failed"
                    todo.error = "cancelled"
                    failed_ids.add(todo.id)
                    raise
                except Exception as e:
                    detail = str(e).strip() or repr(e) or "(no detail)"
                    todo.status = "failed"
                    todo.error = sanitize_text(
                        f"{type(e).__name__}: {detail}"
                    )
                    failed_ids.add(todo.id)
                    todo.elapsed = time.time() - started
                    worker_ui.error(f"{todo.id} failed: {detail[:200]}")

        tasks = [asyncio.create_task(slot(i)) for i in range(self.workers)]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            raise

    async def _summarize(self, plan: TodoList) -> None:
        results_blob = []
        for t in plan.todos:
            entry = (
                f"## {t.id} — {t.title}\n"
                f"Status: {t.status}\n"
                f"Rounds: {t.rounds}  Elapsed: {t.elapsed:.1f}s\n"
                f"Result:\n{(t.result or t.error or '(no output)')[:2000]}\n"
            )
            results_blob.append(entry)

        user_text = (
            f"Original task:\n{plan.original_task}\n\n"
            f"Plan summary:\n{plan.summary}\n\n"
            f"Todo results:\n\n" + "\n".join(results_blob)
        )

        summarizer = Agent(
            config=self.config,
            client=self.client,
            tools=self.tools,
            ui=self.ui,
            system_prompt=build_summarizer_prompt(),
            max_rounds=3,
            max_tokens=DEFAULT_REVIEWER_TOKENS,
        )

        try:
            await summarizer.send(user_text)
            for msg in reversed(summarizer.messages):
                if msg.get("role") == "assistant" and msg.get("content"):
                    plan.report = msg["content"]
                    break
        except Exception as e:
            self.ui.warning(f"Summary generation failed: {e}")

        self._save_run(plan)

        if plan.report:
            self.ui.console.print()
            self.ui.console.print(
                Panel(
                    Markdown(sanitize_text(plan.report)),
                    title="[bold green]Swarm report[/]",
                    border_style="green",
                    box=ROUNDED,
                    padding=(1, 2),
                )
            )

    def _save_run(self, plan: TodoList) -> None:
        try:
            if self.config.project is not None:
                out_dir = self.config.project.swarm_dir()
            else:
                out_dir = Path.cwd() / ".agnes" / "swarm"
                out_dir.mkdir(parents=True, exist_ok=True)
            fname = (
                time.strftime("%Y%m%d-%H%M%S")
                + "-"
                + uuid.uuid4().hex[:6]
                + ".json"
            )
            path = out_dir / fname
            _write_json(path, sanitize_obj(plan.to_dict()))
            if self.config.project is not None:
                self.config.project._reindex_swarm()
            self.ui.info(f"Swarm run saved to {path}")
        except Exception as e:
            self.ui.warning(f"Could not save swarm run: {e}")


@dataclass
class MenuResult:
    mode: str
    project: Optional[Project] = None


class ProjectMenu:
    def __init__(self, manager: ProjectManager, ui: TerminalUI):
        self.manager = manager
        self.ui = ui

    async def run(self) -> MenuResult:
        while True:
            projects = self.manager.list_projects()
            self._render(projects)
            choice = (await self.ui.prompt("Select")).strip().lower()

            if not choice:
                continue

            if choice in ("q", "quit", "exit"):
                return MenuResult(mode="quit")

            if choice in ("c", "chat"):
                return MenuResult(mode="chat")

            if choice in ("n", "new"):
                project = await self._create()
                if project is not None:
                    return MenuResult(mode="project", project=project)
                continue

            if choice in ("i", "import"):
                project = await self._import()
                if project is not None:
                    return MenuResult(mode="project", project=project)
                continue

            if choice in ("d", "delete"):
                await self._delete(projects)
                continue

            if choice.isdigit():
                idx = int(choice)
                if 1 <= idx <= len(projects):
                    return MenuResult(mode="project", project=projects[idx - 1])
                self.ui.warning(f"No project #{idx}.")
                continue

            self.ui.warning(f"Unknown choice: {choice}")

    def _render(self, projects: list[Project]) -> None:
        self.ui.console.print()
        self.ui.console.print(Rule(style="grey35"))

        table = Table(
            show_header=True,
            header_style="bold",
            box=ROUNDED,
            border_style="grey35",
            padding=(0, 1),
        )
        table.add_column("#", style="dim", justify="right", no_wrap=True)
        table.add_column("", no_wrap=True)
        table.add_column("Project", style="bold cyan", no_wrap=False, max_width=32)
        table.add_column("Kind", no_wrap=True)
        table.add_column("Language", no_wrap=True)
        table.add_column("Template", style="dim", no_wrap=True)
        table.add_column("Updated", style="dim", no_wrap=True)
        table.add_column("Chats", justify="right", no_wrap=True)

        table.add_row(
            "c", "💬", "[green]Chat — ephemeral[/]", "chat", "—", "—", "—", "—"
        )

        if not projects:
            table.add_row(
                "—", "—", "[dim](no projects yet)[/]", "—", "—", "—", "—", "—"
            )
        else:
            for i, p in enumerate(projects, 1):
                conv_count = len(p.list_conversations()) + (
                    1 if p.current_conversation_path().exists() else 0
                )
                table.add_row(
                    str(i),
                    "📁",
                    p.name[:32],
                    p.kind,
                    p.language,
                    p.template,
                    humanize_time(p.updated_at),
                    str(conv_count) if conv_count else "—",
                )

        self.ui.console.print(
            Panel(
                table,
                title="[bold cyan]◆ Agnes — choose a session[/]",
                border_style="cyan",
                box=ROUNDED,
                padding=(1, 1),
            )
        )
        self.ui.console.print(
            "[dim]Enter number to open · "
            "[bold]c[/]hat · "
            "[bold]n[/]ew project · "
            "[bold]i[/]mport existing · "
            "[bold]d[/]elete · "
            "[bold]q[/]uit[/]"
        )

    async def _create(self) -> Optional[Project]:
        self.ui.console.print()
        self.ui.console.print(Rule(style="grey35"))
        self.ui.console.print(Text("Create project", style="bold cyan"))
        self.ui.console.print()

        name = (await self.ui.prompt("Name")).strip()
        if not name:
            self.ui.warning("Name is required.")
            return None

        description = (await self.ui.prompt("Description (one line)")).strip()

        self.ui.console.print()
        self.ui.console.print("[dim]Kinds: " + ", ".join(PROJECT_KINDS) + "[/]")
        kind = (await self.ui.prompt("Kind", default="other")).strip().lower()
        if kind not in PROJECT_KINDS:
            kind = "other"

        self.ui.console.print()
        self.ui.console.print(
            "[dim]Languages: " + ", ".join(PROJECT_LANGUAGES) + "[/]"
        )
        language = (await self.ui.prompt("Language", default="python")).strip().lower()
        if language not in PROJECT_LANGUAGES:
            language = "other"

        suggested_template = suggest_template(kind, language)
        self.ui.console.print()
        self.ui.console.print(
            "[dim]Templates: " + ", ".join(TEMPLATE_CHOICES) + "[/]"
        )
        template = (
            await self.ui.prompt("Template", default=suggested_template)
        ).strip().lower()
        if template not in TEMPLATE_CHOICES:
            self.ui.warning(f"Unknown template '{template}', using 'minimal'.")
            template = "minimal"

        starter_files = template != "minimal"
        if template != "minimal":
            starter_files = await self.ui.confirm(
                f"Create starter files for '{template}'?"
            )

        git = False
        if shutil.which("git"):
            git = await self.ui.confirm(
                "Initialize a git repository with an initial commit?"
            )

        tags_raw = (
            await self.ui.prompt("Tags (comma-separated)", default="")
        ).strip()
        tags = (
            [t.strip() for t in tags_raw.split(",") if t.strip()]
            if tags_raw
            else []
        )

        self.ui.console.print()
        summary = Table.grid(padding=(0, 2))
        summary.add_column(style="dim", justify="right")
        summary.add_column(style="white")
        summary.add_row("Name", name)
        summary.add_row("Kind", kind)
        summary.add_row("Language", language)
        summary.add_row("Template", template)
        summary.add_row("Starter files", "yes" if starter_files else "no")
        summary.add_row("Git", "yes" if git else "no")
        summary.add_row("Tags", ", ".join(tags) if tags else "—")
        self.ui.console.print(
            Panel(
                summary,
                title="[bold]Confirm[/]",
                border_style="grey35",
                box=ROUNDED,
                padding=(1, 1),
            )
        )

        if not await self.ui.confirm("Create this project?"):
            self.ui.info("Cancelled.")
            return None

        try:
            project = self.manager.create(
                name=name,
                description=description,
                kind=kind,
                language=language,
                tags=tags,
                template=template,
                git=git,
                starter_files=starter_files,
            )
        except Exception as e:
            self.ui.error(f"Could not create project: {e}")
            return None

        self.ui.success(
            f"Created project [bold]{project.name}[/] at {project.root}"
        )
        return project

    async def _import(self) -> Optional[Project]:
        self.ui.console.print()
        self.ui.console.print(Rule(style="grey35"))
        self.ui.console.print(Text("Import existing directory", style="bold cyan"))
        self.ui.console.print()

        path_raw = (await self.ui.prompt("Source directory")).strip()
        if not path_raw:
            return None
        source = Path(path_raw).expanduser()
        if not source.exists() or not source.is_dir():
            self.ui.error(f"Not a directory: {source}")
            return None

        source = source.resolve()
        default_name = source.name
        name = (await self.ui.prompt("Project name", default=default_name)).strip()
        description = (await self.ui.prompt("Description (one line)")).strip()

        self.ui.console.print()
        self.ui.console.print("[dim]Kinds: " + ", ".join(PROJECT_KINDS) + "[/]")
        kind = (await self.ui.prompt("Kind", default="other")).strip().lower()
        if kind not in PROJECT_KINDS:
            kind = "other"

        self.ui.console.print()
        self.ui.console.print(
            "[dim]Languages: " + ", ".join(PROJECT_LANGUAGES) + "[/]"
        )
        language = (await self.ui.prompt("Language", default="other")).strip().lower()
        if language not in PROJECT_LANGUAGES:
            language = "other"

        git = False
        if shutil.which("git"):
            git = await self.ui.confirm(
                "Run 'git init' in the source directory if it is not already a repo?"
            )

        try:
            project = self.manager.import_existing(
                source=source,
                name=name,
                description=description,
                kind=kind,
                language=language,
                git=git,
            )
        except Exception as e:
            self.ui.error(f"Import failed: {e}")
            return None

        linked = project.settings.get("workspace_linked", False)
        mode = "symlinked" if linked else "copied"
        self.ui.success(
            f"Imported [bold]{project.name}[/] ({mode}) from {source}"
        )
        return project

    async def _delete(self, projects: list[Project]) -> None:
        if not projects:
            self.ui.warning("No projects to delete.")
            return

        self.ui.console.print()
        self.ui.console.print(Rule(style="grey35"))
        choice = (await self.ui.prompt("Number to delete (or 'cancel')")).strip()
        if not choice or choice.lower() in ("cancel", "c", "q"):
            return
        if not choice.isdigit():
            self.ui.warning(f"Not a number: {choice}")
            return
        idx = int(choice)
        if not (1 <= idx <= len(projects)):
            self.ui.warning(f"No project #{idx}.")
            return

        project = projects[idx - 1]
        self.ui.warning(
            f"You are about to permanently delete "
            f"[bold]{project.name}[/] ({project.slug})"
        )
        self.ui.warning(f"All files under {project.root} will be removed.")
        if project.workspace is not None and project.workspace.is_symlink():
            self.ui.warning(
                "The workspace is a symlink — the source directory will NOT "
                "be deleted."
            )

        self.ui.console.print()
        self.ui.console.print(
            f"Type [bold red]{project.slug}[/] to confirm deletion:"
        )
        typed = (await self.ui.prompt("Confirm slug")).strip()
        if typed != project.slug:
            self.ui.info("Cancelled (slug did not match).")
            return

        if self.manager.delete(project.slug):
            self.ui.success(f"Deleted {project.slug}.")
        else:
            self.ui.error(f"Could not delete {project.slug}.")


HELP_ROWS: list[tuple[str, str]] = [
    ("/help", "Show this help"),
    ("/exit", "Exit the session"),
    ("/clear", "Clear the screen"),
    ("/new", "Archive current chat and start a fresh one"),
    ("/retry", "Re-send the last user message"),
    ("/swarm <task>", "Decompose and run a task with the agent swarm"),
    ("/swarm-status", "Show the last swarm run status"),
    ("/projects", "Switch project (or jump to chat mode)"),
    ("/new-project", "Create a new project inline"),
    ("/import-project", "Import an existing directory as a project"),
    ("/project", "Show current project details"),
    ("/history", "List past conversations in this project"),
    ("/resume <id>", "Load a past conversation"),
    ("/archive", "Archive the current conversation now"),
    ("/notes", "Show project notes"),
    ("/open", "Show the project directory path"),
    ("/tools", "List available tools"),
    ("/tools on <name>…", "Enable one or more tools"),
    ("/tools off <name>…", "Disable one or more tools"),
    ("/thinking on|off", "Toggle Agnes Thinking mode"),
    ("/stream on|off", "Toggle streaming"),
    ("/verbose on|off", "Show full tool result payloads"),
    ("/tokens", "Show token usage and cost estimate"),
    ("/limits", "Show per-key rate-limit status"),
    ("/model", "Show the current model name"),
    ("/save <path>", "Save the current conversation to JSON"),
    ("/load <path>", "Load a conversation from JSON"),
    ("/export <path>", "Export the current conversation as Markdown"),
]


def print_help(ui: TerminalUI) -> None:
    table = Table(
        show_header=False,
        box=ROUNDED,
        border_style="grey35",
        padding=(0, 2),
    )
    table.add_column("Command", style="cyan", no_wrap=True)
    table.add_column("Description")
    for c, d in HELP_ROWS:
        table.add_row(c, d)
    ui.console.print(
        Panel(
            table,
            title="[bold]Commands[/]",
            border_style="grey35",
            box=ROUNDED,
            padding=(1, 1),
        )
    )


def print_tools(ui: TerminalUI, registry: ToolRegistry) -> None:
    table = Table(
        show_header=True,
        header_style="bold",
        box=ROUNDED,
        border_style="grey35",
        padding=(0, 1),
    )
    table.add_column("Tool", style="bold cyan", no_wrap=True)
    table.add_column("Source", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Description")

    for s in registry.all_schemas():
        fn = s["function"]
        name = fn["name"]
        source = "web" if name in ("web_search", "web_browser") else "code"
        status = "[green]on[/]" if registry.is_enabled(name) else "[dim]off[/]"
        desc = fn.get("description", "").strip().replace("\n", " ")
        if len(desc) > 80:
            desc = desc[:77] + "…"
        table.add_row(name, source, status, desc)

    ui.console.print(
        Panel(
            table,
            title="[bold]Tools[/]",
            border_style="grey35",
            box=ROUNDED,
            padding=(1, 1),
        )
    )


def print_limits(ui: TerminalUI, client: AgnesClient, config: Config) -> None:
    tier = ACCESS_TIERS.get(config.tier, ACCESS_TIERS[DEFAULT_TIER])
    entries = client.pool_status()

    table = Table(
        show_header=True,
        header_style="bold",
        box=ROUNDED,
        border_style="grey35",
        padding=(0, 1),
    )
    table.add_column("Key", style="cyan", no_wrap=True)
    table.add_column("Used", justify="right")
    table.add_column("Remaining", justify="right")
    table.add_column("Cooldown", justify="right")
    table.add_column("Retries", justify="right")
    table.add_column("Total", justify="right")

    for e in entries:
        cd = f"{e['cooldown_seconds']:.1f}s" if e["cooldown_seconds"] > 0 else "—"
        table.add_row(
            e["key"],
            f"{e['used_in_window']}/{e['rpm_limit']}",
            str(e["remaining"]),
            cd,
            str(e["cooldown_count"]),
            str(e["requests"]),
        )

    totals = client.pool_totals()
    ui.console.print(
        Panel(
            table,
            title=(
                f"[bold]Rate limits[/]  "
                f"[dim]·  {tier['label']}  ·  "
                f"{totals['keys']} keys × {totals['rpm_per_key']} RPM "
                f"= {totals['total_rpm']} RPM total[/]"
            ),
            border_style="grey35",
            box=ROUNDED,
            padding=(1, 1),
        )
    )


def print_project(ui: TerminalUI, project: Optional[Project]) -> None:
    if project is None:
        ui.info("Mode: chat (ephemeral, no project)")
        return
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim", justify="right")
    table.add_column(style="white")
    table.add_row("Name", f"[bold]{project.name}[/]")
    table.add_row("Slug", project.slug)
    table.add_row("Kind", project.kind)
    table.add_row("Language", project.language)
    table.add_row("Template", project.template)
    table.add_row("Description", project.description or "—")
    table.add_row("Tags", ", ".join(project.tags) if project.tags else "—")
    table.add_row("Git", "initialized" if project.git_initialized else "—")
    table.add_row("Schema", f"v{project.schema_version}")
    table.add_row("Root", str(project.root))
    table.add_row("Workspace", str(project.workspace))
    if project.workspace is not None and project.workspace.is_symlink():
        try:
            target = os.readlink(project.workspace)
            table.add_row("  ↳ linked to", str(target))
        except OSError:
            pass
    table.add_row(
        "Created",
        f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(project.created_at))} "
        f"({humanize_time(project.created_at)})",
    )
    table.add_row(
        "Updated",
        f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(project.updated_at))} "
        f"({humanize_time(project.updated_at)})",
    )
    ui.console.print(
        Panel(
            table,
            title="[bold cyan]Project[/]",
            border_style="cyan",
            box=ROUNDED,
            padding=(1, 1),
        )
    )


def print_history(
    ui: TerminalUI,
    project: Optional[Project],
    chat_store: Optional[ChatStore],
) -> None:
    if project is None:
        entries = chat_store.list_conversations() if chat_store else []
        source = "chat history"
    else:
        entries = project.list_conversations()
        source = f"project '{project.slug}' history"

    if not entries:
        ui.info(f"No archived conversations in {source}.")
        return

    table = Table(
        show_header=True,
        header_style="bold",
        box=ROUNDED,
        border_style="grey35",
        padding=(0, 1),
    )
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Title", max_width=48, no_wrap=False)
    table.add_column("Messages", justify="right")
    table.add_column("Updated", style="dim", no_wrap=True)

    for e in entries:
        table.add_row(
            e["id"],
            (e["title"] or "untitled")[:48],
            str(e["messages"]),
            humanize_time(e["updated_at"]),
        )

    ui.console.print(
        Panel(
            table,
            title=f"[bold]History · {source}[/]",
            border_style="grey35",
            box=ROUNDED,
            padding=(1, 1),
        )
    )
    ui.info("Use /resume <id> to load a conversation.")


class App:
    def __init__(
        self,
        args: argparse.Namespace,
        manager: ProjectManager,
        chat_store: ChatStore,
        home: Path,
    ):
        self.args = args
        self.settings = resolve_runtime_settings(args)
        self.manager = manager
        self.chat_store = chat_store
        self.home = home
        self.ui = TerminalUI()

        self.config: Optional[Config] = None
        self.pool: Optional[KeyPool] = None
        self.client: Optional[AgnesClient] = None
        self.web_agent: Optional[Any] = None
        self.code_agent: Optional[Any] = None
        self.tools: Optional[ToolRegistry] = None
        self.session: Optional[Session] = None
        self.swarm: Optional[Swarm] = None

        self.project: Optional[Project] = None
        self.swarm_state: dict[str, Any] = {"running": False, "last_plan": None}
        self.menu = ProjectMenu(manager, self.ui)

    async def _ensure_provider_choice(self) -> None:
        provider_value = str(
            getattr(self.args, "provider", None)
            or os.environ.get("AGNES_PROVIDER", "")
            or ""
        ).strip().lower()
        if provider_value and provider_value in MODEL_PROVIDERS:
            return

        self.ui.console.print()
        self.ui.console.print(Rule(style="grey35"))
        self.ui.console.print(
            Panel(
                Text("Choose the LLM API provider for this session.", style="bold cyan"),
                border_style="cyan",
                box=ROUNDED,
                padding=(1, 1),
            )
        )
        for idx, provider in enumerate(sorted(MODEL_PROVIDERS.keys()), start=1):
            self.ui.console.print(f"[{idx}] {provider} — {MODEL_PROVIDERS[provider]}")

        while True:
            choice = (await self.ui.prompt("Select provider", default="deepseek")).strip().lower()
            if choice.isdigit():
                idx = int(choice)
                names = sorted(MODEL_PROVIDERS.keys())
                if 1 <= idx <= len(names):
                    provider_value = names[idx - 1]
                    break
            elif choice in MODEL_PROVIDERS:
                provider_value = choice
                break
            self.ui.warning("Pick a valid provider from the list above.")

        self.args.provider = provider_value
        if getattr(self.args, "model", None) in (None, "", DEFAULT_MODEL):
            self.args.model = PROVIDER_DEFAULT_MODELS.get(provider_value, DEFAULT_MODEL)
        self.settings = resolve_runtime_settings(self.args)

    async def start(self) -> int:
        await self._ensure_provider_choice()

        keys = _parse_key_list(
            single=os.environ.get("AGNES_API_KEY"),
            many=self.args.api_keys,
            repeated=self.args.api_key,
        )
        if not keys:
            self.ui.error(
                "No API key. Set AGNES_API_KEY or AGNES_API_KEYS, put them in "
                ".env, or pass --api-key (repeatable) / --api-keys k1,k2,k3."
            )
            return 2

        settings = self.settings
        tier = ACCESS_TIERS.get(settings["tier"], ACCESS_TIERS[DEFAULT_TIER])
        rpm_per_key = settings["rpm"] or tier["rpm"]

        self.config = Config(
            api_keys=keys,
            provider=settings["provider"],
            base_url=settings["base_url"],
            model=settings["model"],
            agent_name=settings["agent_name"],
            agent_tagline=settings["agent_tagline"],
            custom_instructions=settings["custom_instructions"],
            temperature=settings["temperature"],
            max_tokens=settings["max_tokens"],
            max_rounds=settings["max_rounds"],
            thinking=settings["thinking"],
            stream=settings["stream"],
            verbose=settings["verbose"],
            headless_browser=settings["headless_browser"],
            tier=settings["tier"],
            rpm_override=settings["rpm"],
            max_retries=settings["max_retries"],
            swarm_workers=settings["swarm_workers"],
            swarm_default=settings["swarm_default"],
        )
        self.ui._agent_name = self.config.agent_name

        self.pool = KeyPool(keys=keys, rpm_per_key=rpm_per_key)
        self.client = AgnesClient(
            self.config, self.pool, on_retry=self.ui.retry_notice
        )

        if _HAS_WEB and not self.args.no_web:
            try:
                self.web_agent = WebAgent(
                    headless=self.config.headless_browser,
                    downloads_dir=str(self.config.effective_downloads_dir),
                )
                await self.web_agent.start()
            except Exception as e:
                self.ui.warning(f"Web agent failed to start: {e}")
                self.web_agent = None

        if self.web_agent is None and (not _HAS_CODE or self.args.no_code):
            self.ui.error(
                "Neither the web agent nor the coding agent is available.\n"
                "Install `webagent` and/or `codeagent` next to this file."
            )
            return 2

        try:
            await self._run()
        finally:
            await self._close_all()
        return 0

    async def _run(self) -> None:
        explicit = self.args.project
        if explicit:
            project = self.manager.find(explicit)
            if project is None:
                self.ui.warning(f"Project '{explicit}' not found.")
                project = self.manager.create(name=explicit)
                self.ui.success(f"Created project '{project.slug}'.")
            await self._open(project=project)
        else:
            while True:
                result = await self.menu.run()
                if result.mode == "quit":
                    return
                if result.mode == "chat":
                    await self._open(project=None)
                elif result.mode == "project" and result.project is not None:
                    await self._open(project=result.project)
                break

        await self._repl()

    async def _open(self, project: Optional[Project]) -> None:
        await self._save_current()

        if self.code_agent is not None:
            try:
                await self.code_agent.close()
            except Exception:
                pass
            self.code_agent = None

        self.project = project
        self.config.project = project

        if project is not None:
            if project.workspace is None:
                project.workspace = project.root / "workspace"
            if not project.workspace.exists():
                project.workspace.mkdir(parents=True, exist_ok=True)
            project.downloads_dir()
            project.logs_dir()
            project.conversations_dir()
            project.swarm_dir()
            self.config.downloads_dir = project.downloads_dir()
            self.manager.touch(project)
        else:
            self.config.fallback_root = Path.cwd()

        if _HAS_CODE and not self.args.no_code:
            try:
                self.code_agent = CodeAgent(
                    project_root=str(self.config.project_root),
                    web_agent=self.web_agent,
                    deploy_authorizer=lambda action: False,
                )
            except Exception as e:
                self.ui.warning(f"Coding agent failed to start: {e}")
                self.code_agent = None

        self.tools = ToolRegistry(self.web_agent, self.code_agent)
        self.session = Session(self.config, self.client, self.tools, self.ui)
        self.swarm = Swarm(self.config, self.client, self.tools, self.ui)
        self.swarm_state = {"running": False, "last_plan": None}

        if project is not None:
            loaded = project.load_current_conversation()
        else:
            loaded = self.chat_store.load_current()
        if loaded:
            self.session.messages = _normalize_messages_for_api(loaded)
            self.ui.info(f"Resumed conversation ({len(loaded)} messages).")

        self.ui.console.print()
        self.ui.console.print(Rule(style="cyan"))

        self.ui.banner(
            config=self.config,
            tools_count=len(self.tools.schemas()),
            web_ok=self.web_agent is not None,
            code_ok=self.code_agent is not None,
            client=self.client,
        )

        totals = self.client.pool_totals()
        if totals["keys"] > 1:
            self.ui.info(
                f"Loaded {totals['keys']} API keys · "
                f"{totals['total_rpm']} RPM total budget · "
                f"swarm will use up to "
                f"{self.config.swarm_workers or totals['keys']} workers"
            )

        if self.config.swarm_default:
            self.ui.info(
                "Swarm mode is ON — every message will be decomposed "
                "and run by the swarm."
            )

    async def _save_current(self) -> None:
        if self.session is None or not self.session.messages:
            return
        try:
            if self.project is not None:
                self.project.save_current_conversation(self.session.messages)
                self.manager.touch(self.project)
            else:
                self.chat_store.save_current(self.session.messages)
        except Exception as e:
            self.ui.warning(f"Could not save conversation: {e}")

    async def _archive_current(self) -> Optional[str]:
        await self._save_current()
        try:
            if self.project is not None:
                return self.project.archive_current_conversation()
            return self.chat_store.archive_current()
        except Exception as e:
            self.ui.warning(f"Archive failed: {e}")
            return None

    async def _switch_project(self) -> None:
        self.ui.console.print()
        self.ui.console.print(Rule(style="grey35"))

        if not await self.ui.confirm("Switch session? Current chat will be saved"):
            return

        await self._save_current()

        result = await self.menu.run()
        if result.mode == "quit":
            return
        if result.mode == "chat":
            await self._open(project=None)
        elif result.mode == "project" and result.project is not None:
            await self._open(project=result.project)

    async def _resume(self, conversation_id: str) -> None:
        if not conversation_id:
            self.ui.error("Usage: /resume <id>")
            return
        if conversation_id == "current":
            self.ui.info("Already in current conversation.")
            return

        loaded: Optional[list[dict]] = None
        if self.project is not None:
            loaded = self.project.load_conversation(conversation_id)
        else:
            loaded = self.chat_store.load_conversation(conversation_id)

        if loaded is None:
            self.ui.error(f"Conversation '{conversation_id}' not found.")
            return

        await self._save_current()
        await self._archive_current()

        if self.session is not None:
            self.session.messages = _normalize_messages_for_api(loaded)
            self.session.prompt_tokens = 0
            self.session.completion_tokens = 0
            self.session.cached_tokens = 0

        self.ui.success(
            f"Resumed conversation {conversation_id} ({len(loaded)} messages)."
        )

    async def _close_all(self) -> None:
        await self._save_current()
        try:
            if self.code_agent is not None:
                await self.code_agent.close()
        except Exception:
            pass
        try:
            if self.web_agent is not None:
                await self.web_agent.close()
        except Exception:
            pass
        if self.client is not None:
            await self.client.close()

    async def _repl(self) -> None:
        while True:
            try:
                line = await self.ui.ask()
            except KeyboardInterrupt:
                self.ui.console.print()
                self.ui.info("Use /exit to quit.")
                continue

            if not line:
                continue

            if line.startswith("/"):
                try:
                    should_exit = await self._handle_command(line)
                except KeyboardInterrupt:
                    self.ui.console.print()
                    self.ui.warning("Command interrupted.")
                    continue
                except Exception as e:
                    detail = str(e).strip() or repr(e) or "(no detail)"
                    self.ui.error(f"{type(e).__name__}: {detail}")
                    continue
                if should_exit:
                    return
                continue

            if self.config.swarm_default and self.swarm is not None:
                if self.swarm_state.get("running"):
                    self.ui.warning("A swarm run is already in progress.")
                    continue
                self.swarm_state["running"] = True
                try:
                    plan = await self.swarm.run(line)
                    self.swarm_state["last_plan"] = plan
                except asyncio.CancelledError:
                    self.ui.warning("Swarm run cancelled.")
                except Exception as e:
                    detail = str(e).strip() or repr(e) or "(no detail)"
                    self.ui.error(
                        f"Swarm failed: {type(e).__name__}: {detail}"
                    )
                finally:
                    self.swarm_state["running"] = False
                    await self._save_current()
                continue

            try:
                await self.session.send(line)
            except KeyboardInterrupt:
                self.ui.console.print()
                self.ui.warning("Interrupted.")
            except AgnesAPIError as e:
                self.ui.error(str(e))
            except Exception as e:
                detail = str(e).strip() or repr(e) or "(no detail)"
                self.ui.error(f"{type(e).__name__}: {detail}")
            finally:
                await self._save_current()

    async def _handle_command(self, line: str) -> bool:
        parts = line.split(maxsplit=1)
        cmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        args = rest.split() if rest and cmd != "/swarm" else []

        if cmd in ("/exit", "/quit", "/q"):
            return True

        if cmd == "/help":
            print_help(self.ui)
            return False

        if cmd == "/clear":
            self.ui.console.clear()
            return False

        if cmd == "/new":
            archived = await self._archive_current()
            if self.session is not None:
                self.session.reset()
            if archived:
                self.ui.success(
                    f"Archived as {archived}. New conversation started."
                )
            else:
                self.ui.success("New conversation started.")
            return False

        if cmd == "/retry":
            if self.session is None or not self.session._last_user_message:
                self.ui.warning("Nothing to retry.")
                return False
            while (
                self.session.messages
                and self.session.messages[-1]["role"] != "user"
            ):
                self.session.messages.pop()
            if (
                self.session.messages
                and self.session.messages[-1]["role"] == "user"
            ):
                self.session.messages.pop()
            await self.session.send(self.session._last_user_message)
            await self._save_current()
            return False

        if cmd == "/swarm":
            if not rest.strip():
                self.ui.error("Usage: /swarm <task description>")
                return False
            if self.swarm is None:
                self.ui.error("Swarm is not available in this session.")
                return False
            if self.swarm_state.get("running"):
                self.ui.warning("A swarm run is already in progress.")
                return False

            self.swarm_state["running"] = True
            try:
                plan = await self.swarm.run(rest.strip())
                self.swarm_state["last_plan"] = plan
            except asyncio.CancelledError:
                self.ui.warning("Swarm run cancelled.")
                raise
            except Exception as e:
                detail = str(e).strip() or repr(e) or "(no detail)"
                self.ui.error(
                    f"Swarm failed: {type(e).__name__}: {detail}"
                )
            finally:
                self.swarm_state["running"] = False
                await self._save_current()
            return False

        if cmd == "/swarm-status":
            last = self.swarm_state.get("last_plan")
            if last is None:
                self.ui.info("No swarm run yet.")
                return False
            counts = last.counts()
            self.ui.info(
                f"Todos: {len(last.todos)}  ·  "
                f"done={counts.get('done', 0)}  "
                f"failed={counts.get('failed', 0)}  "
                f"pending={counts.get('pending', 0)}  "
                f"in_progress={counts.get('in_progress', 0)}  "
                f"skipped={counts.get('skipped', 0)}"
            )
            return False

        if cmd == "/projects":
            await self._switch_project()
            return False

        if cmd == "/new-project":
            await self._save_current()
            project = await self.menu._create()
            if project is not None:
                await self._open(project=project)
            return False

        if cmd == "/import-project":
            await self._save_current()
            project = await self.menu._import()
            if project is not None:
                await self._open(project=project)
            return False

        if cmd == "/project":
            print_project(self.ui, self.project)
            return False

        if cmd == "/open":
            if self.project is None:
                self.ui.info(
                    f"Chat mode · workspace at {self.config.project_root}"
                )
                return False
            self.ui.info(f"Project root: {self.project.root}")
            self.ui.info(f"Workspace:   {self.project.workspace}")
            self.ui.info(f"Downloads:   {self.project.downloads_dir()}")
            self.ui.info(f"Logs:        {self.project.logs_dir()}")
            return False

        if cmd == "/history":
            print_history(self.ui, self.project, self.chat_store)
            return False

        if cmd == "/resume":
            await self._resume(rest.strip())
            return False

        if cmd == "/archive":
            archived = await self._archive_current()
            if archived:
                self.ui.success(f"Archived as {archived}.")
            else:
                self.ui.info("Nothing to archive.")
            return False

        if cmd == "/notes":
            if self.project is None:
                self.ui.info("Notes are only available in project mode.")
                return False
            notes = self.project.read_notes()
            if notes.strip():
                self.ui.console.print(
                    Panel(
                        Markdown(sanitize_text(notes)),
                        title=f"[bold]Notes · {self.project.slug}[/]",
                        border_style="cyan",
                        box=ROUNDED,
                        padding=(1, 2),
                    )
                )
            else:
                self.ui.info("No notes yet.")
            return False

        if cmd == "/tools":
            if args and args[0] in ("on", "off") and len(args) > 1:
                action = args[0]
                for n in args[1:]:
                    ok = (
                        self.tools.enable(n)
                        if action == "on"
                        else self.tools.disable(n)
                    )
                    if ok:
                        self.ui.success(f"{action.title()} → {n}")
                    else:
                        self.ui.warning(f"Unknown tool: {n}")
            else:
                print_tools(self.ui, self.tools)
            return False

        if cmd == "/thinking":
            if args and args[0] in ("on", "off"):
                self.config.thinking = args[0] == "on"
            self.ui.info(
                f"Thinking: {'on' if self.config.thinking else 'off'}"
            )
            return False

        if cmd == "/stream":
            if args and args[0] in ("on", "off"):
                self.config.stream = args[0] == "on"
            self.ui.info(f"Stream: {'on' if self.config.stream else 'off'}")
            return False

        if cmd == "/verbose":
            if args and args[0] in ("on", "off"):
                self.config.verbose = args[0] == "on"
            self.ui.info(
                f"Verbose: {'on' if self.config.verbose else 'off'}"
            )
            return False

        if cmd == "/tokens":
            total = self.session.prompt_tokens + self.session.completion_tokens
            cost = self.session.cost_estimate()
            self.ui.info(
                f"Prompt: {self.session.prompt_tokens}  ·  "
                f"Completion: {self.session.completion_tokens}  ·  "
                f"Total: {total}  ·  "
                f"Est. cost: ${cost:.4f}"
            )
            return False

        if cmd == "/limits":
            print_limits(self.ui, self.client, self.config)
            return False

        if cmd == "/model":
            self.ui.info(f"Model: {self.client.model}")
            return False

        if cmd == "/save":
            if not rest.strip():
                self.ui.error("Usage: /save <path>")
                return False
            path = Path(rest.strip()).expanduser()
            try:
                _write_json(path, sanitize_obj(self.session.messages))
                self.ui.success(
                    f"Saved {len(self.session.messages)} messages to {path}"
                )
            except Exception as e:
                self.ui.error(f"Save failed: {e}")
            return False

        if cmd == "/load":
            if not rest.strip():
                self.ui.error("Usage: /load <path>")
                return False
            path = Path(rest.strip()).expanduser()
            if not path.exists():
                self.ui.error(f"Not found: {path}")
                return False
            try:
                data = _read_json(path, None)
                if isinstance(data, list):
                    self.session.messages = _normalize_messages_for_api(data)
                elif isinstance(data, dict) and data.get("messages"):
                    self.session.messages = _normalize_messages_for_api(
                        list(data["messages"])
                    )
                else:
                    self.ui.error("File does not contain a message list.")
                    return False
                self.ui.success(
                    f"Loaded {len(self.session.messages)} messages from {path}"
                )
            except Exception as e:
                self.ui.error(f"Load failed: {e}")
            return False

        if cmd == "/export":
            if not rest.strip():
                self.ui.error("Usage: /export <path>")
                return False
            path = Path(rest.strip()).expanduser()
            try:
                path.write_text(
                    sanitize_text(self._render_markdown_transcript()),
                    encoding="utf-8",
                )
                self.ui.success(f"Exported transcript to {path}")
            except Exception as e:
                self.ui.error(f"Export failed: {e}")
            return False

        self.ui.error(f"Unknown command: {cmd}. Type /help.")
        return False

    def _render_markdown_transcript(self) -> str:
        if self.session is None:
            return ""
        parts: list[str] = []
        header = "# Agnes Agent transcript\n"
        if self.project is not None:
            header += (
                f"\n- Project: `{self.project.name}` (`{self.project.slug}`)\n"
                f"- Kind: `{self.project.kind}`  ·  "
                f"Language: `{self.project.language}`\n"
            )
        else:
            header += "\n- Mode: chat (ephemeral)\n"
        header += (
            f"- Model: `{self.client.model}`\n"
            f"- Workspace: `{self.config.project_root}`\n"
            f"- Messages: {len(self.session.messages)}\n"
        )
        parts.append(header)
        for msg in self.session.messages:
            role = msg.get("role", "?")
            content = msg.get("content") or ""
            if role == "user":
                parts.append(f"## User\n\n{content}\n")
            elif role == "assistant":
                parts.append(f"## Assistant\n\n{content or '_[tool call]_'}\n")
                for tc in msg.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    parts.append(
                        f"> tool: `{fn.get('name')}`  \n"
                        f"> args: `{(fn.get('arguments') or '')[:400]}`\n"
                    )
            elif role == "tool":
                text = content if isinstance(content, str) else str(content)
                if len(text) > 2000:
                    text = text[:2000] + "\n…"
                parts.append(f"### Tool result\n\n```\n{text}\n```\n")
        return "\n".join(parts)


def _parse_key_list(
    single: Optional[str],
    many: Optional[str],
    repeated: Optional[list[str]],
) -> list[str]:
    keys: list[str] = []
    if repeated:
        keys.extend(k.strip() for k in repeated if k and k.strip())
    if many:
        keys.extend(k.strip() for k in many.split(",") if k.strip())
    if single and single.strip():
        keys.append(single.strip())

    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="agnes-agent",
        description="Agnes 3.0 Flash terminal agent — projects, chat, swarm.",
    )
    p.add_argument(
        "--config",
        default=os.environ.get("AGNES_CONFIG"),
        help="Path to a JSON profile with agent branding and defaults.",
    )
    p.add_argument(
        "--agent-name",
        default=None,
        help="Override the displayed agent name.",
    )
    p.add_argument(
        "--agent-tagline",
        default=None,
        help="Override the displayed agent tagline.",
    )
    p.add_argument(
        "--custom-instructions",
        default=None,
        help="Additional instructions appended to the agent system prompt.",
    )
    p.add_argument(
        "--provider",
        choices=sorted(MODEL_PROVIDERS.keys()),
        default=os.environ.get("AGNES_PROVIDER"),
        help="Model provider backend to target.",
    )
    p.add_argument(
        "--project",
        default=os.environ.get("AGNES_PROJECT"),
        help="Open a specific project by slug. Skips the menu.",
    )
    p.add_argument(
        "--home",
        default=os.environ.get("AGNES_HOME"),
        help="Agent home directory (default: ~/.agnes).",
    )
    p.add_argument(
        "--api-key",
        action="append",
        default=None,
        help="Agnes API key. Repeat to provide multiple keys.",
    )
    p.add_argument(
        "--api-keys",
        default=os.environ.get("AGNES_API_KEYS"),
        help="Comma-separated list of Agnes API keys.",
    )
    p.add_argument(
        "--base-url",
        default=os.environ.get("AGNES_BASE_URL", DEFAULT_BASE_URL),
        help=f"Agnes API base URL (default: {DEFAULT_BASE_URL}).",
    )
    p.add_argument(
        "--model",
        default=os.environ.get("AGNES_MODEL"),
        help="Model name. Defaults to the selected provider model.",
    )
    p.add_argument(
        "--temperature",
        type=float,
        default=float(os.environ.get("AGNES_TEMPERATURE", "0.3")),
        help="Sampling temperature (default: 0.3).",
    )
    p.add_argument(
        "--max-tokens",
        type=int,
        default=int(os.environ.get("AGNES_MAX_TOKENS", str(DEFAULT_MAX_TOKENS))),
        help=f"Max output tokens (default: {DEFAULT_MAX_TOKENS}).",
    )
    p.add_argument(
        "--max-rounds",
        type=int,
        default=int(os.environ.get("AGNES_MAX_ROUNDS", str(DEFAULT_MAX_ROUNDS))),
        help=f"Max tool-call rounds per turn (default: {DEFAULT_MAX_ROUNDS}).",
    )
    p.add_argument(
        "--tier",
        choices=sorted(ACCESS_TIERS.keys()),
        default=os.environ.get("AGNES_TIER", DEFAULT_TIER),
        help="Access tier for the client-side RPM budget.",
    )
    p.add_argument(
        "--rpm",
        type=int,
        default=(
            int(os.environ["AGNES_RPM"]) if os.environ.get("AGNES_RPM")
            else None
        ),
        help="Override the per-key RPM budget.",
    )
    p.add_argument(
        "--max-retries",
        type=int,
        default=int(os.environ.get("AGNES_MAX_RETRIES", str(DEFAULT_MAX_RETRIES))),
        help=f"Max retries on 429 or transient network errors "
             f"(default: {DEFAULT_MAX_RETRIES}).",
    )
    p.add_argument(
        "--swarm-workers",
        type=int,
        default=(
            int(os.environ["AGNES_SWARM_WORKERS"])
            if os.environ.get("AGNES_SWARM_WORKERS")
            else None
        ),
        help="Number of parallel workers. Defaults to the number of API keys.",
    )
    p.add_argument(
        "--swarm",
        action="store_true",
        default=None,
        help="Route every message through the swarm instead of the single agent.",
    )
    p.add_argument(
        "--thinking",
        action="store_true",
        default=None,
        help="Enable Agnes Thinking mode from the start.",
    )
    p.add_argument(
        "--no-stream",
        action="store_true",
        default=None,
        help="Disable streaming (request the whole reply at once).",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        default=None,
        help="Show full tool result payloads.",
    )
    p.add_argument(
        "--show-browser",
        action="store_true",
        default=None,
        help="Run the browser with a visible window (default: headless).",
    )
    p.add_argument(
        "--no-web",
        action="store_true",
        help="Disable the web agent even if it is installed.",
    )
    p.add_argument(
        "--no-code",
        action="store_true",
        help="Disable the coding agent even if it is installed.",
    )
    return p.parse_args(argv)


_ENV_LINE_RE = re.compile(
    r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$"
)


def _strip_quotes(v: str) -> str:
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    return v


def _interpolate(v: str, env: dict[str, str]) -> str:
    def repl(m: re.Match) -> str:
        name = m.group(1) or m.group(2)
        return env.get(name, os.environ.get(name, ""))

    return re.sub(
        r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)",
        repl,
        v,
    )


def load_dotenv(
    path: Optional[str] = None, *, override: bool = False
) -> Optional[Path]:
    candidates: list[Path] = []
    if path:
        candidates.append(Path(path).expanduser())
    else:
        candidates.extend([
            Path(".env"),
            Path(".env.local"),
            Path.home() / ".agnes" / ".env",
        ])

    for p in candidates:
        if not p.exists() or not p.is_file():
            continue
        try:
            loaded: dict[str, str] = {}
            for raw in p.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                m = _ENV_LINE_RE.match(raw)
                if not m:
                    continue
                k, v = m.group(1), _strip_quotes(m.group(2))
                v = _interpolate(v, loaded)
                loaded[k] = v
            for k, v in loaded.items():
                if override or k not in os.environ:
                    os.environ[k] = v
            return p
        except Exception:
            continue
    return None


def load_agent_config(config_path: Optional[str] = None) -> dict[str, Any]:
    if config_path is None:
        config_path = os.environ.get("AGNES_CONFIG")
    candidates: list[Path] = []
    if config_path:
        candidates.append(Path(config_path).expanduser())
    candidates.extend([
        Path("agent.json"),
        Path(".agnes") / "agent.json",
        Path.home() / ".agnes" / "agent.json",
        Path.home() / ".agnes" / "config.json",
    ])

    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        try:
            data = _read_json(path, {})
            if isinstance(data, dict):
                return sanitize_obj(data)
        except Exception:
            continue
    return {}


def load_provider_defaults(config_path: Optional[str] = None) -> dict[str, Any]:
    if config_path is None:
        config_path = os.environ.get("AGNES_PROVIDERS_CONFIG")
    candidates: list[Path] = []
    if config_path:
        candidates.append(Path(config_path).expanduser())
    candidates.extend([
        Path("providers.json"),
        Path(".agnes") / "providers.json",
        Path.home() / ".agnes" / "providers.json",
    ])

    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        try:
            data = _read_json(path, {})
            if isinstance(data, dict):
                return sanitize_obj(data)
        except Exception:
            continue
    return {}


def resolve_runtime_settings(args: argparse.Namespace) -> dict[str, Any]:
    profile = load_agent_config(getattr(args, "config", None))
    provider_defaults = load_provider_defaults()

    provider_value = str(
        getattr(args, "provider", profile.get("provider", DEFAULT_PROVIDER))
        or profile.get("provider", DEFAULT_PROVIDER)
    ).strip().lower() or DEFAULT_PROVIDER

    def pick(key: str, default: Any, *, cast: Optional[type] = None) -> Any:
        cli_value = getattr(args, key, None)
        if isinstance(cli_value, bool) and cli_value is False:
            cli_value = None
        if cli_value is not None:
            value = cli_value
        else:
            value = profile.get(key, default)
        if cast is not None:
            try:
                value = cast(value)
            except (TypeError, ValueError):
                value = default
        return value

    provider_cfg = provider_defaults.get(provider_value, {}) if isinstance(provider_defaults, dict) else {}
    default_base_url = provider_cfg.get("base_url") or PROVIDER_DEFAULT_BASE_URLS.get(provider_value, DEFAULT_BASE_URL)
    default_model = provider_cfg.get("model") or PROVIDER_DEFAULT_MODELS.get(provider_value, DEFAULT_MODEL)
    configured = {
        "provider": provider_value,
        "agent_name": pick("agent_name", profile.get("agent_name", DEFAULT_AGENT_NAME), cast=str),
        "agent_tagline": pick("agent_tagline", profile.get("agent_tagline", DEFAULT_AGENT_TAGLINE), cast=str),
        "base_url": pick("base_url", profile.get("base_url", default_base_url), cast=str),
        "model": pick("model", profile.get("model", default_model), cast=str),
        "temperature": pick("temperature", profile.get("temperature", 0.3), cast=float),
        "max_tokens": pick("max_tokens", profile.get("max_tokens", DEFAULT_MAX_TOKENS), cast=int),
        "max_rounds": pick("max_rounds", profile.get("max_rounds", DEFAULT_MAX_ROUNDS), cast=int),
        "thinking": pick("thinking", profile.get("thinking", False), cast=bool),
        "stream": pick("stream", profile.get("stream", True), cast=bool),
        "verbose": pick("verbose", profile.get("verbose", False), cast=bool),
        "headless_browser": pick("headless_browser", profile.get("headless_browser", True), cast=bool),
        "tier": pick("tier", profile.get("tier", DEFAULT_TIER), cast=str),
        "rpm": pick("rpm", profile.get("rpm", None), cast=int),
        "max_retries": pick("max_retries", profile.get("max_retries", DEFAULT_MAX_RETRIES), cast=int),
        "swarm_workers": pick("swarm_workers", profile.get("swarm_workers", None), cast=int),
        "swarm_default": pick("swarm", profile.get("swarm", False), cast=bool),
        "custom_instructions": pick("custom_instructions", profile.get("custom_instructions", DEFAULT_CUSTOM_INSTRUCTIONS), cast=str),
    }

    if getattr(args, "no_stream", None) is True:
        configured["stream"] = False
    elif getattr(args, "no_stream", None) is False and "stream" in profile:
        configured["stream"] = bool(profile["stream"])

    if getattr(args, "show_browser", None) is True:
        configured["headless_browser"] = False
    elif getattr(args, "show_browser", None) is False and "headless_browser" in profile:
        configured["headless_browser"] = bool(profile["headless_browser"])

    if getattr(args, "swarm", None) is True:
        configured["swarm_default"] = True
    elif getattr(args, "swarm", None) is False and "swarm" in profile:
        configured["swarm_default"] = bool(profile["swarm"])

    if getattr(args, "thinking", None) is True:
        configured["thinking"] = True
    elif getattr(args, "thinking", None) is False and "thinking" in profile:
        configured["thinking"] = bool(profile["thinking"])

    if configured["agent_name"] is None or not str(configured["agent_name"]).strip():
        configured["agent_name"] = DEFAULT_AGENT_NAME
    if configured["agent_tagline"] is None or not str(configured["agent_tagline"]).strip():
        configured["agent_tagline"] = DEFAULT_AGENT_TAGLINE
    if configured["custom_instructions"] is None:
        configured["custom_instructions"] = DEFAULT_CUSTOM_INSTRUCTIONS

    return configured


def _ensure_home(home: Path) -> dict:
    home.mkdir(parents=True, exist_ok=True)
    (home / "projects").mkdir(exist_ok=True)
    (home / "chats").mkdir(exist_ok=True)
    (home / "logs").mkdir(exist_ok=True)

    config_path = home / "config.json"
    if not config_path.exists():
        _write_json(config_path, {
            "schema_version": SCHEMA_VERSION,
            "default_tier": DEFAULT_TIER,
            "default_model": DEFAULT_MODEL,
            "default_language": "python",
            "default_kind": "other",
            "git_by_default": False,
            "agent_name": DEFAULT_AGENT_NAME,
            "agent_tagline": DEFAULT_AGENT_TAGLINE,
            "custom_instructions": DEFAULT_CUSTOM_INSTRUCTIONS,
        })

    data = _read_json(config_path, None)
    if not isinstance(data, dict):
        return {}
    if int(data.get("schema_version", 0)) < SCHEMA_VERSION:
        data["schema_version"] = SCHEMA_VERSION
        _write_json(config_path, data)
    return data


async def async_main(args: argparse.Namespace) -> int:
    home = (
        Path(args.home).expanduser().resolve()
        if args.home
        else Path.home() / ".agnes"
    )
    _ensure_home(home)

    manager = ProjectManager(home / "projects")
    chat_store = ChatStore(home / "chats")

    app = App(args, manager, chat_store, home)
    return await app.start()


def main() -> int:
    load_dotenv()
    args = parse_args()
    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    sys.exit(main())