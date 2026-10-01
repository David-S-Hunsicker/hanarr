# Hanarr development log

`TRANSITION_PLAN.md` documented the `job-search-copilot` → Hanarr transition and is retired now
that the transition it describes is complete (full text remains in git history if it's ever
needed). **README.md stays the current-state reference** — setup, status, and what's verified
live there. This file is the ongoing one: a running log of notable changes as they ship, and a
place to keep real design plans for what's next before they're built, so a plan doesn't have to
live only in a chat transcript.

## Planned

Nothing is currently scoped and unstarted. Per-task model sizing shipped — see the Log below. STAR
story builder, mini-interview skill assessment, and coaching project loop closure shipped before
it. The "How to use Hanarr" guide page shipped earlier still.

## Deferred / low priority

Real ideas, but nobody's actually blocked by either — no need pulling on them right now, so they
sit here instead of Planned. Revisit if a concrete case for one comes up.

- **Fuzzy/synonym skill matching** (e.g. "JS" ↔ "JavaScript") — would reduce occasional false
  "missing" flags, but a wrong synonym mapping risks the opposite failure (a false *positive* —
  claiming a skill that isn't really there), which is worse than what it fixes.
- **Persisted, cross-run activity history** — the Jobs page already shows a live scrolling log
  during an active search; this would only add value across restarts/past runs (an audit
  nice-to-have), not something blocking real use today.

## Log

Dated entries go here as work ships, newest first. Not a full history — `git log` is authoritative
for that; this captures the *why* behind notable changes, the way commit messages don't always
carry forward into a skimmable list.

### 2026-10-01 — v0.1.15

Shipped: Guide page catch-up and a manual "Mark complete" action for coaching projects (see below).

### 2026-10-01 — Guide page catch-up and manual project completion

Two small, real gaps found by surveying the app rather than inventing new work:

1. **Guide page was stale.** "What each page is for" still listed only the original six pages —
   no card for **Prep** (shipped in v0.1.13), and the Skills/Settings cards said nothing about
   "Quick skill check" (v0.1.12) or "Per-task model sizing" (v0.1.14). Caught up all three, plus
   the Coaching card now describes auto-complete-on-pass, manual completion, cancellation, and the
   stale-project nudge.
2. **No manual project completion.** The original coaching-project loop-closure scoping wanted
   completion "suggested once all tasks are done and/or a submission has passed, not
   auto-forced" — what shipped (v0.1.10) was only auto-complete-on-pass plus Cancel, with no way
   to mark a project complete without ever submitting evidence for an LLM review. New
   `POST /api/coaching-projects/{id}/complete` and a "Mark complete" button alongside the existing
   Cancel button. Deliberately asymmetric with a passed evaluation: it sets `Project.status` and
   `completed_at` only — it never touches `ProvenSkill`, bumps `ProfileSkill.confidence`, or marks
   a `JobSkill.gap_status` satisfied, since those are evidence-backed claims an unreviewed
   self-declaration shouldn't silently earn.

Three new regression tests across `test_dashboard_app.py` and `test_coaching_projects.py`; `/prep`
added to the existing nav-link and shared-shell-layout consistency tests (it was missing from both
page lists since it shipped).

### 2026-10-01 — v0.1.14

Shipped: per-task model sizing (see below).

### 2026-10-01 — Per-task model sizing

Closed the last Backlog item, starting with the research step the Backlog entry itself demanded
("not guessed at"): read every `orchestrator.client_for(...)` call site in `dashboard/app.py` to
characterize what each of the five roles actually does.

**The real finding, which changed the plan.** The original framing assumed frequency and
complexity move together — "lighter model for cheap/frequent calls, heavier for the calls that
need it." They don't. `market_analysis` (`matching.score_fit`, called once per posting in every
search — the single highest-frequency LLM call in the app) is also the most complex: full resume
text plus the full posting, careful "claimed vs. demonstrated experience" judgment (see
`matching.SYSTEM_PROMPT`). It's the one role that should shift to a *heavier* model, not a lighter
one. `curriculum` (coaching-project briefs, STAR practice questions) is the one role where
"lighter" is actually safe — short structured JSON, triggered manually and infrequently, and
already has a deterministic fallback on bad output. `profiler`, `evaluator`, and `resume_writer`
each mix simple and complex sub-tasks without a clear case to shift either way, so they stay at
the hardware-based baseline.

