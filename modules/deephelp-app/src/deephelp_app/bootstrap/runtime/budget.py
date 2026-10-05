"""Explicit live assembly and cumulative budget gate. Importing this module does no I/O."""

from decimal import Decimal
from pathlib import Path
from typing import Any

from deephelp_app.application.dense import atomic_json, load_json
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.common import ErrorCode


class BudgetSession:
    """One process writer; synchronous reservations persist before external dispatch."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.state: dict[str, Any] = load_json(path)
        self.lock_path = path.with_suffix(".lock")
        self.lock: Any = None
        for field in ("max_calls", "max_tokens", "max_cost_cny"):
            value = Decimal(str(self.state.get(field, 0)))
            if not value.is_finite() or value <= 0:
                raise ConfigurationError("Positive cumulative call/token/cost limits required")

    def open(self) -> None:
        try:
            self.lock = self.lock_path.open("x", encoding="utf-8")
        except FileExistsError:
            raise ConfigurationError("MVP budget is locked; inspect previous process") from None
        # Re-read after taking the writer lock; a constructor snapshot cannot authorize spend.
        self.state = load_json(self.path)

    def close(self) -> None:
        if self.lock:
            self.lock.close()
            self.lock_path.unlink()
            self.lock = None

    def request(self) -> ExecutionBudget:
        if self.lock is None:
            raise ConfigurationError("Budget writer is not open")
        calls = int(self.state["max_calls"]) - int(self.state["attempts"])
        tokens = int(self.state["max_tokens"]) - int(self.state["charged_tokens"])
        cost = Decimal(str(self.state["max_cost_cny"])) - Decimal(str(self.state["cost_upper_cny"]))
        if min(calls, tokens) <= 0 or cost <= 0:
            raise AppError(ErrorCode.BUDGET_EXHAUSTED, "MVP cumulative budget exhausted")
        budget = ExecutionBudget.start(
            90,
            min(40, calls),
            2,
            token_limit=min(150000, tokens),
            cost_limit=min(Decimal("2"), cost),
        )
        previous = {
            "attempts": 0,
            "tokens": 0,
            "charged_tokens": 0,
            "cost_upper_cny": Decimal("0"),
            "uncertain_attempts": 0,
        }

        def persist() -> None:
            current = {
                "attempts": budget.attempts_used,
                "tokens": budget.tokens_used,
                "charged_tokens": budget.token_upper_bound,
                "cost_upper_cny": budget.cost_upper_bound,
                "uncertain_attempts": budget.uncertain_attempts,
            }
            for key, value in current.items():
                updated = (
                    Decimal(str(self.state[key]))
                    + Decimal(str(value))
                    - Decimal(str(previous[key]))
                )
                self.state[key] = str(updated) if key == "cost_upper_cny" else int(updated)
            previous.update(current)
            atomic_json(self.path, self.state)
            if (
                self.state["attempts"] > self.state["max_calls"]
                or self.state["charged_tokens"] > self.state["max_tokens"]
                or Decimal(self.state["cost_upper_cny"]) > Decimal(str(self.state["max_cost_cny"]))
            ):
                raise AppError(ErrorCode.BUDGET_EXHAUSTED, "Cumulative reservation exhausted")

        budget.on_change = persist
        return budget


def validate_control_paths(paths: list[Path], *, output: Path | None = None) -> None:
    reserved: set[Path] = set()
    for path in paths:
        owned = {
            path.resolve(),
            path.with_suffix(".lock").resolve(),
            path.with_suffix(".tmp").resolve(),
            path.with_suffix(path.suffix + ".tmp").resolve(),
        }
        if reserved & owned:
            raise ConfigurationError("Auth/budget/pointer/provider control paths must differ")
        reserved.update(owned)
    if output:
        generated = {
            output.resolve(),
            output.with_suffix(".trace.jsonl").resolve(),
            output.with_suffix(".restart.jsonl").resolve(),
        }
        if reserved & generated or len(generated) != 3:
            raise ConfigurationError("Report and derived trace paths overlap control files")


def validate_trace_path(path: Path, controls: list[Path]) -> None:
    validate_control_paths([*controls, path])
    key = path.with_suffix(path.suffix + ".key").resolve()
    if key in {control.resolve() for control in controls}:
        raise ConfigurationError("Trace redaction key overlaps a control file")
