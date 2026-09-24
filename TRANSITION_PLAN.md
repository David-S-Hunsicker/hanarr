# Hannar transition plan

**Status:** Phase 2 schema foundation complete. Alembic migrations, a frozen compatibility
baseline, score snapshots, normalized skills, coaching records, proven skills, and resume
proposal storage are in place without changing existing job-search behavior.

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
- Use expanded first-class navigation with these tabs: **Jobs**, **Coaching**, **Resume**,
  **Skills**, **Applications**, and **Settings**. Each page has a clear responsibility:
  jobs are for discovery and decisions, coaching for active guidance and projects, resume
  for source content and proposals, skills for capability/evidence, applications for
  submission and status history, and settings for configuration.
- Keep pages uncluttered. Do not make one dashboard page carry every workflow; use concise
  summaries and deliberate links into the responsible page.
- Provide a direct **Improve my fit** action from a job posting. It starts the appropriate
  coaching/project flow for that posting while preserving the existing apply and status
  actions during coaching.
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
- Project completion is a loop, not an endpoint: completing a project generates a resume
  proposal; user approval makes that proposal the active resume; the approved resume is
  automatically re-parsed and fed back into job matching without requiring a re-upload.
- Keep capability, evidence, and resume wording distinct. A capability is what the user
  can do, evidence is why Hannar believes it, and resume wording is the proposed way to
  communicate it. Improving one must not silently fabricate or conflate the others.
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

1. **Presentation layer:** the dashboard becomes Hannar's primary UI with clear tab
   navigation: Jobs, Coaching, Resume, Skills, Applications, and Settings. The Jobs page
   owns discovery, fit, skill gaps, **Improve my fit**, apply, status, and reminders;
   Coaching owns active guidance and project work; Resume owns readable active content,
   extracted profile, proposals, versions, and rollback; Skills owns capabilities and
   evidence; Applications owns submission artifacts and application history; Settings owns
   configuration. Job cards expose fit, skill gaps, project actions, score history, and
   coaching context without hiding apply/status actions or making the page a workflow
   dumping ground.
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
   coaching/agent runs, resume versions/proposals, and submission artifacts. Skill records
   must distinguish capability, supporting evidence, and resume wording rather than
   storing them as one undifferentiated claim.
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
  diff with `pending`, `approved`, or `rejected` state. An approved proposal becomes the
  active version, triggers re-parsing from stored content, and feeds the refreshed
  extracted profile into matching without requiring another upload.
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
7. Preserve the active resume and prior versions as readable content. The Resume page
   must expose active contents, extracted profile, pending/decided proposals, version
   history, and an explicit rollback action; rollback selects a prior version and runs the
   same parse-and-rematch path rather than editing files behind the user's back.

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

### Implementation progress

#### Phase 0 — branding and boundaries: complete

**Completed deliverables**

- Adopted Hannar as the dashboard and product name while retaining the `jobcopilot` Python
  package, CLI command, routes, and local database path for compatibility.
- Documented the incremental transition, local-only operating model, Ollama-first inference,
  public-API connector policy, and deferred coaching/submission capabilities in the README.
- Added a dashboard regression test for the product name and FastAPI title.

**Validation**

- Targeted dashboard tests pass: `pytest tests/test_dashboard_app.py`.
- Existing behavior remains covered by the repository test suite; no schema, connector,
  scheduler, or command behavior changed in this increment.

**Next handoff**

Proceed to Phase 1 only after selecting and documenting the migration mechanism. The next
bounded increment should freeze the current schema, add additive/versioned migration support,
and introduce only the compatibility-safe foundational tables (including score snapshots).
Do not begin skills, projects, orchestration, or submission flows in that increment.

### Phase 1 — migrations and foundational models

- Add migration tooling and the additive domain tables.
- Preserve current profile, job, status, reminder, connector, and scheduler behavior.
- Add score snapshots and a safe compatibility path for legacy postings.

**Exit:** An existing database upgrades without data loss, and old commands/dashboard
flows still work.

#### Phase 1 — migrations and foundational models: complete

**Migration tooling and schema decisions**

- Selected Alembic with SQLAlchemy and SQLite; `0001` freezes the pre-migration tables and
  `0002` adds the additive `score_snapshots` table.
- Existing databases are detected and stamped at the frozen baseline rather than recreated.
  Fresh databases run the same baseline migration normally.
