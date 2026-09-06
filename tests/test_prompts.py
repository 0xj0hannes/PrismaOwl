"""Tests for src.prompts.generate_prompt (dynamic, criteria-driven prompt)."""
from src.prompts import generate_prompt


def test_prompt_includes_title_and_abstract(sample_criteria):
    prompt = generate_prompt(
        title="My Title", abstract="My Abstract", criteria=sample_criteria
    )
    assert "My Title" in prompt
    assert "My Abstract" in prompt


def test_prompt_describes_every_criterion(sample_criteria):
    prompt = generate_prompt("t", "a", sample_criteria)
    for key, c in sample_criteria.items():
        assert key in prompt
        assert c["name"] in prompt
        assert c["definition"] in prompt
        assert c["signals"] in prompt
        assert c["negative_indicators"] in prompt


def test_response_schema_lists_all_criteria_keys(sample_criteria):
    prompt = generate_prompt("t", "a", sample_criteria)
    # The expected JSON schema references each criterion key and the joined form
    # used to enumerate unmet criteria.
    assert "+".join(sample_criteria.keys()) in prompt
    assert "decision" in prompt


def test_adding_a_criterion_changes_the_prompt(sample_criteria):
    base = generate_prompt("t", "a", sample_criteria)
    extended = dict(sample_criteria)
    extended["IC3"] = {
        "name": "New criterion",
        "definition": "def",
        "signals": "sig",
        "negative_indicators": "neg",
    }
    new = generate_prompt("t", "a", extended)
    # The new criterion's description block only appears once it is configured.
    assert "New criterion" not in base
    assert "New criterion" in new
    assert "**IC3: New criterion**" in new


def test_prompt_strictness_levels(sample_criteria):
    from src.prompts import generate_prompt, STRICTNESS_LEVELS
    strict = generate_prompt("t", "a", sample_criteria)
    balanced = generate_prompt("t", "a", sample_criteria, strictness="balanced")
    lenient = generate_prompt("t", "a", sample_criteria, strictness="lenient")
    assert strict == generate_prompt("t", "a", sample_criteria, strictness="strict")
    assert "Favor precision over sensitivity" in strict
    assert "uncertainty means \"Maybe\"" in balanced
    assert "Favor sensitivity over precision" in lenient
    # rules 1 and 5 are common to every level; rules 2-4 differ
    for p in (strict, balanced, lenient):
        assert "1. Evaluate the inclusion criteria strictly and separately." in p
        assert '5. If the decision is "Exclude" or "Maybe"' in p
    assert set(STRICTNESS_LEVELS) == {"strict", "balanced", "lenient"}
