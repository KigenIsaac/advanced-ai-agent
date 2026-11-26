"""Source inventories backed by syntax trees and manifest readers."""
import json
import re
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib
from functools import lru_cache
from pathlib import Path
from . import reports

from tree_sitter import Language, Parser
import tree_sitter_c_sharp
import tree_sitter_go
import tree_sitter_rust
import tree_sitter_typescript


GRAMMARS = {"rust": tree_sitter_rust.language, "go": tree_sitter_go.language,
            "csharp": tree_sitter_c_sharp.language, "nextjs": tree_sitter_typescript.language_tsx}
LABELS = {"rust": "Rust", "go": "Go", "csharp": "C#", "nextjs": "Next.js"}
ACTIONS = {
    "cargo_report": "Read newline-delimited Cargo JSON messages",
    "go_test": "Read go test -json events", "trx": "Read dotnet test TRX results",
    "package": "Inspect package.json",
    "node": "Locate syntax constructs", "call": "Locate calls by written name",
    "macro": "Locate macro invocations", "cargo": "Read Cargo.toml section",
    "msbuild": "Read literal project XML elements", "gomod": "List go.mod or go.work directives",
    "member": "Locate member assignments or method declarations",
    "export": "Locate named exports", "directive": "Locate directive prologues",
    "component": "Locate JSX usage of default imports from", "import": "Locate static imports from",
    "config": "Locate candidate config object properties",
}
RULES = {}
DESCRIPTIONS = {}
for group in GRAMMARS:
    for line in Path(__file__).with_name(group + ".rules").read_text(encoding="utf8").splitlines():
        if line.startswith("["):
            mode = line[1:-1]
            continue
        name, selector = line.split("=", 1)
        skill = f"{group}_{name}"
        if skill in RULES:
            raise ValueError(f"Duplicate skill {skill}")
        RULES[skill] = group, mode, selector
        DESCRIPTIONS[skill] = f"{LABELS[group]} {name.replace('_', ' ')}. {ACTIONS[mode]}: {selector}."


@lru_cache(maxsize=4)
def grammar(group):
    return Language(GRAMMARS[group]())


def walk(root):
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.named_children))


def spelling(node):
    return node.text.decode("utf-8") if node else ""


def quoted(node):
    return spelling(node)[1:-1]


def tail_names(node):
    """Return up to two trailing syntax identifiers, ignoring type arguments."""
    parts = []
    while node and len(parts) < 2:
        if node.type in {"generic_function", "generic_name", "generic_type"}:
            node = node.named_children[0]
            continue
        if node.type in {"conditional_access_expression", "member_binding_expression"}:
            node = node.named_children[-1]
            continue
        if node.type in {"identifier", "field_identifier", "type_identifier", "property_identifier"}:
            parts.append(spelling(node))
            break
        field = (node.child_by_field_name("name") or node.child_by_field_name("field")
                 or node.child_by_field_name("property"))
        if not field:
            break
        if field.type == "generic_name":
            field = field.named_children[0]
        parts.append(spelling(field))
        node = (node.child_by_field_name("path") or node.child_by_field_name("operand")
                or node.child_by_field_name("object") or node.child_by_field_name("expression")
                or node.child_by_field_name("value"))
    return parts[::-1]


def is_directive(node, selector):
    if node.type != "string" or quoted(node) != selector:
        return False
    statement = node.parent
    if statement.type != "expression_statement":
        return False
    scope = statement.parent
    if scope.type != "program" and not (
        scope.type == "statement_block" and scope.parent.child_by_field_name("body") == scope
        and scope.parent.type in {"function_declaration", "function_expression", "arrow_function",
                                  "method_definition", "generator_function_declaration", "generator_function"}
    ):
        return False
    prev = statement.prev_named_sibling
    while prev:
        if prev.type not in {"comment", "hash_bang_line"} and not (
            prev.type == "expression_statement" and prev.named_children and prev.named_children[0].type == "string"
        ):
            return False
        prev = prev.prev_named_sibling
    return True


def exported_names(node):
    if node.type != "export_statement" or any(child.type == "default" for child in node.children):
        return []
    decl = node.child_by_field_name("declaration")
    if decl:
        if decl.type in {"lexical_declaration", "variable_declaration"}:
            return [spelling(child.child_by_field_name("name")) for child in decl.named_children]
        return [spelling(decl.child_by_field_name("name"))]
    return [spelling(item.child_by_field_name("alias") or item.child_by_field_name("name"))
            for clause in node.named_children if clause.type == "export_clause"
            for item in clause.named_children if item.type == "export_specifier"]


