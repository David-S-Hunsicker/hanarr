from jobcopilot.dashboard.app import task_is_stuck


def test_task_is_stuck_false_when_not_running():
    assert task_is_stuck({"running": False, "started_at": None}, 1800.0) is False
    assert task_is_stuck({"running": False, "started_at": 100.0}, 1800.0, now=99999.0) is False


def test_task_is_stuck_false_when_started_at_missing():
    assert task_is_stuck({"running": True, "started_at": None}, 60.0, now=99999.0) is False


def test_task_is_stuck_false_within_ceiling():
    assert task_is_stuck({"running": True, "started_at": 1000.0}, 1800.0, now=1100.0) is False


def test_task_is_stuck_true_past_ceiling():
    # ceiling = timeout_seconds + 30s buffer
    assert task_is_stuck({"running": True, "started_at": 1000.0}, 60.0, now=1000.0 + 60.0 + 30.0 + 1) is True


def test_task_is_stuck_false_exactly_at_ceiling():
    assert task_is_stuck({"running": True, "started_at": 1000.0}, 60.0, now=1000.0 + 60.0 + 30.0) is False
