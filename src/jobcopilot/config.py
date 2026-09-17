"""Loads and validates config.yaml into typed settings objects.

Everything user-specific (preferences, resume path, sources, LLM choice)
lives in config.yaml, which is gitignored. config.example.yaml is the
committed template — copy it to get started (see `jobcopilot init`).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

DEFAULT_CONFIG_PATH = Path("config.yaml")


class Preferences(BaseModel):
    target_titles: list[str] = Field(default_factory=list)
    keywords_boost: list[str] = Field(default_factory=list)
    keywords_exclude: list[str] = Field(default_factory=list)
    seniority: str = "mid"
    employment_types: list[str] = Field(default_factory=lambda: ["full_time"])
    locations: list[str] = Field(default_factory=list)
    remote_ok: bool = True
    onsite_ok: bool = False
    willing_to_relocate: bool = False
    # Applies even to remote postings — a "remote" job is often remote
    # *within a specific country* (e.g. "Remote - Canada only"), which a
    # plain locations/remote_ok check doesn't catch. Empty string disables
    # this check entirely.
    work_country: str = "United States"
    salary_floor_usd: Optional[int] = None
    company_size_min: Optional[int] = None
    company_size_max: Optional[int] = None
    industries_include: list[str] = Field(default_factory=list)
    industries_exclude: list[str] = Field(default_factory=list)
    dealbreakers: list[str] = Field(default_factory=list)


class ProfileConfig(BaseModel):
    name: str = "Your Name"
    resume_path: str = "resumes/resume.md"


class MatchingConfig(BaseModel):
    min_fit_score: int = 60


class GreenhouseSource(BaseModel):
    enabled: bool = False
    company_boards: list[str] = Field(default_factory=list)


class LeverSource(BaseModel):
    enabled: bool = False
    companies: list[str] = Field(default_factory=list)


class RemoteOKSource(BaseModel):
    enabled: bool = False
    tags: list[str] = Field(default_factory=list)


class ArbeitnowSource(BaseModel):
    enabled: bool = False


class SourcesConfig(BaseModel):
    greenhouse: GreenhouseSource = Field(default_factory=GreenhouseSource)
    lever: LeverSource = Field(default_factory=LeverSource)
    remoteok: RemoteOKSource = Field(default_factory=RemoteOKSource)
    arbeitnow: ArbeitnowSource = Field(default_factory=ArbeitnowSource)


class LLMConfig(BaseModel):
    provider: Literal["ollama", "anthropic", "none"] = "ollama"
    model: str = "qwen2.5:14b"
    base_url: str = "http://localhost:11434"
    timeout_seconds: float = 1800.0
    api_key: Optional[str] = None


class ScheduleConfig(BaseModel):
    search_interval_hours: int = 6
    reminder_check_interval_hours: int = 1


class EmailReminderConfig(BaseModel):
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: Optional[str] = None
    to_address: str = ""


class RemindersConfig(BaseModel):
    follow_up_after_days: int = 7
    desktop_notifications: bool = True
    email: EmailReminderConfig = Field(default_factory=EmailReminderConfig)


class DashboardConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8420


class Settings(BaseModel):
    profile: ProfileConfig = Field(default_factory=ProfileConfig)
    preferences: Preferences = Field(default_factory=Preferences)
    matching: MatchingConfig = Field(default_factory=MatchingConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    reminders: RemindersConfig = Field(default_factory=RemindersConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)

    # Where instance data (db, logs) lives; not user-configurable via YAML
    # to keep it out of the way of the gitignore boundary.
    data_dir: Path = Path("data")

    class Config:
        arbitrary_types_allowed = True


def load_settings(config_path: Path | str = DEFAULT_CONFIG_PATH) -> Settings:
    """Load config.yaml (falling back to defaults for anything missing) and
    apply .env secrets on top."""
    load_dotenv()
    config_path = Path(config_path)

    raw: dict = {}
    if config_path.exists():
        with open(config_path, "r") as f:
            raw = yaml.safe_load(f) or {}
    else:
        raise FileNotFoundError(
            f"{config_path} not found. Run `jobcopilot init` to create one "
            f"from config.example.yaml."
        )

    settings = Settings(**raw)

    # Secrets come from the environment, never from config.yaml, so a
    # config file is always safe to hand to a friend or commit by mistake
    # minus the personal preferences it already excludes.
    if settings.llm.provider == "anthropic" and not settings.llm.api_key:
        settings.llm.api_key = os.getenv("ANTHROPIC_API_KEY")
    if settings.reminders.email.enabled and not settings.reminders.email.smtp_password:
        settings.reminders.email.smtp_password = os.getenv("SMTP_PASSWORD")

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings


def save_settings_to_yaml(settings: Settings, config_path: Path | str = DEFAULT_CONFIG_PATH) -> None:
    """Writes settings back to config.yaml, omitting secrets that
    load_settings() populates from the environment (ANTHROPIC_API_KEY,
    SMTP_PASSWORD) — those belong in .env, never in the gitignored-but-
    still-plaintext config file."""
    data = settings.model_dump(mode="json", exclude={"data_dir"})
    data["llm"].pop("api_key", None)
    data["reminders"]["email"].pop("smtp_password", None)
    with open(config_path, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
