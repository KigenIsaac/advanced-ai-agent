"""Software-engineering workflows against the current workspace."""
import ast
from collections import Counter
from pathlib import PurePosixPath
import subprocess

from .formats import python_source


def _git(ws, *args):
    cmd = ["git", "--no-optional-locks", "-C", str(ws.root)]
    root = subprocess.check_output(cmd + ["rev-parse", "--show-toplevel"], text=True,
                                   stderr=subprocess.DEVNULL, timeout=5).rstrip("\n")
    if str(ws.root) != root:
        raise ValueError("open the repository root as the workspace")
    result = subprocess.run(cmd + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace")[:300])
    return result.stdout.decode("utf-8", errors="replace")


def _tracked(ws):
    return list(filter(None, _git(ws, "ls-files", "-z").split("\0")))


def project_inventory(ws):
    """Summarize tracked file types and identify build and dependency manifests."""
    files = _tracked(ws)
    manifests = {"pyproject.toml","requirements.txt","package.json","Cargo.toml","go.mod",
                 "pom.xml","build.gradle","Dockerfile","Makefile","composer.json"}
    return {"tracked": len(files), "file_types": dict(Counter(PurePosixPath(p).suffix or "(none)" for p in files)),
            "manifests": [p for p in files if PurePosixPath(p).name in manifests]}


def project_change_summary(ws):
    """Report staged, unstaged and untracked paths independently, including on an unborn branch."""
    def names(*args):
        return list(filter(None, _git(ws, *args).split("\0")))
    return {"staged": names("diff","--cached","--no-renames","--name-only","-z"),
            "unstaged": names("diff","--no-renames","--name-only","-z"),
            "untracked": names("ls-files","--others","--exclude-standard","-z")}


def project_recent_changes(ws, limit=10):
    """Summarize recent commits with short IDs, dates and subjects, newest first."""
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    records = _git(ws, "log", f"-{limit}", "-z", "--format=%h%x00%cs%x00%s").split("\0")
    return [{"commit": sha, "date": date, "subject": subject}
            for sha, date, subject in zip(records[::3], records[1::3], records[2::3])]


def project_related_tests(ws, path):
    """Find tracked Python and JS/TS tests named after a source file and rank nearby candidates."""
    target = PurePosixPath(ws._rel(ws._resolve(path)))
    stem = target.stem
    matches = []
    for name in _tracked(ws):
        file = PurePosixPath(name)
        if file == target:
            continue
        if target.suffix == ".py":
            relevant = file.name in {f"test_{stem}.py", f"{stem}_test.py"}
        elif target.suffix in {".js",".jsx",".ts",".tsx"}:
            relevant = file.stem in {f"{stem}.test", f"{stem}.spec"} and file.suffix in {".js",".jsx",".ts",".tsx"}
        else:
            relevant = False
        if relevant:
            matches.append(name)
    return {"source": str(target), "candidates": sorted(matches, key=lambda p: (PurePosixPath(p).parent != target.parent,p))}


def project_public_api(ws, path):
    """Extract public Python classes, functions, methods and their signatures without importing code."""
    tree = python_source(ws.read_utf8_document(path, 32768))
    rows = []
    def declarations(body, prefix=""):
        for node in body:
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) or node.name.startswith("_"):
                continue
            name = prefix + node.name
            if isinstance(node, ast.ClassDef):
                rows.append({"name":name,"kind":"class","line":node.lineno})
                declarations(node.body, name + ".")
            else:
                rows.append({"name":name,"kind":"async" if isinstance(node,ast.AsyncFunctionDef) else "function",
                             "parameters":ast.unparse(node.args),"returns":ast.unparse(node.returns) if node.returns else None,
                             "line":node.lineno})
    declarations(tree.body)
    return rows


def project_api_diff(ws, before, after):
    """Compare public Python declarations in two files for additions, removals and signature changes."""
    def index(path):
        return {row["name"]: {k:v for k,v in row.items() if k not in {"line","name"}}
                for row in project_public_api(ws, path)}
    old, new = index(before), index(after)
    return {"added":sorted(new.keys()-old.keys()),"removed":sorted(old.keys()-new.keys()),
            "changed":[{"name":n,"before":old[n],"after":new[n]} for n in sorted(old.keys() & new.keys())
                       if old[n] != new[n]]}
