from hanarr.resume_sections import split_resume_into_sections


def test_splits_on_all_caps_section_headers():
    content = (
        "Jane Doe\n"
        "555-1234 | jane@example.com\n"
        "AI engineer with 8 years of experience.\n"
        "SKILLS\n"
        "Python | PyTorch | Kubernetes\n"
        "EXPERIENCE\n"
        "Senior Engineer, Acme Corp\n"
        "Built things.\n"
        "EDUCATION\n"
        "B.S. Computer Science"
    )

    sections = split_resume_into_sections(content)

    headings = [s.heading for s in sections]
    assert headings == [None, "SKILLS", "EXPERIENCE", "EDUCATION"]
    assert "Jane Doe" in sections[0].body
    assert "Python | PyTorch | Kubernetes" in sections[1].body
    assert "Built things." in sections[2].body
    assert "B.S. Computer Science" in sections[3].body


def test_splits_on_markdown_headings():
    content = "# Jane Doe\n\nSummary text.\n\n## Skills\n\nPython, Kubernetes\n\n## Experience\n\nDid things."

    sections = split_resume_into_sections(content)

    assert [s.heading for s in sections] == ["Jane Doe", "Skills", "Experience"]


def test_recognizes_known_title_case_headings_without_all_caps():
    content = "Jane Doe\nProfessional Experience\nDid things.\nEducation\nA degree."

    sections = split_resume_into_sections(content)

    assert [s.heading for s in sections] == [None, "Professional Experience", "Education"]
    assert sections[0].body == "Jane Doe"


def test_does_not_split_on_content_lines_that_happen_to_be_short():
    """Lines with pipes (category lists like "Languages: Python | Go"),
    lines ending in punctuation, and bulleted lines are resume body
    content, not section headers, even when short."""
    content = (
        "Jane Doe\n"
        "SKILLS\n"
        "Languages: Python | Go\n"
        "- Built a thing.\n"
        "Led a team of 5.\n"
    )

    sections = split_resume_into_sections(content)

    assert [s.heading for s in sections] == [None, "SKILLS"]
    assert "Languages: Python | Go" in sections[1].body
    assert "- Built a thing." in sections[1].body
    assert "Led a team of 5." in sections[1].body


def test_all_caps_name_is_treated_as_the_leading_section_heading():
    """An ALL CAPS name line (a common resume convention) is indistinguishable
    from an ALL CAPS section header by this heuristic, and that's an
    accepted tradeoff, not a bug: it just means the name reads as a bolded
    label above the contact/summary block, which is a reasonable look."""
    content = "JANE DOE\n555-1234 | jane@example.com\nSKILLS\nPython, Kubernetes"

    sections = split_resume_into_sections(content)

    assert [s.heading for s in sections] == ["JANE DOE", "SKILLS"]
    assert "jane@example.com" in sections[0].body


def test_falls_back_to_a_single_unheaded_section_when_nothing_looks_like_a_header():
    """The safe degrade: if no section can be confidently identified, the
    whole document stays as one block, reproducing the pre-existing
    unsplit display rather than mangling unusual formatting."""
    content = "This is just a paragraph of unstructured text about a candidate, with no clear sections at all."

    sections = split_resume_into_sections(content)

    assert len(sections) == 1
    assert sections[0].heading is None
    assert sections[0].body == content


def test_empty_content_produces_no_sections():
    assert split_resume_into_sections("") == []
    assert split_resume_into_sections("   \n  \n") == []
