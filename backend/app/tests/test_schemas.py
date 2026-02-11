from pydantic import ValidationError

from app.schemas import RunsCreateRequest, RuntimeConfigUpdate


def test_runs_create_request_rejects_threshold_below_95() -> None:
    try:
        RunsCreateRequest(entry_ids=["ent_1"], quality_threshold=90)
        assert False, "expected ValidationError"
    except ValidationError:
        assert True


def test_runtime_config_update_rejects_threshold_below_95() -> None:
    try:
        RuntimeConfigUpdate(quality_threshold=90)
        assert False, "expected ValidationError"
    except ValidationError:
        assert True


def test_runtime_config_update_rejects_worker_count_out_of_range() -> None:
    try:
        RuntimeConfigUpdate(max_parallel_runs=0)
        assert False, "expected ValidationError"
    except ValidationError:
        assert True

    try:
        RuntimeConfigUpdate(max_parallel_runs=51)
        assert False, "expected ValidationError"
    except ValidationError:
        assert True
