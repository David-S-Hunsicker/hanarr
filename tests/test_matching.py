import datetime as dt
import json

from hanarr.config import Preferences
from hanarr.connectors.base import RawJobPosting
from hanarr.llm.base import LLMClient
from hanarr.matching import _rule_based_score, passes_prefilter, score_fit


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


def test_prefilter_rejects_intern_title_for_senior_candidate():
    job = make_job(title="Software Engineering Intern")
    prefs = Preferences(seniority="senior", remote_ok=True)
    assert passes_prefilter(job, prefs) is False


def test_prefilter_rejects_exec_title_for_senior_candidate():
    job = make_job(title="VP of Engineering")
    prefs = Preferences(seniority="senior", remote_ok=True)
    assert passes_prefilter(job, prefs) is False


def test_prefilter_tolerates_one_rung_seniority_gap():
    job = make_job(title="Staff Backend Engineer")
    prefs = Preferences(
        seniority="senior",
        remote_ok=True,
        target_titles=["Backend Engineer"],
    )
    assert passes_prefilter(job, prefs) is True


def test_prefilter_ignores_seniority_when_title_has_no_level_signal():
    job = make_job(title="Backend Engineer, Core Technology")
    prefs = Preferences(
        seniority="senior",
        remote_ok=True,
        target_titles=["Backend Engineer"],
    )
    assert passes_prefilter(job, prefs) is True


def test_preferences_migrates_old_string_seniority_to_list():
    prefs = Preferences(seniority="senior")
    assert prefs.seniority == ["senior"]


def test_prefilter_multi_select_seniority_widens_tolerance():
    # 2 rungs from "senior" alone, but only 1 rung from "staff" -- should
    # pass when the candidate selected both levels.
    job = make_job(title="Principal Backend Engineer")
    prefs = Preferences(
        seniority=["senior", "staff"],
        remote_ok=True,
        target_titles=["Backend Engineer"],
    )
    assert passes_prefilter(job, prefs) is True


def test_prefilter_multi_select_seniority_still_rejects_outside_all_selected():
    job = make_job(title="Software Engineering Intern")
    prefs = Preferences(seniority=["senior", "staff"], remote_ok=True)
    assert passes_prefilter(job, prefs) is False


def test_prefilter_rejects_zero_title_and_keyword_overlap():
    job = make_job(title="Marketing Manager", description="Own our social media campaigns.")
    prefs = Preferences(
        remote_ok=True,
        target_titles=["Backend Engineer"],
        keywords_boost=["python"],
    )
    assert passes_prefilter(job, prefs) is False


def test_prefilter_accepts_boost_keyword_overlap_even_without_title_overlap():
    job = make_job(title="Platform Reliability Role", description="Deep Python and distributed systems work.")
    prefs = Preferences(
        remote_ok=True,
        target_titles=["Backend Engineer"],
        keywords_boost=["python"],
    )
    assert passes_prefilter(job, prefs) is True


def test_prefilter_rejects_remote_posting_restricted_to_another_country():
    job = make_job(location="Remote - Canada")
    prefs = Preferences(remote_ok=True, work_country="United States")
    assert passes_prefilter(job, prefs) is False


def test_prefilter_accepts_remote_posting_explicitly_us():
    job = make_job(location="Remote - US")
    prefs = Preferences(remote_ok=True, work_country="United States")
    assert passes_prefilter(job, prefs) is True


def test_prefilter_accepts_remote_posting_with_no_country_mentioned():
    job = make_job(location="Remote")
    prefs = Preferences(remote_ok=True, work_country="United States")
    assert passes_prefilter(job, prefs) is True


def test_prefilter_rejects_on_clear_country_restriction_phrasing_in_description():
    job = make_job(location="Remote", description="Germany-based candidates only for this position.")
    prefs = Preferences(remote_ok=True, work_country="United States")
    assert passes_prefilter(job, prefs) is False


