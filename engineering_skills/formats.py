"""Strict, bounded parsers for text supplied to engineering skills."""
import ast
import configparser
import csv
import io
import json
import math
import tokenize
import xml.etree.ElementTree as ET


def python_source(text):
    # Chained binary/unary operators can exhaust the compiler's C stack even
    # when their source uses no parentheses. Limit parser input before ast.parse.
    tokens = tokenize.generate_tokens(io.StringIO(text).readline)
    if sum(1 for _ in tokens) > 1800:
        raise ValueError("split this Python source into smaller sections")
    return ast.parse(text)


def json_value(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key: " + key)
            result[key] = value
        return result

    def number(raw):
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("non-finite JSON number")
        return value

    return json.loads(text, object_pairs_hook=unique, parse_constant=number, parse_float=number)


def table(text):
    rows = csv.reader(io.StringIO(text, newline=""), strict=True)
    header = next(rows, None)
    if not header or any(not key.strip() for key in header):
        raise ValueError("CSV requires a nonblank name for every column")
    if len(set(header)) != len(header):
        raise ValueError("duplicate column name")
    records = []
    for row in rows:
        if not row:
            continue
        if len(row) != len(header):
            raise ValueError(f"wrong column count at line {rows.line_num}")
        records.append(row)
    return header, records


def ini(text):
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    config.read_string(text)
    if len(config.sections()) * len(config.defaults()) > 16_384:
        raise ValueError("too many inherited INI options; split the document")
    return config


def manifest(text):
    value = json_value(text)
    if not isinstance(value, dict):
        raise ValueError("package.json must contain an object")
    return value


def junit(text):
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise ValueError("JUnit input must omit DTDs and entities")
    root = ET.fromstring(text)
    for item in root.iter():
        item.tag = item.tag.rsplit("}", 1)[-1]
    if root.tag not in {"testsuite", "testsuites"}:
        raise ValueError("expected a JUnit testsuite or testsuites")
    return list(root.iter("testcase"))
