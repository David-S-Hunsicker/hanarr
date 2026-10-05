"""Best-effort categorization of the shipped default company boards, used to
filter which companies get queried based on the candidate's own resume.

Why this exists: the ~100 companies shipped by default in config.example.yaml
are almost entirely VC-funded tech/startup companies. That's fine for a
software engineer, but an accounting or payroll specialist searching against
all of them sees hundreds of postings dominated by engineering roles, with
only a handful of real matches scattered in -- not because matching is
broken, but because those companies genuinely have very few accounting/
payroll openings relative to their (mostly-engineering) headcount.

This filters WHICH companies get queried, based on what kinds of roles each
one is actually known to hire for, matched against categories inferred from
the candidate's resume. It's role-function categories (SOFTWARE, FINANCE,
etc.), not business "industries" -- what matters here is what kinds of jobs
a company posts, not what market it's in. A "fintech" company might still be
almost entirely software roles.

Two safety properties, both deliberate:
- Only companies IN this registry (the shipped defaults) are ever filtered
  out. Anything a user adds themselves is never in here and is always
  queried -- silently dropping a board someone explicitly typed in would be
  a surprising, unwelcome kind of "smart."
- An ambiguous/uncategorizable resume (categories_for_profile returns an
  empty set) disables filtering entirely rather than matching nothing --
  "don't know" must never mean "show zero results."

This categorization is a best-effort starting point, not a precise
industry census -- it's easy to refine over time since it's just a data
table, not an algorithm.
"""
from __future__ import annotations

from .config import Preferences
from .matching import EXAMPLE_TARGET_TITLES

SOFTWARE = "software"
FINANCE = "finance"  # accounting, payroll, financial analysis, audit, treasury
HR_PEOPLE = "hr_people"  # recruiting, HR, people ops, benefits
SALES_MARKETING = "sales_marketing"
OPERATIONS = "operations"  # logistics, supply chain, customer support/success, retail/field ops
LEGAL_COMPLIANCE = "legal_compliance"
HEALTHCARE = "healthcare"
GENERAL = "general"  # cross-functional / broad enough that filtering shouldn't apply

