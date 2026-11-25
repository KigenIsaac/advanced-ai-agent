"""Review operation-level HTTP contracts in OpenAPI 3.0 and 3.1 JSON."""

import re
from collections import Counter
from .common import mapping, items, names

METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def parse(doc):
    if not re.fullmatch(r"3\.[01]\.\d+", str(doc.get("openapi", ""))):
        raise ValueError("expected OpenAPI 3.0 or 3.1 JSON")

    def resolve(obj):
        seen = set()
        obj = mapping(obj, "OpenAPI object")
        while "$ref" in obj:
            ref = obj["$ref"]
            if not isinstance(ref, str) or not ref.startswith("#/") or ref in seen:
                raise ValueError("expected an acyclic local $ref")
            seen.add(ref)
            obj = doc
            for part in ref[2:].split("/"):
                obj = obj[part.replace("~1", "/").replace("~0", "~")]
            obj = mapping(obj, "reference target")
        return obj

    records = []
    for path, path_spec in mapping(doc["paths"], "paths").items():
        if not path.startswith("/"):
            raise ValueError("expected path beginning with /")
        path_spec = resolve(path_spec)
        for method, op in path_spec.items():
            if method not in METHODS:
                continue
            op = mapping(op, "operation")
            if "operationId" in op and not isinstance(op["operationId"], str):
                raise ValueError("operationId must be a string")
            names(op.get("tags", []), "tags")
            params = {}
            for source in (path_spec, op):
                seen_params = set()
                for raw in items(source.get("parameters", []), "parameters"):
                    p = resolve(raw)
                    key = p["in"], p["name"]
                    if key in seen_params or key[0] not in {
                        "header",
                        "query",
                        "cookie",
                        "path",
                    }:
                        raise ValueError("duplicate or invalid parameter")
                    names([key[1]], "parameter name")
                    seen_params.add(key)
                    params[key] = p
            responses = {
                code: resolve(body)
                for code, body in mapping(op.get("responses", {}), "responses").items()
            }
            body = resolve(op.get("requestBody", {}))
            for obj in [body, *responses.values()]:
                mapping(obj.get("content", {}), "content")
            records.append(
                {
                    "route": method.upper() + " " + path,
                    "method": method,
                    "path": path,
                    "op": op,
                    "params": params,
                    "responses": responses,
                    "body": body,
                    "security": items(
                        op.get("security", doc.get("security", [])), "security"
                    ),
                }
            )
    return records


def project_route_inventory(routes):
    """List documented HTTP routes, operation IDs and service tags."""
    return [
        {
            "route": r["route"],
            "operation_id": r["op"].get("operationId"),
            "tags": r["op"].get("tags", []),
        }
        for r in routes
    ]


def project_operation_id_gaps(routes):
    """Find missing and repeated operation IDs that interfere with SDK generation."""
    ids = Counter(r["op"].get("operationId") for r in routes)
    return [
        r["route"]
        for r in routes
        if not r["op"].get("operationId") or ids[r["op"]["operationId"]] > 1
    ]


def project_path_binding_gaps(routes):
    """Find missing, optional and unused path parameters after applying operation overrides."""
    results = []
    for r in routes:
        expected = set(re.findall(r"\{([^{}]+)\}", r["path"]))
        actual = {k: p for (loc, k), p in r["params"].items() if loc == "path"}
        for k in sorted(expected | actual.keys()):
            issue = (
                "unused"
                if k not in expected
                else "missing"
                if k not in actual
                else "optional"
                if actual[k].get("required") is not True
                else None
            )
            if issue:
                results.append({"route": r["route"], "parameter": k, "issue": issue})
    return results


def project_response_test_matrix(routes):
    """Generate contract-test cases for declared response codes and response media types."""
    return [
        {
            "route": r["route"],
            "status": code,
            "media_types": list(resp.get("content", {})),
        }
        for r in routes
        for code, resp in r["responses"].items()
    ]


def project_error_contract_gaps(routes):
    """Find operations without any documented 4xx, 5xx or default error response."""
    return [
        r["route"]
        for r in routes
        if not any(
            code == "default" or re.fullmatch(r"[45](\d\d|XX)", code)
            for code in r["responses"]
        )
    ]


def project_request_media_matrix(routes):
    """Map request media types and required-body flags for operations accepting bodies."""
    return [
        {
            "route": r["route"],
            "required": r["body"].get("required", False),
            "media_types": list(r["body"].get("content", {})),
        }
        for r in routes
        if r["body"]
    ]


def project_auth_contract(routes):
    """Show effective security alternatives and whether anonymous access is declared."""
    return [
        {
            "route": r["route"],
            "alternatives": r["security"],
            "anonymous": not r["security"] or {} in r["security"],
        }
        for r in routes
    ]


def project_deprecation_inventory(routes):
    """Identify deprecated operations and parameters that callers should migrate."""
    return [
        {
            "route": r["route"],
            "operation_deprecated": r["op"].get("deprecated", False),
            "parameters": [
                p["name"] for p in r["params"].values() if p.get("deprecated")
            ],
        }
        for r in routes
        if r["op"].get("deprecated")
        or any(p.get("deprecated") for p in r["params"].values())
    ]


def project_required_input_cases(routes):
    """Generate negative contract-test scenarios omitting one required request input at a time."""
    cases = []
    for r in routes:
        for (loc, k), p in r["params"].items():
            if p.get("required"):
                cases.append({"route": r["route"], "omit": k, "location": loc})
        if r["body"].get("required"):
            cases.append(
                {"route": r["route"], "omit": "requestBody", "location": "body"}
            )
    return cases
