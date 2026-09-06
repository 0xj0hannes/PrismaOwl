"""Tests for src.screening.screen_record with the OrcaRouter client mocked.

No API key or network access is needed: the module-level ``client`` and the
loaded ``config`` are monkeypatched.
"""
import json

import pytest

import src.screening as screening
from src.llm import LLMError, LLMResponse, ScreeningModelError
from src.models import ScreeningResult

from tests.conftest import make_record


class _FakeClient:
    """Returns a canned response, or raises a canned exception."""

    def __init__(self, text=None, exc=None, resolved=""):
        self._text = text
        self._exc = exc
        self._resolved = resolved
        self.calls = []

    def chat(self, messages, model="m", json_mode=False, **kwargs):
        self.calls.append({"messages": messages, "model": model, "json_mode": json_mode})
        if self._exc is not None:
            raise self._exc
        return LLMResponse(text=self._text, model=model, resolved_model=self._resolved)


TWO_CRITERIA = {
    "IC1": {"name": "c1", "definition": "d", "signals": "s",
            "negative_indicators": "n"},
    "IC2": {"name": "c2", "definition": "d", "signals": "s",
            "negative_indicators": "n"},
}


def _patch_config(monkeypatch, max_retries=3, provider="orcarouter", screening_model=""):
    monkeypatch.setitem(screening.config, "CRITERIA", TWO_CRITERIA)
    monkeypatch.setitem(screening.config, "LLM_PROVIDER", provider)
    monkeypatch.setitem(screening.config, "MODEL_NAME", "test-model")
    monkeypatch.setitem(screening.config, "MODEL_SCREENING", screening_model)
    monkeypatch.setitem(screening.config, "MAX_RETRIES", max_retries)
    monkeypatch.setitem(screening.config, "SCREENING_STRICTNESS", "strict")


def test_successful_screening_parses_all_criteria(monkeypatch):
    _patch_config(monkeypatch)
    payload = {
        "decision": "Include",
        "unmet_criteria": "",
        "notes": "looks good",
        "IC1": {"score": 0.9, "evidences": ["foo"], "rationale": "because"},
        "IC2": {"score": 0.7, "evidences": [], "rationale": "ok"},
    }
    monkeypatch.setattr(screening, "client",
                        _FakeClient(text=json.dumps(payload)))

    result = screening.screen_record(make_record(id="x"))

    assert result.record_id == "x"
    assert result.decision == "Include"
    assert result.model_version == "test-model"
    assert set(result.criteria) == {"IC1", "IC2"}
    assert result.criteria["IC1"].score == 0.9
    assert result.criteria["IC1"].evidences == ["foo"]


def test_missing_criterion_in_response_defaults_to_zero(monkeypatch):
    _patch_config(monkeypatch)
    payload = {"decision": "Maybe", "IC1": {"score": 0.5}}  # IC2 omitted
    monkeypatch.setattr(screening, "client",
                        _FakeClient(text=json.dumps(payload)))

    result = screening.screen_record(make_record())

    assert result.criteria["IC2"].score == 0.0
    assert result.criteria["IC2"].evidences == []


def test_persistent_error_returns_failed_maybe(monkeypatch):
    _patch_config(monkeypatch, max_retries=1)
    monkeypatch.setattr(screening.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        screening, "client",
        _FakeClient(exc=LLMError("Model Not Found", status=404, code="model_not_found")),
    )

    result = screening.screen_record(make_record(id="y"))

    assert result.decision == "Maybe"
    assert "Failed after" in result.notes
    assert result.record_id == "y"


MODEL_GONE_NOTES = (
    "Failed after 3 attempts. Last error: HTTP 404 [model_not_found] "
    "The model 'foo/bar' does not exist or you do not have access to it."
)

CREDITS_GONE_NOTES = (
    "Failed after 1 attempts. Last error: HTTP 402 [insufficient_user_quota] "
    "You're out of credits - this request needs $0.000036."
)


def test_is_model_unavailable_detects_deprecated_model():
    assert screening.is_model_unavailable(MODEL_GONE_NOTES)
    # Only failure notes count, and only model-shaped failures.
    assert not screening.is_model_unavailable("")
    assert not screening.is_model_unavailable(
        "Failed after 3 attempts. Last error: invalid JSON")
    assert not screening.is_model_unavailable("404 in abstract text")


def test_batch_screen_stops_when_model_unavailable(monkeypatch):
    _patch_config(monkeypatch, screening_model="anthropic/claude-sonnet-5")

    def fake_screen(record):
        return screening.ScreeningResult(
            record_id=record.id, decision="Maybe", notes=MODEL_GONE_NOTES)

    monkeypatch.setattr(screening, "screen_record", fake_screen)

    records = [make_record(id="a"), make_record(id="b", doi="10.1000/b")]
    results = list(screening.batch_screen(records))

    # The first failure aborts the batch instead of retrying every record.
    assert [rid for rid, _ in results] == ["a"]


