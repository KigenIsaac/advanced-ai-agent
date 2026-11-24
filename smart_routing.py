"""Opt-in multi-provider task routing and persistent estimated API budget ledger.

No third-party dependencies. The daily cap is a conservative *client-side*
estimate, not a provider billing cap (tokenizers, retries and side fees vary).
"""
from __future__ import annotations

import contextvars
import json
import math
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional


class BudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelRoute:
    name: str
    provider: str
    model: str
    base_url: str
    tasks: tuple[str, ...]
    input_per_million_usd: float
    output_per_million_usd: float
    quality: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "ModelRoute":
        required = ("name", "provider", "model", "base_url", "tasks",
                    "input_per_million_usd", "output_per_million_usd")
        for key in required:
            if key not in data:
                raise ValueError(f"Route requires {key!r}")
        tasks = data["tasks"]
        if not isinstance(tasks, list) or not tasks or not all(isinstance(t, str) for t in tasks):
            raise ValueError("route tasks must be a non-empty array of strings")
        rates = (float(data["input_per_million_usd"]),
                 float(data["output_per_million_usd"]))
        if not all(math.isfinite(x) and 0 <= x < 1000 for x in rates):
            raise ValueError("route pricing must be between 0 and 1000 USD per million tokens")
        quality = data.get("quality", {})
        if not isinstance(quality, dict) or not all(
            isinstance(v, (int, float)) and 0 <= v <= 1 for v in quality.values()
        ):
            raise ValueError("quality must map task names to measured scores in [0,1]")
        return cls(
            name=str(data["name"]).strip(),
            provider=str(data["provider"]).strip().lower(),
            model=str(data["model"]).strip(),
            base_url=str(data["base_url"]).strip(),
            tasks=tuple(tasks),
            input_per_million_usd=rates[0],
            output_per_million_usd=rates[1],
            quality=dict(quality),
        )

    def projected_usd(self, input_tokens: int, output_tokens: int) -> float:
        return (
            max(0, input_tokens) * self.input_per_million_usd
            + max(0, output_tokens) * self.output_per_million_usd
        ) / 1_000_000


def classify_task(text: str) -> str:
    """Small, inspectable keyword router; not a learned quality predictor."""
    sample = (text or "").lower()[:8000]
    rules = (
        ("security", r"\b(security|vulnerab|threat.model|sql injection|xss|csrf|audit)\b"),
        ("debug", r"\b(debug|fix|failing|failure|traceback|stack trace|exception|error|crash|bug)\b"),
        ("planning", r"\b(architect|design|plan|roadmap|system design|scalab|migration)\b"),
        ("review", r"\b(review|refactor|improv|optimi[sz]|critique|assess)\b"),
        ("coding", r"\b(implement|build|code|write|create|feature|test|function|endpoint)\b"),
    )
    for task, pattern in rules:
        if re.search(pattern, sample):
            return task
    return "general"


def choose_route(routes: list[ModelRoute], prompt: str) -> tuple[ModelRoute, str]:
    """Prefer cheaper eligible routes; measured quality can raise harder tasks.

    Quality scores are supplied by a user-run benchmark, never fabricated.
    """
    task = classify_task(prompt)
    eligible = [r for r in routes if task in r.tasks or "all" in r.tasks]
    if not eligible:
        eligible = [r for r in routes if "general" in r.tasks or "all" in r.tasks]
    if not eligible:
        raise ValueError(f"No configured model route can handle task {task!r}")

    def rank(route: ModelRoute):
        cost = route.projected_usd(4000, 2000)
        # Quality-first only when a measured score is present for the task,
        # otherwise select the cheaper model rather than assume one is better.
        score = route.quality.get(task, -1.0)
        if score >= 0 and task in {"security", "debug", "planning", "review", "coding"}:
            return (0, -score, cost, route.name)
        return (1, cost, 0, route.name)

    return min(eligible, key=rank), task


