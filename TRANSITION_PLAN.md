# Hanarr transition plan

**Status:** Phase 10 Windows-first installer and distribution planning is complete; the first
desktop-launch foundation and Windows packaging foundation are implemented, while signing and
clean-machine release validation remain deferred. Phase 6
submission, evaluator, and local-first provider-routing
foundations are complete. Alembic migrations, a
frozen compatibility baseline, score snapshots, normalized skills, coaching records, proven
skills, resume proposal storage, review-first submissions, and structured evaluation
attempts are in place without changing existing job-search behavior.

## 1. Purpose and decisions

The goal is to evolve `job-search-copilot` into **Hanarr**, a unified, local-first
job-search and career-coaching application. `job-search-copilot` is the implementation
foundation; Hanarr is not a parallel rewrite or a hosted multi-user product at this stage.

The following decisions are treated as requirements for the transition:

- Keep the existing dashboard as the first integration surface. Coaching should appear
  where the user already reviews jobs, statuses, reminders, and resume information.
- Preserve the local SQLite data store and the existing local-only operating model. Hanarr
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
- Projects are manually opted into. Hanarr may recommend a project, but it must not
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
  can do, evidence is why Hanarr believes it, and resume wording is the proposed way to
  communicate it. Improving one must not silently fabricate or conflate the others.
- Future submission types include local files/folders, written responses, and GitHub.
  Submission is a later capability and must remain opt-in and reviewable.
- Defer Anthropic web search and scraping. Hanarr should use the existing public
  connectors and user-provided/local material until a future decision establishes a
  compliant, useful web-research boundary.

## 2. Current-state comparison

### `job-search-copilot` (foundation)

The current repository already provides the safest path to a useful Hanarr v1:

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

The separate Hanarr work represents the broader product direction: coaching, a
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

Hanarr should remain a modular local application with the existing dashboard and pipeline
at its center:

1. **Presentation layer:** the dashboard becomes Hanarr's primary UI with clear tab
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
   the first Hanarr analysis can populate `ProfileSkill` and `JobSkill`.
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

- Establish Hanarr naming, local application title, navigation language, and README/setup
  updates without changing behavior.
- Define provider/agent configuration, privacy promises, approval boundaries, and the
  deferred web-search/scraping decision.
- Inventory the separate `hanarr` project and record mappings/decisions.

**Exit:** Hanarr can be launched as the same local app with clear product language and no
loss of existing job-search functionality.

### Implementation progress

#### Phase 0 — branding and boundaries: complete

**Completed deliverables**

- Adopted Hanarr as the dashboard and product name while retaining the `jobcopilot` Python
  package, CLI command, routes, and local database path for compatibility.
- Documented the incremental transition, local-only operating model, Ollama-first inference,
  public-API connector policy, and deferred coaching/submission capabilities in the README.