- Before an upgrade, the local database is copied to `data/backups/jobcopilot-<UTC>.db`.
  Migration errors are raised explicitly; the original database is not deleted or replaced.

**Completed deliverables**

- Added versioned migration configuration and documentation.
- Added immutable score snapshots with legacy backfill metadata and initial snapshots for
  newly matched postings.
- Added focused migration tests covering fresh databases, legacy row preservation, backfill,
  and backup creation.

**Validation**

- `pytest tests/test_migrations.py tests/test_pipeline.py`
- Existing dashboard and connector behavior remains covered by the repository test suite.

**Next handoff**

Proceed to Phase 2 only after confirming this migration path in a representative local
database. The Phase 2 schema increment below is complete; the next handoff is behavior and
dashboard integration, not agent orchestration.

### Phase 2 — skills and gap integration

#### Phase 2 — skills and gap integration: schema foundation complete

**Completed deliverables**

- Added migration `0003` with normalized `skills`, profile-skill evidence, and job-skill
  requirement/gap records.
- Added manually opted-in coaching projects with posting-specific or reusable-skill mode,
  project skills, submissions, evaluations, and proven-skill evidence.
- Added immutable resume versions and pending/approved/rejected proposal storage, including
  provenance links to a project and base version.
- Kept `0001` explicitly limited to the frozen compatibility tables so future model additions
  cannot accidentally alter the legacy baseline.

**Validation**

- Model tests persist skill evidence, gap classifications, project submissions/evaluations,
  proven skills, and pending resume proposals.
- Migration tests verify fresh and representative legacy databases upgrade through `0003`.

**Next handoff**

Build the explainable skill extraction/gap analysis service and dashboard indicators. Keep
project creation manually opted-in; defer orchestration and automatic resume activation.

- Extract canonical profile skills and posting requirements using an explainable agent.
- Persist strengths, gaps, uncertainty, evidence, and analysis timestamps.
- Add visual indicators to job cards and a detail view; distinguish missing, weak,
  inferred, and satisfied skills.
- Provide manual refresh/re-analysis rather than silently changing user-visible scores.

**Exit:** A user can understand why a posting has a gap and can identify the next useful
skill action from the dashboard.

#### Phase 2 — requirements and gap vertical slice: complete

**Completed deliverables**

- Added `skill_analysis` application service that materializes resume-summary skills,
  incorporates proven skills, extracts posting requirements through the existing LLM
  abstraction, and falls back to a deterministic common-skill vocabulary when inference
  is unavailable.
- Persisted normalized `JobSkill` records with required/preferred classification, evidence,
  confidence, analysis timestamp, and `satisfied`, `partial`, or `missing` status.
- Added dashboard contracts `POST /api/jobs/{job_id}/skill-gaps/analyze` for explicit
  re-analysis and `GET /api/skill-gaps` for analyzed gaps grouped by saved posting.
  Analysis does not alter fit scores, application status, or existing search behavior.

**Validation**

- `python -m pytest -q tests/test_skill_analysis.py tests/test_career_schema.py tests/test_migrations.py tests/test_dashboard_app.py`
  (29 passed).
- The focused API test confirms fit score preservation and that only analyzed saved jobs
  appear in the gap listing.

**Next handoff**

The first compact job-card/detail presentation is now complete. Proceed to manually opted-in
project actions. Keep re-analysis explicit; do not couple gap analysis to automatic scoring
or create coaching projects implicitly.

#### Phase 2 — Jobs dashboard gap awareness: complete

**Completed deliverables**

- Added a compact analyzed-gap indicator and inline fit-analysis detail surface to each
  eligible Jobs card.
- Added a direct **Improve my fit** action that explicitly runs the existing gap analysis
  API, without changing fit scores, apply links, search, or application status controls.
- Added `GET /api/jobs/{job_id}/skill-gaps` for a single-job detail surface, including an
  explicit unanalyzed response.

**Validation**

- Focused skill-analysis and dashboard regression tests cover analyzed/unanalyzed API states,
  rendered gap indicators/details, and preserved status actions.
- Targeted and full test commands are recorded in the handoff commit.

**Next handoff**

Add only manually opted-in posting-specific or reusable-skill project creation from a gap
detail, then define the smallest coaching panel. Do not add automatic project creation,
resume activation, or rescoring in this milestone.

#### Phase 3 — first coaching-project flow: complete

**Completed deliverables**

- Added additive migration `0004` for generated project briefs, ordered project tasks, and
  project-to-affected-job links.
