from aurono.execution.events import ExecutionEvent, FundsReserved


def test_execution_events_are_frozen():
    assert ExecutionEvent.__dataclass_params__.frozen is True
    assert FundsReserved.__dataclass_params__.frozen is True