- Added a dashboard regression test for the product name and FastAPI title.
- Corrected the product spelling to Hanarr across user-facing docs, dashboard titles/content,
  migration documentation, and the related regression test; retained the lowercase `hanarr`
  project identifier because it is a technical reference.

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

  #### Progress — local and written submission contract

  Commit `3ab7187` established coaching projects. The next bounded milestone adds normalized,
  profile-scoped written-response and local-file submission records, additive migration `0005`,
  local artifact storage under `data/submissions/`, validation for response size, file count,
  file size, and traversal, draft history, and an explicit submit transition. The project API
  and Coaching page expose both submission kinds and their history. GitHub delivery, evaluator
  agents, and resume writing remain intentionally out of scope.

  Validation covers project linking, normalized manifests, draft-to-submitted history, local
  artifact persistence, traversal rejection, migrations, and the existing coaching flow.
  The next handoff is to add review/diff presentation and a GitHub-ready adapter without
  granting unattended external submission.

  #### Progress — project-completion resume loop

  The project-completion milestone is complete. A passing evaluation now marks the project
  completed, records proven-skill evidence, and creates one pending, versioned resume proposal
  through the configured LLM abstraction. Invalid or unavailable model output uses a
  deterministic evidence-only proposal and never replaces the active resume.

  The Resume page and API expose readable active contents, proposal diffs and decisions,
  version history, and explicit rollback proposals. Approval is required before activation;
  approval re-parses the stored approved content, refreshes normalized profile skills, and
  re-scores affected project jobs (or all saved jobs for an explicit rollback). Each result
  is stored as an immutable `ScoreSnapshot` with before/after metadata and an explanation.
  Existing apply/status behavior and historical snapshots remain unchanged.

  **Validation:** `python -m pytest -q tests/test_resume_loop.py
  tests/test_coaching_projects.py tests/test_career_schema.py tests/test_migrations.py` and
  `python -m compileall -q src`. The next handoff is review/diff presentation polish and a
  GitHub-ready adapter; unattended external delivery and automatic proposal approval remain
  deferred.

  #### Progress — first-class Resume page UI

  The Resume milestone is complete. The dedicated page now presents the active resume as
  readable source content, the parsed titles/skills/industries/seniority/summary, pending
  and decided proposals with explicit approve/reject actions, version history, and rollback
  proposal actions. It clearly identifies the active version as the input to job matching,
  explains that approval re-parses and rematches, and presents persisted before/after score
  impacts for rematched jobs without changing Jobs, Coaching, or application actions.

  **Validation:** `python -m pytest -q tests/test_resume_loop.py tests/test_dashboard_app.py`
  and the full `python -m pytest -q` suite passed. The next handoff is a GitHub-ready
  adapter and review/diff refinements; unattended external delivery and automatic proposal
  approval remain deferred.

  #### Progress — first-class Skills page and evidence UX

  The Skills milestone is complete. A dedicated Skills page now shows each normalized
  capability's current level, confidence, source, and evidence alongside separate project
  evidence, proven-skill status, resume wording, and affected analyzed jobs. The page links
  project evidence to Coaching and affected jobs to their saved postings, while preserving
  the distinction that resume wording or capability evidence is not proof. A correction form
  and `PATCH /api/skills/{skill_id}/profile` endpoint allow a user to replace capability
  proficiency, confidence, and evidence with bounded values; corrections are stored as
  manual `ProfileSkill` data and never create a `ProvenSkill` record.

  **Validation:** `python -m pytest -q tests/test_skill_analysis.py tests/test_dashboard_app.py`,
  `python -m compileall -q src`, `git diff --check`, and the full `python -m pytest -q`
  suite passed. The next handoff is a GitHub-ready submission adapter and review/diff
  refinements; automatic proven-skill claims, proposal approval, and external delivery
  remain explicitly gated.

  #### Progress — deterministic evaluator stage

  Commit `7efb28c` established the explicit submission boundary. This milestone adds
  additive migration `0006` and an evaluator service using the existing configurable LLM
  abstraction (the configured provider remains local-first by default). Evaluations include
  attempt history, outcome, overall and rubric scores, strengths, improvements, actionable
  feedback, provider/fallback identity, and captured model errors. Malformed or unavailable
  model responses use a deterministic safe fallback and never mark a skill proven.

  `POST /api/coaching-projects/{project_id}/submissions/{submission_id}/evaluate` exposes
  the evaluator and `.../resubmit` reopens an evaluated submission for another explicit
  attempt. Project, submission-list, and submission-detail responses include all evaluation
  results. Focused tests cover a passing structured response, retry attempt numbering, and
  malformed-model fallback/error persistence.

  **Validation:** `python -m pytest -q tests/test_coaching_projects.py
  tests/test_career_schema.py tests/test_migrations.py` and `python -m compileall -q src`.
  The next handoff is review/diff presentation and a GitHub-ready adapter; external delivery,
  automatic project completion, and automatic proven-skill claims remain deferred.

  #### Progress — local-first provider routing foundation

  The provider-routing milestone is complete. `Settings` now supports typed, additive
  per-agent overrides for profiler, market analysis, curriculum, evaluator, and resume
  writer roles, plus named task overrides. Routes inherit from the global Ollama-first
  configuration in a deterministic order: `llm`, `agents.default`, role, then task.
  Anthropic remains opt-in and all API keys are resolved from the environment and omitted
  when settings are saved.

  `AgentOrchestrator` is a deliberately small boundary that resolves a role/task into an
  existing LLM client without rewriting completed services or granting tools. The dashboard
  now injects role-specific clients into existing resume, matching, coaching, evaluator,
  and proposal flows. Provider failures and malformed model output continue to use each
  service's explicit deterministic fallback; no unattended action was added.

  **Validation:** `python -m pytest -q tests/test_agent_orchestration.py
  tests/test_coaching_projects.py tests/test_resume_loop.py tests/test_dashboard_app.py`
  and the full `python -m pytest -q` suite passed, with `python -m compileall -q src` and
  `git diff --check` clean. The next handoff is review/diff presentation and a
  GitHub-ready adapter; sequencing, durable agent runs, automatic proposal approval, and
  external delivery remain deferred.

  #### Progress — GitHub repository submission adapter

  The GitHub submission milestone is complete. The normalized submission contract now
  accepts a profile-scoped GitHub repository URL plus an optional branch, tag, or commit
  reference. The adapter canonicalizes and validates HTTPS `github.com` repository
  references, rejects credentials, query/fragment injection, traversal, unsafe refs, and
  oversized/control-character input, and persists only metadata. It never clones,
  downloads, imports, or executes repository content; existing written and local-file
  submissions remain unchanged.

  `POST /api/coaching-projects/{project_id}/submissions/github` and the Coaching page
  expose the adapter as a draft-first flow. Evaluator input remains compatible because
  the canonical reference is represented in the existing submission content contract.

  **Validation:** `python -m pytest -q tests/test_coaching_projects.py` and
  `python -m compileall -q src` passed. The focused security/regression coverage verifies
  canonical persistence, rejection of non-GitHub/query-bearing references, and absence of
  local artifact storage. The next handoff is review/diff presentation and an explicit
  external-delivery design; cloning, checkout, code execution, and unattended submission
  remain deferred.

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

