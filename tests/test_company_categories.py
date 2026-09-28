from hanarr.company_categories import (
    FINANCE,
    GENERAL,
    SOFTWARE,
    board_matches_profile,
    categories_for_profile,
    filter_boards,
)
from hanarr.config import Preferences


def test_categories_for_profile_detects_finance_from_resume_titles():
    summary = {"titles": ["Payroll Specialist"], "industries": [], "skills": []}
    categories = categories_for_profile(summary, Preferences())

    assert FINANCE in categories
    assert SOFTWARE not in categories


def test_categories_for_profile_detects_software_from_skills():
    summary = {"titles": [], "industries": [], "skills": ["Python", "backend development"]}
    categories = categories_for_profile(summary, Preferences())

    assert SOFTWARE in categories


def test_categories_for_profile_also_considers_target_titles_preference():
    summary = {"titles": [], "industries": [], "skills": []}
    prefs = Preferences(target_titles=["Staff Accountant"])

    assert FINANCE in categories_for_profile(summary, prefs)


def test_categories_for_profile_returns_empty_set_when_no_signal_at_all():
    """No signal must mean "don't filter", never "matches nothing" -- an
    empty resume/preferences shouldn't make every search return zero
    results."""
    assert categories_for_profile({}, Preferences()) == frozenset()


def test_categories_for_profile_ignores_the_unedited_example_target_titles():
    """Regression test: Preferences() defaults target_titles to the example
    template's placeholder ("Software Engineer", "Backend Engineer"). Before
    this was excluded, a payroll/accounting resume combined with an
    unedited-default profile produced {finance, software} instead of just
    {finance} -- and since nearly every company in the registry carries
    SOFTWARE, that diluted the filter down to barely removing anything."""
    summary = {"titles": ["Payroll Specialist"], "industries": [], "skills": []}
    categories = categories_for_profile(summary, Preferences())  # target_titles still the example default

    assert categories == frozenset({FINANCE})


def test_board_matches_profile_true_for_unknown_board_regardless_of_categories():
    """A company the user typed in themselves (not in the shipped
    registry) must never be silently filtered out."""
    assert board_matches_profile("some-companys-own-slug", frozenset({FINANCE})) is True


def test_board_matches_profile_true_when_profile_has_no_categories():
    assert board_matches_profile("stripe", frozenset()) is True


def test_board_matches_profile_requires_category_overlap_for_known_boards():
    # "vercel" is categorized as software-only in the registry.
    assert board_matches_profile("vercel", frozenset({FINANCE})) is False
    assert board_matches_profile("vercel", frozenset({SOFTWARE})) is True


def test_board_matches_profile_general_category_always_matches():
    # "glossier" carries GENERAL alongside its other categories.
    assert board_matches_profile("glossier", frozenset({FINANCE})) is True


def test_filter_boards_keeps_only_matching_and_unknown_boards():
    boards = ["vercel", "stripe", "my-own-custom-board"]
    filtered = filter_boards(boards, frozenset({FINANCE}))

    assert "vercel" not in filtered  # software-only, no finance overlap
    assert "stripe" in filtered  # tagged with finance
    assert "my-own-custom-board" in filtered  # unknown -- never dropped
