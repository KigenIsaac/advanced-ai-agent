"""Capacity and reliability estimates; units are explicit in every contract."""

import math
from .common import positive, number


def _utilization(doc):
    fraction = positive(doc["target_utilization"], "target_utilization")
    if fraction > 1:
        raise ValueError("target_utilization must be in (0,1]")
    return fraction


def project_worker_sizing(doc):
    """Estimate workers from arrival_rps, mean_service_ms and target_utilization (0–1)."""
    offered = (
        number(doc["arrival_rps"], "arrival_rps")
        * positive(doc["mean_service_ms"], "mean_service_ms")
        / 1000
    )
    return {
        "offered_concurrency": offered,
        "workers": math.ceil(offered / _utilization(doc)),
    }


def project_queue_recovery(doc):
    """Estimate time to clear backlog_items at arrival_rps and processing_rps."""
    backlog = number(doc["backlog_items"], "backlog_items")
    spare = number(doc["processing_rps"], "processing_rps") - number(
        doc["arrival_rps"], "arrival_rps"
    )
    return {
        "drainable": not backlog or spare > 0,
        "seconds": 0 if not backlog else backlog / spare if spare > 0 else None,
    }


def project_retention_storage(doc):
    """Estimate steady-state storage from events_per_day, bytes_per_event, retention_days and copies."""
    copies = doc["copies"]
    if type(copies) is not int or not 1 <= copies <= 100:
        raise ValueError("copies must be an integer from 1 to 100")
    size = number(doc["events_per_day"], "events_per_day") * positive(
        doc["bytes_per_event"], "bytes_per_event"
    )
    total = size * positive(doc["retention_days"], "retention_days") * copies
    return {"replicated_bytes": total, "gib": total / 2**30}


def project_transfer_window(doc):
    """Estimate transfer seconds from payload_bytes, link_mbps and target_utilization."""
    seconds = (
        number(doc["payload_bytes"], "payload_bytes")
        * 8
        / (positive(doc["link_mbps"], "link_mbps") * 1_000_000)
    )
    return {"seconds": seconds / _utilization(doc)}


def project_error_budget(doc):
    """Calculate allowed downtime and remaining minutes from window_days, target_percent and outage_minutes."""
    target = number(doc["target_percent"], "target_percent")
    if target > 100:
        raise ValueError("target_percent must be in [0,100]")
    allowed = positive(doc["window_days"], "window_days") * 1440 * (100 - target) / 100
    return {
        "allowed_minutes": allowed,
        "remaining_minutes": allowed - number(doc["outage_minutes"], "outage_minutes"),
    }


def project_retry_envelope(doc):
    """Bound request duration for attempts, timeout_ms, initial_backoff_ms and max_backoff_ms; no jitter."""
    attempts = doc["attempts"]
    if type(attempts) is not int or not 1 <= attempts <= 32:
        raise ValueError("attempts must be an integer from 1 to 32")
    timeout = positive(doc["timeout_ms"], "timeout_ms")
    initial = number(doc["initial_backoff_ms"], "initial_backoff_ms")
    cap = number(doc["max_backoff_ms"], "max_backoff_ms")
    waits = [min(cap, initial * 2**i) for i in range(attempts - 1)]
    return {"waits_ms": waits, "worst_case_ms": attempts * timeout + sum(waits)}


def project_db_pool_budget(doc):
    """Divide max_connections minus reserved_connections across peak_replicas during rollout."""
    maximum, reserved, replicas = (
        doc[k] for k in ("max_connections", "reserved_connections", "peak_replicas")
    )
    if (
        any(type(v) is not int for v in (maximum, reserved, replicas))
        or not 0 <= reserved <= maximum
        or replicas < 1
    ):
        raise ValueError("invalid database connection allocation")
    pool, left = divmod(maximum - reserved, replicas)
    return {"per_replica": pool, "unallocated": left, "can_connect_all": pool >= 1}


def project_cache_origin_load(doc):
    """Estimate origin request rate from request_rps and cache hit_percent."""
    traffic = number(doc["request_rps"], "request_rps")
    hit = number(doc["hit_percent"], "hit_percent")
    if hit > 100:
        raise ValueError("hit_percent must be at most 100")
    return {
        "origin_rps": traffic * (100 - hit) / 100,
        "served_from_cache_rps": traffic * hit / 100,
    }


def project_rollout_capacity(doc):
    """Check remaining replica throughput during a rollout against peak_rps."""
    replicas, unavailable = doc["replicas"], doc["unavailable_replicas"]
    if (
        type(replicas) is not int
        or type(unavailable) is not int
        or not 0 <= unavailable <= replicas
    ):
        raise ValueError("invalid replica counts")
    capacity = (replicas - unavailable) * positive(
        doc["per_replica_rps"], "per_replica_rps"
    )
    margin = capacity - number(doc["peak_rps"], "peak_rps")
    return {"available_rps": capacity, "spare_rps": margin, "meets_peak": margin >= 0}
