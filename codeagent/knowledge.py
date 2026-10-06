"""
knowledge.py — Documentation lookup, project memory, and tasks.

Layer 22 Documentation
Layer 23 Project memory / context
Layer 24 Task management
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

from .workspace import Workspace


# --------------------------------------------------------------------------- #
# Layer 22 — Documentation
# --------------------------------------------------------------------------- #

class Documentation:
    def __init__(self, workspace: Workspace, web_search=None):
        self.ws = workspace
        self.search = web_search

    async def search_docs(self, query: str, prefer_official: bool = True) -> dict:
        if self.search is None:
            return {"error": "web search not connected"}
        domains = ["docs.python.org", "developer.mozilla.org", "react.dev",
                   "doc.rust-lang.org", "go.dev", "nodejs.org"] if prefer_official else []
        result = await self.search(
            query=f"{query} documentation",
            domains=domains or None,
            max_results=5,
        )
        return result

    def find_api_reference(self, symbol: str) -> dict:
        """Search locally for references to a symbol in the codebase."""
        return {"hits": self.ws.search_code(symbol)}

    def read_docstring(self, file: str, symbol: Optional[str] = None) -> dict:
        import ast
        p = self.ws._resolve(file)
        tree = ast.parse(p.read_text(errors="replace"))
        if symbol is None:
            return {"module_docstring": ast.get_docstring(tree)}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                    and node.name == symbol:
                return {"name": symbol,
                        "docstring": ast.get_docstring(node)}
        return {"error": f"{symbol} not found"}

    def get_package_docs(self, package: str) -> dict:
        """Try to import the package and read its docstring."""
        try:
            mod = __import__(package)
            return {"package": package,
                    "docstring": getattr(mod, "__doc__", None),
                    "file": getattr(mod, "__file__", None)}
        except ImportError as e:
            return {"error": str(e)}


# --------------------------------------------------------------------------- #
# Layer 23 — Project memory
# --------------------------------------------------------------------------- #

@dataclass
class ProjectNote:
    key: str
    value: str
    kind: str = "note"      # note | architecture | convention | command | issue
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)


class ProjectMemory:
    """Persistent, file-backed memory for a project."""

    def __init__(self, workspace: Workspace, path: str = ".codeagent/memory.json"):
        self.ws = workspace
        self.path = workspace._resolve(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._notes: dict[str, ProjectNote] = {}
        self._load()

    def _load(self):
        if self.path.exists():
            data = json.loads(self.path.read_text())
            for k, v in data.get("notes", {}).items():
                self._notes[k] = ProjectNote(**v)

    def _save(self):
        self.path.write_text(json.dumps(
            {"notes": {k: asdict(v) for k, v in self._notes.items()}},
            indent=2,
        ))

    def save(self, key: str, value: str, kind: str = "note") -> dict:
        note = self._notes.get(key)
        if note:
            note.value = value
            note.kind = kind
            note.updated = time.time()
        else:
            self._notes[key] = ProjectNote(key=key, value=value, kind=kind)
        self._save()
        return {"key": key, "kind": kind}

    def read(self, key: str) -> Optional[dict]:
        note = self._notes.get(key)
        return asdict(note) if note else None

    def update(self, key: str, value: str) -> dict:
        return self.save(key, value)

    def delete(self, key: str) -> bool:
        if key in self._notes:
            del self._notes[key]
            self._save()
            return True
        return False

    def list(self, kind: Optional[str] = None) -> list[dict]:
        return [asdict(n) for n in self._notes.values()
                if kind is None or n.kind == kind]

    # ------------------------------------------------------------------ #
    # High-level context
    # ------------------------------------------------------------------ #

    def get_project_context(self) -> dict:
        return {
            "root": str(self.ws.root),
            "managers": _managers(self.ws),
            "structure": self.ws.get_project_structure(max_depth=3),
            "notes": self.list(),
        }

    def get_project_instructions(self) -> Optional[str]:
        for name in ("AGENTS.md", "CLAUDE.md", ".cursorrules",
                     "CONTRIBUTING.md", "README.md"):
            p = self.ws.root / name
            if p.exists():
                return p.read_text(errors="replace")
        return None

    def get_conventions(self) -> list[dict]:
        return self.list(kind="convention")

    def get_architecture(self) -> Optional[dict]:
        note = self._notes.get("architecture")
        return asdict(note) if note else None

    def get_recent_changes(self, limit: int = 10) -> list[dict]:
        import subprocess
        try:
            r = subprocess.run(
                ["git", "log", f"-{limit}", "--pretty=format:%h %s"],
                cwd=str(self.ws.root), capture_output=True, text=True, timeout=10,
            )
            return [{"line": l} for l in r.stdout.splitlines()]
        except Exception:
            return []


def _managers(ws: Workspace) -> list[str]:
    from .packages import detect_managers
    return detect_managers(ws.root)


# --------------------------------------------------------------------------- #
# Layer 24 — Task management
# --------------------------------------------------------------------------- #

@dataclass
class Task:
    id: str
    title: str
    description: str = ""
    status: str = "pending"   # pending | in_progress | done | cancelled
    parent: Optional[str] = None
    subtasks: list[str] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    result: Optional[str] = None


class Tasks:
    def __init__(self, workspace: Workspace, path: str = ".codeagent/tasks.json"):
        self.ws = workspace
        self.path = workspace._resolve(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._tasks: dict[str, Task] = {}
        self._load()

    def _load(self):
        if self.path.exists():
            data = json.loads(self.path.read_text())
            for tid, t in data.get("tasks", {}).items():
                self._tasks[tid] = Task(**t)

    def _save(self):
        self.path.write_text(json.dumps(
            {"tasks": {tid: asdict(t) for tid, t in self._tasks.items()}},
            indent=2,
        ))

    def create(self, title: str, description: str = "",
               parent: Optional[str] = None) -> dict:
        tid = uuid.uuid4().hex[:8]
        task = Task(id=tid, title=title, description=description, parent=parent)
        self._tasks[tid] = task
        if parent and parent in self._tasks:
            self._tasks[parent].subtasks.append(tid)
        self._save()
        return asdict(task)

    def get(self, tid: str) -> Optional[dict]:
        t = self._tasks.get(tid)
        return asdict(t) if t else None

    def list(self, status: Optional[str] = None) -> list[dict]:
        return [asdict(t) for t in self._tasks.values()
                if status is None or t.status == status]

    def update(self, tid: str, **fields) -> Optional[dict]:
        t = self._tasks.get(tid)
        if not t:
            return None
        for k, v in fields.items():
            if hasattr(t, k):
                setattr(t, k, v)
        t.updated = time.time()
        self._save()
        return asdict(t)

    def complete(self, tid: str, result: Optional[str] = None) -> Optional[dict]:
        return self.update(tid, status="done", result=result)

    def cancel(self, tid: str) -> Optional[dict]:
        return self.update(tid, status="cancelled")