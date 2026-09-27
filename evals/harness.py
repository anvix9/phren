"""
Tessera Phase 5 — Eval Harness

Runs tasks against the terminal engine and measures:
  1. Governance correctness: did the terminal deny what it should deny?
  2. Task completion: did the scripted sequence reach the expected state?
  3. Metrics: steps, denials, errors, elapsed time

Two modes:
  - Scripted: predetermined action sequences (no LLM needed)
  - Agent: LLM picks actions (requires Ollama — Phase 5b)

Task format:
  {
    "id": "shopping-anon-denied-checkout",
    "site": "shopping",
    "description": "Anonymous agent should be denied checkout",
    "agent": {"trust_level": "anonymous", ...},
    "contract_overrides": {"rate_limits": {"max_transaction_amount": 100}},
    "steps": [
      {"action": "search", "params": {"q": "laptop"}, "expect": "success"},
      {"action": "place_order", "params": {...}, "expect": "denied"},
    ],
    "assert_governance": {
      "denied_actions": ["place_order"],
      "denial_reason_contains": "trust level"
    }
  }
"""
import json
import time
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone, timedelta

from tessera.contract.schema import (
    TesseraContract, AgentProfile, AgentTrust, AgentCapabilities, RateLimit,
)
from tessera.terminal.engine import TesseraTerminal


# ── Task Definition ──

@dataclass
class TaskStep:
    """A single step in a scripted task."""
    action: str
    params: dict = field(default_factory=dict)
    expect: str = "success"  # "success", "denied", "error", "confirmation_required"
    expect_reason_contains: Optional[str] = None  # substring in denial reason


@dataclass
class TaskDefinition:
    """A complete eval task."""
    id: str
    site: str
    description: str
    agent_config: dict  # → AgentProfile
    steps: list[TaskStep]
    contract_overrides: dict = field(default_factory=dict)
    category: str = "general"  # "governance", "workflow", "injection", "stress"


# ── Task Result ──

@dataclass
class StepResult:
    """Result of executing one step."""
    step_index: int
    action: str
    expected: str
    actual: str
    passed: bool
    reason: str = ""
    response: dict = field(default_factory=dict)


@dataclass
class TaskResult:
    """Result of running a complete task."""
    task_id: str
    category: str
    description: str
    passed: bool
    steps_total: int
    steps_passed: int
    steps_failed: int
    step_results: list[StepResult]
    elapsed_ms: float
    failure_reason: str = ""


# ── Eval Runner ──