The transition is ready for an initial Hanarr release when all of the following are true:

- Existing users can upgrade a copy of their SQLite database without losing profiles,
  jobs, statuses, reminders, resume text, or connector configuration.
- Navigation provides Jobs, Coaching, Resume, Skills, Applications, and Settings, with
  uncluttered responsibilities; Jobs still supports searching, reviewing, apply/status
  actions, reminders, and direct **Improve my fit** coaching.
- The Resume page is first-class and readable: it shows active contents, extracted
  profile, proposals, version history, and rollback.
- Hanarr remains single-user and local-first by default; Ollama works without a hosted API
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

### Phase 7 — market-demand learning loop

#### Progress — reusable demand summaries and skill priorities

This bounded learning-loop milestone is complete. Derived summaries aggregate analyzed
saved-job requirements by canonical skill, including required versus preferred demand,
missing versus partial gaps, affected-job count, a deterministic priority score,
estimated effort, and manually corrected capability evidence when present. The summaries
reuse existing `JobSkill` and `ProfileSkill` records and do not alter fit, search, or
application behavior.

`GET /api/coaching` exposes ranked reusable coaching suggestions and the full market-demand
summary. `GET /api/skills` and the Skills page expose the same demand data alongside
capability, evidence, resume wording, projects, and job evidence. The Coaching page now
shows one ranked suggestion per skill and a compact market-demand summary rather than
repeating one suggestion for every affected posting. Skills inferred only from analyzed
jobs remain visible with an explicit unrecorded capability state.

Validation:

- `python -m pytest -q tests/test_skill_analysis.py tests/test_coaching_projects.py`
  (15 passed)
- `python -m pytest -q` (111 passed)

**Next handoff**

Add review/diff presentation and a GitHub-ready adapter for the existing review-first
submission model, while preserving explicit approval and local artifact boundaries.

#### Progress — connected coaching and job-impact UX

This bounded integration milestone closes the highest-value links in the local user
journey. Job cards now have stable dashboard anchors, affected-job links from Coaching
and Skills return to the relevant card while preserving the external posting link, and
the Jobs page surfaces the latest approved-resume before/after score impact with a link
to the detailed explanation on Resume. The existing status selector, apply/posting link,
and reminders remain unchanged.

