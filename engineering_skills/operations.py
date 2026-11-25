"""Dedicated, deterministic tools. Functions consume one parsed document."""
import ast
import hashlib
import json
import math
import re
from collections import Counter


def python_functions(tree):
    """List sync and async functions with source lines."""
    return [(n.name, n.lineno) for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def python_classes(tree):
    """List class declarations with source lines."""
    return [(n.name, n.lineno) for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]


def python_imports(tree):
    """List import statements, including relative imports."""
    return [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]


def python_doc_gaps(tree):
    """Locate public declarations missing docstrings."""
    kinds = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    return [(n.name, n.lineno) for n in ast.walk(tree) if isinstance(n, kinds)
            and not n.name.startswith("_") and not ast.get_docstring(n)]


def python_bare_handlers(tree):
    """Locate except clauses that catch every exception."""
    return [n.lineno for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler) and n.type is None]


def python_assertions(tree):
    """Locate assertions and report their test expressions."""
    return [(n.lineno, ast.unparse(n.test)) for n in ast.walk(tree) if isinstance(n, ast.Assert)]


def python_mutable_defaults(tree):
    """Locate functions with literal mutable argument defaults."""
    found = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defaults = n.args.defaults + n.args.kw_defaults
            if any(isinstance(d, (ast.List, ast.Dict, ast.Set)) for d in defaults):
                found.append((n.name, n.lineno))
    return found


