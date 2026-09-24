# Hannar transition plan

**Status:** Planning document only. This plan does not implement the Hannar product or
change the current application behavior.

## 1. Purpose and decisions

The goal is to evolve `job-search-copilot` into **Hannar**, a unified, local-first
job-search and career-coaching application. `job-search-copilot` is the implementation
foundation; Hannar is not a parallel rewrite or a hosted multi-user product at this stage.

The following decisions are treated as requirements for the transition:

- Keep the existing dashboard as the first integration surface. Coaching should appear
  where the user already reviews jobs, statuses, reminders, and resume information.
- Preserve the local SQLite data store and the existing local-only operating model. Hannar
  is single-user for now; do not introduce accounts, hosted tenancy, or a remote database
  as part of this transition.
- Preserve the public-API connector policy and scheduler. Do not add ToS-violating
  scraping. Existing search cycles and reminder checks remain reliable background jobs.
- Keep Ollama/local-first inference as the default. Anthropic remains optional and
  configurable per agent, rather than becoming a global dependency.
- Add visual skill-gap indicators to each eligible job posting. A user should be able to
  see both the relevant strengths and the missing or uncertain skills before deciding
  whether to pursue a role.
- Projects are manually opted into. Hannar may recommend a project, but it must not
  silently create work, change a resume, or submit an application.
- Support two project modes:
  - **Posting-specific:** a project is tied to one job and targets that posting's gaps.
  - **Reusable skill project:** a project targets a transferable skill and can contribute
    to multiple related jobs.
- When a project is passed, automatically re-score related saved jobs and show the
  before/after impact. Re-scoring must be explainable and must not overwrite history.
- Use a multi-agent specialized orchestrator. Agents have narrow responsibilities and
  individually selected providers/models; the orchestrator owns sequencing, evidence,
  permissions, and user approval.
- Resume changes are versioned proposals requiring explicit approval. No agent may replace
  the active resume without the user accepting a proposal.
- Future submission types include local files/folders, written responses, and GitHub.
  Submission is a later capability and must remain opt-in and reviewable.
- Defer Anthropic web search and scraping. Hannar should use the existing public
  connectors and user-provided/local material until a future decision establishes a
  compliant, useful web-research boundary.

## 2. Current-state comparison

### `job-search-copilot` (foundation)

The current repository already provides the safest path to a useful Hannar v1:

- A FastAPI/Jinja server-rendered dashboard with job review, status updates, resume upload,
  configuration, search controls, and reminder views.
- SQLAlchemy models stored in a local SQLite database. `Profile` scopes jobs and reminders
  for the current single-user installation.
- Public-API connectors for Greenhouse, Lever, RemoteOK, and Arbeitnow, with a connector
  registry and a pipeline that prefilters, scores, and stores postings.
- APScheduler jobs for recurring searches and reminder delivery.
- Follow-up, interview-prep, desktop, and optional email reminders.
- LLM abstraction with Ollama as the default, Anthropic as an optional provider, and a
  no-LLM keyword fallback.
- Resume parsing and structured profile data used by fit scoring.
- Deliberately local data/privacy boundaries and no authentication or multi-tenant
  deployment.

The current model is intentionally narrow: a posting has one current fit score and
rationale; there are no skill-gap entities, learning projects, agent runs, resume proposal
versions, submission artifacts, or score-history records. `SeenPosting` also prevents
automatic rescoring after the first evaluation, so that behavior must be extended
carefully rather than treated as an existing capability.

### Separate `hanarr` project

The separate Hannar work represents the broader product direction: coaching, a
multi-agent loop, skill-building projects, proposal-based resume evolution, and eventual
submission workflows. It should be treated as product/design input, not as the migration
authority for job-search data. The transition should avoid a wholesale code transplant:
the proven connector, dashboard, scheduler, privacy, and local-inference boundaries in
`job-search-copilot` remain the source of truth for those capabilities.

At the start of implementation, create a short inventory of the separate project
(models, prompts, agent contracts, and UI concepts) and map only reusable concepts into
the foundation. Resolve naming and schema differences through explicit migrations and
adapters. Do not import an incompatible persistence layer, remote service, or scraping
behavior merely to match the separate project's structure.

## 3. Target architecture

Hannar should remain a modular local application with the existing dashboard and pipeline
at its center:

1. **Presentation layer:** the dashboard becomes Hannar's primary UI. Job cards expose
   fit, skill gaps, project actions, score history, and coaching context without hiding
   the existing application-status and reminder workflows.
2. **Application services:** separate services coordinate skill extraction, gap analysis,
   project lifecycle, coaching sessions, rescoring, resume proposals, and submissions.
   Services should be callable by HTTP routes, the scheduler, and agent tools without
   embedding business rules in templates.