def test_resolved_model_is_recorded_for_audit(monkeypatch):
    # The router may report a dated snapshot of the pinned model; that is the
    # id that goes into the audit trail.
    _patch_config(monkeypatch, screening_model="deepseek/deepseek-v4-flash")
    payload = {"decision": "Exclude", "IC1": {"score": 0.1}, "IC2": {"score": 0.2}}
    fake = _FakeClient(text=json.dumps(payload), resolved="deepseek/deepseek-v4-flash-20260601")
    monkeypatch.setattr(screening, "client", fake)

    result = screening.screen_record(make_record())

    assert result.model_version == "deepseek/deepseek-v4-flash-20260601"
    assert fake.calls[0]["model"] == "deepseek/deepseek-v4-flash"
    assert fake.calls[0]["json_mode"] is True
    assert fake.calls[0]["messages"][-1]["role"] == "user"


def test_fenced_json_is_tolerated(monkeypatch):
    _patch_config(monkeypatch)
    payload = {"decision": "Include", "IC1": {"score": 1}, "IC2": {"score": 1}}
    text = "```json\n" + json.dumps(payload) + "\n```"
    monkeypatch.setattr(screening, "client", _FakeClient(text=text))

    assert screening.screen_record(make_record()).decision == "Include"


def test_fatal_billing_error_does_not_retry(monkeypatch):
    _patch_config(monkeypatch, max_retries=3)
    sleeps = []
    monkeypatch.setattr(screening.time, "sleep", lambda s: sleeps.append(s))
    fake = _FakeClient(exc=LLMError("out of credits", status=402, code="insufficient_user_quota"))
    monkeypatch.setattr(screening, "client", fake)

    result = screening.screen_record(make_record())

    assert len(fake.calls) == 1          # no pointless retries
    assert sleeps == []
    assert "Failed after 1 attempts" in result.notes
    assert screening.is_billing_or_auth_error(result.notes)
    assert screening.is_fatal_error(result.notes)


def test_transient_error_then_success(monkeypatch):
    _patch_config(monkeypatch)
    monkeypatch.setattr(screening.time, "sleep", lambda *_: None)
    payload = {"decision": "Include", "IC1": {"score": 1}, "IC2": {"score": 1}}

    class Flaky(_FakeClient):
        def chat(self, *a, **k):
            if not self.calls:
                self.calls.append("boom")
                raise LLMError("rate limited", status=429)
            return LLMResponse(text=json.dumps(payload), model="m")

    monkeypatch.setattr(screening, "client", Flaky())
    assert screening.screen_record(make_record()).decision == "Include"


def test_batch_screen_stops_on_billing_error(monkeypatch):
    _patch_config(monkeypatch, screening_model="anthropic/claude-sonnet-5")

    def fake_screen(record):
        return screening.ScreeningResult(record_id=record.id, decision="Maybe", notes=CREDITS_GONE_NOTES)

    monkeypatch.setattr(screening, "screen_record", fake_screen)
    records = [make_record(id="a"), make_record(id="b", doi="10.1000/b")]
    assert [rid for rid, _ in screening.batch_screen(records)] == ["a"]


def test_missing_client_fails_fast(monkeypatch):
    _patch_config(monkeypatch)
    monkeypatch.setattr(screening, "client", None)
    result = screening.screen_record(make_record())
    assert result.decision == "Maybe"
    assert screening.is_fatal_error(result.notes)


# ---------------------------------------------------------------------------
# Screening model pinning (reproducibility): on OrcaRouter, screening must run
# on one concrete model, the router must actually use it, and a batch cannot
# mix models with existing results.
# ---------------------------------------------------------------------------

def test_meta_model_is_refused_for_screening_on_orcarouter(monkeypatch):
    _patch_config(monkeypatch, screening_model="orcarouter/auto")
    fake = _FakeClient(text="{}")
    monkeypatch.setattr(screening, "client", fake)

    result = screening.screen_record(make_record())

    assert "Failed after 0 attempts" in result.notes
    assert "model_not_pinned" in result.notes
    assert screening.is_fatal_error(result.notes)
    assert screening.fatal_error_hint(result.notes) == screening.PINNING_HINT
    assert fake.calls == []  # nothing was sent to the router

    with pytest.raises(ScreeningModelError):
        list(screening.batch_screen([make_record()]))


def test_meta_model_fallback_from_model_name_is_refused(monkeypatch):
    _patch_config(monkeypatch, screening_model="")
    monkeypatch.setitem(screening.config, "MODEL_NAME", "orcarouter/free")
    with pytest.raises(ScreeningModelError):
        screening.check_screening_setup()


def test_concrete_model_is_accepted_and_meta_models_only_matter_on_orcarouter(monkeypatch):
    _patch_config(monkeypatch, screening_model="anthropic/claude-sonnet-5")
    assert screening.check_screening_setup() == "anthropic/claude-sonnet-5"
    # Gemini-direct ids are concrete; the "orcarouter/" check does not apply there.
    _patch_config(monkeypatch, provider="gemini", screening_model="gemini-3.5-flash")
    assert screening.check_screening_setup() == "gemini-3.5-flash"


