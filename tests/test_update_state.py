from hanarr.update_state import clear, mark_checked, new_update_state, reset_for_update


def test_new_update_state_starts_unavailable():
    state = new_update_state()
    assert state["available"] is False
    assert state["version"] is None
    assert state["cancelled"] is False


def test_reset_for_update_marks_available_with_details():
    state = new_update_state()
    reset_for_update(
        state, version="0.1.2", notes_url="https://example.test/notes",
        installer_path="/tmp/Hanarr-Setup-0.1.2.exe", apply_at=12345.0,
    )
    assert state["available"] is True
    assert state["version"] == "0.1.2"
    assert state["notes_url"] == "https://example.test/notes"
    assert state["installer_path"] == "/tmp/Hanarr-Setup-0.1.2.exe"
    assert state["apply_at"] == 12345.0
    assert state["cancelled"] is False


def test_reset_for_update_clears_a_previous_error():
    state = new_update_state()
    state["error"] = "previous failure"
    reset_for_update(state, version="0.1.2", notes_url="x", installer_path="x", apply_at=1.0)
    assert state["error"] is None


def test_clear_resets_availability_and_details():
    state = new_update_state()
    reset_for_update(state, version="0.1.2", notes_url="x", installer_path="x", apply_at=1.0)
    clear(state)
    assert state["available"] is False
    assert state["version"] is None
    assert state["installer_path"] is None
    assert state["apply_at"] is None


def test_mark_checked_sets_a_timestamp():
    state = new_update_state()
    assert state["last_checked_at"] is None
    mark_checked(state)
    assert state["last_checked_at"] is not None
