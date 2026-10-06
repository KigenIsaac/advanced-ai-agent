"""
refactor.py — AST-aware refactoring.

Python-only for the structural operations; the text-level fallbacks work
across languages. When libcst is installed, Python edits preserve formatting.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Optional

from .workspace import Workspace


try:
    import libcst as cst
    _HAS_CST = True
except ImportError:
    _HAS_CST = False


class Refactor:
    def __init__(self, workspace: Workspace):
        self.ws = workspace

    # ------------------------------------------------------------------ #
    # Rename
    # ------------------------------------------------------------------ #

    def rename_symbol(self, old: str, new: str, path: str = ".",
                      dry_run: bool = False) -> dict:
        """
        Rename a Python symbol project-wide.

        Strategy:
          1. Use libcst if available (preserves formatting).
          2. Fall back to a token-level regex rename for Python files.
        """
        pattern = re.compile(rf"\b{re.escape(old)}\b")
        base = self.ws._resolve(path)
        changes: list[dict] = []
        for f in base.rglob("*.py"):
            if self.ws._is_ignored(f):
                continue
            src = f.read_text(errors="replace")
            if old not in src:
                continue
            if _HAS_CST:
                try:
                    new_src = self._cst_rename(src, old, new)
                except Exception:
                    new_src = pattern.sub(new, src)
            else:
                new_src = pattern.sub(new, src)
            if new_src != src:
                n = len(pattern.findall(src))
                changes.append({"file": self.ws._rel(f), "replacements": n})
                if not dry_run:
                    f.write_text(new_src)
        return {"symbol": old, "to": new, "files": len(changes),
                "changes": changes, "dry_run": dry_run}

    @staticmethod
    def _cst_rename(src: str, old: str, new: str) -> str:
        class Renamer(cst.CSTTransformer):
            def leave_Name(self, orig, updated):
                if updated.value == old:
                    return updated.with_changes(value=new)
                return updated
            def leave_Attribute(self, orig, updated):
                if updated.attr.value == old:
                    return updated.with_changes(attr=updated.attr.with_changes(value=new))
                return updated
        tree = cst.parse_module(src)
        return tree.visit(Renamer()).code

    # ------------------------------------------------------------------ #
    # Extract / inline
    # ------------------------------------------------------------------ #

    def extract_function(self, file: str, start_line: int, end_line: int,
                         name: str) -> dict:
        """Extract lines [start_line, end_line] into a new function above them."""
        info = self.ws.read_file(file)
        lines = info["content"].splitlines()
        body = lines[start_line - 1:end_line]
        indent = len(body[0]) - len(body[0].lstrip())
        pad = " " * indent

        # Dedent body one level (assume it's inside an existing block)
        dedented = []
        for line in body:
            dedented.append(line[indent:] if line.startswith(pad) else line)

        fn_lines = [f"{pad}def {name}():"]
        for line in dedented:
            fn_lines.append(f"{pad}    {line}" if line.strip() else "")
        fn_lines.append("")
        fn_lines.append(f"{pad}{name}()")

        new_lines = lines[:start_line - 1] + fn_lines + lines[end_line:]
        self.ws.write_file(file, "\n".join(new_lines) + "\n")
        return {"file": file, "extracted": name, "lines": end_line - start_line + 1}

    def inline_function(self, file: str, name: str) -> dict:
        """Very conservative: replace `name()` calls with the function body."""
        src = self.ws.read_file(file)["content"]
        tree = ast.parse(src)
        target = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                target = node
                break
        if not target or len(target.body) != 1 or not isinstance(target.body[0], ast.Return):
            return {"inlined": False, "reason": "Function too complex to inline safely"}
        expr = ast.unparse(target.body[0].value)
        new_src = re.sub(rf"\b{re.escape(name)}\(\)", f"({expr})", src)
        self.ws.write_file(file, new_src)
        return {"inlined": True, "file": file}

    # ------------------------------------------------------------------ #
    # Imports
    # ------------------------------------------------------------------ #

    def add_import(self, file: str, module: str, name: Optional[str] = None,
                   alias: Optional[str] = None) -> dict:
        src = self.ws.read_file(file)["content"]
        tree = ast.parse(src)

        if name:
            stmt = f"from {module} import {name}"
        else:
            stmt = f"import {module}"
        if alias:
            stmt += f" as {alias}"

        if re.search(rf"^{re.escape(stmt)}\s*$", src, re.M):
            return {"added": False, "reason": "already imported"}

        lines = src.splitlines()
        # After any module docstring and __future__ imports
        insert_at = 0
        if tree.body and isinstance(tree.body[0], ast.Expr) and \
                isinstance(tree.body[0].value, ast.Constant) and \
                isinstance(tree.body[0].value.value, str):
            insert_at = tree.body[0].end_lineno or 0
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                insert_at = max(insert_at, node.end_lineno or 0)

        lines.insert(insert_at, stmt)
        self.ws.write_file(file, "\n".join(lines) + ("\n" if src.endswith("\n") else ""))
        return {"added": True, "statement": stmt}

    def remove_import(self, file: str, module: str,
                      name: Optional[str] = None) -> dict:
        src = self.ws.read_file(file)["content"]
        lines = src.splitlines()
        target_re = (re.compile(rf"^from\s+{re.escape(module)}\s+import\s+.*\b{re.escape(name)}\b")
                     if name else
                     re.compile(rf"^import\s+{re.escape(module)}\b"))
        kept = [l for l in lines if not target_re.match(l)]
        if len(kept) == len(lines):
            return {"removed": False}
        self.ws.write_file(file, "\n".join(kept) + ("\n" if src.endswith("\n") else ""))
        return {"removed": True, "lines_removed": len(lines) - len(kept)}

    # ------------------------------------------------------------------ #
    # Files
    # ------------------------------------------------------------------ #

    def split_file(self, file: str, symbol_names: list[str],
                   target: str) -> dict:
        """Move the given top-level symbols from `file` into `target`."""
        src = self.ws.read_file(file)["content"]
        tree = ast.parse(src)
        lines = src.splitlines()

        moved: list[str] = []
        removed_ranges: list[tuple[int, int]] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                    and node.name in symbol_names:
                s, e = node.lineno - 1, node.end_lineno
                moved.append("\n".join(lines[s:e]))
                removed_ranges.append((s, e))

        if not moved:
            return {"moved": 0}

        # Write target
        existing = self.ws.read_file(target)["content"] if self.ws.file_exists(target) else ""
        self.ws.write_file(target, (existing + "\n\n" if existing else "") + "\n\n".join(moved))

        # Remove from source, descending
        new_lines = lines[:]
        for s, e in sorted(removed_ranges, reverse=True):
            del new_lines[s:e]
        self.ws.write_file(file, "\n".join(new_lines) + "\n")
        return {"moved": len(moved), "to": target, "symbols": symbol_names}

    # ------------------------------------------------------------------ #
    # Cleanup
    # ------------------------------------------------------------------ #

    def remove_dead_code(self, file: str, symbol: str, dry_run: bool = True) -> dict:
        """Remove a top-level symbol if nothing in the project references it."""
        hits = self.ws.search_code(symbol)
        external = [h for h in hits if h["file"] != file]
        if external:
            return {"removed": False, "references": len(external)}

        src = self.ws.read_file(file)["content"]
        tree = ast.parse(src)
        lines = src.splitlines()
        for node in tree.body:
            if getattr(node, "name", None) == symbol:
                s, e = node.lineno - 1, node.end_lineno
                new_lines = lines[:s] + lines[e:]
                if not dry_run:
                    self.ws.write_file(file, "\n".join(new_lines) + "\n")
                return {"removed": True, "lines": e - s, "dry_run": dry_run}
        return {"removed": False}

    def simplify_code(self, file: str, dry_run: bool = True) -> dict:
        """Run ruff --fix if available; otherwise no-op."""
        import shutil, subprocess
        if not shutil.which("ruff"):
            return {"simplified": False, "reason": "ruff not installed"}
        args = ["ruff", "check", "--fix", "--silent", self.ws._resolve(file).__str__()]
        if dry_run:
            args.insert(2, "--diff")
        r = subprocess.run(args, capture_output=True, text=True)
        return {"simplified": r.returncode == 0, "output": r.stdout[:2000], "dry_run": dry_run}