"""Analyze deterministic business-process state machines."""

from collections import deque
from .common import items, names, mapping


def parse(doc):
    states = names(doc["states"], "states")
    initial = doc["initial"]
    if initial not in states:
        raise ValueError("initial state must exist")
    terminal = names(doc.get("terminal", []), "terminal")
    if set(terminal) - set(states):
        raise ValueError("unknown terminal state")
    edges = {s: {} for s in states}
    for row in items(doc["transitions"], "transitions"):
        row = mapping(row, "transition")
        start, event, end = row["from"], row["event"], row["to"]
        names([event], "event")
        if (
            start not in edges
            or end not in edges
            or event in edges[start]
            or start in terminal
        ):
            raise ValueError("invalid, ambiguous or outgoing terminal transition")
        edges[start][event] = end
    events = items(doc.get("events", []), "events")
    if not all(isinstance(e, str) for e in events):
        raise ValueError("event trace must contain strings")
    return edges, initial, terminal, events


def _paths(edges, initial):
    paths = {initial: []}
    queue = deque([initial])
    while queue:
        current = queue.popleft()
        for event, dest in edges[current].items():
            if dest not in paths:
                paths[dest] = paths[current] + [event]
                queue.append(dest)
    return paths


def project_state_reachability(machine):
    """List declared states unreachable from the initial state."""
    edges, initial, _, _ = machine
    return sorted(edges.keys() - _paths(edges, initial).keys())


def project_dead_end_states(machine):
    """Find reachable nonterminal states with no enabled event."""
    edges, initial, terminal, _ = machine
    return [s for s in _paths(edges, initial) if s not in terminal and not edges[s]]


def project_completion_traps(machine):
    """Identify reachable states with no path to any declared terminal state."""
    edges, initial, terminal, _ = machine
    can_finish = set(terminal)
    while True:
        prev = len(can_finish)
        can_finish.update(
            s
            for s, events in edges.items()
            if any(d in can_finish for d in events.values())
        )
        if len(can_finish) == prev:
            return sorted(_paths(edges, initial).keys() - can_finish)


def project_event_permissions(machine):
    """List the states in which each domain event is allowed."""
    result = {}
    for state, events in machine[0].items():
        for event in events:
            result.setdefault(event, []).append(state)
    return result


def project_transition_test_cases(machine):
    """Generate setup, action and expected state for each reachable transition."""
    edges, initial, _, _ = machine
    return [
        {"setup": path, "from": s, "event": event, "expected": dest}
        for s, path in _paths(edges, initial).items()
        for event, dest in edges[s].items()
    ]


def project_invalid_event_matrix(machine):
    """Find defined events that must be rejected in each reachable state."""
    edges, initial, _, _ = machine
    all_events = set(project_event_permissions(machine))
    return {s: sorted(all_events - edges[s].keys()) for s in _paths(edges, initial)}


def project_state_replay(machine):
    """Replay an ordered event trace and report the first invalid event and last state."""
    edges, state, _, events = machine
    visited = [state]
    for index, event in enumerate(events):
        if event not in edges[state]:
            return {
                "accepted": False,
                "index": index,
                "event": event,
                "visited": visited,
            }
        state = edges[state][event]
        visited.append(state)
    return {"accepted": True, "visited": visited}


def project_terminal_scenarios(machine):
    """Generate one shortest acceptance scenario reaching each attainable terminal state."""
    edges, initial, terminal, _ = machine
    paths = _paths(edges, initial)
    return {s: paths[s] for s in terminal if s in paths}


def project_state_table(machine):
    """Generate a state-by-event decision table with explicit rejection cells."""
    edges = machine[0]
    events = sorted(project_event_permissions(machine))
    return {s: {e: edges[s].get(e) for e in events} for s in edges}


def project_state_reducer(machine):
    """Generate a standalone Python state reducer with an explicit invalid-transition error."""
    edges, initial, _, _ = machine
    return (
        f"INITIAL = {initial!r}\nTRANSITIONS = {edges!r}\n\n\n"
        "def transition(state, event):\n"
        "    try:\n"
        "        return TRANSITIONS[state][event]\n"
        "    except KeyError:\n"
        '        raise ValueError(f"invalid transition: {state!r}, {event!r}") from None\n'
    )


def project_state_test_module(machine):
    """Generate pytest acceptance tests for a sibling state_machine.py reducer."""
    cases = [
        (c["setup"], c["event"], c["expected"])
        for c in project_transition_test_cases(machine)
    ]
    rejected = [
        (s, e)
        for s, events in project_invalid_event_matrix(machine).items()
        for e in events
    ]
    # Bound the serialized cases before generating a module.
    if len(rejected) > 1000:
        raise ValueError("test matrix exceeds 1000 negative cases")
    return (
        "import pytest\nfrom state_machine import INITIAL, transition\n\n\n"
        f'@pytest.mark.parametrize("setup,event,expected", {cases!r})\n'
        "def test_valid_transition(setup, event, expected):\n"
        "    state = INITIAL\n    for step in setup:\n        state = transition(state, step)\n"
        "    assert transition(state, event) == expected\n\n\n"
        f'@pytest.mark.parametrize("state,event", {rejected!r})\n'
        "def test_invalid_transition(state, event):\n"
        "    with pytest.raises(ValueError):\n        transition(state, event)\n"
    )
