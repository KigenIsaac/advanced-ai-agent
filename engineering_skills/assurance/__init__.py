"""Engineering acceptance checks over supplied observations and design budgets."""

import ast
import operator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

from . import traces
from ..formats import json_value

OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul}
CMP = {"<=": operator.le, ">=": operator.ge, "==": operator.eq, "<": operator.lt}


def parse_expression(source):
    tree = ast.parse(source, mode="eval").body

    def check(node):
        if isinstance(node, ast.Name):
            return {node.id}
        if isinstance(node, ast.Constant) and type(node.value) is int:
            return set()
        if isinstance(node, ast.BinOp) and type(node.op) in OPS:
            return check(node.left) | check(node.right)
        raise ValueError("invalid policy expression")

    return tree, check(tree)


def evaluate(node, values):
    if isinstance(node, ast.Name):
        return values[node.id]
    if isinstance(node, ast.Constant):
        return Decimal(node.value)
    return OPS[type(node.op)](evaluate(node.left, values), evaluate(node.right, values))


@dataclass
class Policy:
    name: str
    track: str
    domain: str
    purpose: str
    left: str
    op: str
    right: str

    def __post_init__(self):
        self.left_node, lnames = parse_expression(self.left)
        self.right_node, rnames = parse_expression(self.right)
        self.inputs = sorted(lnames | rnames)
        if self.op not in CMP:
            raise ValueError("invalid policy comparison")
        self.denominator = None
        if isinstance(self.right_node, ast.BinOp) and isinstance(
            self.right_node.op, ast.Mult
        ):
            candidate = self.right_node.right
            if (
                any(k.endswith("_ratio") for k in self.inputs)
                or self.name.endswith(("_mae_ceiling", "_rmse_ceiling"))
            ) and isinstance(candidate, ast.Name):
                self.denominator = candidate.id
        self.description = f"{self.purpose} Pass when {self.left} {self.op} {self.right}. Input JSON with {', '.join(self.inputs)}."
        if self.right == "0":
            self.description += " Include observations (number examined)."

    def run(self, data):
        values = {}
        for field in self.inputs + (["observations"] if self.right == "0" else []):
            if field not in data:
                raise ValueError("missing measurement: " + field)
            raw = data[field]
            if type(raw) not in (int, Decimal) or not 0 <= raw <= 10**24:
                raise ValueError(
                    f"{field} requires a nonnegative finite number up to 1e24"
                )
            if isinstance(raw, Decimal) and (
                len(raw.as_tuple().digits) > 48 or raw.as_tuple().exponent < -48
            ):
                raise ValueError(f"{field} exceeds supported decimal precision")
            if field.endswith("_ratio") and raw > 1:
                raise ValueError(f"{field} must be between 0 and 1")
            if field == "observations" or field.endswith(
                ("_count", "_requests", "_items", "_replicas", "_slots", "_minor_units")
            ):
                if int(raw) != raw:
                    raise ValueError(f"{field} requires a whole number")
            values[field] = Decimal(str(raw))
        no_samples = self.right == "0" and values["observations"] == 0
        undefined_rate = self.denominator and values[self.denominator] == 0
        if no_samples or undefined_rate:
            return {
                "status": "insufficient_evidence",
                "reason": "empty observation population",
            }
        # Rate/coverage checks compare a subset count against its measured population.
        if (
            self.denominator
            and any(k.endswith("_ratio") for k in self.inputs)
            and isinstance(self.left_node, ast.Name)
        ):
            if values[self.left_node.id] > values[self.denominator]:
                raise ValueError("subset count exceeds its population")
        if (
            self.name.endswith("_slice_gap")
            and values["lowest_slice_accuracy_ratio"]
            > values["highest_slice_accuracy_ratio"]
        ):
            raise ValueError("lowest slice accuracy exceeds highest")
        with localcontext() as ctx:
            ctx.prec = 256
            lhs, rhs = (
                evaluate(self.left_node, values),
                evaluate(self.right_node, values),
            )
        return {
            "status": "pass" if CMP[self.op](lhs, rhs) else "fail",
            "observed": str(lhs),
            "operator": self.op,
            "target": str(rhs),
        }


POLICIES = {}
for row in (
    Path(__file__).with_name("policies.tsv").read_text(encoding="utf-8").splitlines()
):
    if not row or row.startswith("#"):
        continue
    if row.startswith("["):
        track, domain = row[1:-1].split()
        continue
    short, left, comparison, right, purpose = row.split("|")
    key = f"project_{track}_{domain}_{short}"
    if key in POLICIES:
        raise ValueError("duplicate policy " + key)
    POLICIES[key] = Policy(key, track, domain, purpose, left, comparison, right)

TRACE_TOOLS = {
    n: f for n, f in vars(traces).items() if n.startswith("project_") and callable(f)
}
DESCRIPTIONS = {k: p.description for k, p in POLICIES.items()}
DESCRIPTIONS.update({k: f.__doc__ for k, f in TRACE_TOOLS.items()})


def _decimal(raw):
    if len(raw) > 100:
        raise ValueError("decimal literal too long")
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("unsupported decimal literal") from exc


def analyze(skill, text):
    doc = json_value(text, parse_float=None if skill in TRACE_TOOLS else _decimal)
    if not isinstance(doc, dict):
        raise ValueError("provide a JSON object, inline or in a workspace file")
    if skill in TRACK_REVIEWS:
        return review(skill, doc)
    if skill in POLICIES:
        return POLICIES[skill].run(doc)
    return TRACE_TOOLS[skill](doc)


def review(skill, data):
    """Use per-policy inputs; identical field names in different workloads need not share a value."""
    track = TRACK_REVIEWS[skill]
    evidence = data.get("evidence")
    if not isinstance(evidence, dict) or not evidence:
        raise ValueError("provide evidence keyed by full policy skill ID")
    allowed = {k for k, v in POLICIES.items() if v.track == track}
    if set(evidence) - allowed:
        raise ValueError("unknown policy or policy from another track")
    findings = []
    totals = {"pass": 0, "fail": 0, "insufficient_evidence": 0, "invalid_input": 0}
    for name, values in evidence.items():
        try:
            if not isinstance(values, dict):
                raise ValueError("policy evidence must be an object")
            result = POLICIES[name].run(values)
        except ValueError as exc:
            result = {"status": "invalid_input", "reason": str(exc)}
        totals[result["status"]] += 1
        if result["status"] != "pass":
            findings.append({"skill": name, **result})
    return {
        "track": track,
        "checked": len(evidence),
        "not_evaluated": len(allowed) - len(evidence),
        "outcomes": totals,
        "findings": findings,
    }


TRACK_REVIEWS = {
    f"project_{track}_evidence_review": track
    for track in ("scale", "accuracy", "safety", "reliability")
}
DESCRIPTIONS.update(
    {
        k: f"Evaluate supplied {t} gates, reporting failures and missing coverage. JSON input: {{evidence:{{full_policy_skill_id:{{measurements}}}}}}."
        for k, t in TRACK_REVIEWS.items()
    }
)
