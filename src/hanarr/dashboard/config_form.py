"""Builds/parses the dashboard's config-editing form.

Reuses the Pydantic models in `config.py` as the single source of truth for
validation — a save either produces a valid `Settings` object or is
rejected with the same errors `load_settings()` would raise, so the
dashboard can never write a broken config.yaml.
"""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from .. import secrets_store
from ..config import Settings
from ..config import save_settings_to_yaml as _save_settings_to_yaml
from ..matching import EMPLOYMENT_TYPES, SENIORITY_LEVELS

# Fields the dashboard's structured forms cover, grouped by tab. Anything
# outside these paths (e.g. secrets like llm.api_key or SMTP password) is
# left untouched — it's set via .env, never through the web UI.
PREFERENCES_LIST_FIELDS = [
    "preferences.target_titles",
    "preferences.keywords_boost",
    "preferences.keywords_exclude",
    "preferences.employment_types",
    "preferences.locations",
    "preferences.industries_include",
    "preferences.industries_exclude",
    "preferences.dealbreakers",
    "sources.greenhouse.company_boards",
    "sources.lever.companies",
    "sources.ashby.company_boards",
    "sources.remoteok.tags",
]


def _list_from_form(raw: str) -> list[str]:
    """Textareas hold one entry per line; blank lines are dropped."""
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _list_to_form(values: list[str]) -> str:
    return "\n".join(values)


def settings_to_dict(settings: Settings) -> dict[str, Any]:
    return settings.model_dump(mode="json", exclude={"data_dir"})


def apply_preferences_form(current: dict[str, Any], form: dict[str, str]) -> dict[str, Any]:
    """Returns an updated copy of the settings dict from the Preferences tab's form fields."""
    data = dict(current)
    prefs = dict(data["preferences"])
    matching = dict(data["matching"])
    sources = {k: dict(v) for k, v in data["sources"].items()}

    prefs["target_titles"] = _list_from_form(form.get("target_titles", ""))
    prefs["keywords_boost"] = _list_from_form(form.get("keywords_boost", ""))
    prefs["keywords_exclude"] = _list_from_form(form.get("keywords_exclude", ""))
    prefs["seniority"] = [level for level in SENIORITY_LEVELS if f"seniority_{level}" in form] or prefs["seniority"]
    prefs["employment_types"] = [t for t in EMPLOYMENT_TYPES if f"employment_type_{t}" in form]
    prefs["locations"] = _list_from_form(form.get("locations", ""))
    prefs["remote_ok"] = "remote_ok" in form
    prefs["onsite_ok"] = "onsite_ok" in form
    prefs["willing_to_relocate"] = "willing_to_relocate" in form
    prefs["work_country"] = form.get("work_country", prefs["work_country"]).strip()
    prefs["salary_floor_usd"] = _int_or_none(form.get("salary_floor_usd"))
    prefs["company_size_min"] = _int_or_none(form.get("company_size_min"))
    prefs["company_size_max"] = _int_or_none(form.get("company_size_max"))
    prefs["industries_include"] = _list_from_form(form.get("industries_include", ""))
    prefs["industries_exclude"] = _list_from_form(form.get("industries_exclude", ""))
    prefs["dealbreakers"] = _list_from_form(form.get("dealbreakers", ""))

    matching["min_fit_score"] = _int_or_none(form.get("min_fit_score")) or 0

    sources["greenhouse"]["enabled"] = "greenhouse_enabled" in form
    sources["greenhouse"]["company_boards"] = _list_from_form(form.get("greenhouse_company_boards", ""))
    sources["lever"]["enabled"] = "lever_enabled" in form
    sources["lever"]["companies"] = _list_from_form(form.get("lever_companies", ""))
    sources["ashby"]["enabled"] = "ashby_enabled" in form
    sources["ashby"]["company_boards"] = _list_from_form(form.get("ashby_company_boards", ""))
    sources["remoteok"]["enabled"] = "remoteok_enabled" in form
    sources["remoteok"]["tags"] = _list_from_form(form.get("remoteok_tags", ""))
    sources["arbeitnow"]["enabled"] = "arbeitnow_enabled" in form

    data["preferences"] = prefs
    data["matching"] = matching
    data["sources"] = sources
    return data


