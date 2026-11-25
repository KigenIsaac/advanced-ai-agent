"""Diagnostics computed from event records, independently of policy summaries."""

from collections import defaultdict
from decimal import Decimal


def records(data, field):
    rows = data[field]
    if (
        not isinstance(rows, list)
        or len(rows) > 2000
        or not all(isinstance(r, dict) for r in rows)
    ):
        raise ValueError(f"{field} must be an array of at most 2000 records")
    return rows


def number(value):
    if type(value) not in (float, int) or not 0 <= value <= 10**18:
        raise ValueError("expected finite nonnegative measurement")
    return Decimal(str(value))


def whole(value):
    if type(value) is not int or abs(value) > 10**18:
        raise ValueError("expected an integer up to 1e18 in magnitude")
    return value


def label(row, key):
    val = row[key]
    if not isinstance(val, str) or not val.strip():
        raise ValueError(f"expected nonblank {key}")
    return val


def project_scale_queue_trace(data):
    """Simulate backlog and overflow across ordered windows. Input {capacity, initial, windows:[{arrivals,service_slots}]} using whole item counts. Arrivals precede service."""
    capacity, backlog = whole(data["capacity"]), whole(data["initial"])
    if not 0 <= backlog <= capacity:
        raise ValueError("initial backlog out of bounds")
    peak, dropped, served = backlog, 0, 0
    history = []
    for w in records(data, "windows"):
        arrivals, slots = whole(w["arrivals"]), whole(w["service_slots"])
        if min(arrivals, slots) < 0:
            raise ValueError("negative queue count")
        incoming = backlog + arrivals
        dropped += max(0, incoming - capacity)
        admitted = min(incoming, capacity)
        peak = max(peak, admitted)
        completed = min(admitted, slots)
        served += completed
        backlog = admitted - completed
        history.append(backlog)
    return {
        "peak": peak,
        "dropped": dropped,
        "completed": served,
        "backlog_after_windows": history,
    }


def project_scale_load_knee(data):
    """Find the first tested load step exceeding latency or error budgets. Input {max_p99_ms,max_error_ratio,steps:[{rps,p99_ms,requests,errors}]} in increasing load order."""
    last = None
    first = None
    previous = -1
    latency, ratio = number(data["max_p99_ms"]), number(data["max_error_ratio"])
    if ratio > 1:
        raise ValueError("error ratio out of range")
    rows = records(data, "steps")
    if not rows:
        raise ValueError("no load steps")
    for step in rows:
        rps, p99 = number(step["rps"]), number(step["p99_ms"])
        requests, errors = whole(step["requests"]), whole(step["errors"])
        if rps <= previous or requests <= 0 or not 0 <= errors <= requests:
            raise ValueError("invalid load step")
        previous = rps
        if first is None:
            if p99 > latency or errors > ratio * requests:
                first = step["rps"]
            else:
                last = step["rps"]
    return {"last_passing_rps_before_breach": last, "first_breaching_rps": first}


def project_accuracy_journal_trace(data):
    """Reconcile signed integer postings by transaction and currency. Input {postings:[{transaction,currency,minor_units}]}."""
    balances = defaultdict(int)
    for row in records(data, "postings"):
        balances[label(row, "transaction"), label(row, "currency")] += whole(
            row["minor_units"]
        )
    return {
        "groups_checked": len(balances),
        "unbalanced": [
            {"transaction": t, "currency": c, "net_minor_units": v}
            for (t, c), v in sorted(balances.items())
            if v
        ],
    }


def project_accuracy_classifier_slices(data):
    """Compute confusion counts, precision and recall per cohort. Input {cases:[{slice,expected,predicted}]} with boolean labels; undefined rates are null."""
    slices = defaultdict(lambda: dict(tp=0, fp=0, tn=0, fn=0))
    for row in records(data, "cases"):
        expected, predicted = row["expected"], row["predicted"]
        if type(expected) is not bool or type(predicted) is not bool:
            raise ValueError("boolean labels required")
        counts = slices[label(row, "slice")]
        counts[
            {
                (True, True): "tp",
                (False, True): "fp",
                (False, False): "tn",
                (True, False): "fn",
            }[expected, predicted]
        ] += 1
    for counts in slices.values():
        tp = counts["tp"]
        counts["precision"] = tp / (tp + counts["fp"]) if tp + counts["fp"] else None
        counts["recall"] = tp / (tp + counts["fn"]) if tp + counts["fn"] else None
    return dict(slices)


def project_safety_approval_trace(data):
    """Audit maker/checker decisions against approvals required. Input {required,transactions:[{id,maker,approvers:[identity]}]}. Repeated identities count once."""
    required = whole(data["required"])
    if required < 1:
        raise ValueError("required must be positive")
    findings = []
    for row in records(data, "transactions"):
        maker, key = label(row, "maker"), label(row, "id")
        approvals = row["approvers"]
        if not isinstance(approvals, list) or not all(
            isinstance(v, str) and v.strip() for v in approvals
        ):
            raise ValueError("invalid approvers")
        unique = set(approvals)
        if maker in unique or len(unique - {maker}) < required:
            findings.append(
                {
                    "id": key,
                    "self_approval": maker in unique,
                    "independent": len(unique - {maker}),
                }
            )
    return {"findings": findings, "checked": len(data["transactions"])}


def project_safety_isolation_matrix(data):
    """Compare intended and observed access decisions by role/resource. Input {cases:[{role,resource,expected,allowed}]} with boolean decisions."""
    mismatches = []
    for idx, row in enumerate(records(data, "cases")):
        role, resource = label(row, "role"), label(row, "resource")
        if type(row["expected"]) is not bool or type(row["allowed"]) is not bool:
            raise ValueError("boolean decisions required")
        if row["expected"] != row["allowed"]:
            mismatches.append(
                {
                    "case": idx,
                    "role": role,
                    "resource": resource,
                    "issue": "overgrant" if row["allowed"] else "overdeny",
                }
            )
    return {"checked": len(data["cases"]), "mismatches": mismatches}


def project_reliability_outage_union(data):
    """Calculate observed availability without double-counting overlapping outages. Input {window_sec,outages:[{start_sec,end_sec}]} using offsets within the window."""
    window = number(data["window_sec"])
    if window == 0:
        raise ValueError("window must be positive")
    ranges = []
    for row in records(data, "outages"):
        a, b = number(row["start_sec"]), number(row["end_sec"])
        if not a <= b <= window:
            raise ValueError("interval outside window")
        ranges.append((a, b))
    down = Decimal(0)
    end = Decimal(0)
    for a, b in sorted(ranges):
        down += max(0, b - max(a, end))
        end = max(end, b)
    return {
        "downtime_sec": float(down),
        "availability_ratio": float((window - down) / window),
    }


def project_reliability_retry_trace(data):
    """Measure request amplification and exhausted operations. Input {attempts:[{id,attempt,success}]}; numbering begins at 1 and stops on success."""
    ops = defaultdict(list)
    for row in records(data, "attempts"):
        key = label(row, "id")
        n = whole(row["attempt"])
        if (
            type(row["success"]) is not bool
            or n != len(ops[key]) + 1
            or (ops[key] and ops[key][-1])
        ):
            raise ValueError("invalid attempt sequence")
        ops[key].append(row["success"])
    return {
        "operations": len(ops),
        "amplification": len(data["attempts"]) / len(ops) if ops else None,
        "without_success": sorted(k for k, v in ops.items() if not v[-1]),
    }
