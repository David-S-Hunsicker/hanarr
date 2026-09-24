# Hanarr

Hanarr is the product name for this local-first career companion. The Python package and
`jobcopilot` command remain unchanged during the transition so existing installations and
scripts continue to work.

A configurable, self-hosted job-search foundation. It parses your resume, searches job sources that
have legitimate public APIs (no ToS-violating scraping), scores how well each posting fits your
stated preferences, tracks your application status, and reminds you to follow up or prep for
interviews. You review matches and make the calls — this handles the searching and bookkeeping so
you can spend your time on interview prep instead.

Hanarr runs entirely on your own machine. Your resume, preferences, and match data stay in a local
SQLite file; nothing is sent anywhere except the job-source APIs you enable and (optionally) the
LLM provider you configure.

The transition is intentionally incremental. The existing Jobs dashboard, public-API connectors,
SQLite store, scheduler, and Ollama-first inference remain the source of truth while coaching,
skills, projects, resume proposals, and submission workflows are added in later milestones.
Hanarr does not add accounts, hosted tenancy, scraping, automatic project creation, automatic
resume activation, or unattended application submission.

Database upgrades use Alembic migrations. Existing databases are upgraded in place without
recreating legacy rows, and a timestamped SQLite backup is written to `data/backups/` before an
upgrade. Migrations are additive; do not delete the database to resolve a migration error.
At startup, a migration failure stops the process with the database path and the underlying
error; restore the newest backup in `data/backups/` before retrying. Start the server from the
repository root so the configured `resumes/` and `data/` paths resolve predictably.

## How it works

1. You write your preferences into `config.yaml` (titles, locations, salary floor, dealbreakers,
   which job sources to use) and drop your resume in `resumes/`.
2. `jobcopilot init` parses your resume into a structured profile using a local LLM.
3. `jobcopilot search` (or the scheduler in `jobcopilot serve`) fetches postings from your enabled
   sources, rule-filters them against your preferences, and scores the survivors for fit.
4. You review matches via `jobcopilot list` or the local dashboard, and mark status as you apply /
   hear back / interview.
5. Marking a job "applied" or "interviewing" schedules a reminder automatically.

## Status: what's actually verified

The local-first release validation pass completed the automated checks: the full suite passes
(151 tests), `src/` byte-compiles cleanly, and `git diff --check` is clean. The migration,
upload-boundary, approval-gate, and localhost-default behaviors are covered by the test suite.
The first desktop-launch foundation is now implemented: `jobcopilot serve` can retain the
foreground server (`none`), open the dashboard in the default browser (`browser`), or host the
same dashboard in an optional desktop webview (`webview`). This is not an installer and does not
bundle Python, Ollama, or a runtime. Still requiring a real local installation or manual
walk-through are the CLI commands end to end, the dashboard in a browser/webview, Ollama/Anthropic
clients against a real model, RemoteOK/Arbeitnow against live APIs, and desktop notifications via
`plyer`.

## Setup

Requires Python 3.10+.

```bash
git clone https://github.com/David-S-Hunsicker/hanarr.git
cd hanarr
pip install -e ".[dev]"
```

A virtual environment (`python3 -m venv .venv && source .venv/bin/activate`) is optional but recommended to isolate dependencies from other Python projects on your machine.

### 1. Set up a local LLM (recommended default)