**Shipped.** `ollama_setup.recommend_model_for_role(role, hardware)`: the same hardware-tier
baseline as `recommend_model()`, shifted one tier up for `market_analysis`, one tier down for
`curriculum`, unchanged for the other three — and never shifted past the existing <10GB-free-
storage safety floor, even for a role that would otherwise want a heavier model. A new "Per-task
model sizing" section on Settings → App lists all five roles with a one-line description of what
each actually does, a dropdown (installed models + the role's suggestion + custom) defaulting to
"use the shared model above," and the reasoning behind each suggestion. `AgentRoute.model`
overrides were already the storage mechanism (`agent_orchestration.py`/`config.py`) — this just
exposes it; a blank field keeps inheriting the shared model, exactly as before.

Seven new regression tests across `test_ollama_setup.py` and `test_dashboard_app.py`.

### 2026-09-29 — v0.1.13

Shipped: STAR behavioral-story builder, and a new top-level **Prep** nav tab (see below).

### 2026-09-29 — STAR behavioral-story builder

Built the feature scoped in the removed Planned section above, resolving its own open nav
question: a new top-level **Prep** tab, added to all nine page templates (no shared nav partial
exists, so each got the same one-line insertion) since mini-interview shipped first and now
there's a real second interview-prep feature to justify a dedicated tab rather than guessing the
shape upfront.

New `StarQuestion`/`StarStory` tables (migration `0016`) and `star_stories.py`:
`generate_star_questions()` asks the LLM for up to 6 questions tagged by competency (leadership,
conflict, failure, ambiguity, technical tradeoff, cross-team collaboration), based on the resume's
titles/seniority/industries and, optionally, a specific saved job's title/description — falling
back to a fixed set of 6 competency-tagged questions (never job-weighted) on bad LLM output.
`review_story()` reviews a draft's four STAR fields in one pass and returns per-field "tightened"
wording suggestions the person can accept or ignore, plus feedback flagging vague components or a
Result with no measurable outcome — never invents a fact, and the deterministic fallback echoes
the original text back unchanged rather than guessing at a rewrite.

One deliberate scope cut from the original scoping: a single review pass over the whole draft
instead of a multi-turn "one component at a time with a clarifying follow-up" conversation — the
core value (catching vagueness, tightening wording, flagging an unmeasured Result) doesn't need a
stateful multi-turn conversation machine to deliver, and this is far simpler to build and reason
about. A story is upserted per question (edit and re-practice in place) rather than versioned.

New routes: `POST /api/star/questions/generate`, `POST /api/star/questions/{id}/story`,
`PATCH /api/star/stories/{id}`, `POST /api/star/stories/{id}/review`,
`POST /api/star/stories/{id}/status`. Seven new regression tests in `test_star_stories.py`.

### 2026-09-29 — v0.1.12

Shipped: an explicit improvement plan and structured resources on a mini-interview verdict, a
standalone "start a coaching project" action on the Skills page, and a real bug fix in reusable-
skill project creation (see below).

### 2026-09-29 — Mini-interview follow-through: explicit plan, structured resources, direct project start

Direct feedback: a person shouldn't be left to invent their own improvement plan after a skill
check. Three changes to `skill_interview.py`/`coaching_projects.py`/`skills.html`:

1. **Deterministic improvement plan.** `submit_interview()` now writes a fixed, ordered
   `plan_json` (new column, migration `0015`) keyed off the verdict — not LLM-generated, so it's
   reliable and free: `remediate` → review resources, then retake; `rebuild` → start a project,
   submit evidence, then retake; `solid`/`could_not_assess` get their own one-line plans. Rendered
   as an ordered checklist in both the live result panel and the history list.
2. **Structured resources.** `resources` changed from bare strings to `{"title", "why"}` objects
   so a remediation suggestion says what it addresses, not just a name — still explicitly
   plain-text/unverified LLM output, not fetched or checked. (Considered giving Hanarr a real
   web-search capability to fetch verifiable links instead — deliberately not doing that; it's a
   real architecture change against the "no scraping, legitimate APIs only" principle the rest of
   the app follows, and wasn't asked for.)
3. **Direct project start.** A "Start a coaching project for this skill" button on every skill row
   (not gated behind running an interview first) reuses the same reusable-skill
   `POST /api/coaching-projects` flow. Doing this surfaced a real bug: `create_coaching_project`'s
   reusable-skill branch built its `skills` list only from existing analyzed `JobSkill` gap rows —
   for a skill with zero of those (exactly the case a mini-interview verdict hits, since it never
   required a job analysis), the project silently ended up linked to *no* skill at all, with a
   blank title. Fixed: the branch now always includes the directly-looked-up skill.

Three new regression tests across `test_skill_interview.py` and `test_coaching_projects.py`.

### 2026-09-29 — v0.1.11

Shipped: mini-interview skill assessment (see below).

### 2026-09-29 — Mini-interview skill assessment

Built the feature scoped in the removed Planned section above, with one deliberate scope cut from
that scoping: an inline expansion under the existing skill row (`skills.html`) instead of the
dedicated `/skills/{id}/interview` page it leaned toward — kept it inline since the actual flow
(2-4 short questions, one round of free-text answers) turned out small enough not to need a
separate page, and it matches the existing `confidence-editor` inline-expansion pattern already on
that row.

New `SkillInterview` table (migration `0014`) and `skill_interview.py`: `start_interview()` asks
the LLM for 2-4 questions calibrated to the skill's claimed proficiency/confidence, existing
evidence, and the most recent prior interview's verdict, falling back to two fixed
still-non-generic questions on bad LLM output (`evaluator="deterministic"`); `submit_interview()`
classifies the answers into `solid`/`remediate`/`rebuild`, or `could_not_assess` on bad LLM output
(`evaluator="deterministic-fallback"`, no ProfileSkill change either way).

A `solid` verdict writes/updates a `source="interview"` `ProfileSkill` row at a fixed 0.75
confidence floor (not scaled by a score — unlike the coaching-project evaluator, this LLM call
returns a category, not a comparable 0-100 number) — same non-clobbering rule as the coaching
project bump: a higher-trust resume/manual source's confidence only ever rises, never silently
drops, and the corroboration is recorded in evidence text. A `rebuild` verdict offers a "Start a
coaching project" button that reuses the existing reusable-skill `POST /api/coaching-projects`
flow rather than a second project system. `remediate` surfaces the LLM's suggested refresher
resources, explicitly plain-text/unverified (Hanarr has no web-search capability to check them).
Past interviews for a skill show in a small history list on the row.

Two new routes: `POST /api/skills/{id}/interview/start`, `POST
/api/skills/{id}/interview/{interview_id}/submit`. Seven new regression tests in
`test_skill_interview.py`.

### 2026-09-29 — v0.1.10

Shipped: coaching project loop closure (see below), plus a README pass to fix the stale test count
(319 → 453) and refresh the Roadmap ideas section, which still described the Workday connector as
"next up" after it had already shipped.

### 2026-09-29 — Coaching project loop closure

Closed the gap traced and scoped in the removed Planned section above: a passed evaluation used
to update nothing beyond `ProvenSkill` and offering a resume proposal. Now, in
`evaluate_project_submission` (`dashboard/app.py`):
1. **Confidence bump, scaled by score.** A passed evaluation writes/updates a `source="project"`
   `ProfileSkill` row at `evaluation.score / 100`. An existing `source="project"` row is updated in
   place; a higher-trust `"resume"`/`"manual"` row is never silently overwritten downward -- its
   confidence only rises (never falls) and the corroboration is recorded in its evidence text, not
   applied silently.
2. **`JobSkill.gap_status` set to `SATISFIED`** for the project's affected jobs' matching skills,
   so gap cards on Jobs reflect a proven skill immediately instead of only after the job is
   re-analyzed.
3. **`POST /api/coaching-projects/{id}/cancel`** -- the missing "abandon this honestly" action;
   blocks cancelling an already-completed/cancelled project. Project completion itself already
   auto-fires on a passed evaluation (existing behavior, kept as-is rather than made a separate
   manual step).
4. **Stale-project nudge** -- an `active` project with no task-status change or submission in 14+
   days (fixed threshold; project task counts are too small for a per-project-cadence signal to be
   meaningful) shows a "stale" badge on the Coaching page. New `ProjectTask.updated_at` column
   (migration `0013`) tracks the signal; existing rows read as `NULL` and fall back to the
   project's `opted_in_at`.
Bridging a proven skill to a resume proposal was already wired in (`create_resume_proposal` on a
pass) -- confirmed working, not new. Six new regression tests in `test_coaching_projects.py`.

### 2026-09-29 — v0.1.9

Shipped: navigate straight to a newly created coaching project instead of reloading the Jobs page
(see below), plus the LICENSE audit and roadmap scoping entries from earlier today.

### 2026-09-29 — LICENSE audit: attribution, trademark, and distribution-scope gaps

Full adversarial re-read of `LICENSE`, prompted by a direct worry about someone taking the code,
reselling it, or claiming it as their own. Found and closed four real gaps: (1) "distributing the
software" didn't explicitly say "in whole or in part," leaving an excerpt arguably ambiguous; (2)
"making changes or new works based on the software" didn't explicitly cover a rewrite/reimplementation
based on having studied the code, only literal edits; (3) nothing barred removing the copyright
notice or claiming authorship of the software or a derivative of it -- added an explicit
Attribution and No False Claims section; (4) nothing addressed trademark/branding at all -- added a
No Trademark License section so "Hanarr" can't be used on a fork, rebrand, or competing product
even if someone otherwise had permission to use the code (which they don't, but this closes the gap
regardless). Also added an explicit "what a license can and can't do" note: license text is a legal
remedy (infringement claim, DMCA, cease-and-desist), not a technical lock -- it can't stop someone
with source access from copying files in the first place. The only real control over that is repo
access (public vs. private), which is a separate, still-open decision.

### 2026-09-29 — Fix: Patent License clause wasn't scoped to the permitted purpose

Real gap caught by a direct question about the patent clause: `LICENSE`'s Copyright License grant
is explicitly scoped to "any permitted purpose," but the Patent License grant right below it just
said "by using the software" — not tied to the permitted-purpose restriction at all. Since the
whole point of the narrowing above is "personal job search, nothing else, full stop," an
inconsistently-scoped patent grant undermined that. Added "for a permitted purpose" to the Patent
License clause so both grants are scoped identically.

### 2026-09-29 — v0.1.8

Shipped: the license narrowed to personal job-search use only, and the org-exemption removal
before it (both described below).

### 2026-09-29 — v0.1.7

Shipped: the license change, SQLite WAL mode + checkpoint-before-backup, and file-backed logging
with a catch-all exception handler, all described below.

### 2026-09-29 — License narrowed to personal job-search use only

Follow-up to the license change below: "any noncommercial purpose" was still broader than
intended — it would have covered unrelated hobby projects, research, or general tinkering, not
just the thing Hanarr is actually for. Replaced PolyForm Strict's "Noncommercial Purposes"/
"Personal Uses" sections with a custom "Permitted Purpose" section: the *only* permitted use is
running Hanarr, unmodified, for your own personal job search (searching postings, tracking your
own applications, preparing your own materials). Everything else — any organizational or
commercial use, or any use unrelated to your own job search — needs the copyright holder's
express permission, no exceptions. This is now enough of a departure from the original license
text that it's named/framed in `LICENSE` as a custom license based on PolyForm Strict, rather than
"PolyForm Strict, modified" — the underlying mechanics (no modification, no redistribution,
patent/liability terms) are still borrowed from it, but what counts as permitted use is entirely
custom now.

### 2026-09-29 — License changed from MIT to a modified PolyForm Strict 1.0.0

Repo stays public/source-available, but the license itself now restricts what people can do with
the code: view and run it for personal/noncommercial use, but not modify it, redistribute it
(modified or not), or use it commercially without a separate agreement with the copyright holder.
PolyForm Strict does exactly this out of the box, with one adjustment: its stock text carves out a
blanket exemption for any charity/school/government/public-research/etc. organization regardless
of funding source — removed here, since the ask was no organization gets a free pass, full stop.
"Any noncommercial purpose is a permitted purpose" is now the only test, applied the same way to
an individual, a business, or a nonprofit alike. See `LICENSE` (full text, with the removed
section and the reasoning called out at the top, plus a plain-English summary at the bottom) and
the polyformproject.org
source this was pulled from verbatim, rather than drafting custom legal text from scratch.
(Narrowed further the same day — see the entry above.)

### 2026-09-29 — SQLite WAL mode, for the same bug report

Follow-up to the logging fix below, same bug report (a 500 after starting a search, on the
Coaching page). Couldn't get a real traceback yet (the installed build predates the logging fix),
but the shape fits a known SQLite footgun well enough to fix proactively: a background search
commits frequently (once per matched/rejected posting) while the dashboard's own page loads run
concurrent reads on separate connections from the same pool. SQLite's default rollback-journal
mode lets a writer's transaction briefly block readers — under real contention (slow disk,
antivirus intercepting file I/O, a slower LLM stretching out how long the search runs) that can
exceed the default lock wait and surface as an unhandled "database is locked" error on whatever
page was loading. `db.py` now sets `PRAGMA journal_mode=WAL` and a 30s `busy_timeout` on every
connection — WAL is SQLite's standard fix for readers not blocking on an in-progress writer.

Side effect worth calling out: WAL mode means a recent commit can sit only in the `-wal` file
until checkpointed, so the existing pre-migration backup (a plain `shutil.copy2` of `hanarr.db`)
needed a `PRAGMA wal_checkpoint` added right before it, or it could silently copy a stale (in one
tested case, nearly-empty) snapshot. Tried `TRUNCATE`/`FULL` checkpoint mode first — both block
against a concurrent reader/writer, and tested directly against one, `TRUNCATE` actually corrupted
the checkpoint. Landed on `PASSIVE` (the default, no argument): never blocks or forces anything,
just folds in whatever it safely can, which is what an already-single-threaded startup moment
needs anyway.

This may or may not be the actual bug behind the report — still waiting on a real traceback to
confirm — but it's a real, independently-worth-fixing class of issue either way.

### 2026-09-29 — File-backed logging for the packaged build

Real gap found from a live bug report: a 500 on the Jobs page after starting a search, reported
against the standalone packaged build, with nothing to go on beyond "internal server error" — the
packaged executable runs `--windowed` (no console), so every unhandled exception's traceback was
going to stderr and vanishing into nothing. `logging_setup.configure_file_logging()` adds a
rotating file handler (`<data_dir>/logs/hanarr.log`, capped at ~2MB × 3 files) alongside the
existing console logging; `create_app()` also gained a catch-all FastAPI exception handler so an
unhandled error is guaranteed to log a full traceback before returning a plain 500, rather than
relying on uvicorn's own exception logging path. This doesn't fix the reported bug by itself —
still needs a real reproduction with a traceback in hand — but makes any future occurrence (this
one included) actually diagnosable instead of a dead end.

### 2026-09-29 — Workday connector

Greenhouse/Lever/Ashby all skew toward VC-funded tech/startup companies; this reaches the
traditional-enterprise/healthcare/large-non-tech employers that mostly post through Workday
instead. Unlike the others, this is an *undocumented* endpoint (the same JSON API a Workday
careers page's own JS calls, not something Workday publishes or supports) — researched against
several independent real-world write-ups before writing anything, then live-verified against
NVIDIA's actual public Workday careers site.

Two things that make it a genuinely different shape from the other connectors: (1) the list
endpoint doesn't include a job description at all, only a second per-job request does, so this
is real N+1 request cost bounded by a `MAX_POSTINGS_PER_SITE` cap (200) rather than the single
request per company the others need; (2) configuring a company means pasting its full public
careers URL (tenant/shard/site aren't discoverable any other way — there's no directory), parsed
server-side rather than asking for three separate fields. `postedOn`/`startDate` in Workday's
response are either a localized relative string or ambiguous in meaning, so `posted_at` is left
unset rather than guessed.

### 2026-09-29 — v0.1.6

Shipped: the version number is now visible in the app itself (Settings page header), not just
`pyproject.toml`/`__init__.py` — no reason to have to dig into the source tree to tell what
you're running.

### 2026-09-29 — Per-profile search preferences

Multiple local profiles have shared one set of match criteria (target titles, locations, salary
floor, dealbreakers, etc.) from `config.yaml` since profiles first existed — real for a solo user,
but not meaningfully multi-user for a household sharing an instance. `Profile.preferences_json`
(empty by default) plus `config.effective_preferences(profile, settings)` (falls back live to the
shared value until a profile saves its own) makes this per-profile with zero migration step for an
existing single-profile install. Settings → Preferences now saves onto the active profile; a new
profile starts from bare defaults, not a copy of whatever's already configured — likely a
different person. Job sources, LLM provider, and schedule stay shared.

Found a real bug via the isolation test itself, not from a bug report: the stale-save conflict
check used one global version counter for every tab *and* every profile, so one profile saving
would spuriously block a different profile's unrelated save. Fixed with a separate per-profile
`preferences_version` counter (migration 0012) that only the Preferences tab checks.

### 2026-09-29 — Manual job entry

Third smaller backlog item: a "+ Add a job manually" form on the Jobs page for a posting
Hanarr's own connectors didn't find. Scored through the exact same `matching.score_fit` path a
real search cycle uses, so it gets a real fit score/rationale, not a placeholder. No hard-delete
exists yet for a stray manual entry (only status changes) — worth adding if this gets used a lot.

### 2026-09-29 — .ics calendar export

Second smaller backlog item: `GET /reminders/{id}.ics` and `GET /reminders.ics` export
follow-up/interview-prep reminders as real calendar events. Hand-wrote minimal RFC 5545 rather
than adding a dependency. See `calendar_export.py`.

### 2026-09-29 — Cover-letter drafting

Shipped the first of the smaller backlog items: cover-letter drafting for a saved job, reusing
the existing `resume_writer` agent role with a deterministic mail-merge fallback. Simpler than
the resume-proposal loop by design — a draft never needs approval to "activate" anything, so it's
just stored text the user edits and copies themselves. See `cover_letter.py`.

### 2026-09-29 — v0.1.5

Shipped: profile renaming (a profile named for a one-off purpose, e.g. "UI Check" from a QA
pass, was stuck that way with no fix short of editing the database directly), and a company-name
styling fix — dropped an unwanted "Company:" prefix in favor of a dedicated `--company-accent`
color so the company name stands out without a label.

### 2026-09-29 — Guide page

Shipped the first of the three planned features: an in-app `/guide` page explaining Hanarr's
workflow, what each page is for, what fit scores/skill-gap statuses mean, and the local-Ollama-
vs-hosted-Anthropic-vs-none trade-off. Hand-written content, not LLM-generated, since it explains
Hanarr's own real behavior rather than something a model should guess at.

### 2026-09-29 — v0.1.4

Shipped the `DEVELOPMENT_LOG.md`/`TRANSITION_PLAN.md` restructure described above, plus two Jobs-
page UX fixes reported directly: a duplicate header link ("Preferences & config") pointed at the
exact same route as the Settings tab immediately below it, and job posting title links had no
underline and the same color as body text, so they didn't read as clickable until hovered — both
fixed, plus an explicit "Apply" link and a "Company:" label on the company line.

### 2026-09-29 — v0.1.3

Shipped: the config-autosave data-loss fix (optimistic concurrency control + `config.yaml`
backups — the incident that prompted it is worth remembering: a stale autosave tab silently wiped
`locations`/`salary_floor_usd`/job-source config because the form always resubmitted every field,
not just the one edited), the "Why this score" duplicate-label fix, "Improve my fit" staleness
detection (resurfaces the button when the active resume changes after an analysis) and real error
surfacing instead of silently reverting on failure, a "View on Skills page" deep link next to
"Start project for {skill}" on a job's gap list, and best-effort skill-evidence sync from a
GitHub profile URL found in the resume (`source="github"` `ProfileSkill` rows from public,
non-fork repo languages — niche, mainly useful for software engineers, never overrides existing
evidence).

Retired `TRANSITION_PLAN.md` in favor of this file, and scoped the three planned features above.