def python_signatures(tree):
    """Extract top-level function signatures without importing the module."""
    return [f"{n.name}({ast.unparse(n.args)})" for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def json_pretty(value):
    """Format JSON with two-space indentation."""
    return json.dumps(value, indent=2, ensure_ascii=False)


def json_compact(value):
    """Minify JSON while preserving key order."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def json_sort_keys(value):
    """Sort object keys recursively for readable diffs."""
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False)


def _nodes(value):
    # Retain path segments, escaping RFC 6901 only when emitting a result.
    stack = [((), value)]
    while stack:
        path, item = stack.pop()
        if len(path) > 64:
            raise ValueError("JSON nesting exceeds 64 levels")
        yield path, item
        children = item.items() if isinstance(item, dict) else enumerate(item) if isinstance(item, list) else ()
        stack.extend((path + (str(k),), v) for k, v in reversed(list(children)))


def _pointer(path):
    return "".join("/" + k.replace("~", "~0").replace("/", "~1") for k in path)


def _matching_paths(value, matches):
    result = []
    length = 0
    for path, item in _nodes(value):
        if matches(item):
            pointer = _pointer(path)
            length += len(pointer) + 2
            if length > 24_000:
                raise ValueError("too many matching paths; split the document")
            result.append(pointer)
    return result


def json_null_paths(value):
    """Locate null values by JSON Pointer."""
    return _matching_paths(value, lambda v: v is None)


def json_empty_paths(value):
    """Locate empty arrays, strings and objects by JSON Pointer."""
    return _matching_paths(value, lambda v: isinstance(v, (str, list, dict)) and not v)


def json_depth(value):
    """Measure the maximum nesting depth, counting the root as zero."""
    return max(len(p) for p, _ in _nodes(value))


def json_type_counts(value):
    """Count all JSON values by type, distinguishing booleans and numbers."""
    names = {str: "string", list: "array", dict: "object", type(None): "null", bool: "boolean", int: "number", float: "number"}
    return dict(Counter(names[type(v)] for _, v in _nodes(value)))


def json_top_keys(value):
    """List top-level object keys in source order."""
    if not isinstance(value, dict):
        raise ValueError("expected an object")
    return list(value)


def csv_headers(table):
    """Extract ordered column names."""
    return table[0]


def csv_shape(table):
    """Count columns and nonblank data records."""
    return {"columns": len(table[0]), "rows": len(table[1])}


def csv_missing(table):
    """Count empty cells per column."""
    header, rows = table
    return {key: sum(row[i] == "" for row in rows) for i, key in enumerate(header)}


def csv_cardinality(table):
    """Count distinct values in each column, including empty cells."""
    header, rows = table
    return {key: len({row[i] for row in rows}) for i, key in enumerate(header)}


def csv_duplicate_rows(table):
    """Report repeated records and their occurrence counts."""
    return [{"row": row, "count": count} for row, count in Counter(map(tuple, table[1])).items() if count > 1]


def csv_to_json(table):
    """Convert rows to JSON records while preserving leading zeros."""
    return [dict(zip(table[0], row)) for row in table[1]]


def csv_widths(table):
    """Calculate maximum character width per column including its header."""
    header, rows = table
    widths = list(map(len, header))
    for row in rows:
        widths = [max(width, len(cell)) for width, cell in zip(widths, row)]
    return dict(zip(header, widths))


def csv_constant_columns(table):
    """Identify columns with one distinct value across data rows."""
    return [name for name, count in csv_cardinality(table).items() if count == 1]


def text_line_endings(text):
    """Count LF, CRLF and standalone CR line endings."""
    pairs = text.count("\r\n")
    return {"CRLF": pairs, "CR": text.count("\r") - pairs, "LF": text.count("\n") - pairs}


def text_normalize_lf(text):
    """Convert CRLF and standalone CR to LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def text_trailing_space(text):
    """Locate lines ending in spaces or tabs."""
    return [i for i, line in enumerate(text.splitlines(), 1) if line.endswith((" ", "\t"))]


def text_indent_tabs(text):
    """Locate lines containing tabs in leading indentation."""
    return [i for i, line in enumerate(text.splitlines(), 1) if re.match(r"^[ ]*\t", line)]


def text_conflict_markers(text):
    """Locate line-start merge conflict markers."""
    return [i for i, line in enumerate(text.splitlines(), 1) if re.match(r"^(<{7}|={7}|>{7}|\|{7})(?:\s|$)", line)]


def text_sha256(text):
    """Compute SHA-256 over the exact UTF-8 text."""
    return hashlib.sha256(text.encode()).hexdigest()


def text_duplicate_lines(text):
    """Count repeated nonblank lines with exact whitespace."""
    return {k: v for k, v in Counter(text.splitlines()).items() if k.strip() and v > 1}


def text_size(text):
    """Measure code points, UTF-8 bytes and lines."""
    return {"characters": len(text), "bytes": len(text.encode()), "lines": len(text.splitlines())}


def ini_sections(config):
    """List explicit INI sections in document order."""
    return config.sections()


def ini_defaults(config):
    """Extract INI default settings without interpolation."""
    return dict(config.defaults())


def ini_to_json(config):
    """Export effective settings per section, including inherited defaults."""
    return {section: dict(config[section]) for section in config.sections()}


def ini_empty_options(config):
    """Find effective settings with empty values."""
    return [(s, k) for s in config.sections() for k, v in config[s].items() if not v]


def ini_reused_keys(config):
    """Find option names effective in multiple sections."""
    counts = Counter(k for section in config.sections() for k in config[section])
    return {k: v for k, v in counts.items() if v > 1}


def ini_section_sizes(config):
    """Count effective settings per section."""
    return {s: len(config[s]) for s in config.sections()}


def ini_multiline(config):
    """Locate effective settings containing continuation lines."""
    return [(s, k) for s in config.sections() for k, v in config[s].items() if "\n" in v]


def ini_empty_sections(config):
    """Locate sections with no effective settings."""
    return [s for s in config.sections() if not config[s]]


def _mapping(pkg, field):
    value = pkg.get(field, {})
    if not isinstance(value, dict) or any(not isinstance(v, str) for v in value.values()):
        raise ValueError(f"{field} must map names to strings")
    return value


def package_scripts(pkg):
    """Extract package.json script definitions as text."""
    return _mapping(pkg, "scripts")


def package_runtime_deps(pkg):
    """Extract runtime dependency declarations."""
    return _mapping(pkg, "dependencies")


def package_dev_deps(pkg):
    """Extract development dependency declarations."""
    return _mapping(pkg, "devDependencies")


def package_peer_deps(pkg):
    """Extract peer dependency requirements."""
    return _mapping(pkg, "peerDependencies")


def package_engines(pkg):
    """Extract declared runtime engine constraints."""
    return _mapping(pkg, "engines")


def package_dep_overlap(pkg):
    """Identify dependencies declared for both runtime and development."""
    return sorted(package_runtime_deps(pkg).keys() & package_dev_deps(pkg).keys())


def package_script_hooks(pkg):
    """Match pre and post scripts with their base script."""
    scripts = package_scripts(pkg)
    return {k: [p + k for p in ("pre", "post") if p + k in scripts] for k in scripts if "pre" + k in scripts or "post" + k in scripts}


def package_workspaces(pkg):
    """Extract workspace patterns from array or packages-object syntax."""
    paths = pkg.get("workspaces", [])
    if isinstance(paths, dict):
        paths = paths.get("packages")
    if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
        raise ValueError("expected workspace pattern array")
    return paths


def _case(c):
    return {"class": c.get("classname", ""), "name": c.get("name", "")}


def _status(c):
    return next((s for s in ("error", "failure", "skipped") if c.find(s) is not None), "passed")


def junit_summary(cases):
    """Count actual test cases by outcome across nested suites."""
    return dict(Counter(map(_status, cases)))


def junit_failures(cases):
    """Extract assertion failure messages and test identities."""
    return [{**_case(c), "message": node.get("message") or node.text or ""} for c in cases for node in c.findall("failure")]


def junit_errors(cases):
    """Extract test execution errors and their types."""
    return [{**_case(c), "type": node.get("type", "")} for c in cases for node in c.findall("error")]


def junit_skips(cases):
    """List skipped tests with their stated reasons."""
    return [{**_case(c), "reason": node.get("message") or node.text or ""} for c in cases for node in c.findall("skipped")]


def junit_durations(cases):
    """Rank timed test cases by descending duration in seconds."""
    timed = []
    for c in cases:
        if "time" in c.attrib:
            seconds = float(c.get("time"))
            if not math.isfinite(seconds) or seconds < 0:
                raise ValueError("invalid testcase duration")
            timed.append({**_case(c), "seconds": seconds})
    return sorted(timed, key=lambda row: -row["seconds"])


def junit_class_counts(cases):
    """Count tests grouped by class name."""
    return dict(Counter(c.get("classname", "") for c in cases))


def junit_repeats(cases):
    """Locate duplicate test identities across suites."""
    counts = Counter((c.get("classname", ""), c.get("name", "")) for c in cases)
    return [{"class": cls, "name": name, "count": n} for (cls, name), n in counts.items() if n > 1]


def junit_untimed(cases):
    """Identify cases for which timing data is missing."""
    return [_case(c) for c in cases if "time" not in c.attrib]


def python_review(document):
    """Review a Python module for mutable defaults, bare handlers and documentation gaps."""
    return {
        "mutable_defaults": python_mutable_defaults(document),
        "bare_handlers": python_bare_handlers(document),
        "doc_gaps": python_doc_gaps(document),
    }


def csv_quality_report(document):
    """Profile a CSV extract for missing data, redundant rows and constant columns."""
    return {
        "shape": csv_shape(document),
        "missing": csv_missing(document),
        "duplicate_rows": csv_duplicate_rows(document),
        "constant_columns": csv_constant_columns(document),
    }


def json_structure_report(document):
    """Profile JSON structure and locate missing or empty values."""
    return {
        "depth": json_depth(document),
        "type_counts": json_type_counts(document),
        "null_paths": json_null_paths(document),
        "empty_paths": json_empty_paths(document),
    }


def text_patch_preflight(document):
    """Inspect text before a patch for conflicts, whitespace and mixed line endings."""
    return {
        "conflict_markers": text_conflict_markers(document),
        "trailing_space": text_trailing_space(document),
        "indent_tabs": text_indent_tabs(document),
        "line_endings": text_line_endings(document),
    }


def ini_config_review(document):
    """Review INI sections for empty settings, repeated keys and multiline values."""
    return {
        "empty_sections": ini_empty_sections(document),
        "empty_options": ini_empty_options(document),
        "reused_keys": ini_reused_keys(document),
        "multiline": ini_multiline(document),
    }


def package_install_review(document):
    """Inspect a Node project for runtime constraints, duplicated dependencies and install lifecycle hooks."""
    return {
        "engines": package_engines(document),
        "dep_overlap": package_dep_overlap(document),
        "script_hooks": package_script_hooks(document),
        "install_scripts": {k: v for k, v in package_scripts(document).items()
                            if k in {"preinstall", "install", "postinstall", "prepare"}},
        "workspaces": package_workspaces(document),
    }


def junit_triage(document):
    """Triage a JUnit run by outcome, assertion failure, runtime error and skipped case."""
    return {
        "summary": junit_summary(document),
        "failures": junit_failures(document),
        "errors": junit_errors(document),
        "skips": junit_skips(document),
    }