class BudgetLedger:
    """Cross-process safe ledger using short SQLite IMMEDIATE transactions.

    All amounts stored as integer micro-US dollars. Unknown response usage
    charges the reserved ceiling. UTC day boundaries are used throughout.
    """
    def __init__(self, path: str | Path, daily_budget_usd: float):
        self.path = Path(path)
        self.daily_budget_usd = float(daily_budget_usd)
        if not math.isfinite(self.daily_budget_usd) or not 0 < self.daily_budget_usd <= 1_000_000:
            raise ValueError("daily_budget_usd must be positive and finite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS calls ("
                "id TEXT PRIMARY KEY, utc_date TEXT NOT NULL, "
                "provider TEXT NOT NULL, model TEXT NOT NULL, "
                "reserved_micro INTEGER NOT NULL, charged_micro INTEGER NOT NULL, "
                "pending INTEGER NOT NULL)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS calls_day ON calls(utc_date)")

    def _conn(self):
        return sqlite3.connect(self.path, timeout=30, isolation_level=None)

    @staticmethod
    def _micros(usd: float) -> int:
        import math
        return max(0, math.ceil(usd * 1_000_000))

    def reserve(self, route: ModelRoute, messages: list[dict], max_tokens: int) -> str:
        if route.input_per_million_usd <= 0 or route.output_per_million_usd <= 0:
            # Zero-price local providers should not be used for this paid ledger.
            raise ValueError("Configured monetary budgets require positive rates for every routed cloud model")
        # Approximation: one charged input token per JSON character (normally
        # a substantial overestimate for English), plus max output tokens.
        chars = len(json.dumps(messages, ensure_ascii=False, default=str))
        reserve_usd = route.projected_usd(max(1, chars), max(1, int(max_tokens)))
        reserved = self._micros(reserve_usd)
        cap = self._micros(self.daily_budget_usd)
        today = datetime.now(timezone.utc).date().isoformat()
        key = uuid.uuid4().hex
        with self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                spent = db.execute(
                    "SELECT COALESCE(SUM(charged_micro + "
                    "CASE WHEN pending=1 THEN reserved_micro ELSE 0 END),0) "
                    "FROM calls WHERE utc_date=?", (today,)
                ).fetchone()[0]
                if spent + reserved > cap:
                    raise BudgetExceeded(
                        f"Estimated API budget reached: already allocated ${spent / 1_000_000:.4f} "
                        f"of ${self.daily_budget_usd:.4f} UTC daily cap; "
                        f"next request reserves ${reserve_usd:.4f}. "
                        "Reduce max-tokens, change route rates, or increase the cap explicitly."
                    )
                db.execute(
                    "INSERT INTO calls VALUES (?, ?, ?, ?, ?, 0, 1)",
                    (key, today, route.provider, route.model, reserved),
                )
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
        return key

    def finish(self, key: str, route: ModelRoute, usage: Optional[dict]) -> None:
        charged = None
        if usage:
            prompt = usage.get("prompt_tokens")
            completion = usage.get("completion_tokens")
            if isinstance(prompt, (int, float)) and isinstance(completion, (int, float)):
                charged = self._micros(route.projected_usd(prompt, completion))
        with self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                reserved = db.execute(
                    "SELECT reserved_micro FROM calls WHERE id=? AND pending=1", (key,)
                ).fetchone()
                if reserved:
                    # Missing usage keeps the worst-case reservation.
                    db.execute(
                        "UPDATE calls SET charged_micro=?, reserved_micro=0, pending=0 WHERE id=?",
                        (charged if charged is not None else reserved[0], key),
                    )
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

    def summary(self) -> dict:
        today = datetime.now(timezone.utc).date().isoformat()
        with self._conn() as db:
            row = db.execute(
                "SELECT COALESCE(SUM(charged_micro),0),"
                " COALESCE(SUM(CASE WHEN pending=1 THEN reserved_micro ELSE 0 END),0),"
                " COUNT(*) FROM calls WHERE utc_date=?", (today,)
            ).fetchone()
        return {
            "utc_date": today,
            "budget_usd": self.daily_budget_usd,
            "charged_estimate_usd": row[0] / 1_000_000,
            "pending_reserved_usd": row[1] / 1_000_000,
            "calls": row[2],
        }


class RoutedClient:
    """Lazy per-provider API clients, safe for concurrent agent-worker contexts."""
    def __init__(
        self, primary: Any, routes: list[ModelRoute],
        factory: Callable[[ModelRoute], Any], ledger: Optional[BudgetLedger] = None,
        announce: Optional[Callable[[str], None]] = None,
    ):
        self.primary = primary
        self.routes = routes
        self._factory = factory
        self._clients: dict[str, Any] = {}
        self.ledger = ledger
        self.announce = announce
        self._current = contextvars.ContextVar("model_route", default=None)
        self.last_route: Optional[ModelRoute] = None

    @property
    def model(self) -> str:
        route = self._current.get()
        return route.model if route else self.primary.model

    @property
    def provider(self) -> str:
        route = self._current.get()
        return route.provider if route else self.primary.provider

    @property
    def last_key_label(self) -> str:
        route = self._current.get()
        client = self._clients.get(route.name) if route else self.primary
        return getattr(client, "last_key_label", "—")

    def select_task(self, prompt: str) -> None:
        route, task = choose_route(self.routes, prompt)
        self._current.set(route)
        self.last_route = route
        if self.announce:
            self.announce(f"Routed {task} task → {route.provider}/{route.model}")

    def pool_totals(self) -> dict:
        return self.primary.pool_totals()

    async def close(self) -> None:
        for client in self._clients.values():
            await client.close()
        await self.primary.close()

    async def stream_chat(self, messages: list[dict], **kwargs):
        route = self._current.get()
        if route is None:
            # An unselected direct client call must not bypass the budget.
            last_user = next(
                (m.get("content", "") for m in reversed(messages)
                 if m.get("role") == "user"), ""
            )
            self.select_task(str(last_user))
            route = self._current.get()
        client = self._clients.get(route.name)
        if client is None:
            client = self._factory(route)
            self._clients[route.name] = client

        reservation = None
        if self.ledger is not None and route.provider not in {"local", "ollama"}:
            reservation = self.ledger.reserve(route, messages, kwargs.get("max_tokens", 16384))
        usage = None
        try:
            async for chunk in client.stream_chat(messages, **kwargs):
                if isinstance(chunk, dict) and isinstance(chunk.get("usage"), dict):
                    usage = chunk["usage"]
                yield chunk
        finally:
            if reservation is not None:
                self.ledger.finish(reservation, route, usage)