3. **Agent orchestration:** a specialized orchestrator runs bounded agent tasks with
   explicit inputs, outputs, provider configuration, permissions, and durable run status.
   Suggested initial roles are job analyst, skills/gap analyst, coach/project planner,
   resume editor, and submission-preparation reviewer. A coordinator decides which role
   runs next and when user approval is required.
4. **Domain model:** extend the existing profile/job/reminder model with normalized skills,
   job-skill evidence, projects, project skills, project outcomes, score snapshots,
   coaching/agent runs, resume versions/proposals, and submission artifacts.
5. **Persistence:** continue using SQLite and SQLAlchemy. Add migrations before relying on
   new tables, preserve existing IDs and timestamps, and keep user content on disk/local
   unless the user explicitly configures an external provider.
6. **Inference boundary:** route each agent through a provider configuration. Ollama is
   the default for local-first operation; Anthropic can be selected for a specific agent
   or task. Prompts and model/provider choices must be recorded with outputs where
   reproducibility matters.
7. **Integrations:** retain public job connectors and APScheduler. Add local file/folder,
   written-response, and GitHub submission adapters only after the core approval and
   artifact model is stable.

## 4. Data model and migration strategy

### Proposed domain additions

Names are provisional and should be finalized during schema design:

- `Skill` and `ProfileSkill`: canonical skill identity plus evidence, proficiency, and
  confidence for the user's profile.
- `JobSkill`: required/preferred skill, evidence span or rationale, gap classification,
  and analysis timestamp for a posting.
- `Project`: manually opted-in work with `posting-specific` or `reusable-skill` mode,
  status, scope, and target outcomes.
- `ProjectSkill`: skills practiced by a project, including target level and evidence.
- `ProjectOutcome`: completion/pass decision, evidence, completed time, and approver.
- `ScoreSnapshot`: immutable fit score, rationale, skill-gap inputs, scorer/provider
  metadata, and trigger (`initial`, `project_passed`, `manual`, or `criteria_changed`).
- `AgentRun` and `AgentArtifact`: orchestrator state, role, inputs, outputs, model/provider,
  errors, approvals, and links to affected jobs/projects/proposals.
- `ResumeVersion` and `ResumeProposal`: immutable resume content/metadata and a proposed
  diff with `pending`, `approved`, or `rejected` state.
- `Submission` and `SubmissionArtifact`: destination type, local path or GitHub reference,
  written response, review state, and eventual submission result.

Existing `Profile`, `JobPosting`, `SeenPosting`, `Reminder`, and application statuses remain
the compatibility core. `JobPosting.fit_score` may remain as the current/latest display
value, but every new score must also create a `ScoreSnapshot`. Existing `fit_rationale`
is retained for backwards compatibility and should point to or be superseded by the
latest explainable analysis.

### Migration sequence

1. Freeze and document the current schema and back up the SQLite file before an upgrade.
2. Add additive tables and nullable columns through versioned migrations. Never require
   users to delete or recreate their database.
3. Backfill one `ScoreSnapshot` from each existing posting's current score/rationale,
   tagged `initial` with legacy metadata where exact inputs are unavailable.
4. Normalize resume-derived skills lazily: existing resume text and summary remain valid;
   the first Hannar analysis can populate `ProfileSkill` and `JobSkill`.
5. Preserve `SeenPosting` for fetch deduplication, but separate that concern from scoring
   eligibility so a project pass or explicit criteria change can trigger a new score.
6. Make migration idempotent, report failures clearly, and keep a rollback/backup
   procedure documented for local installations.

No data migration should send existing resumes, job descriptions, or database contents to
Anthropic without the user's configured consent.

## 5. Phased implementation plan

### Phase 0 — branding and boundaries

- Establish Hannar naming, local application title, navigation language, and README/setup
  updates without changing behavior.
- Define provider/agent configuration, privacy promises, approval boundaries, and the
  deferred web-search/scraping decision.
- Inventory the separate `hanarr` project and record mappings/decisions.

**Exit:** Hannar can be launched as the same local app with clear product language and no
loss of existing job-search functionality.

### Phase 1 — migrations and foundational models

- Add migration tooling and the additive domain tables.
- Preserve current profile, job, status, reminder, connector, and scheduler behavior.
- Add score snapshots and a safe compatibility path for legacy postings.

**Exit:** An existing database upgrades without data loss, and old commands/dashboard
flows still work.

### Phase 2 — skills and gap integration

- Extract canonical profile skills and posting requirements using an explainable agent.
- Persist strengths, gaps, uncertainty, evidence, and analysis timestamps.
- Add visual indicators to job cards and a detail view; distinguish missing, weak,
  inferred, and satisfied skills.
- Provide manual refresh/re-analysis rather than silently changing user-visible scores.

**Exit:** A user can understand why a posting has a gap and can identify the next useful
skill action from the dashboard.

