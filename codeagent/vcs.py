"""
vcs.py — Git and remote repository operations.

Layer 7  Git
Layer 8  GitHub / GitLab
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .workspace import Workspace


class GitError(Exception): ...


class Git:
    def __init__(self, workspace: Workspace):
        self.ws = workspace

    def _git(self, *args: str, check: bool = True, timeout: int = 60) -> dict:
        if not shutil.which("git"):
            raise GitError("git is not installed")
        r = subprocess.run(
            ["git", *args],
            cwd=str(self.ws.root),
            capture_output=True, text=True, timeout=timeout,
        )
        if check and r.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
        return {"stdout": r.stdout, "stderr": r.stderr, "code": r.returncode}

    def is_repo(self) -> bool:
        r = self._git("rev-parse", "--is-inside-work-tree", check=False)
        return r["code"] == 0 and r["stdout"].strip() == "true"

    def init(self) -> dict:
        return self._git("init")

    # ------------------------------------------------------------------ #
    # Status / diff / log
    # ------------------------------------------------------------------ #

    def status(self, porcelain: bool = True) -> dict:
        args = ["status", "--porcelain=v2", "--branch"] if porcelain else ["status"]
        r = self._git(*args)
        return {"raw": r["stdout"]}

    def diff(self, staged: bool = False, path: Optional[str] = None,
             stat: bool = False) -> dict:
        args = ["diff"]
        if staged:
            args.append("--cached")
        if stat:
            args.append("--stat")
        if path:
            args += ["--", path]
        return {"diff": self._git(*args)["stdout"]}

    def log(self, limit: int = 20, path: Optional[str] = None,
            oneline: bool = True) -> list[dict]:
        fmt = "%H%x09%an%x09%ae%x09%aI%x09%s"
        args = ["log", f"-{limit}", f"--pretty=format:{fmt}"]
        if path:
            args += ["--", path]
        r = self._git(*args)
        out = []
        for line in r["stdout"].splitlines():
            parts = line.split("\t")
            if len(parts) == 5:
                out.append({"hash": parts[0], "author": parts[1],
                            "email": parts[2], "date": parts[3],
                            "subject": parts[4]})
        return out

    def show(self, commit: str) -> dict:
        return {"show": self._git("show", commit)["stdout"]}

    # ------------------------------------------------------------------ #
    # Branches
    # ------------------------------------------------------------------ #

    def current_branch(self) -> str:
        return self._git("rev-parse", "--abbrev-ref", "HEAD")["stdout"].strip()

    def branches(self, remote: bool = False) -> list[str]:
        args = ["branch", "-a"] if remote else ["branch"]
        out = self._git(*args)["stdout"]
        return [b.strip().lstrip("* ").strip() for b in out.splitlines() if b.strip()]

    def create_branch(self, name: str, base: Optional[str] = None) -> dict:
        args = ["checkout", "-b", name]
        if base:
            args.append(base)
        return self._git(*args)

    def switch_branch(self, name: str) -> dict:
        return self._git("checkout", name)

    def delete_branch(self, name: str, force: bool = False) -> dict:
        return self._git("branch", "-D" if force else "-d", name)

    # ------------------------------------------------------------------ #
    # Staging / committing
    # ------------------------------------------------------------------ #

    def add(self, files: list[str] | str = ".") -> dict:
        if isinstance(files, str):
            files = [files]
        return self._git("add", *files)

    def reset(self, files: Optional[list[str]] = None) -> dict:
        args = ["reset"] + (files or [])
        return self._git(*args)

    def commit(self, message: str, add_all: bool = True) -> dict:
        if add_all:
            self.add(".")
        return self._git("commit", "-m", message)

    def amend(self, message: Optional[str] = None) -> dict:
        args = ["commit", "--amend"]
        if message:
            args += ["-m", message]
        else:
            args.append("--no-edit")
        return self._git(*args)

    # ------------------------------------------------------------------ #
    # Stash / revert / reset
    # ------------------------------------------------------------------ #

    def stash(self, message: Optional[str] = None) -> dict:
        args = ["stash"]
        if message:
            args += ["push", "-m", message]
        return self._git(*args)

    def stash_pop(self, index: int = 0) -> dict:
        return self._git("stash", "pop", f"stash@{{{index}}}")

    def stash_list(self) -> list[dict]:
        r = self._git("stash", "list")
        return [{"entry": l} for l in r["stdout"].splitlines()]

    def revert(self, commit: str, no_commit: bool = False) -> dict:
        args = ["revert", "--no-edit"]
        if no_commit:
            args.append("--no-commit")
        args.append(commit)
        return self._git(*args)

    def reset_hard(self, ref: str = "HEAD") -> dict:
        return self._git("reset", "--hard", ref)

    def clean(self, force: bool = False, directories: bool = True) -> dict:
        args = ["clean", "-f"]
        if directories:
            args.append("-d")
        if force:
            args.append("-x")
        return self._git(*args)

    # ------------------------------------------------------------------ #
    # Merge / rebase / cherry-pick
    # ------------------------------------------------------------------ #

    def merge(self, branch: str, no_ff: bool = False) -> dict:
        args = ["merge"]
        if no_ff:
            args.append("--no-ff")
        args.append(branch)
        return self._git(*args)

    def rebase(self, branch: str) -> dict:
        return self._git("rebase", branch)

    def cherry_pick(self, commit: str) -> dict:
        return self._git("cherry-pick", commit)

    # ------------------------------------------------------------------ #
    # History helpers
    # ------------------------------------------------------------------ #

    def blame(self, file: str, start: Optional[int] = None,
              end: Optional[int] = None) -> list[dict]:
        args = ["blame", "--line-porcelain"]
        if start and end:
            args += ["-L", f"{start},{end}"]
        args.append(file)
        r = self._git(*args)
        return [{"raw": l} for l in r["stdout"].splitlines() if l]

    def file_history(self, file: str, limit: int = 20) -> list[dict]:
        return self.log(limit=limit, path=file)


# --------------------------------------------------------------------------- #

class Remote:
    """
    GitHub / GitLab client. Uses `gh` or `glab` CLI when available,
    otherwise falls back to the REST API via httpx if a token is provided.
    """

    def __init__(self, workspace: Workspace, provider: str = "github",
                 token: Optional[str] = None):
        self.ws = workspace
        self.provider = provider
        self.token = token or os.environ.get(
            "GITHUB_TOKEN" if provider == "github" else "GITLAB_TOKEN"
        )
        self._cli = "gh" if provider == "github" else "glab"

    def _run(self, *args: str, check: bool = True) -> dict:
        if not shutil.which(self._cli):
            raise GitError(f"{self._cli} CLI not installed")
        r = subprocess.run([self._cli, *args],
                           cwd=str(self.ws.root),
                           capture_output=True, text=True, timeout=120)
        if check and r.returncode != 0:
            raise GitError(f"{self._cli} {' '.join(args)}: {r.stderr.strip()}")
        return {"stdout": r.stdout, "stderr": r.stderr, "code": r.returncode}

    def clone(self, url: str, destination: Optional[str] = None,
              branch: Optional[str] = None) -> dict:
        args = ["git", "clone", url]
        if destination:
            args.append(destination)
        if branch:
            args += ["--branch", branch]
        r = subprocess.run(args, capture_output=True, text=True,
                           cwd=str(self.ws.root), timeout=600)
        if r.returncode != 0:
            raise GitError(r.stderr)
        return {"cloned": destination or url}

    def pull(self, remote: str = "origin", branch: Optional[str] = None) -> dict:
        args = ["pull", remote] + ([branch] if branch else [])
        return self._run("repo", "sync") if False else subprocess.run(
            ["git", *args], cwd=str(self.ws.root), capture_output=True, text=True
        ).stdout or {"ok": True}

    def push(self, remote: str = "origin", branch: Optional[str] = None) -> dict:
        args = ["git", "push", remote] + ([branch] if branch else [])
        r = subprocess.run(args, cwd=str(self.ws.root), capture_output=True, text=True)
        if r.returncode != 0:
            raise GitError(r.stderr)
        return {"ok": True, "stdout": r.stdout}

    # --- issues / PRs -------------------------------------------------- #

    def list_issues(self, limit: int = 20, state: str = "open") -> list[dict]:
        r = self._run("issue", "list", "--limit", str(limit),
                      "--state", state, "--json",
                      "number,title,state,author,labels,createdAt")
        return json.loads(r["stdout"] or "[]")

    def get_issue(self, number: int) -> dict:
        r = self._run("issue", "view", str(number), "--json",
                      "number,title,body,state,author,labels,comments")
        return json.loads(r["stdout"] or "{}")

    def create_issue(self, title: str, body: str = "",
                     labels: Optional[list[str]] = None) -> dict:
        args = ["issue", "create", "--title", title, "--body", body]
        for l in labels or []:
            args += ["--label", l]
        r = self._run(*args)
        return {"url": r["stdout"].strip()}

    def comment_issue(self, number: int, body: str) -> dict:
        r = self._run("issue", "comment", str(number), "--body", body)
        return {"ok": True, "output": r["stdout"].strip()}

    def list_prs(self, limit: int = 20, state: str = "open") -> list[dict]:
        r = self._run("pr", "list", "--limit", str(limit),
                      "--state", state, "--json",
                      "number,title,state,author,headRefName,baseRefName")
        return json.loads(r["stdout"] or "[]")

    def get_pr(self, number: int) -> dict:
        r = self._run("pr", "view", str(number), "--json",
                      "number,title,body,state,author,files,comments,reviews")
        return json.loads(r["stdout"] or "{}")

    def get_pr_diff(self, number: int) -> str:
        r = self._run("pr", "diff", str(number))
        return r["stdout"]

    def create_pr(self, title: str, body: str = "",
                  base: Optional[str] = None, head: Optional[str] = None,
                  draft: bool = False) -> dict:
        args = ["pr", "create", "--title", title, "--body", body]
        if base:
            args += ["--base", base]
        if head:
            args += ["--head", head]
        if draft:
            args.append("--draft")
        r = self._run(*args)
        return {"url": r["stdout"].strip()}

    def update_pr(self, number: int, title: Optional[str] = None,
                  body: Optional[str] = None) -> dict:
        args = ["pr", "edit", str(number)]
        if title:
            args += ["--title", title]
        if body:
            args += ["--body", body]
        self._run(*args)
        return {"ok": True}