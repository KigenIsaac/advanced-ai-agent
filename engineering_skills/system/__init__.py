"""Discoverable design and verification skills operating on JSON documents."""

from . import planning, storage, contracts, lifecycle, capacity
from .common import mapping
from ..formats import json_value

# Input hints travel with discovery so a model can call a skill without reading source.
FAMILIES = [
    (planning, "JSON {tasks:[{id,hours,depends?:[id],owner?,tests?:[id]}]}"),
    (
        storage,
        'JSON {tables:{name:{pk,columns:{field:"INTEGER|REAL|TEXT|BLOB"},required?:[field],unique?:[field],references?:{field:table}}}}',
    ),
    (contracts, "OpenAPI 3.0/3.1 JSON with paths; local references supported"),
    (
        lifecycle,
        "JSON {states:[name],initial,terminal:[name],transitions:[{from,event,to}],events?:[event]}",
    ),
    (capacity, "JSON object using the named numeric inputs"),
]

TOOLS = {}
DESCRIPTIONS = {}


def _reader(module):
    def read(text):
        doc = mapping(json_value(text), "input")
        return module.parse(doc) if hasattr(module, "parse") else doc

    return read


for module, hint in FAMILIES:
    reader = _reader(module)
    for name, handler in vars(module).items():
        if name.startswith("project_") and callable(handler):
            if name in TOOLS:
                raise ValueError(f"duplicate system skill {name}")
            TOOLS[name] = (reader, handler)
            DESCRIPTIONS[name] = f"{handler.__doc__} Input via text or path: {hint}."


def save_result(ws, output, result):
    """Publish a complete artifact once, including when two workers choose the same name."""
    import json
    import os
    import tempfile
    from pathlib import Path

    target = ws._resolve(output)
    data = (
        result
        if isinstance(result, str)
        else json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    payload = data.encode("utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as tmp:
        temp_path = Path(tmp.name)
    try:
        temp_path.write_bytes(payload)
        os.link(temp_path, target)
    finally:
        temp_path.unlink(missing_ok=True)
    return {"path": ws._rel(target), "bytes": len(payload)}
