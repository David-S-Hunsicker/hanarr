"""Orchestrates one search cycle: fetch from all enabled connectors,
prefilter, score, and upsert into the DB. This is what `jobcopilot search`
and the scheduled background job both call.
"""
from __future__ import annotations

import json
import logging
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .connectors import build_enabled_connectors
from .llm.base import LLMClient
from .matching import passes_prefilter, score_fit
from .models import JobPosting, Profile, SeenPosting

logger = logging.getLogger(__name__)

# Fired with a small dict describing pipeline progress; see the individual
# call sites below for the event shapes. Optional — CLI and scheduler
# callers pass nothing and get no overhead beyond one None-check per job.
ProgressCallback = Callable[[dict], None]

# Polled between postings and between sources — not mid-LLM-call, since an
# in-flight score_fit() can't be interrupted without real complexity (killing
# an HTTP request to Ollama mid-response). Optional; defaults to "never stop"
# so CLI/scheduler callers are unaffected.
StopCheck = Callable[[], bool]


def run_search_cycle(
    session: Session,
    settings: Settings,
    profile: Profile,
    llm: LLMClient,
    on_progress: Optional[ProgressCallback] = None,
    should_stop: Optional[StopCheck] = None,
) -> int:
    """Returns the number of new postings that were scored and stored."""
    resume_summary = json.loads(profile.resume_summary_json or "{}")
    resume_text = profile.resume_text or ""
    connectors = build_enabled_connectors(settings.sources)
    new_count = 0
    stopped = False

    for connector in connectors:
        if should_stop and should_stop():
            stopped = True
            break

        if on_progress:
            on_progress({"event": "source_start", "source": connector.name})
        try:
            raw_jobs = connector.fetch()
        except Exception:  # noqa: BLE001
            logger.exception("Connector %s failed; skipping", connector.name)
            if on_progress:
                on_progress({"event": "source_error", "source": connector.name})
            continue

        if on_progress:
            on_progress({"event": "source_fetched", "source": connector.name, "count": len(raw_jobs)})

        for job in raw_jobs:
            if should_stop and should_stop():
                stopped = True
                break

            if not passes_prefilter(job, settings.preferences):
                continue

            already_seen = session.execute(
                select(SeenPosting.id).where(
                    SeenPosting.profile_id == profile.id,
                    SeenPosting.source == job.source,
                    SeenPosting.external_id == job.external_id,
                )
            ).scalars().first()
            if already_seen is not None:
                continue  # fetched and scored on a previous search, whether matched or rejected

            if on_progress:
                on_progress({"event": "scoring", "source": job.source, "title": job.title, "company": job.company})

            score, rationale = score_fit(job, resume_summary, resume_text, settings.preferences, llm)
            session.add(SeenPosting(profile_id=profile.id, source=job.source, external_id=job.external_id))

            if score < settings.matching.min_fit_score:
                session.commit()
                continue

            posting = JobPosting(
                profile_id=profile.id,
                source=job.source,
                external_id=job.external_id,
                company=job.company,
                title=job.title,
                location=job.location,
                remote=job.remote,
                url=job.url,
                description=job.description,
                salary_min=job.salary_min,
                salary_max=job.salary_max,
                posted_at=job.posted_at,
                fit_score=score,
                fit_rationale=rationale,
            )
            session.add(posting)
            session.commit()
            new_count += 1
            if on_progress:
                on_progress(
                    {
                        "event": "matched",
                        "source": posting.source,
                        "title": posting.title,
                        "company": posting.company,
                        "fit_score": posting.fit_score,
                    }
                )

        session.commit()
        if on_progress:
            on_progress({"event": "source_done", "source": connector.name})

        if stopped:
            break

    if on_progress:
        on_progress({"event": "stopped" if stopped else "complete", "new_count": new_count})

    return new_count
