"""Tests for the LLM-assisted helpers (search strategy, criteria assistant,
chat) with the model call mocked."""
import json

import pytest

import src.chat as chat
import src.criteria_assist as ca
import src.search_strategy as ss


# --- search strategy --------------------------------------------------------

def test_strategy_prompt_lists_every_database_and_context():
    prompt = ss.build_strategy_prompt("hackers", current={"research_question": "old"}, feedback="add X")
    for key in ss.DATABASES:
        assert f"- {key} (" in prompt
        assert f'"{key}"' in prompt   # schema
    assert "Current strategy" in prompt and "old" in prompt
    assert "add X" in prompt


def test_normalize_strategy_coerces_shapes():
    raw = {"research_question": " q ", "concepts": [{"name": "A", "terms": "x; y ;"}, "junk"],
           "queries": {"scopus": " TITLE-ABS-KEY(a) ", "custom_db": "z"}}
    out = ss.normalize_strategy(raw)
    assert out["research_question"] == "q"
    assert out["concepts"] == [{"name": "A", "terms": ["x", "y"]}]
    assert out["queries"]["scopus"] == "TITLE-ABS-KEY(a)"
    assert out["queries"]["arxiv"] == ""            # every known db present
    assert out["queries"]["custom_db"] == "z"       # user extras preserved


def test_generate_strategy_uses_query_task(monkeypatch):
    seen = {}

    def fake_generate_json(prompt, task="", **kw):
        seen["task"] = task
        return {"research_question": "RQ", "concepts": [], "queries": {"openalex": "a AND b"}}

    monkeypatch.setattr(ss, "generate_json", fake_generate_json)
    out = ss.generate_strategy("topic")
    assert seen["task"] == "query"
    assert out["queries"]["openalex"] == "a AND b"
    with pytest.raises(ValueError):
        ss.generate_strategy("   ")


# --- criteria ---------------------------------------------------------------

def test_validate_criteria_accepts_and_normalises():
    out = ca.validate_criteria({"IC1": {"name": " N ", "definition": "D", "signals": None}})
    assert out == {"IC1": {"name": "N", "definition": "D", "signals": "", "negative_indicators": ""}}


@pytest.mark.parametrize("bad", [
    {}, [], "x",
    {"IC 1": {"name": "n", "definition": "d"}},         # space in key
    {"1IC": {"name": "n", "definition": "d"}},          # leading digit
    {"IC1": "not an object"},
    {"IC1": {"name": "", "definition": "d"}},           # missing name
])
def test_validate_criteria_rejects(bad):
    with pytest.raises(ValueError):
        ca.validate_criteria(bad)


def test_generate_criteria_validates_model_output(monkeypatch):
    monkeypatch.setattr(ca, "generate_json", lambda prompt, task="", **kw: {
        "IC1": {"name": "A", "definition": "d", "signals": "s", "negative_indicators": "n"}})
    out = ca.generate_criteria("topic", count=1)
    assert list(out) == ["IC1"]
    monkeypatch.setattr(ca, "generate_json", lambda prompt, task="", **kw: {"bad key!": {}})
    with pytest.raises(ValueError):
        ca.generate_criteria("topic")


def test_criteria_prompt_includes_current_and_count():
    p = ca.build_criteria_prompt("t", current={"IC9": {"name": "x"}}, feedback="fb", count=4)
    assert "IC9" in p and "fb" in p and "Propose 4 inclusion" in p


# --- chat -------------------------------------------------------------------

RECORDS = [
    {"id": "a", "title": "A", "abstract": "aa", "authors": "X", "year": "2020"},
    {"id": "b", "title": "B", "abstract": "bb"},
    {"id": "c", "title": "C", "abstract": "cc"},
    {"id": "d", "title": "D", "abstract": "dd", "is_duplicate": True},
    {"id": "e", "title": "E", "abstract": "ee"},   # never screened
]
RESULTS = {
    "a": {"record_id": "a", "decision": "Include", "criteria": {"IC1": {"score": 0.9, "rationale": "r"}}},
    "b": {"record_id": "b", "decision": "Maybe", "final_decision": "Include", "human_reviewed": True},
    "c": {"record_id": "c", "decision": "Maybe"},
    "d": {"record_id": "d", "decision": "Include"},
}


def test_select_records_by_scope():
    ids = lambda scope: [r["id"] for r, _ in chat.select_records(RECORDS, RESULTS, scope)]
    assert ids("included") == ["a", "b"]          # final_decision wins; duplicate skipped
    assert ids("included_maybe") == ["a", "b", "c"]
    assert ids("all_screened") == ["a", "b", "c"]
    with pytest.raises(ValueError):
        chat.select_records(RECORDS, RESULTS, "bogus")


def test_system_prompt_contains_corpus_and_criteria():
    pairs = chat.select_records(RECORDS, RESULTS, "included")
    prompt = chat.build_system_prompt(pairs, "included", {"IC1": {"name": "N", "definition": "Def"}})
    assert "[a] A" in prompt and "[b] B" in prompt and "[c]" not in prompt
    assert "IC1 score=0.9: r" in prompt
    assert "(human reviewed)" in prompt
    assert "- IC1 (N): Def" in prompt
    assert "2 records" in prompt


def test_corpus_truncates_when_huge(monkeypatch):
    monkeypatch.setattr(chat, "MAX_CONTEXT_CHARS", 300)
    monkeypatch.setattr(chat, "ABSTRACT_CAP_WHEN_TRUNCATING", 20)
    big = [({"id": f"r{i}", "title": "T", "abstract": "x" * 500}, {"decision": "Include"}) for i in range(5)]
    corpus = chat.build_corpus(big)
    assert len(corpus) <= 300 + len("\n\n[corpus truncated]")