class EvalRunner:
    """
    Runs tasks against a Tessera terminal and collects results.

    Usage:
        runner = EvalRunner()
        runner.add_task(task)
        results = runner.run_all()
        runner.print_report(results)
    """

    def __init__(self):
        self.tasks: list[TaskDefinition] = []

    def add_task(self, task: TaskDefinition):
        self.tasks.append(task)

    def add_tasks(self, tasks: list[TaskDefinition]):
        self.tasks.extend(tasks)

    def run_task(self, task: TaskDefinition) -> TaskResult:
        """Run a single task and return the result."""
        start = time.monotonic()

        # Build contract
        contract_data = {
            "contract_id": f"eval-{task.id}",
            "site_name": f"Eval: {task.site}",
            "site_url": "http://localhost:9999",
            "require_identification": False,
            **task.contract_overrides,
        }
        contract = TesseraContract(**contract_data)
        terminal = TesseraTerminal(contract, "http://localhost:9999")

        # Build agent
        agent_config = {
            "provider": "eval",
            "agent_name": "eval-agent",
            "trust_level": AgentTrust.ANONYMOUS,
            "purpose": "eval",
            **task.agent_config,
        }
        # Handle trust_level as string
        if isinstance(agent_config.get("trust_level"), str):
            agent_config["trust_level"] = AgentTrust(agent_config["trust_level"])
        # Handle capabilities
        if "capabilities" in agent_config and isinstance(agent_config["capabilities"], dict):
            agent_config["capabilities"] = AgentCapabilities(**agent_config["capabilities"])

        agent = AgentProfile(**agent_config)

        # Connect
        connect_result = terminal.connect(agent)
        step_results = []

        if connect_result["status"] == "denied":
            # Check if denial was expected
            if task.steps and task.steps[0].expect == "connect_denied":
                step_results.append(StepResult(
                    step_index=0, action="connect", expected="connect_denied",
                    actual="denied", passed=True,
                    reason=connect_result.get("reason", ""),
                ))
                elapsed = (time.monotonic() - start) * 1000
                return TaskResult(
                    task_id=task.id, category=task.category,
                    description=task.description, passed=True,
                    steps_total=1, steps_passed=1, steps_failed=0,
                    step_results=step_results, elapsed_ms=elapsed,
                )
            else:
                step_results.append(StepResult(
                    step_index=0, action="connect", expected="success",
                    actual="denied", passed=False,
                    reason=connect_result.get("reason", ""),
                ))
                elapsed = (time.monotonic() - start) * 1000
                return TaskResult(
                    task_id=task.id, category=task.category,
                    description=task.description, passed=False,
                    steps_total=len(task.steps), steps_passed=0, steps_failed=1,
                    step_results=step_results, elapsed_ms=elapsed,
                    failure_reason=f"Connect denied: {connect_result.get('reason', '')}",
                )

        session_id = connect_result["session_id"]

        # Execute steps
        for i, step in enumerate(task.steps):
            try:
                result = terminal.execute_action(
                    session_id, step.action, step.params,
                    confirmed=step.params.get("confirmed", False),
                )

                actual = result.get("status", "unknown")
                reason = result.get("reason", result.get("message", ""))

                # Check expectation
                passed = actual == step.expect

                # Check reason substring if specified
                if passed and step.expect_reason_contains:
                    if step.expect_reason_contains.lower() not in reason.lower():
                        passed = False
                        reason = f"Expected reason containing '{step.expect_reason_contains}', got: {reason}"

                step_results.append(StepResult(
                    step_index=i, action=step.action,
                    expected=step.expect, actual=actual,
                    passed=passed, reason=reason, response=result,
                ))

            except Exception as e:
                step_results.append(StepResult(
                    step_index=i, action=step.action,
                    expected=step.expect, actual="exception",
                    passed=(step.expect == "error"),
                    reason=str(e),
                ))

        elapsed = (time.monotonic() - start) * 1000
        passed_count = sum(1 for s in step_results if s.passed)
        failed_count = sum(1 for s in step_results if not s.passed)
        all_passed = failed_count == 0

        failure_reason = ""
        if not all_passed:
            first_fail = next(s for s in step_results if not s.passed)
            failure_reason = (
                f"Step {first_fail.step_index}: {first_fail.action} "
                f"expected={first_fail.expected} actual={first_fail.actual} "
                f"reason={first_fail.reason}"
            )

        return TaskResult(
            task_id=task.id, category=task.category,
            description=task.description, passed=all_passed,
            steps_total=len(step_results), steps_passed=passed_count,
            steps_failed=failed_count, step_results=step_results,
            elapsed_ms=elapsed, failure_reason=failure_reason,
        )

    def run_all(self) -> list[TaskResult]:
        """Run all tasks and return results."""
        return [self.run_task(t) for t in self.tasks]

    @staticmethod
    def print_report(results: list[TaskResult]):
        """Print a summary report."""
        total = len(results)
        passed = sum(1 for r in results if r.passed)
        failed = total - passed

        print(f"\n{'='*65}")
        print(f"  TESSERA EVAL REPORT")
        print(f"{'='*65}")
        print(f"  Total: {total}  Passed: {passed}  Failed: {failed}")
        print(f"  Pass rate: {passed/total*100:.0f}%" if total > 0 else "  No tasks")
        print(f"{'='*65}\n")

        # By category
        categories = {}
        for r in results:
            cat = r.category
            if cat not in categories:
                categories[cat] = {"passed": 0, "failed": 0, "results": []}
            if r.passed:
                categories[cat]["passed"] += 1
            else:
                categories[cat]["failed"] += 1
            categories[cat]["results"].append(r)

        for cat, data in sorted(categories.items()):
            total_cat = data["passed"] + data["failed"]
            print(f"  [{cat.upper()}] {data['passed']}/{total_cat}")
            for r in data["results"]:
                icon = "✅" if r.passed else "❌"
                ms = f"{r.elapsed_ms:.0f}ms"
                print(f"    {icon} {r.task_id:45} {r.steps_passed}/{r.steps_total} steps  {ms}")
                if not r.passed:
                    print(f"       → {r.failure_reason[:80]}")
            print()

    @staticmethod
    def to_json(results: list[TaskResult]) -> str:
        """Export results as JSON for CI gating."""
        return json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total": len(results),
            "passed": sum(1 for r in results if r.passed),
            "failed": sum(1 for r in results if not r.passed),
            "results": [
                {
                    "task_id": r.task_id,
                    "category": r.category,
                    "passed": r.passed,
                    "steps_total": r.steps_total,
                    "steps_passed": r.steps_passed,
                    "elapsed_ms": r.elapsed_ms,
                    "failure_reason": r.failure_reason,
                }
                for r in results
            ],
        }, indent=2)
