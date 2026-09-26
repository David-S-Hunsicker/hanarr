"""Loads and validates config.yaml into typed settings objects.

Everything user-specific (preferences, resume path, sources, LLM choice)
lives in config.yaml, which is gitignored. config.example.yaml is the
committed template — copy it to get started (see `hanarr init`).
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Literal, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

DEFAULT_CONFIG_PATH = Path("config.yaml")
EXAMPLE_CONFIG_PATH = Path("config.example.yaml")


class Preferences(BaseModel):
    target_titles: list[str] = Field(default_factory=list)
    keywords_boost: list[str] = Field(default_factory=list)
    keywords_exclude: list[str] = Field(default_factory=list)
    # Accepts a plain string too (old config.yaml format, pre-multi-select) —
    # see _coerce_seniority_list below — and normalizes it to a one-item list.
    seniority: list[str] = Field(default_factory=lambda: ["mid"])
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

    @field_validator("seniority", mode="before")
    @classmethod
    def _coerce_seniority_list(cls, value):
        """A config.yaml from before multi-select seniority has a plain
        string (e.g. "senior"); wrap it into a one-item list so old configs
        keep loading without a manual edit."""
        if isinstance(value, str):
            return [value]
        return value


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


class AshbySource(BaseModel):
    enabled: bool = False
    company_boards: list[str] = Field(default_factory=list)


class RemoteOKSource(BaseModel):
    enabled: bool = False
    tags: list[str] = Field(default_factory=list)


class ArbeitnowSource(BaseModel):
    enabled: bool = False


class SourcesConfig(BaseModel):
    greenhouse: GreenhouseSource = Field(default_factory=GreenhouseSource)
    lever: LeverSource = Field(default_factory=LeverSource)
    ashby: AshbySource = Field(default_factory=AshbySource)
    remoteok: RemoteOKSource = Field(default_factory=RemoteOKSource)
    arbeitnow: ArbeitnowSource = Field(default_factory=ArbeitnowSource)


class LLMConfig(BaseModel):
    provider: Literal["ollama", "anthropic", "none"] = "ollama"
    model: str = "qwen2.5:14b"
    base_url: str = "http://localhost:11434"
    timeout_seconds: float = 1800.0
    api_key: Optional[str] = None


AgentProvider = Literal["ollama", "anthropic", "none"]
AgentName = Literal["profiler", "market_analysis", "curriculum", "evaluator", "resume_writer"]


class AgentRoute(BaseModel):
    """Optional overrides for one specialized agent or named task."""

    provider: Optional[AgentProvider] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    timeout_seconds: Optional[float] = None
    api_key: Optional[str] = None


class AgentsConfig(BaseModel):
    """Local-first defaults plus narrow role/task overrides.

    An unset field inherits from ``llm``. Task names are intentionally free-form
    so callers can introduce bounded workflows without changing this schema.
    """

    default: AgentRoute = Field(default_factory=AgentRoute)
    profiler: AgentRoute = Field(default_factory=AgentRoute)
    market_analysis: AgentRoute = Field(default_factory=AgentRoute)
    curriculum: AgentRoute = Field(default_factory=AgentRoute)
    evaluator: AgentRoute = Field(default_factory=AgentRoute)
    resume_writer: AgentRoute = Field(default_factory=AgentRoute)
    tasks: dict[str, AgentRoute] = Field(default_factory=dict)


class ScheduleConfig(BaseModel):
    # A floor, not just a default -- an accidental 0 or negative interval
    # would make APScheduler fire back-to-back with no real gap, which for
    # the search job means near-continuous LLM/GPU load. 1 hour is still
    # frequent; anyone who deliberately wants less often can go arbitrarily
    # high (days = hours * 24), just never lower than this.
    search_interval_hours: int = Field(default=6, ge=1)
    reminder_check_interval_hours: int = Field(default=1, ge=1)


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
    launch_mode: Literal["none", "browser", "webview"] = "none"


class UpdatesConfig(BaseModel):
    enabled: bool = False
    endpoint: str = ""
    github_repository: str = ""
    timeout_seconds: float = 10.0


class Settings(BaseModel):
    profile: ProfileConfig = Field(default_factory=ProfileConfig)
    preferences: Preferences = Field(default_factory=Preferences)
    matching: MatchingConfig = Field(default_factory=MatchingConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    reminders: RemindersConfig = Field(default_factory=RemindersConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    updates: UpdatesConfig = Field(default_factory=UpdatesConfig)

    # Where instance data (db, logs) lives; not user-configurable via YAML
    # to keep it out of the way of the gitignore boundary.
    data_dir: Path = Path("data")

    class Config:
        arbitrary_types_allowed = True


def load_settings(config_path: Path | str = DEFAULT_CONFIG_PATH) -> Settings:
    """Load config.yaml (falling back to defaults for anything missing) and
    apply .env secrets on top.

    A missing config.yaml is not an error: it means this is a first run, so
    the template is copied into place automatically (falling back to
    built-in defaults if even the template is missing, e.g. running outside
    a repo checkout) and startup proceeds normally. Callers that want to
    react to a first run — e.g. showing a one-time welcome message — can
    check `not config_path.exists()` themselves before calling this."""
    load_dotenv()
    config_path = Path(config_path)

    if not config_path.exists():
        example = EXAMPLE_CONFIG_PATH
        if example.exists():
            shutil.copy(example, config_path)

    raw: dict = {}
    if config_path.exists():
        with open(config_path, "r") as f:
            raw = yaml.safe_load(f) or {}

    settings = Settings(**raw)

    # Secrets come from the environment, never from config.yaml, so a
    # config file is always safe to hand to a friend or commit by mistake
    # minus the personal preferences it already excludes.
    if settings.llm.provider == "anthropic" and not settings.llm.api_key:
        settings.llm.api_key = os.getenv("ANTHROPIC_API_KEY")
    for route in (
        settings.agents.default,
        settings.agents.profiler,
        settings.agents.market_analysis,
        settings.agents.curriculum,
        settings.agents.evaluator,
        settings.agents.resume_writer,
        *settings.agents.tasks.values(),
    ):
        if route.provider == "anthropic" and not route.api_key:
            route.api_key = os.getenv("ANTHROPIC_API_KEY")
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
    data["agents"]["default"].pop("api_key", None)
    for name in ("profiler", "market_analysis", "curriculum", "evaluator", "resume_writer"):
        data["agents"][name].pop("api_key", None)
    for route in data["agents"]["tasks"].values():
        route.pop("api_key", None)
    data["reminders"]["email"].pop("smtp_password", None)
    with open(config_path, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
