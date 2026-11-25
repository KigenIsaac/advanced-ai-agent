"""Implementation plans with explicit prerequisites, estimates and acceptance tests."""

from graphlib import TopologicalSorter, CycleError
from .common import items, mapping, names, positive


def parse(doc):
    tasks = {}
    for entry in items(doc["tasks"], "tasks"):
        t = mapping(entry, "task")
        (key,) = names([t["id"]], "id")
        if key in tasks:
            raise ValueError("duplicate task " + key)
        tasks[key] = {
            "deps": names(t.get("depends", []), "depends"),
            "hours": positive(t["hours"], "hours"),
            "tests": names(t.get("tests", []), "tests"),
            "owner": t.get("owner"),
        }
        if tasks[key]["owner"] is not None:
            names([tasks[key]["owner"]], "owner")
    for task in tasks.values():
        if set(task["deps"]) - tasks.keys():
            raise ValueError("unknown prerequisite")
    graph = TopologicalSorter({k: v["deps"] for k, v in tasks.items()})
    try:
        graph.prepare()
    except CycleError as e:
        raise ValueError(f"dependency cycle: {e.args[1]}") from e
    waves = []
    while graph.is_active():
        wave = sorted(graph.get_ready())
        waves.append(wave)
        graph.done(*wave)
    return tasks, waves


def project_build_waves(plan):
    """Group implementation tasks into parallel dependency waves."""
    return plan[1]


def project_earliest_schedule(plan):
    """Calculate earliest start and finish hours with unlimited workers."""
    tasks, waves = plan
    schedule = {}
    for wave in waves:
        for key in wave:
            start = max((schedule[d]["finish"] for d in tasks[key]["deps"]), default=0)
            schedule[key] = {"start": start, "finish": start + tasks[key]["hours"]}
    return schedule


def project_critical_path(plan):
    """Return one longest prerequisite chain and its duration in hours."""
    tasks, _ = plan
    schedule = project_earliest_schedule(plan)
    chain = []
    key = max(schedule, key=lambda k: schedule[k]["finish"], default=None)
    while key is not None:
        chain.append(key)
        key = max(tasks[key]["deps"], key=lambda k: schedule[k]["finish"], default=None)
    return {
        "path": list(reversed(chain)),
        "hours": schedule[chain[0]]["finish"] if chain else 0,
    }


def project_task_slack(plan):
    """Calculate how many hours each task can slip before project completion."""
    tasks, waves = plan
    schedule = project_earliest_schedule(plan)
    duration = max((v["finish"] for v in schedule.values()), default=0)
    latest = {}
    for wave in reversed(waves):
        for key in wave:
            finish = min(
                (latest[k] for k, v in tasks.items() if key in v["deps"]),
                default=duration,
            )
            latest[key] = finish - tasks[key]["hours"]
    return {k: latest[k] - schedule[k]["start"] for k in schedule}


def project_dependency_impact(plan):
    """Identify all downstream tasks affected by a delay to each task."""
    tasks, waves = plan
    impacted = {k: set() for k in tasks}
    for wave in reversed(waves):
        for key in wave:
            for dep in tasks[key]["deps"]:
                impacted[dep].add(key)
                impacted[dep].update(impacted[key])
    return {k: sorted(v) for k, v in impacted.items()}


def project_effort_by_owner(plan):
    """Aggregate estimated hours by owner and report unassigned work separately."""
    loads, unassigned = {}, 0
    for task in plan[0].values():
        owner = task["owner"]
        if owner is None:
            unassigned += task["hours"]
        else:
            loads[owner] = loads.get(owner, 0) + task["hours"]
    return {"owners": loads, "unassigned_hours": unassigned}


def project_acceptance_trace(plan):
    """Map acceptance test IDs to the implementation tasks that require them."""
    tests = {}
    for key, task in plan[0].items():
        for test in task["tests"]:
            tests.setdefault(test, []).append(key)
    return tests


def project_handoff_map(plan):
    """Identify prerequisite handoffs between named implementation owners."""
    tasks, _ = plan
    return [
        {
            "from_task": d,
            "to_task": k,
            "from_owner": tasks[d]["owner"],
            "to_owner": v["owner"],
        }
        for k, v in tasks.items()
        for d in v["deps"]
        if tasks[d]["owner"] and v["owner"] and tasks[d]["owner"] != v["owner"]
    ]


def project_release_gate(plan):
    """Find planned tasks missing an owner or a declared acceptance test."""
    gaps = {
        k: [
            reason
            for reason, missing in (
                ("owner", not v["owner"]),
                ("tests", not v["tests"]),
            )
            if missing
        ]
        for k, v in plan[0].items()
    }
    return {
        "ready": bool(gaps) and not any(gaps.values()),
        "gaps": {k: v for k, v in gaps.items() if v},
    }
