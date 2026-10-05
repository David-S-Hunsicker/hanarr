"""Loads and validates config.yaml into typed settings objects.

Everything user-specific (preferences, resume path, sources, LLM choice)
lives in config.yaml, which is gitignored. config.example.yaml is the
committed template — copy it to get started (see `hanarr init`).
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

from . import secrets_store

if TYPE_CHECKING:
    from .models import Profile

DEFAULT_CONFIG_PATH = Path("config.yaml")
EXAMPLE_CONFIG_PATH = Path("config.example.yaml")

logger = logging.getLogger(__name__)


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


def effective_preferences(profile: "Profile", settings: "Settings") -> Preferences:
    """A profile's own saved preferences (once it has any — see the
    /profiles and Preferences-tab-save routes, which write
    `profile.preferences_json`), or the shared config.yaml value as a live
    fallback until then. This is deliberately live, not cached: a
    pre-existing single-profile install that has never touched the new
    per-profile behavior keeps reading config.yaml exactly as before, with
    zero migration step required. Job sources, LLM provider, and schedule
    are NOT part of this -- those stay instance-wide/shared even for a
    profile that has customized its own match criteria."""
    if profile.preferences_json:
        return Preferences.model_validate_json(profile.preferences_json)
    return settings.preferences


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


class WorkableSource(BaseModel):
    enabled: bool = False
    # Workable has no per-company "board token" the way Greenhouse/Lever/
    # Ashby do -- most Workable-hosted accounts return zero current
    # postings even for well-known names (see connectors/workable.py's
    # module docstring), so this connector queries Workable's own public
    # cross-employer search (jobs.workable.com) by keyword instead, the
    # same shape as RemoteOKSource.tags.
    queries: list[str] = Field(default_factory=list)


class RecruiteeSource(BaseModel):
    enabled: bool = False
    company_boards: list[str] = Field(default_factory=list)


class UsajobsSource(BaseModel):
    enabled: bool = False
    # USAJOBS requires every caller to register their own free API key at
    # developer.usajobs.gov/apirequest, tied to an email address -- there is
    # no shared/anonymous access the way Greenhouse/Lever/Ashby/Recruitee
    # allow. user_agent_email is sent as the required User-Agent header (not
    # secret, just an identifier); api_key is a real credential and follows
    # the same OS-keyring-not-config.yaml treatment as llm.api_key (see
    # secrets_store.py, load_settings(), save_settings_to_yaml()).
    user_agent_email: str = ""
    api_key: Optional[str] = None
    # Federal job titles/keywords to search for -- there's no "company
    # board" concept here (postings span every federal agency), so this is
    # shaped like WorkableSource.queries rather than company_boards.
    queries: list[str] = Field(default_factory=list)


class WorkdaySource(BaseModel):
    enabled: bool = False
    # A company's own public Workday careers URL (e.g.
    # https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite) --
    # tenant/shard/site/locale are parsed out of it by the connector, since
    # there's no directory mapping a company name to these values. See
    # connectors/workday.py's module docstring for why this source needs a
    # full URL rather than a plain slug like the others.
    career_site_urls: list[str] = Field(default_factory=list)


class SourcesConfig(BaseModel):
    greenhouse: GreenhouseSource = Field(default_factory=GreenhouseSource)
    lever: LeverSource = Field(default_factory=LeverSource)
    ashby: AshbySource = Field(default_factory=AshbySource)
    workday: WorkdaySource = Field(default_factory=WorkdaySource)
    remoteok: RemoteOKSource = Field(default_factory=RemoteOKSource)
    arbeitnow: ArbeitnowSource = Field(default_factory=ArbeitnowSource)
    workable: WorkableSource = Field(default_factory=WorkableSource)
    recruitee: RecruiteeSource = Field(default_factory=RecruiteeSource)
    usajobs: UsajobsSource = Field(default_factory=UsajobsSource)
    # The shipped default company boards skew heavily toward tech/startup
    # companies -- when on, a search only queries default-list companies
    # whose known hiring categories overlap the candidate's own resume, so a
    # non-technical profile isn't drowned in hundreds of engineering
    # postings from companies with very few openings in their actual field.
    # Companies the user adds themselves are never filtered by this, only
    # the shipped defaults are. Off disables filtering entirely (query every
    # configured board, the original behavior).
    filter_boards_by_profile: bool = True


class LLMConfig(BaseModel):
    provider: Literal["ollama", "anthropic", "none"] = "ollama"
    model: str = "qwen2.5:14b"
    base_url: str = "http://localhost:11434"
    timeout_seconds: float = 1800.0
    api_key: Optional[str] = None


AgentProvider = Literal["ollama", "anthropic", "none"]
AgentName = Literal["profiler", "market_analysis", "curriculum", "evaluator", "resume_writer", "coach"]


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
    coach: AgentRoute = Field(default_factory=AgentRoute)
    tasks: dict[str, AgentRoute] = Field(default_factory=dict)


class ScheduleConfig(BaseModel):
    # A floor, not just a default -- an accidental 0 or negative interval
    # would make APScheduler fire back-to-back with no real gap, which for
    # the search job means near-continuous LLM/GPU load. 1 hour is still
    # frequent; anyone who deliberately wants less often can go arbitrarily
    # high (days = hours * 24), just never lower than this.
    search_interval_hours: int = Field(default=6, ge=1)
    reminder_check_interval_hours: int = Field(default=1, ge=1)
    # "interval" (the original behavior -- every N hours starting from
    # whenever hanarr serve was launched) or "daily" (a fixed clock time,
    # once a day). search_time_of_day is only read when mode is "daily".
    search_schedule_mode: Literal["interval", "daily"] = "interval"
    search_time_of_day: str = "09:00"

    @field_validator("search_time_of_day")
    @classmethod
    def _validate_time_of_day(cls, value: str) -> str:
        """Stored as "HH:MM" in 24-hour, local time -- interpreted by the
        scheduler using this machine's local timezone (see scheduler.py),
        not UTC, since "daily at 9am" should mean 9am where the user is."""
        try:
            hour_str, minute_str = value.split(":")
            hour, minute = int(hour_str), int(minute_str)
        except ValueError:
            raise ValueError('search_time_of_day must be in "HH:MM" 24-hour format') from None
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError('search_time_of_day must be in "HH:MM" 24-hour format')
        return f"{hour:02d}:{minute:02d}"


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
    # On by default, pointed at this project's own GitHub releases -- a
    # fresh install checks for updates without any configuration needed.
    # Still fully opt-out: set enabled=false, or auto_update=false to keep
    # checking but never auto-apply.
    enabled: bool = True
    endpoint: str = ""
    github_repository: str = "David-S-Hunsicker/hanarr"
    timeout_seconds: float = 10.0
    # When a newer release is found: download, verify its checksum, and
    # apply it automatically after a visible, cancellable delay (see
    # scheduler.py/self_update.py) rather than only ever notifying. An
    # in-progress search is never interrupted -- applying waits until the
    # scheduler sees no search running. false keeps checking but never
    # downloads or applies anything -- Settings still shows "Install now"
    # as a manual, explicit action.
    auto_update: bool = True
    # How often the background check runs. A floor, not just a default --
    # same reasoning as ScheduleConfig's search interval floor.
    check_interval_hours: int = Field(default=24, ge=1)
    # How long the "Update ready, installing in Ns" banner counts down
    # before applying, once found. Long enough to actually notice and
    # cancel it; not so long that a real update sits pending for ages.
    apply_delay_seconds: int = Field(default=120, ge=10)


class UiConfig(BaseModel):
    # Master switch for all dismissible tutorials (the onboarding checklist,
    # and any future contextual tutorial popups) -- separate from per-
    # tutorial dismissal, which is per-profile state in the
    # dismissed_tutorials table (see models.py/tutorials.py). This is a
    # single global on/off, so it lives with the rest of Settings in
    # config.yaml rather than per profile.
    tutorials_enabled: bool = True


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
    ui: UiConfig = Field(default_factory=UiConfig)

    # Where instance data (db, logs) lives; not user-configurable via YAML
    # to keep it out of the way of the gitignore boundary.
    data_dir: Path = Path("data")

    class Config:
        arbitrary_types_allowed = True


def _recover_from_latest_backup(config_path: Path) -> dict | None:
    """Best-effort recovery for an existing config.yaml that parsed to
    nothing (see load_settings' use of this -- that's a corruption signal,
    not a legitimate state). Looks in the default backup location
    (data/backups/config-*.yaml, the same directory save_settings_to_yaml's
    _backup_config writes to) for the newest one and returns its parsed
    content, or None if no usable backup exists."""
    backup_dir = config_path.parent / "data" / "backups"
    if not backup_dir.is_dir():
        return None
    backups = sorted(backup_dir.glob("config-*.yaml"))
    for candidate in reversed(backups):
        try:
            with open(candidate, "r") as f:
                recovered = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            continue
        if recovered:
            return recovered
    return None


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
        if not raw:
            # An existing config.yaml that parses to nothing is corruption,
            # not a legitimate "nothing configured yet" state -- a fresh
            # install never reaches this branch (the copy above always
            # populates *something* first). The most common real cause is a
            # non-atomic write interrupted mid-truncate (a crash, a forced
            # quit, antivirus briefly locking the file) leaving an empty or
            # truncated file -- save_settings_to_yaml now writes atomically
            # specifically to prevent this, but an old file written before
            # that fix, or corruption from some other cause, still lands
            # here. Recovering from the newest backup beats silently handing
            # back an all-defaults Settings -- every job source disabled,
            # every preference blanked -- which is exactly the data loss
            # this project has already been burned by, twice, on two
            # separate machines.
            logger.critical(
                "config.yaml exists but contains no usable data (likely a truncated or "
                "corrupted file) -- attempting to recover from the newest backup instead of "
                "silently resetting every setting to its default."
            )
            recovered = _recover_from_latest_backup(config_path)
            if recovered:
                raw = recovered
                logger.warning("Recovered config.yaml from a backup in data/backups/ after detecting corruption.")
            else:
                logger.critical(
                    "No usable config.yaml backup was found in data/backups/ either -- "
                    "continuing with default settings. Check that directory for any "
                    "config-*.yaml file to restore by hand."
                )

    settings = Settings(**raw)

    # Secrets never live in config.yaml, so a config file is always safe to
    # hand to a friend or commit by mistake minus the personal preferences
    # it already excludes. The OS credential store (see secrets_store.py),
    # written to via the Settings UI, is checked first; a plain .env
    # variable is the fallback for anyone who set one up before that UI
    # existed, or who prefers managing it that way.
    anthropic_key = secrets_store.get_secret(secrets_store.ANTHROPIC_API_KEY) or os.getenv("ANTHROPIC_API_KEY")
    if settings.llm.provider == "anthropic" and not settings.llm.api_key:
        settings.llm.api_key = anthropic_key
    for route in (
        settings.agents.default,
        settings.agents.profiler,
        settings.agents.market_analysis,
        settings.agents.curriculum,
        settings.agents.evaluator,
        settings.agents.resume_writer,
        settings.agents.coach,
        *settings.agents.tasks.values(),
    ):
        if route.provider == "anthropic" and not route.api_key:
            route.api_key = anthropic_key
    if settings.reminders.email.enabled and not settings.reminders.email.smtp_password:
        settings.reminders.email.smtp_password = (
            secrets_store.get_secret(secrets_store.SMTP_PASSWORD) or os.getenv("SMTP_PASSWORD")
        )
    if settings.sources.usajobs.enabled and not settings.sources.usajobs.api_key:
        settings.sources.usajobs.api_key = (
            secrets_store.get_secret(secrets_store.USAJOBS_API_KEY) or os.getenv("USAJOBS_API_KEY")
        )

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings


MAX_CONFIG_BACKUPS = 20


def _backup_config(settings: Settings, config_path: Path) -> None:
    """Keeps the last MAX_CONFIG_BACKUPS copies of config.yaml (in
    settings.data_dir/backups/, the same directory and naming pattern
    db.py already uses for pre-migration database backups) before every
    overwrite.

    config.yaml has no version history of its own -- it's gitignored,
    never committed, and every dashboard save fully replaces it. A stale
    browser tab, a bug in a form handler, or anything else that produces a
    bad write is otherwise unrecoverable. Best-effort: a failed backup
    (e.g. a full disk) must never block saving the actual change."""
    if not config_path.exists():
        return
    try:
        backup_dir = settings.data_dir / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = backup_dir / f"config-{timestamp}.yaml"
        suffix = 1
        while backup_path.exists():
            backup_path = backup_dir / f"config-{timestamp}-{suffix}.yaml"
            suffix += 1
        shutil.copy2(config_path, backup_path)

        existing = sorted(backup_dir.glob("config-*.yaml"))
        for stale in existing[:-MAX_CONFIG_BACKUPS]:
            stale.unlink(missing_ok=True)
    except OSError:
        pass


def backup_profile_preferences(settings: Settings, profile_id: int, preferences_json: str | None) -> None:
    """Mirrors _backup_config, but for a profile's own preferences_json.

    The first time a profile's Preferences tab is saved, its match criteria
    fork away from config.yaml's shared defaults into profiles.preferences_json
    (see the "forks a profile" comment in app.py's config-save route) --
    config.yaml's preferences block becomes a dead fallback from that point
    on. That DB column has no backup of its own, unlike config.yaml, which
    meant a bad save there (a stale browser tab resubmitting an old
    snapshot, a bug in a form handler) was silently permanent with no way
    back -- this happened for real: a forked profile's salary floor and
    dealbreakers were found reset to blank with no recorded history to
    recover from. Best-effort, same as _backup_config: a failed backup must
    never block saving the actual change."""
    if not preferences_json:
        return
    try:
        backup_dir = settings.data_dir / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = backup_dir / f"profile-{profile_id}-preferences-{timestamp}.json"
        suffix = 1
        while backup_path.exists():
            backup_path = backup_dir / f"profile-{profile_id}-preferences-{timestamp}-{suffix}.json"
            suffix += 1
        backup_path.write_text(preferences_json, encoding="utf-8")

        existing = sorted(backup_dir.glob(f"profile-{profile_id}-preferences-*.json"))
        for stale in existing[:-MAX_CONFIG_BACKUPS]:
            stale.unlink(missing_ok=True)
    except OSError:
        pass


def save_settings_to_yaml(settings: Settings, config_path: Path | str = DEFAULT_CONFIG_PATH) -> None:
    """Writes settings back to config.yaml, omitting secrets that
    load_settings() populates from the environment (ANTHROPIC_API_KEY,
    SMTP_PASSWORD, USAJOBS_API_KEY) — those belong in .env, never in the
    gitignored-but-still-plaintext config file.

    Writes to a staged temp file and atomically replaces config.yaml
    (os.replace) rather than truncating it in place. A plain `open(path,
    "w")` truncates the file to zero bytes before writing a single line of
    the new content -- if the process is interrupted at that exact moment
    (killed, crashed, the machine loses power, antivirus briefly locks the
    file), config.yaml is left empty. load_settings() then sees an empty
    file, which YAML parses as nothing, and silently hands back an
    all-Pydantic-defaults Settings -- every job source disabled, every
    preference blanked -- indistinguishable from someone deliberately
    clearing their config. This happened for real, independently, on two
    separate machines. The atomic swap means config.yaml is always either
    the complete old content or the complete new content, never a partial
    write caught mid-truncate."""
    config_path = Path(config_path)
    _backup_config(settings, config_path)
    data = settings.model_dump(mode="json", exclude={"data_dir"})
    data["llm"].pop("api_key", None)
    data["agents"]["default"].pop("api_key", None)
    for name in ("profiler", "market_analysis", "curriculum", "evaluator", "resume_writer", "coach"):
        data["agents"][name].pop("api_key", None)
    for route in data["agents"]["tasks"].values():
        route.pop("api_key", None)
    data["reminders"]["email"].pop("smtp_password", None)
    data["sources"]["usajobs"].pop("api_key", None)
    staged = config_path.with_name(f".{config_path.name}.tmp")
    try:
        with open(staged, "w") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(staged, config_path)
    except Exception:
        staged.unlink(missing_ok=True)
        raise