def test_substituted_model_is_fatal_and_result_discarded(monkeypatch):
    _patch_config(monkeypatch, screening_model="anthropic/claude-sonnet-5")
    payload = {"decision": "Include", "IC1": {"score": 1}, "IC2": {"score": 1}}
    fake = _FakeClient(text=json.dumps(payload), resolved="qwen/qwen-4")
    monkeypatch.setattr(screening, "client", fake)
    monkeypatch.setattr(screening.time, "sleep", lambda *_: None)

    result = screening.screen_record(make_record(id="a"))

    assert result.decision == "Maybe"
    assert "model_substituted" in result.notes
    assert screening.is_fatal_error(result.notes)
    assert len(fake.calls) == 1  # fatal: no retries

    records = [make_record(id="a"), make_record(id="b", doi="10.1000/b")]
    assert [rid for rid, _ in screening.batch_screen(records)] == ["a"]


def test_gemini_does_not_apply_router_substitution_check(monkeypatch):
    _patch_config(monkeypatch, provider="gemini", screening_model="gemini-3.5-flash")
    payload = {"decision": "Include", "IC1": {"score": 1}, "IC2": {"score": 1}}
    monkeypatch.setattr(screening, "client",
                        _FakeClient(text=json.dumps(payload), resolved="models/gemini-3.5-flash"))
    assert screening.screen_record(make_record()).decision == "Include"


def test_batch_refuses_to_mix_models_with_existing_results(monkeypatch):
    _patch_config(monkeypatch, screening_model="anthropic/claude-sonnet-5")
    existing = {
        "a": ScreeningResult(record_id="a", decision="Include", model_version="openai/gpt-5"),
        # failed results carry no verdict and are ignored
        "b": ScreeningResult(record_id="b", decision="Maybe", model_version="",
                             notes="Failed after 3 attempts. Last error: boom"),
    }
    with pytest.raises(ScreeningModelError) as ei:
        screening.check_screening_setup(existing)
    assert "openai/gpt-5 (1 records)" in str(ei.value)

    # Same model (even a dated snapshot) or dict-shaped rows from SQLite: fine.
    same = {"a": {"record_id": "a", "decision": "Include", "notes": "",
                  "model_version": "anthropic/claude-sonnet-5-20260301"}}
    assert screening.check_screening_setup(same) == "anthropic/claude-sonnet-5"
    assert screening.existing_screening_models(existing) == {"openai/gpt-5": 1}


# ---------------------------------------------------------------------------
# Decision strictness
# ---------------------------------------------------------------------------

def test_strictness_changes_prompt_and_is_recorded(monkeypatch):
    _patch_config(monkeypatch, screening_model="anthropic/claude-sonnet-5")
    monkeypatch.setitem(screening.config, "SCREENING_STRICTNESS", "lenient")
    payload = {"decision": "Maybe", "IC1": {"score": 0.5}, "IC2": {"score": 0.5}}
    fake = _FakeClient(text=json.dumps(payload))
    monkeypatch.setattr(screening, "client", fake)

    result = screening.screen_record(make_record())

    assert result.strictness == "lenient"
    prompt = fake.calls[0]["messages"][-1]["content"]
    assert "Favor sensitivity over precision" in prompt and "Favor precision" not in prompt


def test_unknown_strictness_falls_back_to_strict(monkeypatch):
    _patch_config(monkeypatch, screening_model="anthropic/claude-sonnet-5")
    monkeypatch.setitem(screening.config, "SCREENING_STRICTNESS", "whatever")
    fake = _FakeClient(text=json.dumps({"decision": "Include", "IC1": {"score": 1}, "IC2": {"score": 1}}))
    monkeypatch.setattr(screening, "client", fake)
    assert screening.screen_record(make_record()).strictness == "strict"
    assert "Favor precision over sensitivity" in fake.calls[0]["messages"][-1]["content"]


def test_batch_refuses_to_mix_strictness_levels(monkeypatch):
    _patch_config(monkeypatch, screening_model="anthropic/claude-sonnet-5")
    monkeypatch.setitem(screening.config, "SCREENING_STRICTNESS", "balanced")
    existing = {
        "a": ScreeningResult(record_id="a", decision="Include", model_version="anthropic/claude-sonnet-5",
                             strictness="strict"),
        # pre-setting results (no level stored) count as strict
        "b": ScreeningResult(record_id="b", decision="Exclude", model_version="anthropic/claude-sonnet-5"),
        "f": ScreeningResult(record_id="f", decision="Maybe", notes="Failed after 3 attempts. Last error: x"),
    }
    with pytest.raises(ScreeningModelError) as ei:
        screening.check_screening_setup(existing)
    assert "Strict (precision first) (2 records)" in str(ei.value) and "Balanced" in str(ei.value)
    assert screening.existing_screening_strictness(existing) == {"strict": 2}

    monkeypatch.setitem(screening.config, "SCREENING_STRICTNESS", "strict")
    assert screening.check_screening_setup(existing) == "anthropic/claude-sonnet-5"
