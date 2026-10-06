"""
workspace.py — filesystem, code search, and AST inspection.

Layer 1  File-system functions
Layer 2  Code search
Layer 3  Code understanding
"""
from __future__ import annotations

import ast
import fnmatch
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


# --------------------------------------------------------------------------- #

class WorkspaceError(Exception): ...
class PathTraversal(WorkspaceError): ...
class NotFound(WorkspaceError): ...
class AlreadyExists(WorkspaceError): ...


DEFAULT_IGNORES = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".next", ".nuxt", "target", ".idea", ".vscode", ".tox",
}

TEXT_EXT = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go", ".rs",
    ".rb", ".php", ".cs", ".c", ".h", ".cc", ".cpp", ".hpp", ".m",
    ".swift", ".scala", ".sh", ".bash", ".zsh", ".fish",
    ".html", ".css", ".scss", ".less",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env",
    ".md", ".rst", ".txt", ".sql", ".graphql", ".proto",
}

MAX_READ_BYTES = 5_000_000


# --------------------------------------------------------------------------- #

@dataclass
class FileInfo:
    path: str
    size: int
    is_dir: bool
    is_file: bool
    is_symlink: bool
    modified: float
    mode: int


class Workspace:
    """
    A path-safe view of a project root. Every method takes paths relative
    to `root` (or absolute paths that must still resolve inside `root`).
    """

    def __init__(self, root: str | Path, extra_ignores: Iterable[str] = ()):
        self.root = Path(root).resolve()
        if not self.root.exists():
            self.root.mkdir(parents=True, exist_ok=True)
        self.ignores = set(DEFAULT_IGNORES) | set(extra_ignores)

    # ------------------------------------------------------------------ #
    # Path safety
    # ------------------------------------------------------------------ #

    def _resolve(self, path: str | Path) -> Path:
        p = Path(path)
        if not p.is_absolute():
            p = self.root / p
        p = p.resolve()
        try:
            p.relative_to(self.root)
        except ValueError:
            raise PathTraversal(f"{path!r} escapes workspace {self.root}")
        return p

    def _rel(self, p: Path) -> str:
        return str(p.relative_to(self.root)).replace(os.sep, "/")

    def _is_ignored(self, p: Path) -> bool:
        for part in p.parts:
            if part in self.ignores:
                return True
        return False

    # ------------------------------------------------------------------ #
    # Layer 1 — file system
    # ------------------------------------------------------------------ #

    def list_files(self, path: str = ".", recursive: bool = False,
                   max_entries: int = 5000) -> list[dict]:
        base = self._resolve(path)
        if not base.exists():
            raise NotFound(str(path))
        out: list[dict] = []
        if base.is_file():
            return [{"path": self._rel(base), "type": "file", "size": base.stat().st_size}]
        it = base.rglob("*") if recursive else base.iterdir()
        for entry in it:
            if len(out) >= max_entries:
                break
            if self._is_ignored(entry):
                continue
            try:
                st = entry.stat()
            except OSError:
                continue
            out.append({
                "path": self._rel(entry),
                "type": "dir" if entry.is_dir() else "file",
                "size": st.st_size if entry.is_file() else 0,
            })
        out.sort(key=lambda d: (d["type"] != "dir", d["path"]))
        return out

    def read_file(self, path: str, encoding: str = "utf-8",
                  start_line: Optional[int] = None,
                  end_line: Optional[int] = None) -> dict:
        p = self._resolve(path)
        if not p.is_file():
            raise NotFound(str(path))
        if p.stat().st_size > MAX_READ_BYTES:
            raise WorkspaceError(f"File too large: {p.stat().st_size} bytes")
        text = p.read_text(encoding=encoding, errors="replace")
        lines = text.splitlines()
        total = len(lines)
        if start_line is not None or end_line is not None:
            s = max(1, start_line or 1) - 1
            e = min(total, end_line or total)
            lines = lines[s:e]
        return {
            "path": self._rel(p),
            "content": "\n".join(lines),
            "lines": len(lines),
            "total_lines": total,
            "encoding": encoding,
        }

    def write_file(self, path: str, content: str, encoding: str = "utf-8",
                   create_dirs: bool = True) -> dict:
        p = self._resolve(path)
        if create_dirs:
            p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding=encoding)
        return {"path": self._rel(p), "bytes": len(content.encode(encoding))}

    def append_file(self, path: str, content: str) -> dict:
        p = self._resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(content)
        return {"path": self._rel(p), "appended": len(content)}

    def edit_file(self, path: str, old_text: str, new_text: str,
                  count: int = 1) -> dict:
        p = self._resolve(path)
        text = p.read_text(encoding="utf-8")
        occurrences = text.count(old_text)
        if occurrences == 0:
            raise NotFound(f"old_text not found in {path}")
        if occurrences > 1 and count == 1:
            raise WorkspaceError(
                f"old_text occurs {occurrences} times in {path}; be more specific "
                f"or set count=-1 to replace all"
            )
        replaced = text.replace(old_text, new_text, count if count >= 0 else occurrences)
        p.write_text(replaced, encoding="utf-8")
        return {"path": self._rel(p), "replaced": occurrences if count < 0 else count}

    def delete_file(self, path: str) -> dict:
        p = self._resolve(path)
        if not p.exists():
            raise NotFound(str(path))
        if p.is_dir():
            raise WorkspaceError(f"{path} is a directory; use delete_directory")
        p.unlink()
        return {"deleted": self._rel(p)}

    def move_file(self, source: str, destination: str) -> dict:
        s, d = self._resolve(source), self._resolve(destination)
        if not s.exists():
            raise NotFound(source)
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(s), str(d))
        return {"from": self._rel(s), "to": self._rel(d)}

    def copy_file(self, source: str, destination: str) -> dict:
        s, d = self._resolve(source), self._resolve(destination)
        d.parent.mkdir(parents=True, exist_ok=True)
        if s.is_dir():
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)
        return {"from": self._rel(s), "to": self._rel(d)}

    def create_directory(self, path: str) -> dict:
        p = self._resolve(path)
        p.mkdir(parents=True, exist_ok=True)
        return {"created": self._rel(p)}

    def delete_directory(self, path: str, recursive: bool = True) -> dict:
        p = self._resolve(path)
        if p == self.root:
            raise WorkspaceError("Refusing to delete workspace root")
        if not p.exists():
            raise NotFound(path)
        if recursive:
            shutil.rmtree(p)
        else:
            p.rmdir()
        return {"deleted": self._rel(p)}

    def file_exists(self, path: str) -> bool:
        try:
            return self._resolve(path).exists()
        except PathTraversal:
            return False

    def get_file_info(self, path: str) -> dict:
        p = self._resolve(path)
        if not p.exists():
            raise NotFound(path)
        st = p.stat()
        return {
            "path": self._rel(p),
            "size": st.st_size,
            "is_dir": p.is_dir(),
            "is_file": p.is_file(),
            "is_symlink": p.is_symlink(),
            "modified": st.st_mtime,
            "created": st.st_ctime,
            "mode": oct(st.st_mode),
        }

    # ------------------------------------------------------------------ #
    # Layer 1/2 — search helpers
    # ------------------------------------------------------------------ #

    def find_files(self, name_pattern: str, path: str = ".",
                   max_results: int = 500) -> list[str]:
        base = self._resolve(path)
        out: list[str] = []
        for entry in base.rglob("*"):
            if len(out) >= max_results:
                break
            if self._is_ignored(entry):
                continue
            if fnmatch.fnmatch(entry.name, name_pattern):
                out.append(self._rel(entry))
        return out

    def _rg(self) -> Optional[str]:
        return shutil.which("rg")

    def grep(self, pattern: str, path: str = ".", glob: Optional[str] = None,
             case_sensitive: bool = True, max_results: int = 500,
             context_lines: int = 0) -> list[dict]:
        """Prefer ripgrep; fall back to a Python scan."""
        base = self._resolve(path)
        rg = self._rg()
        if rg:
            args = [
                rg, "--json", "--max-count", str(max_results),
                "-C", str(context_lines),
            ]
            if not case_sensitive:
                args.append("-i")
            if glob:
                args.extend(["-g", glob])
            else:
                for ig in self.ignores:
                    args.extend(["-g", f"!**/{ig}/**"])
            args.extend(["--", pattern, str(base)])
            proc = subprocess.run(args, capture_output=True, text=True)
            out: list[dict] = []
            for line in proc.stdout.splitlines():
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") != "match":
                    continue
                data = ev["data"]
                out.append({
                    "file": self._rel(Path(data["path"]["text"])),
                    "line": data["line_number"],
                    "text": data["lines"]["text"].rstrip("\n"),
                    "submatches": [m["match"]["text"] for m in data.get("submatches", [])],
                })
            return out[:max_results]

        # Fallback
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            rx = re.compile(pattern, flags)
        except re.error as e:
            raise WorkspaceError(f"Invalid regex: {e}")
        results: list[dict] = []
        for f in base.rglob("*"):
            if len(results) >= max_results or self._is_ignored(f) or not f.is_file():
                continue
            if f.suffix.lower() not in TEXT_EXT and f.suffix != "":
                continue
            try:
                for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
                    if rx.search(line):
                        results.append({
                            "file": self._rel(f), "line": i, "text": line,
                        })
                        if len(results) >= max_results:
                            break
            except (OSError, UnicodeDecodeError):
                continue
        return results

    def search_files(self, pattern: str, path: str = ".", **kw) -> list[dict]:
        return self.grep(pattern, path, **kw)

    def replace_all(self, pattern: str, replacement: str, path: str = ".",
                    glob: Optional[str] = None, regex: bool = False,
                    dry_run: bool = False) -> dict:
        hits = self.grep(pattern, path, glob=glob, max_results=10_000)
        files = sorted({h["file"] for h in hits})
        edits = 0
        for rel in files:
            p = self._resolve(rel)
            text = p.read_text(errors="replace")
            if regex:
                new_text, n = re.subn(pattern, replacement, text)
            else:
                n = text.count(pattern)
                new_text = text.replace(pattern, replacement)
            if n:
                edits += n
                if not dry_run:
                    p.write_text(new_text)
        return {"files": len(files), "edits": edits, "dry_run": dry_run}

    # ------------------------------------------------------------------ #
    # Layer 2 — code search (Python AST; regex fallback for others)
    # ------------------------------------------------------------------ #

    def search_code(self, query: str, path: str = ".",
                    case_sensitive: bool = False,
                    max_results: int = 200) -> list[dict]:
        """A friendlier grep that ranks by symbol-likeness."""
        pattern = re.escape(query) if not any(c in query for c in ".*+?[]()") else query
        hits = self.grep(pattern, path, case_sensitive=case_sensitive,
                         max_results=max_results)
        # Rank: whole-word matches first, then by file
        def rank(h: dict) -> tuple:
            whole = re.search(rf"\b{re.escape(query)}\b", h["text"]) is not None
            return (not whole, h["file"], h["line"])
        hits.sort(key=rank)
        return hits[:max_results]

    def find_symbol(self, name: str, path: str = ".",
                    kind: Optional[str] = None) -> list[dict]:
        """Locate Python top-level / nested definitions by name."""
        out: list[dict] = []
        base = self._resolve(path)
        for f in base.rglob("*.py"):
            if self._is_ignored(f):
                continue
            try:
                tree = ast.parse(f.read_text(errors="replace"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if node.name == name:
                        k = ("function" if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                             else "class")
                        if kind and k != kind:
                            continue
                        out.append({
                            "file": self._rel(f),
                            "line": node.lineno,
                            "col": node.col_offset,
                            "kind": k,
                            "name": node.name,
                            "end_line": getattr(node, "end_lineno", node.lineno),
                        })
        return out

    # ------------------------------------------------------------------ #
    # Layer 3 — code understanding (Python)
    # ------------------------------------------------------------------ #

    def get_ast(self, file: str) -> dict:
        p = self._resolve(file)
        if p.suffix != ".py":
            raise WorkspaceError("get_ast currently supports Python only")
        tree = ast.parse(p.read_text(errors="replace"))
        return {"file": self._rel(p), "ast": _ast_to_dict(tree)}

    def get_symbol_tree(self, file: str) -> list[dict]:
        p = self._resolve(file)
        if p.suffix != ".py":
            return []
        tree = ast.parse(p.read_text(errors="replace"))
        return [_symbol_node(n) for n in tree.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]

    def get_function(self, file: str, symbol: str) -> dict:
        p = self._resolve(file)
        src = p.read_text(errors="replace")
        tree = ast.parse(src)
        lines = src.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol:
                return {
                    "file": self._rel(p),
                    "line": node.lineno,
                    "end_line": node.end_lineno,
                    "signature": _signature(node),
                    "docstring": ast.get_docstring(node),
                    "source": "\n".join(lines[node.lineno - 1:node.end_lineno]),
                }
        raise NotFound(f"function {symbol} not found in {file}")

    def get_class(self, file: str, class_name: str) -> dict:
        p = self._resolve(file)
        src = p.read_text(errors="replace")
        tree = ast.parse(src)
        lines = src.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                methods = [
                    {"name": m.name, "line": m.lineno,
                     "signature": _signature(m)}
                    for m in node.body
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                return {
                    "file": self._rel(p),
                    "line": node.lineno,
                    "end_line": node.end_lineno,
                    "bases": [_unparse(b) for b in node.bases],
                    "docstring": ast.get_docstring(node),
                    "methods": methods,
                    "source": "\n".join(lines[node.lineno - 1:node.end_lineno]),
                }
        raise NotFound(f"class {class_name} not found in {file}")

    def get_imports(self, file: str) -> list[dict]:
        p = self._resolve(file)
        tree = ast.parse(p.read_text(errors="replace"))
        out: list[dict] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    out.append({"type": "import", "module": a.name,
                                "alias": a.asname, "line": node.lineno})
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    out.append({"type": "from", "module": node.module,
                                "name": a.name, "alias": a.asname,
                                "line": node.lineno})
        return out

    def get_exports(self, file: str) -> list[str]:
        p = self._resolve(file)
        tree = ast.parse(p.read_text(errors="replace"))
        names: list[str] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not node.name.startswith("_"):
                    names.append(node.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and not t.id.startswith("_"):
                        names.append(t.id)
        return names

    def get_dependencies(self, file: str, depth: int = 1) -> dict:
        p = self._resolve(file)
        if p.suffix != ".py":
            return {"file": self._rel(p), "local": [], "external": []}
        imports = self.get_imports(file)
        local, external = [], []
        for imp in imports:
            mod = imp.get("module") or imp.get("name") or ""
            top = mod.split(".")[0]
            if (self.root / f"{top}.py").exists() or (self.root / top).is_dir():
                local.append(mod)
            else:
                external.append(mod)
        return {"file": self._rel(p), "local": sorted(set(local)),
                "external": sorted(set(external))}

    def get_project_structure(self, max_depth: int = 6) -> dict:
        def walk(d: Path, depth: int) -> list[dict]:
            if depth > max_depth:
                return []
            out = []
            try:
                entries = sorted(d.iterdir(), key=lambda x: (x.is_file(), x.name))
            except OSError:
                return []
            for e in entries:
                if self._is_ignored(e):
                    continue
                rel = self._rel(e)
                if e.is_dir():
                    out.append({"path": rel, "type": "dir",
                                "children": walk(e, depth + 1)})
                else:
                    out.append({"path": rel, "type": "file",
                                "size": e.stat().st_size})
            return out
        return {"root": str(self.root), "tree": walk(self.root, 0)}

    def get_module_graph(self, max_files: int = 500) -> dict:
        nodes: list[dict] = []
        edges: list[dict] = []
        for f in self.root.rglob("*.py"):
            if self._is_ignored(f) or len(nodes) >= max_files:
                continue
            rel = self._rel(f)
            mod = rel[:-3].replace("/", ".")
            nodes.append({"id": mod, "path": rel})
            try:
                imports = self.get_imports(rel)
            except (SyntaxError, OSError):
                continue
            for imp in imports:
                target = imp.get("module") or ""
                if target:
                    edges.append({"from": mod, "to": target})
        return {"nodes": nodes, "edges": edges}


# --------------------------------------------------------------------------- #
# AST helpers
# --------------------------------------------------------------------------- #

def _ast_to_dict(node: ast.AST) -> dict:
    if isinstance(node, ast.AST):
        return {
            "_type": type(node).__name__,
            **{k: _ast_to_dict(v) for k, v in ast.iter_fields(node)},
        }
    if isinstance(node, list):
        return [_ast_to_dict(x) for x in node]
    return node


def _signature(node) -> str:
    args = node.args
    parts: list[str] = []
    for a in args.posonlyargs:
        parts.append(a.arg)
    if args.posonlyargs:
        parts.append("/")
    for a in args.args:
        parts.append(a.arg)
    if args.vararg:
        parts.append("*" + args.vararg.arg)
    if args.kwonlyargs:
        if not args.vararg:
            parts.append("*")
        for a in args.kwonlyargs:
            parts.append(a.arg)
    if args.kwarg:
        parts.append("**" + args.kwarg.arg)
    prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
    return f"{prefix}{node.name}({', '.join(parts)})"


def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return "?"


def _symbol_node(node) -> dict:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return {
            "kind": "function",
            "name": node.name,
            "line": node.lineno,
            "end_line": node.end_lineno,
            "signature": _signature(node),
            "docstring": ast.get_docstring(node),
        }
    if isinstance(node, ast.ClassDef):
        return {
            "kind": "class",
            "name": node.name,
            "line": node.lineno,
            "end_line": node.end_lineno,
            "bases": [_unparse(b) for b in node.bases],
            "methods": [
                _symbol_node(m) for m in node.body
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            ],
            "docstring": ast.get_docstring(node),
        }
    return {"kind": "unknown", "name": getattr(node, "name", None)}