The Coaching page now presents evaluator feedback and actionable follow-up for each
submission, with explicit evaluate and revise/resubmit actions. A regression also fixes
the project-action event binding so an analyzed gap can always create its opted-in
project; previously those listeners were nested under the unanalyzed-job action loop.
No project, resume, or application action is automatic.

**Validation:** `python -m pytest -q tests/test_skill_analysis.py
tests/test_coaching_projects.py tests/test_resume_loop.py tests/test_dashboard_app.py`,
`python -m pytest -q`, `python -m compileall -q src`, and `git diff --check`.

**Next handoff**

Add review/diff presentation refinements and a GitHub-ready adapter for the existing
review-first submission model. Keep GitHub delivery explicit and approval-gated; do not
add unattended submission or automatic proposal approval.

### Phase 8 — production hardening review

#### Findings and fixes

The integrated transition was reviewed across migration startup, provider fallback,
submission storage, resume activation/rematching, authorization boundaries, and the
server-rendered dashboard:

- Resume proposals could be approved after a different proposal had already activated a
  newer resume. Approval now requires the proposal's base version to still be the active
  version, preventing stale content from replacing the current resume and triggering an
  inconsistent rematch.
- Local submissions accepted duplicate artifact paths and had no aggregate byte limit.
  Validation now rejects duplicate names, rejects `.`/`..` path components, enforces a
  50 MiB aggregate limit in addition to the per-file limit, and verifies resolved
  destinations remain inside the configured submission directory.
- Migration backups used a timestamp that could collide when upgrades were started in the
  same second. Backups now include microseconds and a deterministic suffix collision guard.
- Invalid application status input raised an unhandled enum error. The dashboard now
  returns a user-visible HTTP 400 response.
- Provider failures for resume proposals remain local/deterministic and now leave a
  durable rationale indicating that the model output was unavailable; no provider is
  allowed to silently activate content.
- HTTP resume and local-artifact uploads now stream in bounded chunks, enforce 10 MiB resume,
  10 MiB per-file, 50 MiB aggregate, and 100-file limits, and return clear 413 errors.
  Artifacts are staged and atomically moved into their final directory; failed writes clean up
  both staging files and their database row.

The single-user authorization assumption remains intentional and documented: the app is
local-only by default, routes scope records to the single profile, and submission
evaluation/GitHub adapters do not fetch, execute, or deliver external content. Exposing
the dashboard beyond localhost still requires an explicit future authentication boundary.

#### Validation

- `python -m pytest -q tests/test_migrations.py tests/test_coaching_projects.py tests/test_resume_loop.py tests/test_dashboard_app.py` — targeted suite passed
- `python -m compileall -q src`
- `git diff --check`
- `python -m pytest -q` — full suite passed
- `python -m compileall -q src` and `git diff --check` — clean

#### Remaining blockers

Authentication/CSRF protection for non-local deployment and actual GitHub fetch/diff review
remain intentionally deferred. The current GitHub adapter stores a validated reference only and
performs no network or execution action.

#### Explicit release-readiness checklist

- [x] Additive migrations back up the local SQLite database and fail startup explicitly.
- [x] Resume and local-artifact HTTP uploads are streamed, bounded, staged, and safely cleaned
  up on rejection.
- [x] User-facing upload and migration errors identify the corrective action without exposing
  provider or filesystem internals unnecessarily.
- [x] Local-only default, no GitHub fetch/execute behavior, and approval gates remain intact.
- [x] Targeted and full tests, byte compilation, and diff checks pass.
- [ ] Add authentication/CSRF before any non-local deployment.
- [ ] Design and implement an explicit, approval-gated GitHub fetch/diff workflow.

### Phase 9 — final local-first release validation

#### Validation status

The final bounded local-first pass was completed on 2026-09-24 from the integrated transition
branch. The setup and migration documentation was checked against the implementation: the
repository-root startup requirement, Alembic upgrade path, pre-upgrade backup location and
restore guidance, additive-migration rule, localhost dashboard default, and bounded staged
upload behavior are documented and wired through the existing entry points.

The release checks passed:

- `python -m pytest -q` — 117 tests passed.
- `python -m compileall -q src` — clean.
- `git diff --check` — clean.