# Company slug -> the role categories it's known to hire for in real volume.
# Slugs match config.example.yaml's company_boards/companies lists. Nearly
# everything here includes SOFTWARE since that's genuinely true of most
# VC-backed tech companies; the other categories mark where a company also
# has real, meaningful non-engineering hiring.
COMPANY_ROLE_CATEGORIES: dict[str, frozenset[str]] = {
    # Greenhouse
    "stripe": frozenset({SOFTWARE, FINANCE, SALES_MARKETING}),
    "airbnb": frozenset({SOFTWARE, OPERATIONS, SALES_MARKETING}),
    "robinhood": frozenset({SOFTWARE, FINANCE}),
    "coinbase": frozenset({SOFTWARE, FINANCE}),
    "discord": frozenset({SOFTWARE, SALES_MARKETING}),
    "reddit": frozenset({SOFTWARE, SALES_MARKETING}),
    "pinterest": frozenset({SOFTWARE, SALES_MARKETING}),
    "figma": frozenset({SOFTWARE, SALES_MARKETING}),
    "asana": frozenset({SOFTWARE, SALES_MARKETING}),
    "cloudflare": frozenset({SOFTWARE, SALES_MARKETING}),
    "databricks": frozenset({SOFTWARE, SALES_MARKETING}),
    "anthropic": frozenset({SOFTWARE}),
    "scaleai": frozenset({SOFTWARE, OPERATIONS}),
    "affirm": frozenset({SOFTWARE, FINANCE}),
    "instacart": frozenset({SOFTWARE, OPERATIONS, SALES_MARKETING}),
    "lyft": frozenset({SOFTWARE, OPERATIONS}),
    "gitlab": frozenset({SOFTWARE, SALES_MARKETING}),
    "elastic": frozenset({SOFTWARE, SALES_MARKETING}),
    "mongodb": frozenset({SOFTWARE, SALES_MARKETING}),
    "twilio": frozenset({SOFTWARE, SALES_MARKETING}),
    "datadog": frozenset({SOFTWARE, SALES_MARKETING}),
    "gusto": frozenset({SOFTWARE, FINANCE, HR_PEOPLE, SALES_MARKETING}),
    "brex": frozenset({SOFTWARE, FINANCE, SALES_MARKETING}),
    "chime": frozenset({SOFTWARE, FINANCE}),
    "samsara": frozenset({SOFTWARE, SALES_MARKETING, OPERATIONS}),
    "webflow": frozenset({SOFTWARE, SALES_MARKETING}),
    "vercel": frozenset({SOFTWARE}),
    "carta": frozenset({SOFTWARE, FINANCE, LEGAL_COMPLIANCE}),
    "squarespace": frozenset({SOFTWARE, SALES_MARKETING, OPERATIONS}),
    "amplitude": frozenset({SOFTWARE, SALES_MARKETING}),
    "mixpanel": frozenset({SOFTWARE, SALES_MARKETING}),
    "checkr": frozenset({SOFTWARE, HR_PEOPLE, OPERATIONS}),
    "roblox": frozenset({SOFTWARE, OPERATIONS, SALES_MARKETING}),
    "block": frozenset({SOFTWARE, FINANCE, OPERATIONS}),
    "airtable": frozenset({SOFTWARE, SALES_MARKETING}),
    "dropbox": frozenset({SOFTWARE, SALES_MARKETING}),
    "janestreet": frozenset({SOFTWARE, FINANCE}),
    "okta": frozenset({SOFTWARE, SALES_MARKETING}),
    "peloton": frozenset({SOFTWARE, OPERATIONS, SALES_MARKETING}),
    "sofi": frozenset({SOFTWARE, FINANCE}),
    "toast": frozenset({SOFTWARE, FINANCE, SALES_MARKETING, OPERATIONS}),
    "twitch": frozenset({SOFTWARE, OPERATIONS, SALES_MARKETING}),
    "coursera": frozenset({SOFTWARE, SALES_MARKETING, OPERATIONS}),
    "doximity": frozenset({SOFTWARE, HEALTHCARE, SALES_MARKETING}),
    "faire": frozenset({SOFTWARE, OPERATIONS, SALES_MARKETING}),
    "flexport": frozenset({SOFTWARE, OPERATIONS}),
    "intercom": frozenset({SOFTWARE, SALES_MARKETING}),
    "lattice": frozenset({SOFTWARE, HR_PEOPLE, SALES_MARKETING}),
    "udemy": frozenset({SOFTWARE, SALES_MARKETING, OPERATIONS}),
    "calendly": frozenset({SOFTWARE, SALES_MARKETING}),
    "cribl": frozenset({SOFTWARE}),
    "duolingo": frozenset({SOFTWARE, SALES_MARKETING}),
    "pagerduty": frozenset({SOFTWARE, SALES_MARKETING}),
    "salesloft": frozenset({SOFTWARE, SALES_MARKETING}),
    "zoominfo": frozenset({SOFTWARE, SALES_MARKETING}),
    "cockroachlabs": frozenset({SOFTWARE}),
    "mercury": frozenset({SOFTWARE, FINANCE}),
    "glossier": frozenset({SOFTWARE, OPERATIONS, SALES_MARKETING, GENERAL}),
    "cameo": frozenset({SOFTWARE, OPERATIONS, SALES_MARKETING}),
    "nextdoor": frozenset({SOFTWARE, SALES_MARKETING, OPERATIONS}),
    "attentive": frozenset({SOFTWARE, SALES_MARKETING}),
    "braze": frozenset({SOFTWARE, SALES_MARKETING}),
    "klaviyo": frozenset({SOFTWARE, SALES_MARKETING}),
    "pendo": frozenset({SOFTWARE, SALES_MARKETING}),
    "remote": frozenset({SOFTWARE, FINANCE, HR_PEOPLE, SALES_MARKETING}),
    "justworks": frozenset({SOFTWARE, FINANCE, HR_PEOPLE, SALES_MARKETING, OPERATIONS}),
    "oura": frozenset({SOFTWARE, OPERATIONS, SALES_MARKETING}),
    # Lever
    "palantir": frozenset({SOFTWARE}),
    "ro": frozenset({SOFTWARE, HEALTHCARE, OPERATIONS}),
    "clari": frozenset({SOFTWARE, SALES_MARKETING}),
    "voleon": frozenset({SOFTWARE, FINANCE}),
    "alloy": frozenset({SOFTWARE, FINANCE}),
    "imbue": frozenset({SOFTWARE}),
    "tovala": frozenset({SOFTWARE, OPERATIONS, SALES_MARKETING, GENERAL}),
    "aircall": frozenset({SOFTWARE, SALES_MARKETING}),
    "aledade": frozenset({SOFTWARE, HEALTHCARE, OPERATIONS}),
    "spotify": frozenset({SOFTWARE, SALES_MARKETING, OPERATIONS}),
    "immutable": frozenset({SOFTWARE}),
    "moonpay": frozenset({SOFTWARE, FINANCE}),
    "anchorage": frozenset({SOFTWARE, FINANCE}),
    # Ashby
    "ramp": frozenset({SOFTWARE, FINANCE, SALES_MARKETING}),
    "notion": frozenset({SOFTWARE, SALES_MARKETING}),
    "linear": frozenset({SOFTWARE}),
    "replit": frozenset({SOFTWARE}),
    "openai": frozenset({SOFTWARE}),
    "mercor": frozenset({SOFTWARE, HR_PEOPLE}),
    "cursor": frozenset({SOFTWARE}),
    "perplexity": frozenset({SOFTWARE}),
    "pika": frozenset({SOFTWARE}),
    "runway": frozenset({SOFTWARE}),
    "elevenlabs": frozenset({SOFTWARE}),
    "character": frozenset({SOFTWARE}),
    "suno": frozenset({SOFTWARE}),
    "drata": frozenset({SOFTWARE, LEGAL_COMPLIANCE}),
    "secureframe": frozenset({SOFTWARE, LEGAL_COMPLIANCE}),
    "watershed": frozenset({SOFTWARE, FINANCE}),
    "sardine": frozenset({SOFTWARE, FINANCE}),
    "unit": frozenset({SOFTWARE, FINANCE}),
    "column": frozenset({SOFTWARE, FINANCE}),
    "dave": frozenset({SOFTWARE, FINANCE}),
    "zip": frozenset({SOFTWARE, FINANCE, OPERATIONS}),

    # Recruitee -- genuinely different mix than the lists above: mostly
    # non-tech employers (energy, construction, retail, automotive), the
    # point of adding this source rather than more of the same startups.
    "vandebron": frozenset({OPERATIONS, SALES_MARKETING}),
    "duravermeer": frozenset({OPERATIONS}),
    "myjewellery": frozenset({OPERATIONS, SALES_MARKETING}),
    "dckgroup": frozenset({OPERATIONS, SALES_MARKETING}),
    "bunq": frozenset({SOFTWARE, FINANCE}),
    "vanmossel": frozenset({OPERATIONS, SALES_MARKETING}),
    "channable": frozenset({SOFTWARE, SALES_MARKETING}),
    "keolis": frozenset({OPERATIONS}),
    "pretamanger": frozenset({OPERATIONS, SALES_MARKETING}),
    "boulangerieange": frozenset({OPERATIONS, SALES_MARKETING}),
    "ballastnedam": frozenset({OPERATIONS}),
    "vionfoodgroup": frozenset({OPERATIONS}),
    "sirclecollection": frozenset({OPERATIONS, SALES_MARKETING}),
    "bettercollective": frozenset({SOFTWARE, SALES_MARKETING}),
    "dpd": frozenset({OPERATIONS}),
    "cmcom": frozenset({SOFTWARE, SALES_MARKETING}),
    "livestorm": frozenset({SOFTWARE, SALES_MARKETING}),
    "greenpeacecee": frozenset({GENERAL}),
}

