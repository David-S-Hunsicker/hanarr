# Hanarr

Hanarr is a local-first career companion — a configurable, self-hosted job-search foundation. It parses your resume, searches job sources that
have legitimate public APIs (no ToS-violating scraping), scores how well each posting fits your
stated preferences, tracks your application status, and reminds you to follow up or prep for
interviews. You review matches and make the calls — this handles the searching and bookkeeping so
you can spend your time on interview prep instead.

Hanarr runs entirely on your own machine. Your resume, preferences, and match data stay in a local
SQLite file; nothing is sent anywhere except the job-source APIs you enable and (optionally) the
LLM provider you configure.

The dashboard has eight pages: **Jobs** (discovery and status), **Coaching** (skill-gap projects
with review-first evaluation), **Resume** (source content, sectioned display, proposals, version
history, and a plain-text export), **Skills** (capability evidence, separate from resume claims),
**Applications** (your pipeline by status), **Prep** (behavioral-question practice and a STAR
story bank), **Coach** (a chat assistant grounded in your own resume/jobs/projects, not a blank
general-purpose chatbot, that can also propose actions like starting a project -- always behind an
explicit confirm click), and **Settings**. Local, unauthenticated profile
switching lets more than one person use the same instance — each profile gets its own resume,
jobs, applications, and skills, while search preferences stay shared. Hanarr does not add hosted
accounts/tenancy, scraping, automatic project creation, automatic resume activation, or
unattended application submission.

Database upgrades use Alembic migrations. Existing databases are upgraded in place without
recreating legacy rows, and a timestamped SQLite backup is written to `data/backups/` before an
upgrade. Migrations are additive; do not delete the database to resolve a migration error.
At startup, a migration failure stops the process with the database path and the underlying
error; restore the newest backup in `data/backups/` before retrying. Start the server from the
repository root so the configured `resumes/` and `data/` paths resolve predictably. The database
file is `data/hanarr.db`; an installation from before the project's rename (when it was still
named JobCopilot) has its `jobcopilot.db` renamed to `hanarr.db` automatically, once, the first
time the app starts — nothing to do manually.

`config.yaml` is written atomically (a staged temp file, fsynced, then swapped into place) — an
interrupted write (a crash, a forced quit, a lost-power moment) can never leave it truncated.
Every save is also backed up to `data/backups/` first, and if `config.yaml` is ever found empty
anyway (e.g. one written before this protection existed), startup automatically recovers from the
newest usable backup there instead of silently resetting every setting to its default.

## How it works

1. Run `hanarr serve` and open the dashboard. A first-time "Get set up" checklist walks you
   through the rest — no file editing required.
2. Upload your resume and set your preferences (target titles, locations, salary floor,
   dealbreakers, which job sources to enable) on the **Settings** page.
3. On a schedule you choose, or on demand from the **Jobs** page, Hanarr fetches postings from
   your enabled sources, rule-filters them against your preferences, and scores the survivors
   for fit against your resume.
4. Review matches on **Jobs** and mark status as you apply / hear back / interview.
5. Marking a job "applied" or "interviewing" schedules a reminder automatically.

Everything above is stored in `config.yaml` and the local SQLite database under the hood, but
you shouldn't need to open either by hand — the dashboard is the intended way to configure and
use Hanarr. A CLI (`hanarr init`/`search`/`list`/`status`/`remind`) covers the same actions for
scripting or a one-off run without the dashboard; see **Command line** below.

## Status: what's actually verified