The README now records these automated results and separates them from manual/live checks that
were not claimed: browser dashboard walkthrough, CLI end-to-end use, real Ollama or Anthropic
model calls, live RemoteOK/Arbeitnow responses, and desktop notifications. The remaining manual
items in the README checklist should be completed against a real local installation before a
personal production rollout.

#### Known intentional non-local blockers

Do not widen the release scope to address these items in the local-first transition:

1. Authentication and CSRF protection are not implemented. The dashboard must remain bound to
   `127.0.0.1`; non-local deployment is unsupported until an explicit security boundary exists.
2. GitHub fetch/diff review is not implemented. The adapter only validates and stores a
   GitHub reference, with no network fetch, code execution, or delivery.

#### Future handoff

Future work may add the two deferred capabilities only as separate, reviewed increments:
first define and test the authentication/CSRF boundary for any non-local deployment, then design
an explicit, approval-gated GitHub fetch/diff workflow. Preserve the current local-only default,
review-first submission model, additive migrations, upload limits, and explicit provider consent.
No further local-first release-blocking issues were found in this validation pass. The next
handoff is the Windows installer implementation only after the plan below is approved for
execution; this document change itself adds no installer code.

### Phase 10 — Windows-first installer and distribution plan

This phase records the approved distribution plan and the bounded launch and packaging work
completed against it. Update services, signing, and Ollama setup remain future work.

#### Product and launch shape

- Keep one Hanarr backend and one application surface. A packaged desktop shell should host
  the existing web UI in a desktop webview; the same backend must also support a deliberate
  browser-launch mode for users who prefer a normal browser.
- Treat Windows as the first supported packaged platform. macOS and Linux packaging are
  explicitly deferred until the Windows flow, recovery behavior, and data-preservation
  guarantees have been proven.
- Use a conventional, signed Inno Setup-style installer first. The installer must provide
  Start Menu and optional Desktop shortcuts, a normal uninstaller, and standard Windows
  Add/Remove Programs registration. Do not require Python, a terminal, or a separately
  installed developer toolchain.
- Ship a packaged Hanarr runtime with the application. Installation and launch must work
  for a normal user account according to the supported install-destination policy, while
  preserving the user's existing local data outside the application binaries.

#### First-run setup and local-first provider behavior

- On first launch, show a setup wizard before the main dashboard. It should explain the local
  data location, local-first privacy behavior, optional Anthropic use, and the choices that
  affect downloads or provider configuration.
- Detect an existing Ollama installation and usable local service before attempting any
  Ollama installation. If it is already available, reuse it and show the detected status;
  never overwrite or silently replace an existing installation.
- Any proposed Ollama or model download requires explicit consent. The consent screen must
  state the download size (or a clearly labeled estimate), applicable license/source, the
  destination, and what will remain on the machine. Cancellation must leave the app usable
  in a documented setup-incomplete state.
- Recommend models using detected hardware and available resources (including memory and
  storage where available), explain the recommendation, and let the user choose a different
  compatible model. Model download during setup is optional rather than mandatory.
- If Ollama installation, service startup, model download, or model readiness fails, provide
  a clear recovery path: preserve existing data, show the actionable failure, allow retry,
  allow the user to select or configure an existing provider, and allow the wizard to finish
  without destructive cleanup. Do not leave a success-shaped partial setup.
- Keep local inference as the default. Anthropic remains optional and must be explicitly
  configured/consented to; setup must not transmit local resume, job, or profile data to
  Anthropic without the existing provider and user-approval rules.
- Add provider diagnostics to Settings: detected Ollama/service state, configured model,
  readiness/error details, Anthropic configuration state, privacy/consent status, and safe
  retry or reconfiguration actions. Diagnostics must not expose secrets.

#### Updates, data, and migrations

- Provide approved update checks rather than silent replacement. The user must be able to
  see what is being checked/downloaded and approve installation; checks and update metadata
  must not upload local application data.
- Updates must preserve the user's data location, configuration, model choices, and consent
  records. Run the existing additive migration/backup safeguards before schema changes, fail
  explicitly with restore guidance, and never treat a failed migration as a successful update.