### Phase 3 — dashboard coaching and projects

- Add dashboard coaching panels and a manually invoked coach action.
- Implement posting-specific and reusable-skill project creation, with explicit opt-in,
  scope, status, and completion/pass controls.
- Let the coach recommend projects while preserving user approval and local artifacts.

**Exit:** A user can opt into a project from a posting or skill gap, track it locally, and
mark it passed without affecting unrelated jobs.

### Phase 4 — agent loop and orchestration

- Implement durable agent runs, specialized roles, bounded tools, retries, and
  observability.
- Allow per-agent Ollama/Anthropic/none configuration and record provider/model choices.
- Add approval gates for project activation, high-impact profile changes, and any external
  action.

**Exit:** The orchestrator can execute a complete coaching loop locally, resume safely
  after interruption, and show what each agent did and why.

### Phase 5 — rescoring and resume proposals

- On a passed project, identify related saved jobs by explicit skill overlap and create
  new score snapshots.
- Show before/after score, changed skills, rationale, and the jobs affected; do not
  mutate historical snapshots.
- Generate versioned resume proposals tied to evidence and affected skills.
- Require approval before activating a resume version or changing downstream scoring
  inputs.

**Exit:** Passing one project produces a transparent, bounded rescoring report and an
  optional reviewable resume proposal.

### Phase 6 — submissions

- Add a review-first submission abstraction for local files/folders, written responses,
  and GitHub.
- Store artifacts locally, show a checklist and diff, and require explicit user approval
  immediately before any submission.
- Integrate reminders/status changes only after a submission result is confirmed.

**Exit:** Each supported destination has a dry-run/review path, an auditable artifact, and
  a safe failure/retry story.

### Deferred decisions

- Anthropic web search, web scraping, and any new source whose access terms are unclear.
- Hosted sync, multi-user accounts, authentication, and remote persistence.
- Automatic project creation, automatic resume activation, and unattended application
  submission.
- Whether to add calendar integrations, new public connectors, or a richer front-end
  framework beyond what the dashboard needs.
- Final skill taxonomy, agent names/prompts, model defaults, and score-weight calibration;
  these should be informed by real local usage rather than fixed prematurely.

## 6. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Schema changes break an existing local database | Additive versioned migrations, backups, dry-run migration checks, and rollback instructions |
| LLM output is inconsistent or overconfident | Structured schemas, confidence/evidence fields, validation, provider metadata, and user-visible uncertainty |
| Rescoring changes a user's understanding without explanation | Immutable score snapshots, explicit triggers, before/after comparison, and manual re-analysis controls |
| Agents take unintended actions | Narrow tools, least privilege, durable run logs, approval gates, and no submission capability in early phases |
| Local Ollama is unavailable or slow | Timeouts, clear errors, rule-based fallback where safe, retry controls, and per-agent provider configuration |
| Anthropic use violates privacy expectations or incurs surprise cost | Explicit opt-in configuration, per-agent selection, cost/provider display, and local default |
| Skill extraction creates noisy project recommendations | Evidence-backed gaps, confidence thresholds, manual opt-in, and feedback from completed projects |
| Existing connector/API behavior drifts | Keep connector isolation, mock tests, failure logging, and public-API-only policy |
| Scope expands into a hosted platform | Treat single-user local-only as a release constraint and defer auth/sync/tenancy |

## 7. Acceptance criteria

The transition is ready for an initial Hannar release when all of the following are true:

- Existing users can upgrade a copy of their SQLite database without losing profiles,
  jobs, statuses, reminders, resume text, or connector configuration.
- The dashboard still supports searching, reviewing, status changes, reminders, and resume
  management, and now presents understandable skill-gap indicators.
- Hannar remains single-user and local-first by default; Ollama works without a hosted API
  key, and Anthropic is optional and configurable per agent.
- Public connectors and scheduled searches/reminders remain operational, with no scraping
  added as a shortcut.
- A user can manually opt into either project mode, see project scope and evidence, and
  mark a project passed.
- Passing a project produces bounded, related-job rescoring with immutable before/after
  snapshots and an understandable impact report.
- Resume changes are proposals with versions, diffs, provenance, and explicit approval;
  the active resume cannot be silently replaced.
- Agent runs expose role, provider/model, inputs, outputs/artifacts, status, and errors,
  and recover safely from interruption.
- Submission support, when enabled in a later phase, handles local files/folders, written
  responses, and GitHub through reviewable artifacts and explicit final approval.
- Automated tests cover migrations, legacy compatibility, gap indicators, project modes,
  pass-triggered rescoring, proposal approval/rejection, provider selection, and approval
  gates; a manual dashboard walkthrough verifies the local user journey.

This document is a transition plan rather than immediate implementation. Code changes
should follow the phases, preserve the foundation's verified behavior, and be proposed as
separate, reviewable increments.
