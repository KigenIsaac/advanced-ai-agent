"""
tools.py — The consolidated LLM-facing tool surface.

Layers 1–30 are exposed through 13 tools:
  workspace  terminal  code  editor  test  build
  git        packages  debug  browser  http
  database   project   review  sandbox  deploy
"""

WORKSPACE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "workspace",
        "description": "Filesystem operations and code search.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "list", "read", "write", "append", "edit", "delete",
                        "move", "copy", "mkdir", "rmdir", "exists", "info",
                        "find_files", "grep", "replace_all", "search_code",
                        "find_symbol", "ast", "symbols", "function", "class",
                        "imports", "exports", "dependencies",
                        "project_structure", "module_graph",
                    ],
                },
                "path": {"type": "string"},
                "content": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
                "source": {"type": "string"},
                "destination": {"type": "string"},
                "pattern": {"type": "string"},
                "name_pattern": {"type": "string"},
                "glob": {"type": "string"},
                "regex": {"type": "boolean"},
                "recursive": {"type": "boolean"},
                "symbol": {"type": "string"},
                "class_name": {"type": "string"},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
                "count": {"type": "integer"},
                "dry_run": {"type": "boolean"},
            },
            "required": ["action"],
        },
    },
}

TERMINAL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "terminal",
        "description": "Run shell commands, manage processes.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["run", "background", "list", "get", "kill", "wait"],
                    "default": "run",
                },
                "command": {"type": "string"},
                "cwd": {"type": "string"},
                "timeout": {"type": "integer"},
                "process_id": {"type": "string"},
                "env": {"type": "object"},
                "stdin": {"type": "string"},
            },
            "required": ["action"],
        },
    },
}

CODE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "code",
        "description": "Language-server operations: definitions, references, "
                       "rename, diagnostics, symbols.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["definition", "references", "hover", "rename",
                             "symbols", "workspace_symbols", "diagnostics"],
                },
                "file": {"type": "string"},
                "line": {"type": "integer"},
                "col": {"type": "integer"},
                "new_name": {"type": "string"},
                "query": {"type": "string"},
            },
            "required": ["action"],
        },
    },
}

EDITOR_SCHEMA = {
    "type": "function",
    "function": {
        "name": "editor",
        "description": "Structured editing with preview, apply, and revert.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "preview", "apply", "replace_range", "insert", "delete_range",
                        "rewrite", "revert_last", "revert_index", "history",
                        "rename_symbol", "add_import", "remove_import",
                        "extract_function", "inline_function",
                        "split_file", "remove_dead_code", "simplify",
                    ],
                },
                "file": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
                "content": {"type": "string"},
                "name": {"type": "string"},
                "new_name": {"type": "string"},
                "module": {"type": "string"},
                "symbols": {"type": "array", "items": {"type": "string"}},
                "target": {"type": "string"},
                "index": {"type": "integer"},
                "count": {"type": "integer"},
                "dry_run": {"type": "boolean"},
            },
            "required": ["action"],
        },
    },
}

TEST_SCHEMA = {
    "type": "function",
    "function": {
        "name": "test",
        "description": "Run tests, discover tests, get coverage, parse failures.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["run", "run_file", "run_case", "discover",
                             "coverage", "failures", "generate"],
                },
                "path": {"type": "string"},
                "test_name": {"type": "string"},
                "target_file": {"type": "string"},
                "output_file": {"type": "string"},
                "timeout": {"type": "integer"},
            },
            "required": ["action"],
        },
    },
}

BUILD_SCHEMA = {
    "type": "function",
    "function": {
        "name": "build",
        "description": "Build, clean, typecheck, lint, security scan.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["detect", "build", "clean", "typecheck", "lint",
                             "security", "complexity", "dead_code"],
                },
                "path": {"type": "string"},
            },
            "required": ["action"],
        },
    },
}

GIT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "git",
        "description": "Git operations plus remote repository management.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "status", "diff", "log", "show", "branch_current",
                        "branch_list", "branch_create", "branch_switch",
                        "branch_delete", "add", "reset", "commit", "amend",
                        "stash", "stash_pop", "stash_list", "revert",
                        "reset_hard", "clean", "merge", "rebase", "cherry_pick",
                        "blame", "file_history",
                        # remote
                        "clone", "pull", "push",
                        "pr_list", "pr_get", "pr_diff", "pr_create", "pr_update",
                        "issue_list", "issue_get", "issue_create", "issue_comment",
                    ],
                },
                "files": {"type": "array", "items": {"type": "string"}},
                "path": {"type": "string"},
                "message": {"type": "string"},
                "commit": {"type": "string"},
                "branch": {"type": "string"},
                "base": {"type": "string"},
                "head": {"type": "string"},
                "number": {"type": "integer"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "url": {"type": "string"},
                "destination": {"type": "string"},
                "staged": {"type": "boolean"},
                "no_ff": {"type": "boolean"},
                "force": {"type": "boolean"},
                "dry_run": {"type": "boolean"},
                "limit": {"type": "integer"},
                "state": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
                "draft": {"type": "boolean"},
            },
            "required": ["action"],
        },
    },
}