# Keyword -> category, checked against the candidate's resume-derived titles/
# skills/industries plus their own target_titles preference. Deliberately
# simple substring matching (consistent with the rest of matching.py's
# rule-based prefilter) rather than an LLM call -- this needs to run before
# deciding which connectors to even query, so it has to be cheap and work
# even with provider="none".
_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    SOFTWARE: (
        "software", "engineer", "developer", "programmer", "devops", "sre",
        "data scientist", "machine learning", "backend", "frontend", "full stack",
        "qa engineer", "sysadmin", "architect", "data engineer",
    ),
    FINANCE: (
        "accountant", "accounting", "payroll", "bookkeep", "finance",
        "financial analyst", "controller", "auditor", "tax ", "cpa",
        "treasury", "billing", "accounts payable", "accounts receivable",
    ),
    HR_PEOPLE: (
        "recruiter", "recruiting", "human resources", " hr ", "people ops",
        "talent acquisition", "benefits specialist", "hr generalist", "hr business partner",
    ),
    SALES_MARKETING: (
        "sales", "account executive", "business development", "marketing",
        "growth", "demand generation", " seo ", "content marketing",
    ),
    OPERATIONS: (
        "logistics", "supply chain", "operations", "customer support",
        "customer success", "warehouse", "retail associate", "store manager", "dispatch",
    ),
    LEGAL_COMPLIANCE: (
        "legal", "attorney", "paralegal", "compliance", "contracts manager",
    ),
    HEALTHCARE: (
        "nurse", "clinical", "healthcare", "medical", "physician", "pharmacist", "patient care",
    ),
}


