"""Isolated single-document SQL lint worker. The parent owns the wall-clock limit."""
import json
import sys

from sqlfluff.core import FluffConfig, Linter


def main():
    config = FluffConfig(
        ignore_local_config=True,
        overrides={"dialect": "ansi", "templater": "raw", "rules": sys.argv[1],
                   "disable_noqa": True, "large_file_skip_byte_limit": 0},
    )
    source = sys.stdin.buffer.read().decode("utf-8")
    linted = Linter(config=config).lint_string(source)
    violations = linted.get_violations()
    print(json.dumps({
        "count": len(violations),
        "parse_error": any(v.rule_code() in {"PRS", "LXR", "TMP"} for v in violations),
        "findings": [{"code": v.rule_code(), "line": v.line_no, "column": v.line_pos,
                      "message": v.desc()[:400]} for v in violations[:50]],
    }))


if __name__ == "__main__":
    main()