def test_ask_passes_history_and_scope(monkeypatch):
    captured = {}

    def fake_chat(messages, system_prompt, task="chat"):
        captured["messages"] = messages
        captured["system"] = system_prompt
        return "reply"

    monkeypatch.setattr(chat, "generate_chat", fake_chat)
    out = chat.ask([{"role": "user", "content": "hi"}], RECORDS, RESULTS, {}, scope="included_maybe")
    assert out == {"reply": "reply", "n_records": 3, "scope": "included_maybe"}
    assert "[c] C" in captured["system"]


# ---------------------------------------------------------------------------
# Deterministic query builder + year-range parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("notes, expected", [
    ("2010 onwards, English only", (2010, None)),
    ("Published between 2015 and 2024", (2015, 2024)),
    ("2012-2020, peer reviewed", (2012, 2020)),
    ("from 2018 to 2021", (2018, 2021)),
    ("since 2019", (2019, None)),
    ("after 2015", (2016, None)),
    ("until 2020", (None, 2020)),
    ("before 2020", (None, 2019)),
    ("journal articles, English", (None, None)),
    ("", (None, None)),
])
def test_parse_year_range(notes, expected):
    assert ss.parse_year_range(notes) == expected


def test_parse_year_range_last_n_years():
    from datetime import date
    assert ss.parse_year_range("last 5 years") == (date.today().year - 5, None)


def test_format_year_range():
    assert ss.format_year_range((2010, 2024)) == "2010\u20132024"
    assert ss.format_year_range((2010, None)) == "2010 onwards"
    assert ss.format_year_range((None, 2020)) == "up to 2020"
    assert ss.format_year_range((None, None)) == ""


CONCEPTS = [
    {"name": "Intervention", "terms": ["mindfulness*", "meditation", " mindfulness-based stress reduction "]},
    {"name": "Outcome", "terms": ["burnout", "occupational stress"]},
    {"name": "empty", "terms": []},
]


def test_build_queries_covers_every_database_with_and_or_structure():
    from datetime import date
    q = ss.build_queries(CONCEPTS, "2010 onwards")
    assert set(q) == set(ss.DATABASES)
    assert q["scopus"] == ('TITLE-ABS-KEY((mindfulness* OR meditation OR "mindfulness-based stress reduction") '
                           'AND (burnout OR "occupational stress")) AND PUBYEAR > 2009')
    assert q["web_of_science"].startswith("TS=((mindfulness* OR meditation OR")
    assert q["web_of_science"].endswith(f" AND PY=(2010-{date.today().year})")
    # No wildcards where the API does not support them; years go to the API filter instead.
    assert q["openalex"] == '(mindfulness OR meditation OR "mindfulness-based stress reduction") AND (burnout OR "occupational stress")'
    assert q["semantic_scholar"] == '(mindfulness* | meditation | "mindfulness-based stress reduction") + (burnout | "occupational stress")'
    assert q["arxiv"].startswith('(all:mindfulness OR all:meditation OR all:"mindfulness-based stress reduction") AND (all:burnout')
    assert "submittedDate:[20100101 TO " in q["arxiv"]
    assert '"Abstract":mindfulness* OR "Document Title":mindfulness*' in q["ieee"]
    assert q["acm"].startswith("(Abstract:(mindfulness* OR meditation OR")
    assert "Keyword:(burnout OR" in q["acm"]
    assert q["crossref"] == "mindfulness meditation mindfulness-based stress reduction burnout occupational stress"


def test_build_queries_without_years_or_terms():
    q = ss.build_queries(CONCEPTS, "English, journals")
    assert "PUBYEAR" not in q["scopus"] and "PY=" not in q["web_of_science"] and "submittedDate" not in q["arxiv"]
    assert ss.build_queries([], "2010 onwards") == {k: "" for k in ss.DATABASES}
    assert ss.build_queries([{"name": "x", "terms": "a; b c"}], "")["openalex"] == '(a OR "b c")'


def test_build_endpoint_returns_queries_and_year_info():
    import asyncio
    import app as web_app

    class _Req:
        async def json(self):
            return {"concepts": CONCEPTS, "scope_notes": "between 2015 and 2024"}

    data = asyncio.run(web_app.build_search_queries(_Req()))
    assert data["year_range"] == [2015, 2024] and data["year_range_label"] == "2015\u20132024"
    assert data["concept_count"] == 2
    assert data["queries"]["scopus"].endswith("AND PUBYEAR > 2014 AND PUBYEAR < 2025")
    assert data["year_filter_support"]["acm"] == "manual"

    class _Empty:
        async def json(self):
            return {"concepts": [], "scope_notes": ""}
    assert asyncio.run(web_app.build_search_queries(_Empty())).status_code == 400


def test_build_queries_handles_truncation_where_unsupported():
    concepts = [{"name": "c", "terms": ["cybercrim*", "cybercriminal", "hacker*"]}]
    q = ss.build_queries(concepts)
    # OpenAlex has no truncation: the stem covered by "cybercriminal" is dropped,
    # the uncovered stem "hacker*" is kept bare; Scopus keeps both wildcards.
    assert q["openalex"] == "(cybercriminal OR hacker)"
    assert q["arxiv"] == "(all:cybercriminal OR all:hacker)"
    assert q["crossref"] == "cybercriminal hacker"
    assert q["scopus"] == "TITLE-ABS-KEY((cybercrim* OR cybercriminal OR hacker*))"
    assert q["semantic_scholar"] == "(cybercrim* | cybercriminal | hacker*)"
