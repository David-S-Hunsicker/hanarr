import datetime as dt

from jobcopilot.config import Preferences
from jobcopilot.connectors.base import RawJobPosting
from jobcopilot.matching import _rule_based_score, passes_prefilter


def make_job(**overrides) -> RawJobPosting:
    defaults = dict(
        source="test",
        external_id="1",
        company="Acme",
        title="Backend Engineer",
        location="Remote",
        remote=True,
        url="https://example.com/job/1",
        description="We use python and distributed systems every day.",
        salary_min=None,
        salary_max=None,
        posted_at=dt.datetime.utcnow(),
    )
    defaults.update(overrides)
    return RawJobPosting(**defaults)


def test_prefilter_rejects_excluded_keyword():
    job = make_job(description="This is an unpaid internship.")
    prefs = Preferences(keywords_exclude=["unpaid"])
    assert passes_prefilter(job, prefs) is False


def test_prefilter_rejects_remote_when_not_wanted():
    job = make_job(remote=True)
    prefs = Preferences(remote_ok=False, onsite_ok=True, locations=["Seattle, WA"])
    assert passes_prefilter(job, prefs) is False


def test_prefilter_rejects_below_salary_floor():
    job = make_job(salary_max=90000)
    prefs = Preferences(salary_floor_usd=120000, remote_ok=True)
    assert passes_prefilter(job, prefs) is False


def test_prefilter_accepts_reasonable_match():
    job = make_job()
    prefs = Preferences(remote_ok=True)
    assert passes_prefilter(job, prefs) is True


def test_rule_based_score_rewards_title_and_skill_overlap():
    job = make_job(title="Backend Engineer", description="python distributed systems kubernetes")
    prefs = Preferences(target_titles=["Backend Engineer"], keywords_boost=["kubernetes"])
    resume_summary = {"skills": ["python", "distributed systems"]}

    score, rationale = _rule_based_score(job, resume_summary, prefs)

    assert score > 40  # baseline was boosted
    assert "skill keyword" in rationale


def test_rule_based_score_baseline_when_no_overlap():
    job = make_job(title="Marketing Manager", description="social media and branding")
    prefs = Preferences(target_titles=["Backend Engineer"])
    resume_summary = {"skills": ["python"]}

    score, _ = _rule_based_score(job, resume_summary, prefs)

    assert score == 40
