"""Generate SQLite artifacts from a restricted relational design document."""

import re
from .common import mapping, names

TYPES = {"INTEGER", "REAL", "TEXT", "BLOB"}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z_][A-Za-z_0-9]{0,62}", value
    ):
        raise ValueError("invalid SQL identifier")
    return '"' + value + '"'


def parse(doc):
    tables = mapping(doc["tables"], "tables")
    if not 1 <= len(tables) <= 100:
        raise ValueError("provide 1 to 100 tables")
    for table, schema in tables.items():
        identifier(table)
        if table.lower().startswith("sqlite_") or table != table.lower():
            raise ValueError("use lowercase, non-reserved table identifiers")
        mapping(schema, table)
        columns = mapping(schema["columns"], "columns")
        if not columns or len(columns) > 128 or schema["pk"] not in columns:
            raise ValueError("expected columns and a single-column primary key")
        for col, typ in columns.items():
            identifier(col)
            if col != col.lower() or typ not in TYPES:
                raise ValueError("invalid column name or SQLite type")
        for prop in ("required", "unique"):
            if set(names(schema.get(prop, []), prop)) - columns.keys():
                raise ValueError("unknown column in " + prop)
        for col, dest in mapping(schema.get("references", {}), "references").items():
            if col not in columns or dest not in tables:
                raise ValueError("unknown foreign key column or target")
    for schema in tables.values():
        for col, dest in schema.get("references", {}).items():
            target = tables[dest]
            if schema["columns"][col] != target["columns"][target["pk"]]:
                raise ValueError("foreign key type mismatch")
    return tables


def project_schema_ddl(tables):
    """Generate SQLite CREATE TABLE statements with primary, unique and foreign key constraints."""
    statements = ["PRAGMA foreign_keys = ON;"]
    for name, t in tables.items():
        declarations = []
        for col, typ in t["columns"].items():
            parts = [identifier(col), typ]
            if col == t["pk"]:
                parts.append("PRIMARY KEY")
            if col in t.get("required", []) or col == t["pk"]:
                parts.append("NOT NULL")
            if col in t.get("unique", []):
                parts.append("UNIQUE")
            dest = t.get("references", {}).get(col)
            if dest:
                parts.append(
                    f"REFERENCES {identifier(dest)} ({identifier(tables[dest]['pk'])})"
                )
            declarations.append(" ".join(parts))
        statements.append(
            f"CREATE TABLE {identifier(name)} (\n  "
            + ",\n  ".join(declarations)
            + "\n);"
        )
    return "\n".join(statements)


def project_fk_indexes(tables):
    """Generate child-side SQLite indexes for foreign keys lacking a declared unique or primary index."""
    statements = []
    used = set(tables)
    for table, schema in tables.items():
        for column in schema.get("references", {}):
            if column == schema["pk"] or column in schema.get("unique", []):
                continue
            index = f"ix_{len(statements)}"
            while index in used:
                index += "_"
            used.add(index)
            statements.append(
                f'CREATE INDEX "{index}" ON {identifier(table)} ({identifier(column)});'
            )
    return statements


def project_orphan_checks(tables):
    """Generate SQL checks for child rows whose non-null foreign keys lack a parent."""
    return [
        f"SELECT c.* FROM {identifier(n)} AS c LEFT JOIN {identifier(dest)} AS p "
        f"ON c.{identifier(col)} = p.{identifier(tables[dest]['pk'])} "
        f"WHERE c.{identifier(col)} IS NOT NULL AND p.{identifier(tables[dest]['pk'])} IS NULL;"
        for n, t in tables.items()
        for col, dest in t.get("references", {}).items()
    ]


def project_unique_checks(tables):
    """Generate SQL queries finding duplicate non-null values in declared unique columns."""
    checks = []
    for n, t in tables.items():
        for col in dict.fromkeys([t["pk"], *t.get("unique", [])]):
            c = identifier(col)
            checks.append(
                f"SELECT {c}, COUNT(*) FROM {identifier(n)} WHERE {c} IS NOT NULL GROUP BY {c} HAVING COUNT(*) > 1;"
            )
    return checks


def project_null_checks(tables):
    """Generate null-count queries for mandatory fields before enforcing constraints."""
    return [
        f"SELECT COUNT(*) FROM {identifier(n)} WHERE {identifier(c)} IS NULL;"
        for n, t in tables.items()
        for c in dict.fromkeys([t["pk"], *t.get("required", [])])
    ]


def project_insert_templates(tables):
    """Generate parameterized INSERT statements for application repositories."""
    return {
        n: f"INSERT INTO {identifier(n)} ("
        + ", ".join(map(identifier, t["columns"]))
        + ") VALUES ("
        + ", ".join(":" + c for c in t["columns"])
        + ");"
        for n, t in tables.items()
    }


def project_lookup_templates(tables):
    """Generate parameterized primary-key lookups with explicit result columns."""
    return {
        n: "SELECT "
        + ", ".join(map(identifier, t["columns"]))
        + f" FROM {identifier(n)} WHERE {identifier(t['pk'])} = ?;"
        for n, t in tables.items()
    }


def project_update_templates(tables):
    """Generate primary-key-scoped UPDATE statements for non-key fields."""
    result = {}
    for n, t in tables.items():
        values = [f"{identifier(c)} = :{c}" for c in t["columns"] if c != t["pk"]]
        if values:
            result[n] = (
                f"UPDATE {identifier(n)} SET "
                + ", ".join(values)
                + f" WHERE {identifier(t['pk'])} = :{t['pk']};"
            )
    return result


def project_data_dictionary(tables):
    """Generate a field-level data dictionary with key, nullability and relationship metadata."""
    return {
        n: [
            {
                "field": c,
                "type": typ,
                "primary": c == t["pk"],
                "required": c == t["pk"] or c in t.get("required", []),
                "unique": c == t["pk"] or c in t.get("unique", []),
                "references": t.get("references", {}).get(c),
            }
            for c, typ in t["columns"].items()
        ]
        for n, t in tables.items()
    }


def project_repository_module(tables):
    """Generate a Python SQLite repository with parameterized insert, lookup and update operations."""
    return (
        f"INSERT = {project_insert_templates(tables)!r}\nLOOKUP = {project_lookup_templates(tables)!r}\n"
        f"UPDATE = {project_update_templates(tables)!r}\n\n\n"
        "def insert(connection, table, record):\n"
        "    return connection.execute(INSERT[table], record).rowcount\n\n\n"
        "def lookup(connection, table, key):\n"
        "    return connection.execute(LOOKUP[table], (key,)).fetchone()\n\n\n"
        "def update(connection, table, record):\n"
        "    return connection.execute(UPDATE[table], record).rowcount\n"
    )


def project_schema_smoke_tests(tables):
    """Generate pytest tests that apply sibling schema.sql and inspect actual SQLite table columns and keys."""
    expected = {
        name: [(c, t, 1 if c == spec["pk"] else 0) for c, t in spec["columns"].items()]
        for name, spec in tables.items()
    }
    return (
        "from pathlib import Path\nimport sqlite3\n\nimport pytest\n\n\n"
        f'@pytest.mark.parametrize("name,expected", {list(expected.items())!r})\n'
        "def test_schema(name, expected):\n"
        '    conn = sqlite3.connect(":memory:")\n'
        "    try:\n"
        '        conn.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))\n'
        '        rows = conn.execute("SELECT name,type,pk FROM pragma_table_info(?) ORDER BY cid", (name,))\n'
        "        assert list(rows) == expected\n"
        "    finally:\n        conn.close()\n"
    )
