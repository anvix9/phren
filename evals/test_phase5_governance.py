"""
Tessera Phase 5 — Governance Eval Suite (pytest)

Runs all governance boundary tasks through the eval harness.
Each task is a separate pytest test with a clear pass/fail.
"""
import pytest
from evals.harness import EvalRunner
from evals.tasks.governance import governance_tasks


# Load all governance tasks
_TASKS = governance_tasks()
_RUNNER = EvalRunner()


@pytest.fixture(scope="module")
def all_results():
    """Run all tasks once, share results across tests."""
    runner = EvalRunner()
    runner.add_tasks(_TASKS)
    results = runner.run_all()
    # Print report for visibility
    EvalRunner.print_report(results)
    return {r.task_id: r for r in results}


# ── Generate one test per task ──

class TestGovernanceBoundary:
    """
    Each governance task becomes a test.
    Tests are named by task ID for clear reporting.
    """

    @pytest.mark.parametrize("task", _TASKS, ids=[t.id for t in _TASKS])
    def test_governance_task(self, task):
        runner = EvalRunner()
        result = runner.run_task(task)
        if not result.passed:
            # Show step-level detail on failure
            detail = "\n".join(
                f"  Step {s.step_index}: {s.action} expected={s.expected} "
                f"actual={s.actual} reason={s.reason[:80]}"
                for s in result.step_results if not s.passed
            )
            pytest.fail(
                f"Task '{task.id}' failed:\n"
                f"  {task.description}\n"
                f"{detail}"
            )


class TestGovernanceSummary:
    """Summary assertions across all governance tasks."""

    def test_all_tasks_loaded(self):
        assert len(_TASKS) >= 15, f"Expected at least 15 governance tasks, got {len(_TASKS)}"

    def test_all_categories_covered(self):
        categories = {t.category for t in _TASKS}
        expected = {"tier_escalation", "spend_limits", "rate_limits",
                    "contract_expiry", "item_limits", "user_consent"}
        missing = expected - categories
        assert not missing, f"Missing categories: {missing}"

    def test_full_suite_pass_rate(self, all_results):
        total = len(all_results)
        passed = sum(1 for r in all_results.values() if r.passed)
        rate = passed / total if total > 0 else 0
        # CI gate: 100% pass rate required for governance tasks
        assert rate == 1.0, (
            f"Governance pass rate: {passed}/{total} ({rate*100:.0f}%). "
            f"All governance tasks must pass."
        )
