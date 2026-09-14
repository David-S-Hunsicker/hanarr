"""Orchestrates one search cycle: fetch from all enabled connectors,
prefilter, score, and upsert into the DB. This is what `jobcopilot search`
and the scheduled background job both call.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .connectors import build_enabled_connectors
from .llm.base import LLMClient
from .matching import passes_prefilter, score_fit
from .models import JobPosting, Profile

logger = logging.getLogger(__name__)


def run_search_cycle(session: Session, settings: Settings, profile: Profile, llm: LLMClient) -> int:
    """Returns the number of new postings that were scored and stored."""
    resume_summary = json.loads(profile.resume_summary_json or "{}")
    connectors = build_enabled_connectors(settings.sources)
    new_count = 0

    for connector in connectors:
        try:
            raw_jobs = connector.fetch()
        except Exception:  # noqa: BLE001
            logger.exception("Connector %s failed; skipping", connector.name)
            continue

        for job in raw_jobs:
            if not passes_prefilter(job, settings.preferences):
                continue

            existing = session.execute(
                select(JobPosting).where(
                    JobPosting.source == job.source,
                    JobPosting.external_id == job.external_id,
                )
            ).scalars().first()
            if existing is not None:
                continue  # already seen this posting

            score, rationale = score_fit(job, resume_summary, settings.preferences, llm)
            if score < settings.matching.min_fit_score:
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
            new_count += 1

        session.commit()

    return new_count
