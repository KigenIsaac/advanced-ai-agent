"""On-demand discovery and execution of local engineering skills."""
import configparser
import csv
import json
import tokenize
from xml.etree.ElementTree import ParseError

from . import languages, analyzers, formats, operations, workflows, system, assurance
from collections import Counter

MAX_INPUT = 32_768
MAX_RESULT = 24_000
GROUPS = {
    "python": (formats.python_source, "functions classes imports doc_gaps bare_handlers assertions mutable_defaults signatures review"),
    "json": (formats.json_value, "pretty compact sort_keys null_paths empty_paths depth type_counts top_keys structure_report"),
    "csv": (formats.table, "headers shape missing cardinality duplicate_rows to_json widths constant_columns quality_report"),
    "text": (str, "line_endings normalize_lf trailing_space indent_tabs conflict_markers sha256 duplicate_lines size patch_preflight"),
    "ini": (formats.ini, "sections defaults to_json empty_options reused_keys section_sizes multiline empty_sections config_review"),
    "package": (formats.manifest, "scripts runtime_deps dev_deps peer_deps engines dep_overlap script_hooks workspaces install_review"),
    "junit": (formats.junit, "summary failures errors skips durations class_counts repeats untimed triage"),
}
SKILLS = {
    f"{group}_{name}": (parser, getattr(operations, f"{group}_{name}"))
    for group, (parser, names) in GROUPS.items() for name in names.split()
}


DESCRIPTIONS = {name: tool.__doc__ for name, (_, tool) in SKILLS.items()}
DESCRIPTIONS.update(languages.DESCRIPTIONS)
DESCRIPTIONS.update(analyzers.describe())
DESCRIPTIONS.update(workflows.DESCRIPTIONS)
DESCRIPTIONS.update(system.DESCRIPTIONS)
DESCRIPTIONS.update(assurance.DESCRIPTIONS)
GROUP_COUNTS = dict(Counter(name.split("_", 1)[0] for name in DESCRIPTIONS))


def _schema(name, description, properties, required):
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required,
                           "additionalProperties": False}}}


SCHEMAS = [
    _schema("skill_catalog", "Discover deterministic engineering skills by format or keyword. Omit filters for group counts.",
            {"group": {"type": "string", "enum": list(GROUP_COUNTS)}, "query": {"type": "string"},
             "offset": {"type": "integer", "minimum": 0},
             "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, []),
    _schema("skill_run", "Run one catalog skill. Use options only when the catalog lists Options. All other skills take exactly one of text or path. A workspace-relative path reads UTF-8 locally and preserves line endings.",
            {"skill": {"type": "string"}, "text": {"type": "string", "maxLength": MAX_INPUT}, "path": {"type": "string"}, "options": {"type":"object"},
             "output": {"type":"string","description":"Optional new workspace output file for project design skills with text/path input."}}, ["skill"]),
]


def catalog(group=None, query="", offset=0, limit=20):
    if group is not None and (not isinstance(group, str) or group not in GROUP_COUNTS):
        return {"error": "unknown_group", "groups": list(GROUP_COUNTS)}
    if not isinstance(query, str) or len(query) > 256:
        return {"error": "invalid_query"}
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
        return {"error": "invalid_page"}
    terms = query.lower().split()
    if not group and not terms:
        return {"groups": dict(GROUP_COUNTS)}
    matches = []
    for name, description in DESCRIPTIONS.items():
        if group and not name.startswith(group + "_"):
            continue
        if all(term in (name + " " + description).lower() for term in terms):
            matches.append(name)
    matches.sort(key=lambda name: (name != query.strip().lower(), -sum(t in name for t in terms), name))
    page = matches[offset:offset + limit]
    return {"skills": {name: DESCRIPTIONS[name] for name in page}, "total": len(matches),
            "next_offset": offset + limit if offset + limit < len(matches) else None}


def run(skill, text):
    if not isinstance(skill, str) or skill not in DESCRIPTIONS:
        return {"error": "unknown_skill"}
    if skill in workflows.HANDLERS:
        return {"error": "session_required"}
    if not isinstance(text, str) or len(text) > MAX_INPUT:
        return {"error": "invalid_input", "message": f"provide text up to {MAX_INPUT} characters"}
    try:
        text.encode("utf-8")
        source = text if skill.startswith("text_") else text.removeprefix("\ufeff")
        if skill in languages.RULES:
            result = languages.inspect(skill, source)
        elif skill in assurance.DESCRIPTIONS:
            result = assurance.analyze(skill, source)
        else:
            if skill in system.TOOLS:
                parser, tool = system.TOOLS[skill]
            elif skill not in SKILLS:
                return analyzers.analyze(skill, source)
            else:
                parser, tool = SKILLS[skill]
            document = parser(source)
            if parser in (formats.json_value, formats.manifest):
                operations.json_depth(document)
            result = tool(document)
        size = 0
        for chunk in json.JSONEncoder(ensure_ascii=False).iterencode(result):
            size += len(chunk)
            if size > MAX_RESULT:
                return {"error": "result_too_large", "message": "run on a smaller document"}
        return {"skill": skill, "result": result}
    except (KeyError, TypeError, ValueError, SyntaxError, RecursionError, csv.Error, configparser.Error,
            ParseError, tokenize.TokenError) as exc:
        return {"error": "invalid_input", "message": str(exc)[:400]}
