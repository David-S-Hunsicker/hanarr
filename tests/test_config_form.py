from hanarr.config import Settings
from hanarr.dashboard.config_form import apply_preferences_form, settings_to_dict, validate_and_build


def test_apply_preferences_form_reads_multi_select_seniority_checkboxes():
    settings = Settings()
    current = settings_to_dict(settings)

    form = {
        "target_titles": "Backend Engineer",
        "seniority_senior": "on",
        "seniority_staff": "on",
        "employment_type_full_time": "on",
        "locations": "Remote",
        "remote_ok": "on",
        "min_fit_score": "60",
    }
    updated = apply_preferences_form(current, form)
    new_settings, errors = validate_and_build(updated)

    assert not errors
    assert new_settings.preferences.seniority == ["senior", "staff"]


def test_apply_preferences_form_keeps_existing_seniority_when_none_checked():
    settings = Settings()
    settings.preferences.seniority = ["mid"]
    current = settings_to_dict(settings)

    form = {
        "target_titles": "Backend Engineer",
        "employment_type_full_time": "on",
        "locations": "Remote",
        "remote_ok": "on",
        "min_fit_score": "60",
    }
    updated = apply_preferences_form(current, form)
    new_settings, errors = validate_and_build(updated)

    assert not errors
    assert new_settings.preferences.seniority == ["mid"]


def test_apply_preferences_form_single_seniority_checkbox():
    settings = Settings()
    current = settings_to_dict(settings)

    form = {
        "seniority_principal": "on",
        "remote_ok": "on",
        "min_fit_score": "60",
    }
    updated = apply_preferences_form(current, form)
    new_settings, errors = validate_and_build(updated)

    assert not errors
    assert new_settings.preferences.seniority == ["principal"]


def test_apply_preferences_form_reads_multi_select_employment_types():
    settings = Settings()
    current = settings_to_dict(settings)

    form = {
        "employment_type_full_time": "on",
        "employment_type_contract": "on",
        "remote_ok": "on",
        "min_fit_score": "60",
    }
    updated = apply_preferences_form(current, form)
    new_settings, errors = validate_and_build(updated)

    assert not errors
    assert new_settings.preferences.employment_types == ["full_time", "contract"]


def test_apply_preferences_form_no_employment_types_checked_means_no_restriction():
    settings = Settings()
    settings.preferences.employment_types = ["full_time"]
    current = settings_to_dict(settings)

    # Unlike seniority, an empty employment_types selection is a real,
    # intentional state (accept any employment type) -- it should NOT fall
    # back to whatever was previously configured.
    form = {
        "remote_ok": "on",
        "min_fit_score": "60",
    }
    updated = apply_preferences_form(current, form)
    new_settings, errors = validate_and_build(updated)

    assert not errors
    assert new_settings.preferences.employment_types == []
