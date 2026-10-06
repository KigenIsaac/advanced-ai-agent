"""
agent.py — CodeAgent: binds the two-tool web agent + everything from
Layers 1–30 and dispatches the thirteen tool calls.

Usage:
    async with CodeAgent(project_root="./myproj") as agent:
        await agent.workspace(action="read", path="src/main.py")
        await agent.terminal(action="run", command="pytest")
        ...
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .workspace import Workspace, WorkspaceError
from .editor import Editor, PatchError
from .refactor import Refactor
from .shell import Shell, ShellError, ShellTimeout
from .env import Env
from .vcs import Git, GitError, Remote
from .packages import Packages, Build, PackageError
from .debugger import Debugger, ErrorAnalysis, DebugError
from .testing import Testing, StaticAnalysis
from .runtime import Application, BrowserBridge, HTTP, Database
from .knowledge import Documentation, ProjectMemory, Tasks
from .lsp import LSPClient, LSPError
from .sandbox import Sandboxes, SandboxError
from .review import Reviewer
from .deploy import Deployment as Deployer, Monitoring, DeployForbidden


class CodeAgent:
    def __init__(self, project_root: str | Path = ".",
                 web_agent: Optional[Any] = None,
                 secrets: Optional[dict] = None,
                 deploy_authorizer=None,
                 extra_ignores: tuple = ()):
        self.ws = Workspace(project_root, extra_ignores=extra_ignores)
        self.shell = Shell(self.ws)
        self.env = Env(secret_store=secrets)
        self.editor = Editor(self.ws)
        self.refactor = Refactor(self.ws)
        self.git = Git(self.ws)
        self.remote = Remote(self.ws)
        self.packages = Packages(self.ws, self.shell)
        self.build = Build(self.ws, self.shell)
        self.debugger = Debugger(self.ws)
        self.errors = ErrorAnalysis(self.ws)
        self.testing = Testing(self.ws, self.shell)
        self.static = StaticAnalysis(self.ws, self.shell)
        self.app = Application(self.ws, self.shell)
        self.http = HTTP()
        self.db = Database(self.ws, self.shell)
        self.web = web_agent
        self.browser = BrowserBridge(web_agent) if web_agent else None
        self.docs = Documentation(self.ws, web_agent.web_search if web_agent else None)
        self.memory = ProjectMemory(self.ws)
        self.tasks = Tasks(self.ws)
        self.sandboxes = Sandboxes(self.ws)
        self.reviewer = Reviewer(self.ws, self.git)
        self.deployer = Deployer(self.ws, self.shell,
                                 authorization=deploy_authorizer)
        self.monitoring = Monitoring(self.ws, self.shell)
        self._lsp: Optional[LSPClient] = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def close(self):
        await self.shell.close()
        await self.http.close()
        if self._lsp:
            await self._lsp.stop()

    # ------------------------------------------------------------------ #
    # Tool dispatch
    # ------------------------------------------------------------------ #

    async def dispatch(self, tool: str, **kw) -> dict:
        try:
            handler = getattr(self, f"_tool_{tool}", None)
            if handler is None:
                return {"error": "unknown_tool", "tool": tool}
            return await handler(**kw)
        except (WorkspaceError, ShellError, ShellTimeout, GitError,
                PatchError, DebugError, LSPError, SandboxError,
                PackageError, DeployForbidden) as e:
            return {"error": type(e).__name__, "message": str(e), "tool": tool}
        except Exception as e:
            return {"error": "unexpected", "message": str(e), "tool": tool}

    # ------------------------------------------------------------------ #
    # workspace
    # ------------------------------------------------------------------ #

    async def _tool_workspace(self, action: str, **kw) -> dict:
        ws = self.ws
        if action == "list":
            return {"entries": ws.list_files(kw.get("path", "."),
                                             recursive=kw.get("recursive", False))}
        if action == "read":
            return ws.read_file(kw["path"],
                                start_line=kw.get("start_line"),
                                end_line=kw.get("end_line"))
        if action == "write":
            return ws.write_file(kw["path"], kw["content"])
        if action == "append":
            return ws.append_file(kw["path"], kw["content"])
        if action == "edit":
            return ws.edit_file(kw["path"], kw["old_text"], kw["new_text"],
                                count=kw.get("count", 1))
        if action == "delete":
            return ws.delete_file(kw["path"])
        if action == "move":
            return ws.move_file(kw["source"], kw["destination"])
        if action == "copy":
            return ws.copy_file(kw["source"], kw["destination"])
        if action == "mkdir":
            return ws.create_directory(kw["path"])
        if action == "rmdir":
            return ws.delete_directory(kw["path"], recursive=kw.get("recursive", True))
        if action == "exists":
            return {"exists": ws.file_exists(kw["path"])}
        if action == "info":
            return ws.get_file_info(kw["path"])
        if action == "find_files":
            return {"files": ws.find_files(kw["name_pattern"], path=kw.get("path", "."))}
        if action == "grep":
            return {"hits": ws.grep(kw["pattern"], path=kw.get("path", "."),
                                    glob=kw.get("glob"))}
        if action == "replace_all":
            return ws.replace_all(kw["pattern"], kw["new_text"],
                                  path=kw.get("path", "."),
                                  glob=kw.get("glob"),
                                  regex=kw.get("regex", False),
                                  dry_run=kw.get("dry_run", False))
        if action == "search_code":
            return {"hits": ws.search_code(kw["pattern"],
                                           path=kw.get("path", "."))}
        if action == "find_symbol":
            return {"symbols": ws.find_symbol(kw["symbol"],
                                              path=kw.get("path", "."))}
        if action == "ast":
            return ws.get_ast(kw["path"])
        if action == "symbols":
            return {"symbols": ws.get_symbol_tree(kw["path"])}
        if action == "function":
            return ws.get_function(kw["path"], kw["symbol"])
        if action == "class":
            return ws.get_class(kw["path"], kw["class_name"])
        if action == "imports":
            return {"imports": ws.get_imports(kw["path"])}
        if action == "exports":
            return {"exports": ws.get_exports(kw["path"])}
        if action == "dependencies":
            return ws.get_dependencies(kw["path"])
        if action == "project_structure":
            return ws.get_project_structure()
        if action == "module_graph":
            return ws.get_module_graph()
        return {"error": f"unknown workspace action: {action}"}

    # ------------------------------------------------------------------ #
    # terminal
    # ------------------------------------------------------------------ #

    async def _tool_terminal(self, action: str = "run", **kw) -> dict:
        sh = self.shell
        if action == "run":
            return await sh.run(kw["command"], cwd=kw.get("cwd"),
                                timeout=kw.get("timeout"),
                                env=kw.get("env"), stdin=kw.get("stdin"))
        if action == "background":
            pid = await sh.run_background(kw["command"], cwd=kw.get("cwd"),
                                          env=kw.get("env"))
            return {"process_id": pid}
        if action == "list":
            return {"processes": sh.list_processes()}
        if action == "get":
            return sh.get_process(kw["process_id"])
        if action == "kill":
            return await sh.kill_process(kw["process_id"])
        if action == "wait":
            return await sh.wait_process(kw["process_id"],
                                         timeout=kw.get("timeout", 60))
        return {"error": f"unknown terminal action: {action}"}

    # ------------------------------------------------------------------ #
    # code (LSP)
    # ------------------------------------------------------------------ #

    async def _ensure_lsp(self) -> Optional[LSPClient]:
        if self._lsp:
            return self._lsp
        self._lsp = LSPClient.auto(self.ws)
        if self._lsp:
            await self._lsp.start()
        return self._lsp

    async def _tool_code(self, action: str, **kw) -> dict:
        lsp = await self._ensure_lsp()
        if not lsp:
            # Fall back to workspace-level AST tools
            if action == "definition":
                return {"definitions": self.ws.find_symbol(kw.get("query", ""))}
            if action == "symbols":
                return {"symbols": self.ws.get_symbol_tree(kw["file"])}
            return {"error": "no language server available"}
        if action == "definition":
            return {"definitions": await lsp.definition(kw["file"], kw["line"], kw["col"])}
        if action == "references":
            return {"references": await lsp.references(kw["file"], kw["line"], kw["col"])}
        if action == "hover":
            return await lsp.hover(kw["file"], kw["line"], kw["col"])
        if action == "rename":
            return await lsp.rename(kw["file"], kw["line"], kw["col"], kw["new_name"])
        if action == "symbols":
            return {"symbols": await lsp.document_symbols(kw["file"])}
        if action == "workspace_symbols":
            return {"symbols": await lsp.workspace_symbols(kw["query"])}
        if action == "diagnostics":
            return {"diagnostics": await lsp.diagnostics(kw["file"])}
        return {"error": f"unknown code action: {action}"}

    # ------------------------------------------------------------------ #
    # editor
    # ------------------------------------------------------------------ #

    async def _tool_editor(self, action: str, **kw) -> dict:
        e, r = self.editor, self.refactor
        if action == "preview":
            return {"diff": e.preview_patch(kw["file"], kw["old_text"],
                                            kw["new_text"], kw.get("count", 1))}
        if action == "apply":
            return e.apply_patch(kw["file"], kw["old_text"], kw["new_text"],
                                 kw.get("count", 1))
        if action == "replace_range":
            return e.replace_range(kw["file"], kw["start_line"], kw["end_line"],
                                   kw["content"])
        if action == "insert":
            return e.insert_at_line(kw["file"], kw["start_line"], kw["content"])
        if action == "delete_range":
            return e.delete_range(kw["file"], kw["start_line"], kw["end_line"])
        if action == "rewrite":
            return e.rewrite_file(kw["file"], kw["content"])
        if action == "revert_last":
            return e.revert_last() or {"reverted": None}
        if action == "revert_index":
            return e.revert_patch(kw["index"])
        if action == "history":
            return {"history": e.history()}
        if action == "rename_symbol":
            return r.rename_symbol(kw["name"], kw["new_name"],
                                   path=kw.get("file", "."),
                                   dry_run=kw.get("dry_run", False))
        if action == "add_import":
            return r.add_import(kw["file"], kw["module"], kw.get("name"))
        if action == "remove_import":
            return r.remove_import(kw["file"], kw["module"], kw.get("name"))
        if action == "extract_function":
            return r.extract_function(kw["file"], kw["start_line"], kw["end_line"],
                                      kw["name"])
        if action == "inline_function":
            return r.inline_function(kw["file"], kw["name"])
        if action == "split_file":
            return r.split_file(kw["file"], kw["symbols"], kw["target"])
        if action == "remove_dead_code":
            return r.remove_dead_code(kw["file"], kw["name"],
                                      dry_run=kw.get("dry_run", True))
        if action == "simplify":
            return r.simplify_code(kw["file"], dry_run=kw.get("dry_run", True))
        return {"error": f"unknown editor action: {action}"}

    # ------------------------------------------------------------------ #
    # test
    # ------------------------------------------------------------------ #

    async def _tool_test(self, action: str, **kw) -> dict:
        t = self.testing
        if action == "run":
            return await t.run(path=kw.get("path"), test_name=kw.get("test_name"),
                               timeout=kw.get("timeout", 300))
        if action == "run_file":
            return await t.run_file(kw["path"])
        if action == "run_case":
            return await t.run_case(kw["test_name"])
        if action == "discover":
            return {"tests": t.discover(kw.get("path", "."))}
        if action == "coverage":
            return await t.coverage()
        if action == "failures":
            result = await t.run(path=kw.get("path"))
            return {"failures": result.get("failures", [])}
        if action == "generate":
            return t.generate_test(kw["target_file"], kw.get("output_file"))
        return {"error": f"unknown test action: {action}"}

    # ------------------------------------------------------------------ #
    # build
    # ------------------------------------------------------------------ #

    async def _tool_build(self, action: str, **kw) -> dict:
        b, s = self.build, self.static
        if action == "detect":
            return {"system": b.system(),
                    "managers": self.packages.managers()}
        if action == "build":
            return await b.build()
        if action == "clean":
            return await b.clean()
        if action == "typecheck":
            return await b.typecheck()
        if action == "lint":
            return await s.lint(kw.get("path", "."))
        if action == "security":
            return await s.security_scan(kw.get("path", "."))
        if action == "complexity":
            return await s.complexity(kw.get("path", "."))
        if action == "dead_code":
            return s.dead_code(kw.get("path", "."))
        return {"error": f"unknown build action: {action}"}

    # ------------------------------------------------------------------ #
    # git
    # ------------------------------------------------------------------ #

    async def _tool_git(self, action: str, **kw) -> dict:
        g, r = self.git, self.remote
        if action == "status":         return g.status()
        if action == "diff":           return g.diff(staged=kw.get("staged", False),
                                                     path=kw.get("path"))
        if action == "log":            return {"commits": g.log(limit=kw.get("limit", 20),
                                                                path=kw.get("path"))}
        if action == "show":           return g.show(kw["commit"])
        if action == "branch_current": return {"branch": g.current_branch()}
        if action == "branch_list":    return {"branches": g.branches()}
        if action == "branch_create":  return g.create_branch(kw["branch"], kw.get("base"))
        if action == "branch_switch":  return g.switch_branch(kw["branch"])
        if action == "branch_delete":  return g.delete_branch(kw["branch"], kw.get("force", False))
        if action == "add":            return g.add(kw.get("files") or ".")
        if action == "reset":          return g.reset(kw.get("files"))
        if action == "commit":         return g.commit(kw["message"])
        if action == "amend":          return g.amend(kw.get("message"))
        if action == "stash":          return g.stash(kw.get("message"))
        if action == "stash_pop":      return g.stash_pop(kw.get("index", 0))
        if action == "stash_list":     return {"stashes": g.stash_list()}
        if action == "revert":         return g.revert(kw["commit"])
        if action == "reset_hard":     return g.reset_hard(kw.get("commit", "HEAD"))
        if action == "clean":          return g.clean(force=kw.get("force", False))
        if action == "merge":          return g.merge(kw["branch"], no_ff=kw.get("no_ff", False))
        if action == "rebase":         return g.rebase(kw["branch"])
        if action == "cherry_pick":    return g.cherry_pick(kw["commit"])
        if action == "blame":          return {"blame": g.blame(kw["path"])}
        if action == "file_history":   return {"commits": g.file_history(kw["path"])}
        # remote
        if action == "clone":          return r.clone(kw["url"], kw.get("destination"))
        if action == "pull":           return r.pull(branch=kw.get("branch"))
        if action == "push":           return r.push(branch=kw.get("branch"))
        if action == "pr_list":        return {"prs": r.list_prs(limit=kw.get("limit", 20),
                                                                 state=kw.get("state", "open"))}
        if action == "pr_get":         return r.get_pr(kw["number"])
        if action == "pr_diff":        return {"diff": r.get_pr_diff(kw["number"])}
        if action == "pr_create":      return r.create_pr(kw["title"], kw.get("body", ""),
                                                          base=kw.get("base"),
                                                          head=kw.get("head"),
                                                          draft=kw.get("draft", False))
        if action == "pr_update":      return r.update_pr(kw["number"], kw.get("title"),
                                                          kw.get("body"))
        if action == "issue_list":     return {"issues": r.list_issues(limit=kw.get("limit", 20),
                                                                      state=kw.get("state", "open"))}
        if action == "issue_get":      return r.get_issue(kw["number"])
        if action == "issue_create":   return r.create_issue(kw["title"], kw.get("body", ""),
                                                             kw.get("labels"))
        if action == "issue_comment":  return r.comment_issue(kw["number"], kw["body"])
        return {"error": f"unknown git action: {action}"}

    # ------------------------------------------------------------------ #
    # packages
    # ------------------------------------------------------------------ #

    async def _tool_packages(self, action: str, **kw) -> dict:
        p = self.packages
        if action == "detect":  return {"managers": p.managers(),
                                        "build_system": p.build_system()}
        if action == "list":    return await p.list_dependencies()
        if action == "install": return await p.install(kw["package"],
                                                       manager=kw.get("manager"),
                                                       dev=kw.get("dev", False))
        if action == "remove":  return await p.remove(kw["package"], manager=kw.get("manager"))
        if action == "update":  return await p.update(kw.get("package"),
                                                      manager=kw.get("manager"))
        if action == "audit":   return await p.audit(kw.get("manager"))
        return {"error": f"unknown packages action: {action}"}

    # ------------------------------------------------------------------ #
    # debug
    # ------------------------------------------------------------------ #

    async def _tool_debug(self, action: str, **kw) -> dict:
        d, e = self.debugger, self.errors
        if action == "start":
            return await d.run_under_debugger(kw["script"],
                                              args=kw.get("args"),
                                              breakpoints=kw.get("breakpoints"),
                                              timeout=kw.get("timeout", 60))
        if action == "set_breakpoint":
            return d.set_breakpoint(kw["file"], kw["line"])
        if action == "remove_breakpoint":
            return d.remove_breakpoint(kw["file"], kw["line"])
        if action == "list_breakpoints":
            return d.list_breakpoints()
        if action == "parse_trace":
            return e.parse_stack_trace(kw["output"])
        if action == "explain_error":
            return e.explain_compiler_error(kw["output"])
        if action == "last_error":
            return e.get_last_error() or {}
        if action == "find_source":
            return e.find_error_source(e.parse_stack_trace(kw["output"])) or {}
        if action == "context":
            source = {"file": kw["file"], "line": kw["line"]}
            return e.find_related_code(source)
        return {"error": f"unknown debug action: {action}"}

    # ------------------------------------------------------------------ #
    # browser
    # ------------------------------------------------------------------ #

    async def _tool_browser(self, action: str, **kw) -> dict:
        if not self.web:
            return {"error": "no browser engine attached"}
        return await self.web.web_browser(action=action, **kw)

    # ------------------------------------------------------------------ #
    # http
    # ------------------------------------------------------------------ #

    async def _tool_http(self, action: str = "request", **kw) -> dict:
        h = self.http
        if action == "request":
            return await h.request(
                kw.get("method", "GET"), kw["url"],
                headers=kw.get("headers"), params=kw.get("params"),
                body=kw.get("body"), json_body=kw.get("json_body"),
            )
        if action == "test":
            return await h.test_endpoint(
                kw.get("method", "GET"), kw["url"],
                expected_status=kw.get("expected_status"),
                headers=kw.get("headers"), params=kw.get("params"),
                body=kw.get("body"), json_body=kw.get("json_body"),
            )
        if action == "compare":
            return await h.compare(kw["url"], kw["url_b"], method=kw.get("method", "GET"))
        return {"error": f"unknown http action: {action}"}

    # ------------------------------------------------------------------ #
    # database
    # ------------------------------------------------------------------ #

    async def _tool_database(self, action: str, **kw) -> dict:
        db = self.db
        if action == "detect":         return {"engines": db.detect()}
        if action == "connect_sqlite": db.connect_sqlite(kw["path"]); return {"ok": True}
        if action == "tables":         return {"tables": db.list_tables()}
        if action == "describe":       return {"columns": db.describe_table(kw["table"])}
        if action == "query":          return db.execute_query(kw["query"],
                                                                readonly=kw.get("readonly", True),
                                                                limit=kw.get("limit", 1000))
        if action == "schema":         return db.get_schema()
        if action == "migrations":     return {"migrations": db.find_migrations()}
        if action == "find_usage":     return {"hits": db.find_database_usage(kw["model"])}
        return {"error": f"unknown database action: {action}"}

    # ------------------------------------------------------------------ #
    # project
    # ------------------------------------------------------------------ #

    async def _tool_project(self, action: str, **kw) -> dict:
        m, t = self.memory, self.tasks
        if action == "context":        return m.get_project_context()
        if action == "instructions":   return {"instructions": m.get_project_instructions()}
        if action == "architecture":   return m.get_architecture() or {}
        if action == "conventions":    return {"conventions": m.get_conventions()}
        if action == "recent_changes": return {"changes": m.get_recent_changes(
            limit=kw.get("limit", 10))}
        if action == "note_save":      return m.save(kw["key"], kw["value"],
                                                     kind=kw.get("kind", "note"))
        if action == "note_read":      return m.read(kw["key"]) or {}
        if action == "note_update":    return m.update(kw["key"], kw["value"])
        if action == "note_delete":    return {"deleted": m.delete(kw["key"])}
        if action == "note_list":      return {"notes": m.list(kind=kw.get("kind"))}
        if action == "task_create":    return t.create(kw["title"], kw.get("description", ""),
                                                       parent=kw.get("parent"))
        if action == "task_get":       return t.get(kw["task_id"]) or {}
        if action == "task_list":      return {"tasks": t.list(status=kw.get("status"))}
        if action == "task_update":    return t.update(kw["task_id"], **{
            k: v for k, v in kw.items()
            if k in ("title", "description", "status", "result")
        }) or {}
        if action == "task_complete":  return t.complete(kw["task_id"], kw.get("result")) or {}
        if action == "task_cancel":    return t.cancel(kw["task_id"]) or {}
        return {"error": f"unknown project action: {action}"}

    # ------------------------------------------------------------------ #
    # review
    # ------------------------------------------------------------------ #

    async def _tool_review(self, scope: str, **kw) -> dict:
        r = self.reviewer
        if scope == "file":
            return r.review_file(kw["target"], checks=kw.get("checks"))
        if scope == "diff":
            return r.review_changes(ref=kw.get("ref", "HEAD"),
                                    checks=kw.get("checks"))
        if scope == "pr":
            return r.review_pull_request(kw["number"], checks=kw.get("checks"))
        return {"error": f"unknown review scope: {scope}"}

    # ------------------------------------------------------------------ #
    # sandbox
    # ------------------------------------------------------------------ #

    async def _tool_sandbox(self, action: str, **kw) -> dict:
        s = self.sandboxes
        if action == "create":
            return s.create(backend=kw.get("backend", "subprocess"),
                            image=kw.get("image"))
        if action == "destroy":
            return await s.destroy(kw["sandbox_id"])
        if action == "list":
            return {"sandboxes": s.list()}
        if action == "exec":
            return await s.exec(kw["sandbox_id"], kw["command"],
                                cwd=kw.get("cwd"),
                                timeout=kw.get("timeout", 120))
        if action == "snapshot":
            return s.snapshot(kw["sandbox_id"])
        if action == "restore":
            return s.restore(kw["sandbox_id"], kw["snapshot"])
        return {"error": f"unknown sandbox action: {action}"}

    # ------------------------------------------------------------------ #
    # deploy
    # ------------------------------------------------------------------ #

    async def _tool_deploy(self, action: str, **kw) -> dict:
        d, m = self.deployer, self.monitoring
        if action == "detect":  return {"providers": d.detect()}
        if action == "build":   return await d.build()
        if action == "deploy":  return await d.deploy(provider=kw.get("provider"),
                                                      environment=kw.get("environment", "staging"))
        if action == "status":  return await d.status(kw.get("provider"))
        if action == "logs":    return await d.logs(kw.get("provider"))
        if action == "rollback": return await d.rollback(kw.get("provider"),
                                                         kw.get("target"))
        if action == "health":  return await m.health(kw["url"])
        if action == "metrics": return await m.metrics(kw["url"])
        return {"error": f"unknown deploy action: {action}"}