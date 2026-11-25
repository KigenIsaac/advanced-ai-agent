"""No-paid-API tests for routing, estimated budgets and streaming behavior."""
import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from main import parse_args
from smart_routing import (
    BudgetExceeded, BudgetLedger, ModelRoute, RoutedClient,
    choose_route, classify_task,
)


def route(name="cheap", tasks=None, input_price=1.0, output_price=1.0, quality=None):
    return ModelRoute.from_dict({
        "name": name, "provider": "deepseek", "model": "deepseek-flash",
        "base_url": "https://api.deepseek.com",
        "tasks": tasks or ["all"],
        "input_per_million_usd": input_price,
        "output_per_million_usd": output_price,
        "quality": quality or {},
    })


@pytest.mark.parametrize(("prompt", "task"), [
    ("Fix this traceback and debug the failing test", "debug"),
    ("Design a scalable architecture", "planning"),
    ("Review the quality", "review"),
    ("Implement a new endpoint", "coding"),
    ("Check for SQL injection and vulnerabilities", "security"),
    ("Hello there", "general"),
])
def test_task_classification(prompt, task):
    assert classify_task(prompt) == task


def test_router_favors_low_cost_without_fabricated_quality():
    cheap = route("cheap", input_price=1, output_price=1)
    pricey = route("pricey", input_price=9, output_price=9)
    selected, _ = choose_route([pricey, cheap], "Implement an endpoint")
    assert selected == cheap


def test_router_can_use_measured_quality_for_harder_tasks():
    cheap = route("cheap", input_price=1, output_price=1, quality={"debug": 0.4})
    strong = route("strong", input_price=4, output_price=4, quality={"debug": 0.9})
    selected, task = choose_route([cheap, strong], "Fix this bug")
    assert task == "debug"
    assert selected == strong


def test_task_profile_fallbacks_to_general():
    general = route("general", tasks=["general"])
    selected, _ = choose_route([general], "Audit security problems")
    assert selected == general


def test_budget_blocks_and_persists_across_restart(tmp_path):
    path = tmp_path / "ledger.db"
    one = BudgetLedger(path, 0.010)
    expensive = route(input_price=100, output_price=100)
    messages = [{"role": "user", "content": "hello"}]
    key = one.reserve(expensive, messages, 60)
    with pytest.raises(BudgetExceeded):
        BudgetLedger(path, 0.010).reserve(expensive, messages, 60)
    one.finish(key, expensive, {"prompt_tokens": 5, "completion_tokens": 4})
    stats = BudgetLedger(path, 0.010).summary()
    assert stats["pending_reserved_usd"] == 0
    assert stats["charged_estimate_usd"] == pytest.approx(0.0009)


def test_unknown_usage_charges_reservation(tmp_path):
    ledger = BudgetLedger(tmp_path / "ledger.db", 1.0)
    x = route()
    key = ledger.reserve(x, [{"role": "user", "content": "hello"}], 100)
    reserved = ledger.summary()["pending_reserved_usd"]
    assert reserved > 0
    ledger.finish(key, x, None)
    assert ledger.summary()["charged_estimate_usd"] == reserved


def test_concurrent_reservations_cannot_both_exceed_cap(tmp_path):
    filename = tmp_path / "shared.db"
    model = route(input_price=100, output_price=100)
    messages = [{"role": "user", "content": "x"}]
    # One reservation includes the serialized input as well as 100 output tokens.
    ledger = BudgetLedger(filename, 0.020)
    def reserve_once():
        try:
            return ledger.reserve(model, messages, 100)
        except BudgetExceeded:
            return None
    with ThreadPoolExecutor(max_workers=3) as pool:
        outcomes = list(pool.map(lambda _: reserve_once(), range(3)))
    assert sum(x is not None for x in outcomes) == 1


class FakeClient:
    model = "deepseek-flash"
    provider = "deepseek"
    last_key_label = "fake"
    def __init__(self, stream_usage=True):
        self.stream_usage = stream_usage
        self.called = 0
        self.closed = False
    async def stream_chat(self, messages, **kwargs):
        self.called += 1
        yield {"choices": [{"delta": {"content": "ok"}}]}
        if self.stream_usage:
            yield {"usage": {"prompt_tokens": 7, "completion_tokens": 3}, "choices": []}
    def pool_totals(self):
        return {"keys": 1, "total_rpm": 10}
    async def close(self):
        self.closed = True


def test_routed_client_reserves_and_records_usage(tmp_path):
    original = FakeClient()
    created = []
    def factory(rt):
        client = FakeClient()
        created.append(client)
        return client
    ledger = BudgetLedger(tmp_path / "shared.db", 1)
    client = RoutedClient(original, [route()], factory, ledger)
    async def go():
        # Direct client calls must NOT bypass the cost guard.
        return [part async for part in client.stream_chat(
            [{"role": "user", "content": "fix this bug"}], max_tokens=20,
        )]
    result = asyncio.run(go())
    assert len(result) == 2
    assert original.called == 0
    assert created[0].called == 1
    assert ledger.summary()["charged_estimate_usd"] == pytest.approx(0.000010)


def test_one_shot_argument_is_available():
    args = parse_args(["--provider", "deepseek", "--prompt", "fix the test", "--max-rounds", "2"])
    assert args.prompt == "fix the test"
    assert args.max_rounds == 2


def test_negative_price_and_budget_rejected(tmp_path):
    with pytest.raises(ValueError):
        route(input_price=-1)
    with pytest.raises(ValueError):
        BudgetLedger(tmp_path / "x.db", 0)