The full test suite passes (582 tests), `src/` byte-compiles cleanly, and `git diff --check` is
clean. Tagged releases have shipped through the real GitHub Actions release pipeline — see
[Releases](https://github.com/David-S-Hunsicker/hanarr/releases) for the current published list,
rather than a version count here that goes stale every time a new one ships. Beyond the automated
suite, the following have been verified live
against real infrastructure (a running Ollama instance with a real model loaded onto GPU, a real
Windows build/install/uninstall cycle, real connector APIs) rather than only mocked in tests:

- `hanarr serve` runs the dashboard with a real Ollama model doing fit-scoring — confirmed the
  model is genuinely loaded into GPU VRAM during scoring, not silently falling back.
- A configured-but-unreachable LLM (Ollama down, model not pulled) is caught before a search
  touches any connector, with a specific, actionable error instead of every posting silently
  degrading to rule-based scoring. If the LLM drops *mid-search* instead, the search pauses and
  polls until it's reachable again rather than quietly scoring the rest of the run with the much
  weaker keyword fallback — each job card shows which method actually scored it, a per-job
  "Rematch this job" action (tucked, along with cover-letter/outreach drafting, behind a per-card
  menu button) and a page-wide "Rematch all jobs" button re-score what's already saved, and a
  status light on the Jobs page (click it to force a fresh check) shows whether a real LLM is
  currently reachable. A "Resume now" button appears wherever a pause is shown, to skip the wait
  instead of relying purely on the automatic retry.
- Greenhouse and Lever connectors were confirmed fetching and correctly cleaning real postings
  from live company boards.
- The Windows packaging path was built, installed silently, launched (the installed executable
  served the real dashboard against its own `%LOCALAPPDATA%\Hanarr` data), and uninstalled with
  user data preserved — see [`docs/windows-installer.md`](docs/windows-installer.md) for what
  remains (a genuinely clean machine/VM, and code signing).
- Local model management (dropdown, live download progress, search gated on the model being
  downloaded) was verified against a real Ollama pull, watching the reported percentage
  advance in real time and confirming the model was actually installed afterward.
- Profile-based company-board filtering (see **Job sources** below) was verified against the
  real shipped default list: a payroll/accounting profile drops from 101 to 28 queried
  companies, concentrated on genuinely finance-heavy employers, while a software-engineer
  profile still sees all 101.
- Saving an Anthropic API key or SMTP password through Settings was confirmed to land only in
  the OS's own credential store (Windows Credential Manager), never in `config.yaml`, with the
  page never re-displaying the saved value.

Not yet performed: desktop notifications via `plyer` on a real OS notification center, and
RemoteOK/Arbeitnow against live APIs (Greenhouse/Lever have been; RemoteOK/Arbeitnow are only
covered by mocked connector tests so far).

## Setup

Requires Python 3.10+.

```bash
git clone https://github.com/David-S-Hunsicker/hanarr.git
cd hanarr
pip install -e ".[dev]"
```

A virtual environment (`python3 -m venv .venv && source .venv/bin/activate`) is optional but recommended to isolate dependencies from other Python projects on your machine.

### 1. Start the dashboard

```bash
hanarr serve         # dashboard at http://127.0.0.1:8420, searches + reminders on a timer
```

A brand-new install shows a non-blocking "Get set up" checklist on Jobs and Settings until a
resume and target titles are in place. Everything from here — resume, preferences, job sources,
schedule, LLM provider — is done through **Settings**, not by editing a file. The launch mode
defaults to `none` (open the URL yourself); to have it open a browser or the optional desktop
webview automatically, set that on Settings → App config, or pass `--launch-mode browser`/
`webview` for one run. Webview mode needs `pip install -e ".[desktop]"` — see
[`docs/desktop-launch.md`](docs/desktop-launch.md).

### 2. Set up a local LLM (do this before step 3)

Hanarr is built around an LLM actually reading your resume and each job posting — not keyword
matching — so set this up before uploading your resume in step 3, or extraction degrades to the
keyword-only fallback and you'll need to retry it later once a model is ready.

Settings → App config reports whether Ollama's executable, local service, and configured model
are detected, with a hardware-based starting model recommendation and a "Check for Ollama /
stage installer" button — one explicit click, shows source/license/size before downloading
anything, Hanarr never installs or starts it on its own. Picking a model that isn't downloaded
shows a "Download this model" button with a live progress bar. "Run search now" on the Jobs page
is disabled with a specific reason whenever the model isn't ready — Ollama not installed
(with a direct link to install it), installed but not running, or running but this model not
pulled yet — so a broken setup is obvious before you click. You can also install it yourself
first:

```bash
ollama pull qwen2.5:14b   # good balance of quality/speed; a 7B model works too, just less sharp
```

No API key, no cost, nothing leaves your machine. If you'd rather use a hosted Claude model
instead, set the provider to `anthropic` and enter your API key right there in Settings — it's
handed off to your OS's own credential store (Windows Credential Manager, macOS Keychain, Linux
Secret Service) as soon as you save, never written to `config.yaml` or any file Hanarr writes,
and the field never re-displays the saved value. Using Anthropic incurs API usage costs.
Skipping this entirely (provider `none`) is a degraded fallback, not the intended way to run
Hanarr: resume parsing and job scoring both drop to plain keyword overlap instead of an LLM
actually reading the text.