- The uninstaller must remove packaged application files and registered shortcuts while
  clearly preserving user data by default. Any optional data removal must be a separate,
  explicit user choice with a warning.

#### Phased installer milestones

1. **M1 — packaging contract:** document supported Windows versions, install/data locations,
   signing identities, runtime inventory, browser versus webview launch contract, and the
   no-Python/no-terminal requirement.
2. **M2 — packaged runtime spike:** produce a repeatable signed-build artifact that launches
   the one backend in desktop webview mode and browser mode without a developer environment;
   verify logs, shutdown, and port/lifecycle cleanup.
3. **M3 — installer shell:** add the conventional installer, Start Menu/Desktop shortcuts,
   Add/Remove Programs registration, uninstaller, upgrade behavior, and code-signing
   verification. Keep user data outside the replaceable application directory.
4. **M4 — first-run wizard:** implement detection-before-install for Ollama, explicit
   download/license/destination consent, hardware-aware recommendations, optional model
   download, and recoverable incomplete setup.
5. **M5 — settings and update path:** expose provider diagnostics, privacy/provider consent
   state, approved update checks, backup/migration handling, and data-preserving upgrades.
6. **M6 — release validation:** test clean machines, existing Ollama installations, no-network
   setup, insufficient hardware/storage, cancelled downloads, failed service/model setup,
   upgrade, rollback/recovery, uninstall, browser launch, and webview launch. Publish only
   signed artifacts with reproducible release notes.

#### Acceptance criteria

- [ ] A signed Windows installer installs and launches Hanarr for a normal user without
  Python, a terminal, or a separately prepared runtime.
- [ ] One backend supports both desktop webview and intentional browser launch modes.
- [ ] Start Menu/Desktop shortcuts, Add/Remove Programs registration, upgrade, and uninstaller
  behavior are clear and verified; user data is preserved by default.
- [ ] First run detects existing Ollama before proposing installation and requires explicit,
  informed consent for every Ollama/model download, including size, license/source, and
  destination.
- [ ] Hardware-aware model recommendations and optional model download work, while setup
  remains usable when the user declines or setup fails.
- [ ] Recovery paths are actionable and non-destructive for Ollama, service, model, network,
  storage, and migration failures.
- [ ] Settings exposes safe provider diagnostics; local-first behavior is default and
  Anthropic is optional and consent-gated.
- [ ] Approved update checks preserve data, configuration, consent records, and migration
  safeguards; no silent data deletion or silent replacement occurs.
- [ ] Clean-machine, upgrade, recovery, uninstall, webview, and browser-launch validation is
  recorded before Windows release. macOS/Linux remain explicitly out of scope for this phase.

#### Progress and next handoff

**Progress:** The Windows-first installer/distribution contract, setup behavior, recovery
requirements, update/data guarantees, phased milestones, and acceptance criteria remain the
planning baseline. The M2 launch contract is now implemented in a bounded form: one `jobcopilot
serve` backend supports explicit `none`, `browser`, and optional `webview` launch modes; the
mode is configurable in `config.yaml` or selectable with `--launch-mode`; and the webview
dependency remains optional so existing CLI/browser behavior is preserved. Focused launch tests
cover URL construction, supported-mode validation, and config loading. Validation for this
handoff: `python -m pytest -q tests/test_launch.py tests/test_dashboard_app.py`, the full test
suite, `python -m compileall -q src`, and `git diff --check`.

The M2/M3 packaging foundation is now implemented: `jobcopilot.packaged` provides browser and
desktop entry points that run from `%LOCALAPPDATA%\Hanarr`, preserving configuration, resumes,
SQLite data, and migrations outside replaceable application binaries. `scripts/build_windows.ps1`
builds both PyInstaller runtimes and invokes `installer/hanarr.iss`, which defines a per-user Inno
Setup installer, Start Menu shortcuts, an optional Desktop shortcut, Add/Remove Programs
registration, and an uninstall policy that preserves user data. The script now performs a
deterministic preflight for Python, PyInstaller, Inno Setup, and all packaging inputs; `-ValidateOnly`
provides a tool/input check without building, and a full build verifies the expected non-empty
installer artifact. Static packaging tests cover shortcut, launch, data-preservation, and
preflight/output wiring. The documented build produces an unsigned local artifact only; no
signing, release, update service, or clean-machine verification is claimed.

