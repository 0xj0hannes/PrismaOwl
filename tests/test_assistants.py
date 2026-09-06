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
