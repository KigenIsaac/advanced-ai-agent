"""
editor.py — structured edits with preview/apply/revert.

Layer 6  Code editing
"""
from __future__ import annotations

import difflib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .workspace import Workspace, WorkspaceError, NotFound


class PatchError(Exception): ...


@dataclass
class Patch:
    file: str
    old_text: str
    new_text: str
    count: int = 1
    applied: bool = False
    ts: float = field(default_factory=time.time)


class Editor:
    """High-level structured editing on top of Workspace."""

    def __init__(self, workspace: Workspace):
        self.ws = workspace
        self._history: list[Patch] = []
        self._undo: list[tuple[str, str, str]] = []  # (file, before, after)

    # ------------------------------------------------------------------ #
    # Preview / apply / revert
    # ------------------------------------------------------------------ #

    def preview_patch(self, file: str, old_text: str, new_text: str,
                      count: int = 1) -> str:
        info = self.ws.read_file(file)
        content = info["content"]
        if old_text not in content:
            raise PatchError(f"old_text not found in {file}")
        new_content = content.replace(old_text, new_text,
                                      count if count >= 0 else content.count(old_text))
        return "".join(difflib.unified_diff(
            content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{file}", tofile=f"b/{file}",
        ))

    def apply_patch(self, file: str, old_text: str, new_text: str,
                    count: int = 1) -> dict:
        info = self.ws.read_file(file)
        before = info["content"]
        if old_text not in before:
            raise PatchError(f"old_text not found in {file}")
        self.ws.edit_file(file, old_text, new_text, count=count)
        after = self.ws.read_file(file)["content"]
        self._history.append(Patch(file, old_text, new_text, count, applied=True))
        self._undo.append((file, before, after))
        return {"file": file, "applied": True}

    def revert_last(self) -> Optional[dict]:
        if not self._undo:
            return None
        file, before, _ = self._undo.pop()
        self.ws.write_file(file, before)
        return {"reverted": file}

    def revert_patch(self, index: int) -> dict:
        if index < 0 or index >= len(self._undo):
            raise PatchError(f"No patch at index {index}")
        file, before, _ = self._undo[index]
        self.ws.write_file(file, before)
        del self._undo[index]
        return {"reverted": file}

    def history(self) -> list[dict]:
        return [{"file": p.file, "applied": p.applied, "ts": p.ts}
                for p in self._history]

    # ------------------------------------------------------------------ #
    # Line / range operations
    # ------------------------------------------------------------------ #

    def replace_range(self, file: str, start_line: int, end_line: int,
                      new_content: str) -> dict:
        info = self.ws.read_file(file)
        lines = info["content"].splitlines()
        if start_line < 1 or end_line > len(lines) or start_line > end_line + 1:
            raise PatchError(f"Invalid range {start_line}-{end_line} "
                             f"(file has {len(lines)} lines)")
        before = "\n".join(lines)
        new_lines = lines[:start_line - 1] + new_content.splitlines() + lines[end_line:]
        after = "\n".join(new_lines)
        self.ws.write_file(file, after + ("\n" if info["content"].endswith("\n") else ""))
        self._undo.append((file, before, after))
        return {"file": file, "replaced_lines": end_line - start_line + 1}

    def insert_at_line(self, file: str, line: int, content: str) -> dict:
        return self.replace_range(file, line, line - 1, content)

    def delete_range(self, file: str, start_line: int, end_line: int) -> dict:
        return self.replace_range(file, start_line, end_line, "")

    # ------------------------------------------------------------------ #
    # Bulk write (use sparingly)
    # ------------------------------------------------------------------ #

    def rewrite_file(self, file: str, content: str) -> dict:
        before = self.ws.read_file(file)["content"] if self.ws.file_exists(file) else ""
        self.ws.write_file(file, content)
        self._undo.append((file, before, content))
        return {"file": file, "bytes": len(content)}