The first-run local-AI foundation and the bounded M4 consent slice are implemented without
crossing the installation boundary: typed diagnostics inspect the executable on PATH, local
`/api/tags` service, installed models, configured-model readiness, and best-effort RAM/free-storage
facts. Settings now detects first and presents source, license, size, and destination before an
explicit confirmation. A confirmed Ollama download is streamed to a bounded staging file and is
never executed; a confirmed model action calls the existing local Ollama pull API. Declined,
cancelled, interrupted, over-limit, unavailable, and failed actions clean up partial staging and
leave provider configuration unchanged. Anthropic remains optional and deterministic `none`
fallback behavior is unchanged. Focused tests cover recommendations, parsing, unavailable
services, dashboard no-op/decline behavior, and setup failure/interruption paths.

No signed/released installer, update service, or first-run wizard has been added. The packaged
runtime includes the application and Python dependencies but does not install or bundle Ollama;
the existing explicit-consent setup flow remains responsible for optional provider actions.

The next bounded release-automation milestone is now implemented:
`.github/workflows/windows-installer.yml` runs the full tests, installs the Windows packaging
tools, runs the deterministic preflight, and builds the installer on `windows-latest`.
`packaging/release-metadata.json` is the checked-in version and release-gate contract. A
successful build emits the unsigned installer, SHA-256 sidecar, and output metadata; artifact
upload is conditional on the real build succeeding and missing tools fail the workflow. The
workflow does not publish a GitHub release and does not claim signing or notarization.

The M5 update-check slice is now implemented without crossing into installation: Settings →
Updates is opt-in and supports a configured JSON release endpoint or latest GitHub release.
Validated metadata includes semver, release-notes URL, asset URL, and SHA-256 checksums; the
dashboard shows current/latest versions and a release-notes link, and offline/malformed sources
fail explicitly. A separate update-install API requires explicit approval but returns
`not_implemented` after approval because download, signature verification, replacement, and
migration execution are deliberately deferred. No local data, configuration, migrations, or
Ollama behavior changes during a check.

### Phase 11 — final local-first installer readiness

**Status:** The local-first installer foundation is documented and statically/test validated.
The verified baseline is 151 passing tests, clean source byte-compilation, and clean diff
whitespace checks. Packaging CI now runs those checks before its Windows preflight and unsigned
artifact build. Tests cover the stable per-user install target, shortcut/runtime wiring,
preservation of an existing configuration and SQLite file across the packaged-runtime upgrade
path, and the uninstall policy that intentionally leaves `%LOCALAPPDATA%\Hanarr` untouched.

The release checklist explicitly distinguishes what is verified in source/CI from what still
requires a real Windows machine: Inno Setup/PyInstaller availability, clean-machine install,
upgrade, launch, uninstall, and recovery walkthroughs. The current application still does not
claim a signed or released artifact. Update checks remain opt-in and read-only; approval-gated
download/install execution, silent updates, Authenticode signing, and release publication remain
deferred. Ollama remains detection-first and explicit-consent gated, and the installer never
installs or starts it.

**Future handoff:** On a Windows validation machine, run the documented preflight and unsigned
build, record tool versions and hashes, then perform clean-machine and upgrade/data-preservation
walkthroughs. Only after those results are recorded should a separately approved milestone add
certificate-backed Authenticode signing and publication. Keep update installation, silent
updates, unattended setup, and macOS/Linux packaging as separate future work; do not infer them
from this local-first foundation.

### Phase 11 — first real-machine validation and two packaged-runtime fixes

**Status:** The above future handoff was executed on a real Windows 11 development machine
(not a clean VM). Tool versions: Python 3.14.7, PyInstaller 6.22.3, Inno Setup 6.7.3
(`JRSoftware.InnoSetup` via winget). `-ValidateOnly` preflight passed, and the full build
produced `Hanarr-Setup-0.1.0.exe` (SHA-256 `b454d98c46a9411e6ae657758c760698fd44036202ba576c6cb836a33c67cf25`
for the fixed build).