def apply_app_config_form(current: dict[str, Any], form: dict[str, str]) -> dict[str, Any]:
    data = dict(current)
    profile = dict(data["profile"])
    llm = dict(data["llm"])
    dashboard = dict(data["dashboard"])

    profile["resume_path"] = form.get("resume_path", profile["resume_path"])

    llm["provider"] = form.get("llm_provider", llm["provider"])
    llm["model"] = form.get("llm_model", llm["model"])
    llm["base_url"] = form.get("llm_base_url", llm["base_url"])
    llm["timeout_seconds"] = float(_int_or_none(form.get("llm_timeout_seconds")) or llm["timeout_seconds"])

    dashboard["host"] = form.get("dashboard_host", dashboard["host"])
    dashboard["port"] = _int_or_none(form.get("dashboard_port")) or dashboard["port"]

    # The key itself is never stored in config.yaml (see secrets_store.py) --
    # only reflected here in memory so the rest of Settings validates and the
    # running app has it immediately. persist_secrets_from_form() is what
    # actually writes it to the OS credential store once the save succeeds.
    if "anthropic_api_key_clear" in form:
        llm["api_key"] = None
    elif form.get("anthropic_api_key", "").strip():
        llm["api_key"] = form["anthropic_api_key"].strip()

    data["profile"] = profile
    data["llm"] = llm
    data["dashboard"] = dashboard
    return data


def apply_updates_form(current: dict[str, Any], form: dict[str, str]) -> dict[str, Any]:
    data = dict(current)
    updates = dict(data["updates"])
    updates["enabled"] = "updates_enabled" in form
    updates["endpoint"] = form.get("updates_endpoint", "").strip()
    updates["github_repository"] = form.get("updates_github_repository", "").strip()
    updates["timeout_seconds"] = float(_int_or_none(form.get("updates_timeout_seconds")) or updates["timeout_seconds"])
    data["updates"] = updates
    return data


def apply_schedule_reminders_form(current: dict[str, Any], form: dict[str, str]) -> dict[str, Any]:
    data = dict(current)
    schedule = dict(data["schedule"])
    reminders = dict(data["reminders"])
    email = dict(reminders["email"])

    schedule["search_interval_hours"] = _int_or_none(form.get("search_interval_hours")) or schedule["search_interval_hours"]
    schedule["reminder_check_interval_hours"] = (
        _int_or_none(form.get("reminder_check_interval_hours")) or schedule["reminder_check_interval_hours"]
    )
    schedule["search_schedule_mode"] = form.get("search_schedule_mode", schedule["search_schedule_mode"])
    schedule["search_time_of_day"] = form.get("search_time_of_day", schedule["search_time_of_day"]).strip() or schedule["search_time_of_day"]

    reminders["follow_up_after_days"] = (
        _int_or_none(form.get("follow_up_after_days")) or reminders["follow_up_after_days"]
    )
    reminders["desktop_notifications"] = "desktop_notifications" in form

    email["enabled"] = "email_enabled" in form
    email["smtp_host"] = form.get("smtp_host", email["smtp_host"])
    email["smtp_port"] = _int_or_none(form.get("smtp_port")) or email["smtp_port"]
    email["smtp_user"] = form.get("smtp_user", email["smtp_user"])
    email["to_address"] = form.get("to_address", email["to_address"])

    # Same OS-credential-store handoff as the Anthropic key above -- never
    # written to config.yaml, only reflected in memory here.
    if "smtp_password_clear" in form:
        email["smtp_password"] = None
    elif form.get("smtp_password", "").strip():
        email["smtp_password"] = form["smtp_password"].strip()

    reminders["email"] = email
    data["schedule"] = schedule
    data["reminders"] = reminders
    return data


def persist_secrets_from_form(form: dict[str, str]) -> None:
    """Writes/clears secrets in the OS credential store as a side effect of
    a successful config save. Called separately from apply_*_form (which
    stay pure dict transforms) once validation has actually passed."""
    if "anthropic_api_key_clear" in form:
        secrets_store.clear_secret(secrets_store.ANTHROPIC_API_KEY)
    elif form.get("anthropic_api_key", "").strip():
        secrets_store.set_secret(secrets_store.ANTHROPIC_API_KEY, form["anthropic_api_key"].strip())

    if "smtp_password_clear" in form:
        secrets_store.clear_secret(secrets_store.SMTP_PASSWORD)
    elif form.get("smtp_password", "").strip():
        secrets_store.set_secret(secrets_store.SMTP_PASSWORD, form["smtp_password"].strip())


def _int_or_none(raw: str | None) -> int | None:
    if raw is None or raw.strip() == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def validate_and_build(data: dict[str, Any]) -> tuple[Settings | None, list[str]]:
    """Returns (settings, []) on success, or (None, error_messages) on failure."""
    try:
        settings = Settings(**data)
        return settings, []
    except ValidationError as e:
        messages = [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()]
        return None, messages


# Re-exported so existing callers (app.py) importing save_settings_to_yaml
# from this module keep working — the implementation now lives in config.py
# since it's config persistence, not dashboard-specific.
save_settings_to_yaml = _save_settings_to_yaml
