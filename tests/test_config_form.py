from hanarr.config import Settings
from hanarr.dashboard.config_form import apply_preferences_form, custom_dealbreakers, settings_to_dict, validate_and_build
from hanarr.matching import COMMON_DEALBREAKERS


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


def test_apply_preferences_form_combines_checked_common_and_custom_dealbreakers():
    settings = Settings()
    current = settings_to_dict(settings)
    on_call_key = COMMON_DEALBREAKERS[0][0]
    on_call_label = COMMON_DEALBREAKERS[0][1]

    form = {
        f"dealbreaker_{on_call_key}": "on",
        "dealbreakers_custom": "Must allow pets in the office\nNo mandatory unpaid overtime",
        "remote_ok": "on",
        "min_fit_score": "60",
    }
    updated = apply_preferences_form(current, form)
    new_settings, errors = validate_and_build(updated)

    assert not errors
    assert new_settings.preferences.dealbreakers == [
        on_call_label, "Must allow pets in the office", "No mandatory unpaid overtime",
    ]


def test_apply_preferences_form_unchecking_a_common_dealbreaker_removes_it():
    settings = Settings()
    settings.preferences.dealbreakers = [COMMON_DEALBREAKERS[0][1]]
    current = settings_to_dict(settings)

    form = {"remote_ok": "on", "min_fit_score": "60"}  # box left unchecked
    updated = apply_preferences_form(current, form)
    new_settings, errors = validate_and_build(updated)

    assert not errors
    assert new_settings.preferences.dealbreakers == []


def test_custom_dealbreakers_excludes_common_labels():
    common_label = COMMON_DEALBREAKERS[0][1]
    stored = [common_label, "Must allow pets in the office"]

    assert custom_dealbreakers(stored) == ["Must allow pets in the office"]