This was the first time the packaged executables were actually launched rather than only
built. Doing so surfaced two startup crashes that no existing test caught, because every
prior test ran from a source checkout (`__file__` pointing into `src/`, `sys.stdout`/`stderr`
present) and never simulated the frozen-bundle, no-console conditions a `--windowed`
PyInstaller build actually runs under:

1. **Migration path resolution broke when frozen.** `db.py` located `alembic.ini` via
   `Path(__file__).resolve().parents[2]`, which resolves correctly in a source checkout but
   points outside the bundle entirely once PyInstaller extracts to `sys._MEIPASS`. The app
   crashed immediately with `alembic.util.exc.CommandError: No 'script_location' key found in
   configuration.` A second latent issue sat behind it: `alembic.ini`'s `script_location =
   migrations` is a bare relative path, and the packaged runtime changes its working directory
   to the user-data folder (`%LOCALAPPDATA%\Hanarr`) before startup, so even a correctly
   located `alembic.ini` would have pointed Alembic at a nonexistent `migrations` folder under
   user data. Fixed by resolving the bundle root via `sys._MEIPASS` when frozen (mirroring the
   pattern `packaged.py` already used for its own template lookup) and by setting
   `script_location` on the `Config` object as an absolute path rather than relying on the ini
   file's relative value.
2. **Uvicorn's default logging formatter crashed with no console.** A PyInstaller
   `--windowed` build has `sys.stdout`/`sys.stderr` set to `None` (no attached console).
   Uvicorn's default log formatter calls `sys.stdout.isatty()` while configuring itself,
   raising `AttributeError` (`'NoneType' object has no attribute 'isatty'`) which surfaced as
   `ValueError: Unable to configure formatter 'default'` out of `uvicorn.Config.__init__`.
   Fixed in `packaged.py` by giving both streams a real (discarding) file object before the
   CLI is invoked, when they are `None`.

Both fixes have regression tests (`tests/test_migrations.py::test_migration_resolves_bundle_and_ignores_working_directory_when_frozen`,
`tests/test_windows_packaging.py::test_ensure_standard_streams_handles_none`) that reproduce
the frozen/no-console conditions with `monkeypatch` rather than requiring an actual build.
`.gitignore` now excludes PyInstaller's generated `*.spec` files and `installer/output/`.

**Validation performed on this machine (not a clean VM):**

- `python -m pytest -q` — 154 passed (151 prior + 3 new regression tests).
- `python -m compileall -q src` and `git diff --check` — clean.
- Silent install (`/VERYSILENT`) creates both Start Menu shortcuts and the Add/Remove Programs
  entry with the expected display name/publisher/install location.
- Launching the installed `HanarrBrowser.exe` now serves the dashboard: `GET http://127.0.0.1:8420/`
  returns `200` with `<title>Hanarr</title>`, and `%LOCALAPPDATA%\Hanarr\config.yaml` and
  `data\jobcopilot.db` are created on first run, with an initial migration backup recorded.
- Installing the same version again over an existing install (upgrade-in-place, no prior
  uninstall) leaves the application binaries in place and leaves `config.yaml` and
  `jobcopilot.db` byte-for-byte unchanged (verified with a written marker and a file hash).
- Silent uninstall removes the application directory, both Start Menu shortcuts, and the
  Add/Remove Programs entry, while `%LOCALAPPDATA%\Hanarr` (config, resumes, database) is left
  in place untouched, matching the documented uninstall policy.
- The desktop webview shortcut (`HanarrDesktop.exe`) was launched from a fresh install and
  confirmed to actually render, not just start a process: the resulting window reports title
  `Hanarr`, `Responding: True`, and a real window handle. Both `HanarrBrowser.exe` and
  `HanarrDesktop.exe` are now verified end to end on this machine.

**Not yet performed (still requires a genuinely clean machine/VM):** install with no prior
Python/build tooling present at all, no-network setup, an existing separate Ollama
installation, cancelled/failed provider setup, and a forced migration failure. Certificate-backed
Authenticode signing and release publication remain deferred and out of scope for this pass.