def import_bindings(root):
    components, aliases = {}, {}
    for statement in root.named_children:
        if statement.type != "import_statement":
            continue
        module = quoted(statement.child_by_field_name("source"))
        for clause in statement.named_children:
            if clause.type != "import_clause":
                continue
            for child in clause.named_children:
                if child.type == "identifier":
                    components[spelling(child)] = module
                elif child.type == "named_imports":
                    for item in child.named_children:
                        alias = item.child_by_field_name("alias")
                        if alias:
                            aliases[spelling(alias)] = spelling(item.child_by_field_name("name"))
    return components, aliases


def matches(node, mode, selector, components, aliases):
    if mode == "node":
        return node.type in selector.split()
    if mode == "call":
        if node.type not in {"call_expression", "invocation_expression"}:
            return False
        names = tail_names(node.child_by_field_name("function"))
        if names:
            names[0] = aliases.get(names[0], names[0])
        target = re.split(r"::|\.", selector)
        return names[-len(target):] == target
    if mode == "macro":
        return node.type == "macro_invocation" and tail_names(node.child_by_field_name("macro"))[-1:] == [selector]
    if mode == "directive":
        return is_directive(node, selector)
    if mode == "export":
        return selector in exported_names(node)
    if mode == "import":
        return node.type == "import_statement" and quoted(node.child_by_field_name("source")) == selector
    if mode == "component":
        return (node.type in {"jsx_opening_element", "jsx_self_closing_element"}
                and components.get(spelling(node.child_by_field_name("name"))) == selector)
    if mode == "member":
        key = None
        if node.type == "assignment_expression":
            left = node.child_by_field_name("left")
            key = left.child_by_field_name("property") if left else None
        elif node.type == "method_definition":
            key = node.child_by_field_name("name")
        return spelling(key) == selector
    if mode == "config" and node.type in {"pair", "method_definition", "shorthand_property_identifier"}:
        key = node.child_by_field_name("key") or node.child_by_field_name("name") or node
        return (quoted(key) if key.type == "string" else spelling(key)) == selector
    return False


def go_directives(source, selector):
    values = []
    block = None
    for line_no, line in enumerate(source.splitlines(), 1):
        # A quoted replacement path may itself contain two slashes.
        code = re.sub(r'("(?:\\.|[^"\\])*"|`[^`]*`)|//.*', lambda m: m[1] or "", line).strip()
        if not code:
            continue
        if code == ")":
            block = None
        elif block:
            if block == selector:
                values.append({"line": line_no, "value": code})
        else:
            parts = code.split(None, 1)
            if len(parts) == 2:
                directive, argument = parts
                if argument == "(":
                    block = directive
                elif directive == selector:
                    values.append({"line": line_no, "value": argument})
    return values


def inspect(skill, source):
    group, mode, selector = RULES[skill]
    if mode in reports.READERS:
        return reports.READERS[mode](source, selector)
    if mode == "cargo":
        doc = tomllib.loads(source)
        # TOML permits date/time values in arbitrary package metadata.
        return {"section": selector, "present": selector in doc,
                "value": json.loads(json.dumps(doc.get(selector), default=str))}
    if mode == "msbuild":
        root = reports.xml_document(source)
        if root.tag.split("}")[-1] != "Project":
            raise ValueError("Expected an MSBuild Project root element")
        return [{"element": elem.tag.split("}")[-1], "attributes": elem.attrib,
                 "value": "".join(elem.itertext()).strip()}
                for elem in root.iter() if elem.tag.split("}")[-1] in selector.split()]
    if mode == "gomod":
        return go_directives(source, selector)
    data = source.encode("utf8")
    root = Parser(grammar(group)).parse(data).root_node
    if group == "nextjs" and root.has_error:
        # Angle-bracket type assertions are legal in .ts files but ambiguous in TSX.
        ts = Parser(Language(tree_sitter_typescript.language_typescript())).parse(data).root_node
        if not ts.has_error:
            root = ts
    components, aliases = import_bindings(root) if group == "nextjs" else ({}, {})
    findings = []
    count = 0
    for node in walk(root):
        if matches(node, mode, selector, components, aliases):
            count += 1
            if len(findings) < 50:
                first_line = data[node.start_byte:min(node.end_byte, node.start_byte + 180)].split(b"\n", 1)[0]
                findings.append({"line": node.start_point.row + 1, "end_line": node.end_point.row + 1,
                                 "excerpt": first_line.decode("utf8", errors="ignore").strip()})
    return {"count": count, "matches": findings, "omitted": count - len(findings),
            "parse_errors": root.has_error}
