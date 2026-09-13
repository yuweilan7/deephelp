import pytest

from deephelp_app.experiments import cancellation_experiment, cpu_comparison, io_comparison

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("failure", ["timeout", "error"])
async def test_task_group_cancels_sibling_and_joins_before_return(failure):
    assert await cancellation_experiment(failure) == [
        "sibling_cancelled",
        "sibling_cleaned",
        "group_failed",
        "all_tasks_joined",
    ]


async def test_io_experiment_results_without_fragile_millisecond_gate():
    result = await io_comparison(0.02)
    assert result["serial_results"] == result["concurrent_results"] == ["A", "B"]
    assert result["serial_peak"] == 1
    assert result["concurrent_peak"] == 2
    assert result["serial_seconds"] > 0
    assert result["concurrent_seconds"] > 0


async def test_cpu_job_blocks_loop_and_process_allows_heartbeat():
    result = await cpu_comparison(0.1)
    assert result["blocking_heartbeat_ticks"] == 0
    assert result["process_heartbeat_ticks"] > 0