- Added `POST /api/coaching-projects` with explicit `posting_specific` and `reusable_skill`
  scope validation from analyzed missing/partial gaps. The existing LLM abstraction generates
  a structured brief, with a deterministic three-task fallback when no model is configured or
  the response is invalid.
- Added `GET /api/coaching-projects` and `GET /api/coaching-projects/{id}` status/detail
  surfaces, plus a small Jobs dashboard coaching panel and opt-in project action.
- Preserved search, fit scoring, application status, submission, evaluation, resume, and
  automatic-project behavior; this increment only creates planned local artifacts.

**Validation**

- Focused coaching-project tests cover fallback persistence, task structure, posting-specific
  scope, reusable-skill affected-job coverage, and project status APIs.
- Run `pytest tests/test_coaching_projects.py tests/test_skill_analysis.py tests/test_dashboard_app.py`
  and the full test suite before handoff.

**Next handoff**

Add a dedicated Coaching surface and explicit task/status updates. Keep submission evaluation,
resume writing/activation, rescoring, and automatic project recommendations deferred.

#### Phase 3 — dedicated Coaching dashboard: complete

**Completed deliverables**

- Added a dedicated `/coaching` page with focused sections for analyzed missing/partial gaps,
  opted-in projects, project status/tasks, and affected saved jobs.
- Added explicit task status updates through
  `POST /api/coaching-projects/{project_id}/tasks/{task_id}/status`; starting a planned task
  moves its project to active without changing jobs, applications, or resume content.
- Added the first-class Jobs, Coaching, Resume, Skills, Applications, and Settings navigation
  structure. Resume, Skills, and Applications are intentionally reserved placeholders until
  their respective milestones.

**Validation**

- Added a focused dashboard regression covering suggestions, project/task/job rendering, and
  task activation.
- Run the coaching, skill-analysis, dashboard, and full test suites before handoff.

**Next handoff**

Build the first-class Resume surface and proposal/version workflow. Keep submission evaluation,
resume activation, rescoring, and automatic project recommendations deferred.

### Phase 3 — dashboard coaching and projects

- Add dashboard coaching panels and a manually invoked coach action.
- Establish the Jobs, Coaching, Resume, Skills, Applications, and Settings tabs with
  uncluttered page responsibilities. Add **Improve my fit** directly to job cards/detail
  views and keep apply/status controls available while coaching is active.
- Build the first-class Resume page: readable active resume contents, extracted profile,
  pending and decided proposals, version history, and rollback.
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

- On project completion/pass, identify related saved jobs by explicit skill overlap and
  generate a versioned resume proposal tied to the completed project's evidence and
  affected skills.
- Require user approval before activation. Approval makes the proposal the active resume,
  automatically re-parses the stored approved content, and feeds the refreshed extracted
  profile into job matching without a re-upload.
- Re-score related saved jobs after that approved resume refresh. Show each before/after
  score, changed capabilities and evidence, changed resume wording where relevant, the
  rationale, and why the score changed; do not mutate historical snapshots.
- Keep the full loop visible on the Resume and Coaching pages, with links back to affected
  Jobs and preserved apply/status actions.

**Exit:** Passing one project produces a reviewable resume proposal. After approval, the
stored resume is re-parsed without re-upload, related jobs are re-scored, and the user can
read the before/after explanations and trace the impact back to project evidence.

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
- Navigation provides Jobs, Coaching, Resume, Skills, Applications, and Settings, with
  uncluttered responsibilities; Jobs still supports searching, reviewing, apply/status
  actions, reminders, and direct **Improve my fit** coaching.
- The Resume page is first-class and readable: it shows active contents, extracted
  profile, proposals, version history, and rollback.
- Hannar remains single-user and local-first by default; Ollama works without a hosted API
  key, and Anthropic is optional and configurable per agent.
- Public connectors and scheduled searches/reminders remain operational, with no scraping
  added as a shortcut.
- A user can manually opt into either project mode, see project scope and evidence, and
  mark a project passed.
- Passing a project generates a resume proposal; approval makes it active, automatically
  re-parses the stored resume, and feeds it into matching without re-upload.
- Related saved jobs are re-scored with immutable before/after snapshots that explain why
  each score changed, including capability, evidence, and resume-wording distinctions.
- Resume changes are proposals with versions, diffs, provenance, explicit approval, and
  rollback; the active resume cannot be silently replaced.
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