PACKAGES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "packages",
        "description": "Package manager and build-system operations.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["detect", "list", "install", "remove", "update", "audit"],
                },
                "package": {"type": "string"},
                "manager": {"type": "string"},
                "dev": {"type": "boolean"},
            },
            "required": ["action"],
        },
    },
}

DEBUG_SCHEMA = {
    "type": "function",
    "function": {
        "name": "debug",
        "description": "Debugger control and error analysis.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["start", "set_breakpoint", "remove_breakpoint",
                             "list_breakpoints", "parse_trace",
                             "explain_error", "last_error", "find_source",
                             "context"],
                },
                "file": {"type": "string"},
                "line": {"type": "integer"},
                "script": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}},
                "output": {"type": "string"},
            },
            "required": ["action"],
        },
    },
}

BROWSER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser",
        "description": "Browser automation. Delegates to the web agent.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "url": {"type": "string"},
                "selector": {"type": "string"},
                "text": {"type": "string"},
                "full_page": {"type": "boolean"},
            },
            "required": ["action"],
        },
    },
}

HTTP_SCHEMA = {
    "type": "function",
    "function": {
        "name": "http",
        "description": "HTTP requests and API testing.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["request", "test", "compare"],
                    "default": "request",
                },
                "method": {"type": "string"},
                "url": {"type": "string"},
                "url_b": {"type": "string"},
                "headers": {"type": "object"},
                "params": {"type": "object"},
                "body": {"type": "string"},
                "json_body": {"type": "object"},
                "expected_status": {"type": "integer"},
            },
            "required": ["action", "url"],
        },
    },
}

DATABASE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "database",
        "description": "Database inspection and querying.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["detect", "connect_sqlite", "tables", "describe",
                             "query", "schema", "migrations", "find_usage"],
                },
                "path": {"type": "string"},
                "table": {"type": "string"},
                "query": {"type": "string"},
                "model": {"type": "string"},
                "readonly": {"type": "boolean"},
                "limit": {"type": "integer"},
            },
            "required": ["action"],
        },
    },
}

PROJECT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "project",
        "description": "Project context, memory, and task management.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "context", "instructions", "architecture",
                        "conventions", "recent_changes", "note_save",
                        "note_read", "note_update", "note_delete", "note_list",
                        "task_create", "task_get", "task_list", "task_update",
                        "task_complete", "task_cancel",
                    ],
                },
                "key": {"type": "string"},
                "value": {"type": "string"},
                "kind": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "task_id": {"type": "string"},
                "parent": {"type": "string"},
                "status": {"type": "string"},
                "result": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["action"],
        },
    },
}

REVIEW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "review",
        "description": "Evidence-based code review of files, diffs, or PRs.",
        "parameters": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["file", "diff", "pr"],
                },
                "target": {"type": "string"},
                "ref": {"type": "string"},
                "number": {"type": "integer"},
                "checks": {
                    "type": "array",
                    "items": {"type": "string",
                              "enum": ["bugs", "security", "performance",
                                       "style", "tests", "requirements"]},
                },
            },
            "required": ["scope"],
        },
    },
}

SANDBOX_SCHEMA = {
    "type": "function",
    "function": {
        "name": "sandbox",
        "description": "Isolated execution environments.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "destroy", "list", "exec",
                             "snapshot", "restore"],
                },
                "backend": {"type": "string", "enum": ["subprocess", "docker"]},
                "image": {"type": "string"},
                "sandbox_id": {"type": "string"},
                "command": {"type": "string"},
                "cwd": {"type": "string"},
                "timeout": {"type": "integer"},
                "snapshot": {"type": "string"},
            },
            "required": ["action"],
        },
    },
}

DEPLOY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "deploy",
        "description": "Deployment and monitoring. Requires host authorization.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["detect", "build", "deploy", "status",
                             "logs", "rollback", "health", "metrics"],
                },
                "provider": {"type": "string"},
                "environment": {"type": "string"},
                "url": {"type": "string"},
                "target": {"type": "string"},
            },
            "required": ["action"],
        },
    },
}


TOOLS = [
    WORKSPACE_SCHEMA,
    TERMINAL_SCHEMA,
    CODE_SCHEMA,
    EDITOR_SCHEMA,
    TEST_SCHEMA,
    BUILD_SCHEMA,
    GIT_SCHEMA,
    PACKAGES_SCHEMA,
    DEBUG_SCHEMA,
    BROWSER_SCHEMA,
    HTTP_SCHEMA,
    DATABASE_SCHEMA,
    PROJECT_SCHEMA,
    REVIEW_SCHEMA,
    SANDBOX_SCHEMA,
    DEPLOY_SCHEMA,
]