### 3. Add your resume and preferences (Settings)

- **Resume** — Settings → App config → "Choose file…" (`.pdf`/`.txt`/`.md`). Parsed
  automatically on upload into a structured profile (titles, skills, seniority) used for
  scoring. If structured extraction fails (most often because a local LLM isn't set up yet —
  see step 2), the Resume page has a "Retry extraction" button that redoes it later without
  needing to re-upload.
- **Preferences** — Settings → Preferences: target titles, locations, salary floor, seniority,
  employment types, dealbreakers (common ones are checkboxes, plus free text for anything else),
  and which job sources to enable — see **Job sources** below for what each one needs. Fields
  save automatically as you edit or navigate away; there's still a Save button too.
- **Schedule** — Settings → Scheduling & reminders: how often automatic searches run (a
  repeating interval or a fixed time once a day, in your machine's own local timezone) and how
  follow-up reminders/email digest work.

### 4. Run a search

Click "Run search now" on Jobs, or just leave `hanarr serve` running — it searches automatically
on the schedule you set in step 3. While a search runs, the Jobs page updates live: the job
list, stats bar, and filter counts refresh as each posting is scored, instead of only after the
whole run finishes. Mark a job's status (applied / interviewing / etc.) as you go to get
follow-up reminders automatically.

### Command line (optional)

The dashboard is the intended way to use Hanarr, but the same actions are available without it —
useful for scripting or a one-off run:

```bash
hanarr init                  # parse resumes/resume.md into a structured profile (the dashboard's
                              # resume upload does this automatically; only needed without it)
hanarr search                # one-off search cycle
hanarr list                  # see matches, sorted by fit score
hanarr status 3 applied      # mark job id 3 as applied -> schedules a follow-up reminder
hanarr remind                # check for and deliver due reminders
```

`hanarr init` reads `resumes/resume.md` (or `.txt`/`.pdf`; set `profile.resume_path` in
`config.yaml` for a different name/location) and needs `config.yaml` to already have your
preferences in it — `cp config.example.yaml config.yaml` first if you're setting up this way
instead of through the dashboard. `config.yaml` is gitignored, so your preferences never get
committed even if you fork this repo publicly. See **Configuration reference** below for what's
in it — but if you're using the dashboard, you shouldn't need to open it directly at all.

The Windows packaging foundation is documented in [`docs/windows-installer.md`](docs/windows-installer.md).
It uses PyInstaller plus Inno Setup, keeps user data in `%LOCALAPPDATA%\Hanarr`, provides Start
Menu shortcuts and an optional Desktop shortcut, and preserves data on uninstall. Run
`.\scripts\build_windows.ps1 -ValidateOnly` first to fail clearly if Python, PyInstaller, Inno
Setup, or packaging inputs are unavailable, then run `.\scripts\build_windows.ps1` to produce an
unsigned installer. The script verifies that the expected non-empty artifact exists. This is a
buildable packaging path, not a signed or released artifact;
the installer never installs Ollama and the existing explicit-consent setup flow remains in charge
of optional provider actions.

The **CI — Test and Build Windows Installer** workflow runs the test suite and packaging preflight
on a Windows runner, then builds and uploads the unsigned installer only after a real
PyInstaller/Inno Setup build produces a non-empty executable. Successful builds also include a
SHA-256 sidecar and release metadata. To publish an unsigned release, push a `vX.Y.Z` tag that
matches both version fields, then manually run **Release — Publish Windows Installer** in GitHub
Actions with that tag. It tests and builds from that tag, then publishes a GitHub Release with
the installer, SHA-256 checksum, metadata, and an unsigned-installer warning. The workflow run is
the publication approval; no signing secret is required. Windows may warn that the installer's
publisher is unknown. `v0.1.0` and `v0.1.1` have shipped through this pipeline — see the
[Releases page](https://github.com/David-S-Hunsicker/hanarr/releases). The installer never
installs Ollama.

Update checks are on by default in Settings → Updates. Hanarr reads either a configured JSON
release endpoint or the latest GitHub release, validates semver, release-note URLs, asset URLs,
and SHA-256 checksums, and displays the current/latest version and release notes link. Checks
fail clearly when offline. By default, a found update downloads, verifies its checksum, and
installs itself after a visible, cancellable countdown (shown site-wide, with an "Update now" /
"Cancel" option) — an in-progress search is never interrupted, so applying waits until no search
is running. Unchecking "Automatically download and install updates" (leaving update checks
themselves on) switches to manual-only: updates are found and shown, but only ever installed by
clicking "Install now."

Uploads are bounded and stored locally: resume uploads are limited to 10 MiB, and local
submission artifacts are limited to 100 files, 10 MiB per file, and 50 MiB total. Uploads are
written in chunks to a staging path and moved into place only after validation; rejected or
failed uploads do not become visible artifacts. Keep the dashboard bound to `127.0.0.1` unless
you add an authentication and CSRF boundary; non-local exposure is not a supported release
configuration.

## Job sources

Turn sources on and fill in company boards/tags from Settings → Preferences → Job sources. Only
sources with legitimate public APIs are included — this project won't add scrapers for sites
whose Terms of Service prohibit automated access (LinkedIn, Indeed, etc.); that risks your
account and isn't something to build around.

| Source | What it needs | Notes |
|---|---|---|
| Greenhouse | Company slugs from `boards.greenhouse.io/<slug>` | Thousands of companies use Greenhouse; check a company's careers page for the slug. Ships with 67 verified boards enabled by default. |
| Lever | Company slugs from `jobs.lever.co/<slug>` | Free public API. Fewer large public users than Greenhouse these days; ships with 13 verified companies. |
| Ashby | Company slugs from `jobs.ashbyhq.com/<slug>` | Free public API, popular with AI-native and recent-generation startups. Ships with 21 verified boards. |
| RemoteOK | An optional tag filter | Free public API |
| Arbeitnow | Nothing — just enable it | Free public API, mostly EU-heavy listings |
| Workday | A company's own public Workday careers URL, one per line | Public but *undocumented* — the same JSON endpoint the careers page's own JavaScript calls, not a contract Workday publishes or supports. Reaches traditional-enterprise/healthcare/large-non-tech employers the sources above mostly miss. Slower per company (a second request per job is needed just to get a description) and no shipped defaults, since there's no directory of tenants to sample from. |
| Recruitee | Company slugs from `<slug>.recruitee.com` | Free public API. Skews European and toward small/mid-size employers rather than funded startups — ships with 18 verified boards (transit, food retail/processing, construction, hospitality, logistics, a nonprofit, plus a few SaaS companies), a genuinely different mix than the sources above. |
| Workable | Free-text search keywords (e.g. your target titles) | Free public API, but shaped differently from the sources above: Workable has no stable per-company board directory to ship defaults from (most individual accounts return zero current postings), so this searches Workable's own public cross-employer job search by keyword instead, same shape as RemoteOK's tags. |
| USAJOBS | A free API key + the email you registered it with | The official US federal government jobs API (`developer.usajobs.gov`). Unlike every other source above, there's no anonymous access — register a free key, tied to your email, and both are entered in Settings (the key goes to your OS credential store, never config.yaml). Reaches federal/public-sector postings none of the other sources touch at all. |

The default company boards above skew heavily toward VC-funded tech/startup companies — fine
for a software engineer, but an accounting/payroll/HR/etc. search against all of them gets
buried in postings from companies that genuinely hire almost entirely engineers. The "Only query
default companies that match your resume" checkbox (Settings → Preferences → Job sources, on by
default) filters which *default* companies get queried based on categories inferred from your
resume and target titles — a company you add yourself is never affected by this, only the
shipped defaults are, and an unclear/empty profile disables filtering entirely rather than
matching nothing. See `src/hanarr/company_categories.py`.

Adding a new source is one file: implement `Connector.fetch()` in `src/hanarr/connectors/`
returning a list of `RawJobPosting`, then register it in `connectors/registry.py`. See
`greenhouse.py` for a minimal example.

## Configuration reference

Everything below has a control on a Settings page — this is the reference for what's stored
underneath (`config.yaml`, gitignored so it never gets committed) and where to find it if you're
scripting or reading `config.example.yaml` directly, not instructions for hand-editing a file:

- **Preferences & matching** tab — target titles, keyword boosts/excludes, seniority, locations,
  remote/onsite, salary floor, industries to include/exclude, dealbreakers (common ones like
  on-call, travel, or undisclosed pay are checkboxes, plus free text for anything else), minimum
  fit score, and job sources (see **Job sources** above). Every list field (titles, keywords,
  locations, company boards, etc.) is an input-chip box — type a value and press Enter to add it
  as a removable bubble, instead of a large freeform textarea. Fields autosave.
- **App config** tab — resume upload, LLM provider (`ollama` default/local/free, `anthropic`
  hosted with a usage cost, or `none` for rule-based keyword scoring only), model selection and
  download, and the Anthropic API key (stored via your OS's credential store, never in
  `config.yaml`). Specialized agent roles (`profiler`, `market_analysis`, `curriculum`,
  `evaluator`, `resume_writer`, `coach` — each can independently override the main LLM settings via
  `agents.*` in `config.yaml`) all reuse that one key. A "Tutorials" section here has a master
  "Show tutorials" switch and a "Reset dismissed tutorials" button — each tutorial (currently just
  the onboarding checklist) also has its own "don't show this again" dismiss, tracked per profile
  independently of this switch.
- **Scheduling & reminders** tab — how often searches run (a repeating interval or a fixed time
  once a day, in your machine's own local timezone, not UTC) and how reminders/email digest work
  (the SMTP password is also stored via your OS's credential store). A minimum-interval floor
  and a shared "search running" state (visible everywhere via a site-wide activity badge,
  distinguishing an automatic run from one you clicked) prevent two searches from overlapping.
- **Updates** tab — see the update-checks paragraph above.

## Data & privacy

Everything lives in a local SQLite database under `data/` (gitignored). `config.yaml` (your
preferences) and `resumes/` (your resume) are also gitignored — cloning this repo gets you the
code, not anyone's personal data. Nothing is sent off your machine except: requests to whichever
job-source APIs you enable, and — only if you opt into `llm.provider: anthropic` — your resume
text and job descriptions sent to Anthropic's API for scoring.

## Running your own instance vs. sharing this project

This is still built as **one local instance, no hosted accounts or multi-tenancy** — there's no
login, no remote database, and no isolation between people beyond what a single local machine
already provides. Within that, one instance now supports **multiple local profiles**: each
profile (created from the Profiles page) gets its own resume, jobs, applications, skills, coaching
projects, and — as of this pass — **its own match criteria** (target titles, locations, salary
floor, dealbreakers, seniority, etc.), and a browser cookie tracks which profile it's acting as.
Saving the Settings → Preferences tab while acting as one profile only affects that profile; a new
profile starts from bare defaults rather than inheriting whatever the existing profile already has
configured, since it's likely a different person. Job sources (company boards/connectors), the LLM
provider, and the schedule remain shared across every profile on the instance — useful for a
household sharing one machine's instance without re-entering the same company-board lists per
person.

This is not the same thing as a real multi-tenant, hosted product — there's still no
authentication (anyone with access to the browser can switch profiles), no per-user access
control, and no remote deployment story. If you want to hand this to friends who aren't sharing
your machine, the path today is still "you each clone and configure your own copy." The data
model (everything scoped under `Profile`, now with genuine profile-switching built on top) is
structured so a real hosted multi-tenant version — should this ever become that — is closer to
an extension than a rewrite, but auth and a hosted deployment still haven't been built.

## Development

```bash
pip install -e ".[dev]"
pytest
```

Connector tests mock HTTP responses (via `respx`) — they don't hit real APIs. Matching tests
cover the prefilter and the rule-based fallback scorer.

## Local release-readiness checklist

- [x] Confirm a fresh backup appears under `data/backups/` before an upgrade — this runs
  automatically on every `make_session_factory()` call and is covered by migration tests.
- [x] Start from the repository root and confirm migrations complete without warnings —
  verified across all nine migrations on a real database.
- [x] Confirm the dashboard remains bound to localhost and that resume/local-submission limits
  reject oversized uploads (covered by tests; live-verified for resume uploads).
- [x] Run `python -m pytest -q` (582 tests), `python -m compileall -q src`, and
  `git diff --check`.
- [x] Walk through Jobs, Coaching, Resume, Skills, Applications, Settings, and Profiles on a
  real running instance with real data.
- [ ] Validate `hanarr serve --launch-mode webview` specifically (browser mode and the packaged
  webview executable have both been verified; the dev-mode `--launch-mode webview` path
  hasn't been separately re-checked recently).
- [x] On Windows, ran `.\scripts\build_windows.ps1 -ValidateOnly`, then built the unsigned
  installer, installed it silently, launched the installed executable against its own
  `%LOCALAPPDATA%\Hanarr` data, and uninstalled it with data preserved — all on a real
  Windows dev machine (not yet a clean one).
- [ ] Sign and verify the installer and packaged executables with Authenticode, then validate
  install, shortcuts, launch, upgrade, uninstall, and data preservation on a genuinely clean
  machine/VM with no prior Python/Ollama/Hanarr installation. This is the main remaining gap
  before this could be handed to a non-technical stranger — see
  [`docs/windows-installer.md`](docs/windows-installer.md).
- [ ] Do not enable external GitHub delivery, hosted auth, or CSRF-dependent non-local access;
  those remain future blockers.

## Roadmap ideas

Tracked in [`DEVELOPMENT_LOG.md`](DEVELOPMENT_LOG.md), which also carries the running log of what
shipped and why. Nothing is currently scoped and unstarted. Networking outreach email drafts
shipped last: a "Draft outreach email" button on each job card drafts a short cold-outreach /
informational-interview email for a contact you already found yourself (a name, email, or
LinkedIn URL you type in) -- Hanarr never looks up who to contact, since the tools that do that
well run into the same LinkedIn-ToS/scraped-data problems ruled out earlier. **Coach** shipped in
full (both planned v1 phases) before that: a chat
assistant grounded entirely in your own stored resume, matched jobs (with their actual fit
rationale), and active coaching projects -- the grounding is what makes it worth more than opening
Claude or Gemini directly, not the chat interface itself -- plus a confirm-before-execute action
registry, so you can ask it to do things ("create a project for Kubernetes on the Acme job")
through the same reviewed backend functions the UI already uses, never an arbitrary write; nothing
runs until you click Confirm on the card it shows in the chat. Three more job
source connectors shipped before that: Recruitee (a genuinely
different mix than the startup-heavy defaults — energy, construction, retail, automotive),
Workable (its own public cross-employer keyword search, since Workable has no stable per-company
board directory to ship defaults from the way the others do), and USAJOBS (the official federal
government jobs API, reaching a candidate pool none of the other sources touch at all). Two
automation ideas were considered and dropped instead of scoped: reading a connected email account
for recruiter messages, and automatically clicking through LinkedIn's Easy Apply — the latter runs
straight into LinkedIn's anti-automation ToS and real account-ban risk, which isn't a trade worth
making. Fuzzy/synonym skill matching and
persisted cross-run activity history are deliberately shelved — real ideas, but nothing's actually
blocked by either right now. A Workday connector reaches enterprise/healthcare employers the
startup-focused boards miss, a passed coaching-project evaluation now closes the loop (bumps
proven-skill confidence, satisfies the job's skill gap, and offers a resume proposal) instead of
updating nothing, a "Quick skill check" on the Skills page tests a claimed skill with a short
bounded Q&A instead of trusting resume wording or a self-reported number, the **Prep** page
generates behavioral-interview questions from the resume's actual work history and helps build
STAR stories for them (marking a job "interviewing" surfaces a one-click link to generate
questions weighted toward that specific job), Settings → App has a "Per-task model sizing" section
so the (now six, including Coach) LLM-backed roles can each use a differently-sized model instead
of one blanket choice — researched
from actual call sites rather than assumed: the highest-frequency role (job fit scoring) turned
out to be the one that wants a *heavier* model, not a lighter one, since frequency and task
complexity don't move together here — an in-app **Guide** tab (`/guide` in the dashboard) covers
how to use Hanarr page by page, job cards can draft a cover letter directly from the stored
resume, a "+ Add a job manually" form on Jobs tracks a posting the connectors didn't find, and
reminders export to a real calendar app as .ics files. The site-wide update banner had a real bug
where an inline `display` style silently defeated its `hidden` attribute, leaving "Update now"
visible at all times regardless of whether an update was actually staged — fixed, along with
adding a simulated progress indicator during install and showing the running app version on every
page, not just buried in Settings. The Guide, onboarding checklist, and README's getting-started
steps now tell you to set up a local AI model *before* uploading a resume, since resume parsing
without one silently degrades to a keyword-only fallback rather than an LLM actually reading it.
A follow-up audit of that banner fix found the same `hidden`-defeated-by-`display` defect in two
more spots (an install-progress indicator and the Ollama-setup spinner), both fixed the same way.
The onboarding checklist is now individually dismissible ("don't show this again", persisted per
profile in a `dismissed_tutorials` table) with a master "Show tutorials" switch and a "Reset
dismissed tutorials" button in Settings → App config, the start of a general mechanism for future
contextual tutorial popups rather than a one-off for just this banner. The first such popup
shipped on top of it: a site-wide "New here? The Guide page walks through what each tab does."
banner, shown until dismissed (or until you actually visit Guide, which dismisses it for you).
Three more followed on Coaching, Skills, and Prep, each calling out that page's one non-obvious
first-visit behavior (review-first evidence submission, "Quick skill check" testing a claimed
skill instead of trusting resume wording, and writing a STAR story ahead of time) — each
dismisses independently and needed no migration, which was the point of the narrow table design.

## License

A custom license based on the [PolyForm Strict License 1.0.0](https://polyformproject.org/licenses/strict/1.0.0)
— see `LICENSE`. In short: you're welcome to download and run Hanarr for your own personal job
search — searching for postings, tracking your own applications, preparing your own materials —
and nothing else. Modifying it, distributing it (modified or not), or using it for any other
purpose (a business, nonprofit, school, government agency, or anything unrelated to your own job
search) requires the copyright holder's express prior permission.