def categories_for_profile(resume_summary: dict, preferences: Preferences) -> frozenset[str]:
    """Deterministic keyword match against resume-extracted titles/skills/
    industries plus the candidate's own target_titles preference. An empty
    result means "not enough signal to categorize" -- callers must treat
    that as "don't filter," never as "matches nothing."""
    parts: list[str] = []
    parts.extend(resume_summary.get("titles") or [])
    parts.extend(resume_summary.get("industries") or [])
    parts.extend(resume_summary.get("skills") or [])
    # Still the unedited example template's placeholder ("Software
    # Engineer" / "Backend Engineer") isn't a deliberate signal -- someone
    # who hasn't customized target_titles yet (or whose resume simply
    # hasn't been auto-populated into it) shouldn't have every board's
    # SOFTWARE category force-matched by a value they never actually chose.
    if preferences.target_titles != EXAMPLE_TARGET_TITLES:
        parts.extend(preferences.target_titles)
    text = f" {' '.join(parts).lower()} "
    if not text.strip():
        return frozenset()

    return frozenset(
        category for category, keywords in _CATEGORY_KEYWORDS.items()
        if any(kw in text for kw in keywords)
    )


def board_matches_profile(company_slug: str, profile_categories: frozenset[str]) -> bool:
    """True if this company should be queried for this candidate.

    Unknown boards (anything outside the shipped default registry -- i.e.
    anything the user added themselves) always match: this filter only ever
    prunes among the shipped defaults, never something the user explicitly
    configured. An uncategorized profile (empty profile_categories) also
    always matches, since filtering needs real signal to act on."""
    if not profile_categories:
        return True
    board_categories = COMPANY_ROLE_CATEGORIES.get(company_slug)
    if board_categories is None:
        return True
    if GENERAL in board_categories:
        return True
    return bool(board_categories & profile_categories)


def filter_boards(company_slugs: list[str], profile_categories: frozenset[str]) -> list[str]:
    return [slug for slug in company_slugs if board_matches_profile(slug, profile_categories)]
