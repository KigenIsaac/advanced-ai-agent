"""Readers for recorded toolchain output; none launch a compiler or test runner."""
import json
from xml.etree import ElementTree


def xml_document(source):
    if "<!DOCTYPE" in source:
        raise ValueError("XML must omit document type declarations")
    return ElementTree.fromstring(source)


def events(source):
    for line in source.splitlines():
        if line.strip():
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError("Expected a JSON object per line")
            yield item


def cargo(source, selector):
    rows = []
    for event in events(source):
        reason = event.get("reason")
        if reason == "compiler-message":
            message = event.get("message")
            if not isinstance(message, dict) or message.get("level") != selector:
                continue
            locations = [{"file": s.get("file_name"), "line": s.get("line_start"),
                          "column": s.get("column_start")}
                         for s in message.get("spans", []) if isinstance(s, dict) and s.get("is_primary")]
            rows.append({"message": message.get("message"), "code": message.get("code"), "locations": locations})
        elif reason == selector:
            if reason == "build-finished":
                rows.append({"success": event.get("success")})
            else:
                rows.append({key: event.get(key) for key in ("package_id", "filenames", "executable", "fresh")})
    return rows


def go_tests(source, selector):
    rows = []
    for event in events(source):
        action = event.get("Action")
        if selector == "package_fail":
            matches = action == "fail" and not event.get("Test")
        else:
            matches = action == selector and bool(event.get("Test"))
        if matches:
            rows.append({key: event[key] for key in ("Package", "Test", "Elapsed") if key in event})
    return rows


def trx(source, selector):
    root = xml_document(source)
    if root.tag.split("}")[-1] != "TestRun":
        raise ValueError("Expected a TRX TestRun root element")
    rows = []
    for elem in root.iter():
        name = elem.tag.split("}")[-1]
        if name == selector == "Counters":
            rows.append(elem.attrib)
        elif name == "UnitTestResult" and elem.get("outcome") == selector:
            rows.append({key: elem.get(key) for key in ("testName", "outcome", "duration")})
    return rows


def package(source, selector):
    doc = json.loads(source)
    if not isinstance(doc, dict):
        raise ValueError("Expected a package.json object")
    if selector == "versions":
        return {section: {name: value for name, value in doc.get(section, {}).items()
                          if name in {"next", "react", "react-dom"}}
                for section in ("dependencies", "devDependencies", "peerDependencies")
                if isinstance(doc.get(section), dict)}
    scripts = doc.get("scripts", {})
    if not isinstance(scripts, dict):
        raise ValueError("Expected a scripts object")
    return {"script": selector, "command": scripts.get(selector)}


READERS = {"cargo_report": cargo, "go_test": go_tests, "trx": trx, "package": package}