def test_prefilter_ignores_passing_country_mention_in_description():
    job = make_job(location="Remote", description="We have an engineering office in Germany.")
    prefs = Preferences(remote_ok=True, work_country="United States")
    assert passes_prefilter(job, prefs) is True


def test_prefilter_country_check_disabled_when_work_country_blank():
    job = make_job(location="Remote - Canada")
    prefs = Preferences(remote_ok=True, work_country="")
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


def test_rule_based_score_disqualifies_on_dealbreaker():
    job = make_job(description="Great role, no visa sponsorship available for this position.")
    prefs = Preferences(
        target_titles=["Backend Engineer"],
        dealbreakers=["no visa sponsorship"],
    )
    resume_summary = {"skills": ["python", "distributed systems"]}

    score, rationale = _rule_based_score(job, resume_summary, prefs)

    assert score == 0
    assert "dealbreaker" in rationale.lower()


class _FakeLLM(LLMClient):
    def __init__(self, response: dict):
        self.response = response

    def complete_json(self, system: str, user: str) -> str:
        return json.dumps(self.response)


def test_score_fit_disqualifies_on_llm_dealbreaker_hit():
    job = make_job()
    prefs = Preferences(dealbreakers=["no visa sponsorship available"])
    llm = _FakeLLM({"score": 90, "dealbreaker_hit": True, "rationale": "No visa sponsorship offered."})

    score, rationale = score_fit(job, {}, "resume text", prefs, llm)

    assert score == 0
    assert "visa" in rationale.lower()


def test_score_fit_keeps_llm_score_when_no_dealbreaker_hit():
    job = make_job()
    prefs = Preferences(dealbreakers=["no visa sponsorship available"])
    llm = _FakeLLM({"score": 85, "dealbreaker_hit": False, "rationale": "Strong match."})

    score, rationale = score_fit(job, {}, "resume text", prefs, llm)

    assert score == 85
    assert rationale == "Strong match."


def test_score_fit_surfaces_unmet_requirements_when_llm_omits_rationale():
    job = make_job()
    prefs = Preferences()
    llm = _FakeLLM(
        {
            "score": 35,
            "dealbreaker_hit": False,
            "unmet_requirements": ["5+ years of Kubernetes in production"],
            "rationale": "",
        }
    )

    score, rationale = score_fit(job, {}, "resume text", prefs, llm)

    assert score == 35
    assert "Kubernetes" in rationale


def test_score_fit_disqualifies_on_fails_minimum_requirements():
    job = make_job()
    prefs = Preferences()
    llm = _FakeLLM(
        {
            "score": 60,
            "dealbreaker_hit": False,
            "fails_minimum_requirements": True,
            "unmet_requirements": ["5+ years of cybersecurity investigation experience"],
            "rationale": "",
        }
    )

    score, rationale = score_fit(job, {}, "resume text", prefs, llm)

    assert score == 0
    assert "cybersecurity" in rationale.lower()


def test_score_fit_keeps_score_when_minimum_requirements_met():
    job = make_job()
    prefs = Preferences()
    llm = _FakeLLM(
        {
            "score": 78,
            "dealbreaker_hit": False,
            "fails_minimum_requirements": False,
            "unmet_requirements": [],
            "rationale": "Strong match.",
        }
    )

    score, rationale = score_fit(job, {}, "resume text", prefs, llm)

    assert score == 78
    assert rationale == "Strong match."


def test_score_fit_sends_resume_text_to_llm():
    job = make_job()
    prefs = Preferences()

    captured = {}

    class _CapturingLLM(LLMClient):
        def complete_json(self, system: str, user: str) -> str:
            captured["user"] = user
            return json.dumps({"score": 70, "dealbreaker_hit": False, "rationale": "ok"})

    score_fit(job, {}, "10 years of Python and distributed systems experience", prefs, _CapturingLLM())

    assert "10 years of Python" in captured["user"]
