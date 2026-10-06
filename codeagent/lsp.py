"""
lsp.py — Minimal Language Server Protocol client.

Layer 27 Language Server Protocol

Currently supports servers that speak LSP over stdio with the standard
Content-Length framing. Tested with pylsp and pyright.

The methods below are intentionally thin: they translate to LSP requests
and return the raw response. Higher-level shaping is the caller's job.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from .workspace import Workspace


class LSPError(Exception): ...


class LSPClient:
    def __init__(self, workspace: Workspace, server_command: list[str],
                 language_id: str = "python"):
        self.ws = workspace
        self.server_command = server_command
        self.language_id = language_id
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._msg_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._initialized = False

    # ------------------------------------------------------------------ #
    # Server discovery
    # ------------------------------------------------------------------ #

    @classmethod
    def auto(cls, workspace: Workspace) -> Optional["LSPClient"]:
        if shutil.which("pylsp"):
            return cls(workspace, ["pylsp"], "python")
        if shutil.which("pyright-langserver"):
            return cls(workspace, ["pyright-langserver", "--stdio"], "python")
        if shutil.which("typescript-language-server"):
            return cls(workspace, ["typescript-language-server", "--stdio"], "typescript")
        if shutil.which("gopls"):
            return cls(workspace, ["gopls"], "go")
        if shutil.which("rust-analyzer"):
            return cls(workspace, ["rust-analyzer"], "rust")
        return None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self):
        self._proc = await asyncio.create_subprocess_exec(
            *self.server_command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.ws.root),
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        await self._request("initialize", {
            "processId": os.getpid(),
            "rootUri": self.ws.root.as_uri(),
            "capabilities": {
                "textDocument": {
                    "definition": {"linkSupport": True},
                    "references": {},
                    "hover": {},
                    "documentSymbol": {},
                    "rename": {"prepareSupport": True},
                    "diagnostic": {},
                },
                "workspace": {"symbol": {}},
            },
        })
        self._notify("initialized", {})
        self._initialized = True

    async def stop(self):
        if self._reader_task:
            self._reader_task.cancel()
        if self._proc:
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass

    # ------------------------------------------------------------------ #
    # Framing
    # ------------------------------------------------------------------ #

    async def _read_loop(self):
        assert self._proc and self._proc.stdout
        reader = self._proc.stdout
        while True:
            headers = {}
            while True:
                line = await reader.readline()
                if not line:
                    return
                if line in (b"\r\n", b"\n"):
                    break
                k, _, v = line.decode().partition(":")
                headers[k.strip().lower()] = v.strip()
            length = int(headers.get("content-length", 0))
            body = await reader.readexactly(length)
            msg = json.loads(body)
            if "id" in msg and msg["id"] in self._pending:
                fut = self._pending.pop(msg["id"])
                if "error" in msg:
                    fut.set_exception(LSPError(str(msg["error"])))
                else:
                    fut.set_result(msg.get("result"))

    async def _send(self, msg: dict):
        assert self._proc and self._proc.stdin
        body = json.dumps(msg).encode()
        header = f"Content-Length: {len(body)}\r\n\r\n".encode()
        self._proc.stdin.write(header + body)
        await self._proc.stdin.drain()

    async def _request(self, method: str, params: Any) -> Any:
        self._msg_id += 1
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[self._msg_id] = fut
        await self._send({"jsonrpc": "2.0", "id": self._msg_id,
                          "method": method, "params": params})
        return await asyncio.wait_for(fut, timeout=30)

    async def _notify(self, method: str, params: Any):
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    # ------------------------------------------------------------------ #
    # File sync
    # ------------------------------------------------------------------ #

    def _uri(self, file: str) -> str:
        return self.ws._resolve(file).as_uri()

    async def open_document(self, file: str):
        text = self.ws.read_file(file)["content"]
        await self._notify("textDocument/didOpen", {
            "textDocument": {
                "uri": self._uri(file),
                "languageId": self.language_id,
                "version": 1,
                "text": text,
            }
        })

    # ------------------------------------------------------------------ #
    # LSP methods
    # ------------------------------------------------------------------ #

    async def definition(self, file: str, line: int, col: int) -> list[dict]:
        await self.open_document(file)
        result = await self._request("textDocument/definition", {
            "textDocument": {"uri": self._uri(file)},
            "position": {"line": line - 1, "character": col - 1},
        })
        return _normalize_locations(result, self.ws.root)

    async def references(self, file: str, line: int, col: int,
                         include_declaration: bool = True) -> list[dict]:
        await self.open_document(file)
        result = await self._request("textDocument/references", {
            "textDocument": {"uri": self._uri(file)},
            "position": {"line": line - 1, "character": col - 1},
            "context": {"includeDeclaration": include_declaration},
        })
        return _normalize_locations(result, self.ws.root)

    async def hover(self, file: str, line: int, col: int) -> dict:
        await self.open_document(file)
        result = await self._request("textDocument/hover", {
            "textDocument": {"uri": self._uri(file)},
            "position": {"line": line - 1, "character": col - 1},
        })
        if not result:
            return {}
        contents = result.get("contents")
        if isinstance(contents, dict):
            return {"text": contents.get("value", "")}
        return {"text": str(contents)}

    async def document_symbols(self, file: str) -> list[dict]:
        await self.open_document(file)
        result = await self._request("textDocument/documentSymbol", {
            "textDocument": {"uri": self._uri(file)},
        })
        return _flatten_symbols(result or [])

    async def workspace_symbols(self, query: str) -> list[dict]:
        result = await self._request("workspace/symbol", {"query": query})
        return _flatten_symbols(result or [])

    async def rename(self, file: str, line: int, col: int,
                     new_name: str) -> dict:
        await self.open_document(file)
        result = await self._request("textDocument/rename", {
            "textDocument": {"uri": self._uri(file)},
            "position": {"line": line - 1, "character": col - 1},
            "newName": new_name,
        })
        return result or {}

    async def diagnostics(self, file: str, wait: float = 2.0) -> list[dict]:
        await self.open_document(file)
        await asyncio.sleep(wait)
        # For pull-diagnostics servers
        try:
            result = await self._request("textDocument/diagnostic", {
                "textDocument": {"uri": self._uri(file)},
            })
        except LSPError:
            return []
        return result.get("items", []) if result else []


def _normalize_locations(result: Any, root: Path) -> list[dict]:
    if not result:
        return []
    items = result if isinstance(result, list) else [result]
    out = []
    for it in items:
        uri = it.get("uri") or it.get("targetUri")
        if not uri:
            continue
        path = uri.replace("file://", "")
        try:
            rel = str(Path(path).relative_to(root))
        except ValueError:
            rel = path
        rng = it.get("range") or it.get("targetSelectionRange") or {}
        start = rng.get("start", {})
        out.append({
            "file": rel,
            "line": start.get("line", 0) + 1,
            "col": start.get("character", 0) + 1,
        })
    return out


def _flatten_symbols(symbols: list[dict], parent: Optional[str] = None) -> list[dict]:
    out = []
    for s in symbols:
        loc = s.get("location") or {}
        rng = s.get("range") or loc.get("range") or {}
        start = rng.get("start", {})
        entry = {
            "name": s.get("name"),
            "kind": s.get("kind"),
            "line": start.get("line", 0) + 1,
            "parent": parent,
        }
        out.append(entry)
        if "children" in s and s["children"]:
            out.extend(_flatten_symbols(s["children"], s.get("name")))
    return out