Install [Ollama](https://ollama.com), then pull a model explicitly:

```bash
ollama pull qwen2.5:14b   # good balance of quality/speed; a 7B model works too, just less sharp
```

No API key, no cost, nothing leaves your machine. If you'd rather use a hosted Claude model
instead, set `llm.provider: anthropic` in `config.yaml` and put `ANTHROPIC_API_KEY` in `.env` —
note this incurs API usage costs. Setting `llm.provider: none` skips the LLM entirely and falls
back to keyword-overlap scoring only.

The Settings → App config page reports whether the Ollama executable, local service, and
configured model are detected. It also shows a conservative hardware-based starting model
recommendation (RAM and free-storage heuristics only). The optional setup actions always detect
first and show source, license, size, and destination before asking for confirmation. Confirming
the Ollama action only downloads a bounded installer into `data/setup/`; Hanarr never executes it
or starts a service. Confirming the model action asks the existing local Ollama service to pull
the configured model. Declining, cancelling, or failing leaves the provider configuration and
local data unchanged. If Ollama is unavailable, choose `anthropic` explicitly or use `none` for
deterministic keyword-overlap scoring.

### 2. Configure

```bash
cp config.example.yaml config.yaml   # jobcopilot init also does this for you
```

Edit `config.yaml`: your target titles, locations, salary floor, dealbreakers, and which job
sources to enable (see **Job sources** below — you need to fill in real company boards/tags for
most of them to return anything). `config.yaml` is gitignored, so your preferences never get
committed even if you fork this repo publicly.

Drop your resume at `resumes/resume.md` (or `.txt`/`.pdf`, and update `profile.resume_path` if
you use a different name/location).

### 3. Initialize

```bash
jobcopilot init
```

This parses your resume and stores the structured profile (skills, titles, seniority) used for
scoring.

### 4. Run a search

```bash
jobcopilot search        # one-off search cycle
jobcopilot list          # see matches, sorted by fit score
jobcopilot status 3 applied      # mark job id 3 as applied -> schedules a follow-up reminder
jobcopilot remind        # check for and deliver due reminders
```

Or run continuously with a background scheduler and a local dashboard:

```bash
jobcopilot serve         # dashboard at http://127.0.0.1:8420, searches + reminders on a timer
```

The launch mode defaults to `none`, preserving the existing foreground-server behavior. To open
the local dashboard automatically, choose a mode in `config.yaml`:

```yaml
dashboard:
  launch_mode: "browser"  # none | browser | webview
```

Or select it for one run: `jobcopilot serve --launch-mode browser`. The optional webview mode
requires `pip install -e ".[desktop]"` and can be selected with
`jobcopilot serve --launch-mode webview`. See [`docs/desktop-launch.md`](docs/desktop-launch.md).
The Windows packaging foundation is documented in [`docs/windows-installer.md`](docs/windows-installer.md).
It uses PyInstaller plus Inno Setup, keeps user data in `%LOCALAPPDATA%\Hanarr`, provides Start
Menu shortcuts and an optional Desktop shortcut, and preserves data on uninstall. Run
`.\scripts\build_windows.ps1 -ValidateOnly` first to fail clearly if Python, PyInstaller, Inno
Setup, or packaging inputs are unavailable, then run `.\scripts\build_windows.ps1` to produce an
unsigned installer. The script verifies that the expected non-empty artifact exists. This is a
buildable packaging path, not a signed or released artifact;
the installer never installs Ollama and the existing explicit-consent setup flow remains in charge
of optional provider actions.

The Windows installer workflow runs the test suite and packaging preflight on a Windows runner,
then builds and uploads the unsigned installer only after a real PyInstaller/Inno Setup build
produces a non-empty executable. Successful builds also include a SHA-256 sidecar and release
metadata. The workflow does not claim a release: Authenticode signing remains an explicit
release gate, and no silent Ollama installation or update service is part of this pipeline.

Update checks are available in Settings → Updates but are disabled by default. When enabled,
Hanarr reads either a configured JSON release endpoint or the latest GitHub release, validates
semver, release-note URLs, asset URLs, and SHA-256 checksums, and displays the current/latest
version and release notes link. Checks fail clearly when offline. Download and installation are
not implemented yet; the approval endpoint refuses to change files even after consent, so
updates cannot silently replace the packaged runtime or affect local data.

Uploads are bounded and stored locally: resume uploads are limited to 10 MiB, and local
submission artifacts are limited to 100 files, 10 MiB per file, and 50 MiB total. Uploads are
written in chunks to a staging path and moved into place only after validation; rejected or
failed uploads do not become visible artifacts. Keep the dashboard bound to `127.0.0.1` unless
you add an authentication and CSRF boundary; non-local exposure is not a supported release
configuration.

## Job sources

Only sources with legitimate public APIs are included — this project won't add scrapers for
sites whose Terms of Service prohibit automated access (LinkedIn, Indeed, etc.); that risks your
account and isn't something to build around.

| Source | What it needs | Notes |
|---|---|---|
| Greenhouse | `sources.greenhouse.company_boards` — company slugs from `boards.greenhouse.io/<slug>` | Thousands of companies use Greenhouse; check a company's careers page for the slug |
| RemoteOK | `sources.remoteok.tags` — optional tag filter | Free public API |
| Arbeitnow | none | Free public API, mostly EU-heavy listings |
| Lever | `sources.lever.companies` — company slugs from `jobs.lever.co/<slug>` | Free public API |

Adding a new source is one file: implement `Connector.fetch()` in `src/jobcopilot/connectors/`
returning a list of `RawJobPosting`, then register it in `connectors/registry.py`. See
`greenhouse.py` for a minimal example.

## Configuration reference

See `config.example.yaml` — every field is commented there. Highlights:

- `preferences.*` — target titles, keyword boosts/excludes, seniority, locations, remote/onsite,
  salary floor, industries to include/exclude, dealbreakers (the LLM scorer weighs these).
- `matching.min_fit_score` — postings scoring below this are filtered out before they're even
  stored.
- `llm.provider` — `ollama` (default, local, free), `anthropic` (hosted, needs API key, has
  usage costs), or `none` (rule-based keyword scoring only, no LLM calls at all).
- `agents.*` — specialized roles (`profiler`, `market_analysis`, `curriculum`, `evaluator`,
  and `resume_writer`) inherit the local-first `llm` settings. Override a role or a named
  `agents.tasks.*` workflow independently; Anthropic keys still come from `.env`.
- `schedule.*` — how often `jobcopilot serve` runs searches and checks reminders.
- `reminders.*` — follow-up delay, desktop notifications on/off, optional email digest via SMTP.

## Data & privacy

Everything lives in a local SQLite database under `data/` (gitignored). `config.yaml` (your
preferences) and `resumes/` (your resume) are also gitignored — cloning this repo gets you the
code, not anyone's personal data. Nothing is sent off your machine except: requests to whichever
job-source APIs you enable, and — only if you opt into `llm.provider: anthropic` — your resume
text and job descriptions sent to Anthropic's API for scoring.

## Running your own instance vs. sharing this project

This is built as **one instance per person**: each user clones the repo, fills in their own
`config.yaml` and resume, and runs it locally. That's deliberate — a shared multi-user instance
would mean storing other people's resumes and preferences, handling auth, and isolating their
data from each other, which is real scope beyond a personal tool. If you want to hand this to
friends, the easiest path today is "you each clone and configure your own copy." The data model
(everything scoped under `Profile`) is structured so a real multi-tenant version — should this
ever become that — is an extension rather than a rewrite, but that work (auth, per-user data
isolation, likely a hosted deployment) hasn't been built.

## Development

```bash
pip install -e ".[dev]"
pytest
```

Connector tests mock HTTP responses (via `respx`) — they don't hit real APIs. Matching tests
cover the prefilter and the rule-based fallback scorer.

## Local release-readiness checklist

- [ ] Copy the production SQLite file before upgrading and confirm a fresh backup appears under
  `data/backups/`.
- [ ] Start once from the repository root and confirm migrations complete without warnings.
- [ ] Confirm the dashboard remains bound to localhost and that resume/local-submission limits
  reject oversized or unsafe uploads.
- [x] Run `python -m pytest -q` (151 tests), `python -m compileall -q src`, and
  `git diff --check`.
- [ ] Walk through Jobs, Coaching, Resume, Skills, Applications, and Settings, including a
  review-first submission and a rejected upload.
- [ ] Validate `jobcopilot serve --launch-mode browser` and `--launch-mode webview` on a real
  machine. Webview mode is currently an optional launch path, not an installed desktop product.
- [ ] On Windows, run `.\scripts\build_windows.ps1 -ValidateOnly`, then build the unsigned
  installer and record the artifact hash and tool versions. This is the current local
  packaging milestone; no artifact is claimed here until those tools are available.
- [ ] Sign and verify the installer and packaged executables with Authenticode, then validate
  install, shortcuts, launch, upgrade, uninstall, and data preservation on a clean machine.
- [ ] Do not enable external GitHub delivery, hosted auth, or CSRF-dependent non-local access;
  those remain future blockers.

## Roadmap ideas

- Ashby connector (same pattern as Greenhouse/Lever)
- Cover-letter drafting from the LLM client already in place
- A "why was this filtered out" debug view in the dashboard
- Optional calendar-file (.ics) export for interview reminders

## License

MIT — see `LICENSE`.
