"""Tests for the LLM client wrapper (src.llm; OrcaRouter and Gemini providers) with HTTP mocked."""
import json

import pytest

import src.llm as llm


class _Resp:
    def __init__(self, status=200, body=None, headers=None, text=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.text = text if text is not None else json.dumps(body)

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def _ok_body(content, model="deepseek/x"):
    return {"id": "req1", "model": model,
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"total_tokens": 42}}


def test_chat_parses_content_and_routing_headers(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, body=json)
        return _Resp(200, _ok_body('{"a": 1}'),
                     headers={"x-orca-resolved-model": "qwen/q", "x-orca-cache": "HIT"})

    monkeypatch.setattr(llm.requests, "post", fake_post)
    client = llm.OrcaClient("sk-orca-test", "https://example.test/v1/")

    resp = client.chat([{"role": "user", "content": "hi"}], model="orcarouter/auto", json_mode=True)

    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-orca-test"
    assert captured["body"]["model"] == "orcarouter/auto"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert resp.text == '{"a": 1}'
    assert resp.resolved_model == "qwen/q"
    assert resp.model_label == "qwen/q"
    assert resp.cached is True
    assert resp.usage["total_tokens"] == 42


def test_error_body_becomes_llmerror_with_code(monkeypatch):
    body = {"error": {"message": "You're out of credits", "type": "insufficient_quota",
                      "code": "insufficient_user_quota"}}
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: _Resp(402, body))
    client = llm.OrcaClient("k")

    with pytest.raises(llm.LLMError) as ei:
        client.chat([{"role": "user", "content": "x"}])
    err = ei.value
    assert err.status == 402
    assert err.code == "insufficient_user_quota"
    assert err.fatal and not err.transient
    assert "402" in str(err) and "out of credits" in str(err)


def test_error_classification():
    assert llm.LLMError("x", status=429).transient
    assert llm.LLMError("x", status=503).transient
    assert llm.LLMError("network").transient          # no status = connection problem
    assert not llm.LLMError("x", status=400).transient
    assert llm.LLMError("x", status=401).fatal
    assert llm.LLMError("x", status=404).fatal
    assert llm.LLMError("x", code="model_not_found").fatal
    assert not llm.LLMError("x", status=429).fatal


def test_missing_key_raises():
    with pytest.raises(llm.LLMError):
        llm.OrcaClient("")


def test_parse_json_text_tolerates_fences_and_prose():
    assert llm.parse_json_text('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm.parse_json_text('Sure! Here it is: {"a": [1, 2]} hope that helps') == {"a": [1, 2]}
    assert llm.parse_json_text('{"a": 1}') == {"a": 1}
    with pytest.raises(json.JSONDecodeError):
        llm.parse_json_text("no json here")


def test_model_for_prefers_task_override():
    cfg = {"MODEL_NAME": "orcarouter/auto", "MODEL_CHAT": "anthropic/claude-sonnet-5", "MODEL_QUERY": ""}
    assert llm.model_for("chat", cfg) == "anthropic/claude-sonnet-5"
    assert llm.model_for("query", cfg) == "orcarouter/auto"
    assert llm.model_for("screening", cfg) == "orcarouter/auto"
    assert llm.model_for("", {}) == llm.DEFAULT_MODEL


def test_generate_json_retries_bad_json_then_succeeds(monkeypatch):
    texts = iter(["not json", '{"ok": true}'])

    class FakeClient:
        def chat(self, messages, model=None, json_mode=False, **kw):
            return llm.LLMResponse(text=next(texts), model=model or "m")

    monkeypatch.setattr(llm, "get_client", lambda: FakeClient())
    monkeypatch.setattr(llm, "model_for", lambda task, cfg=None: "m")
    monkeypatch.setattr(llm.time, "sleep", lambda *_: None)

    assert llm.generate_json("prompt", task="query") == {"ok": True}


def test_generate_json_gives_up_on_fatal(monkeypatch):
    calls = []

    class FakeClient:
        def chat(self, *a, **k):
            calls.append(1)
            raise llm.LLMError("bad key", status=401, code="invalid_api_key")

    monkeypatch.setattr(llm, "get_client", lambda: FakeClient())
    monkeypatch.setattr(llm, "model_for", lambda task, cfg=None: "m")
    monkeypatch.setattr(llm.time, "sleep", lambda *_: None)

    with pytest.raises(llm.LLMError):
        llm.generate_json("prompt", retries=3)
    assert len(calls) == 1


def test_generate_chat_builds_openai_messages(monkeypatch):
    captured = {}

    class FakeClient:
        def chat(self, messages, model=None, **kw):
            captured["messages"] = messages
            return llm.LLMResponse(text="answer", model=model or "m")

    monkeypatch.setattr(llm, "get_client", lambda: FakeClient())
    monkeypatch.setattr(llm, "model_for", lambda task, cfg=None: "m")

    out = llm.generate_chat([{"role": "user", "content": "q1"},
                             {"role": "assistant", "content": "a1"},
                             {"role": "user", "content": "q2"}], "SYS")
    assert out == "answer"
    roles = [m["role"] for m in captured["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert captured["messages"][0]["content"] == "SYS"

    with pytest.raises(llm.LLMError):
        llm.generate_chat([{"role": "assistant", "content": "a"}], "SYS")


# ---------------------------------------------------------------------------
# Provider selection (OrcaRouter default, Gemini kept as first-class option)
# ---------------------------------------------------------------------------

def test_resolve_provider_prefers_explicit_then_keys():
    from src import config as cfgmod
    assert cfgmod.resolve_provider("gemini", orca_key="k", gemini_key="g") == "gemini"
    assert cfgmod.resolve_provider("", orca_key="k", gemini_key="g") == "orcarouter"
    assert cfgmod.resolve_provider("", orca_key="", gemini_key="g") == "gemini"
    assert cfgmod.resolve_provider("", orca_key="", gemini_key="") == "orcarouter"
    with pytest.raises(ValueError):
        cfgmod.resolve_provider("openai")


def test_load_config_gemini_only_key_selects_gemini(monkeypatch):
    from src import config as cfgmod
    for var in ("LLM_PROVIDER", "ORCA_API_KEY", "GEMINI_API_KEY", "MODEL_NAME", "GEMINI_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setattr(cfgmod, "load_dotenv", lambda *a, **k: None)
    cfg = cfgmod.load_config()
    assert cfg["LLM_PROVIDER"] == "gemini"
    assert cfg["LLM_API_KEY"] == "g-key"
    assert cfg["LLM_BASE_URL"].startswith("https://generativelanguage.googleapis.com/")
    assert cfg["MODEL_NAME"] == "gemini-3.5-flash"
    assert llm.model_for("screening", cfg) == "gemini-3.5-flash"


def test_load_config_explicit_provider_wins(monkeypatch):
    from src import config as cfgmod
    for var in ("LLM_PROVIDER", "ORCA_API_KEY", "GEMINI_API_KEY", "MODEL_NAME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ORCA_API_KEY", "sk-orca")
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setattr(cfgmod, "load_dotenv", lambda *a, **k: None)
    cfg = cfgmod.load_config()
    assert cfg["LLM_PROVIDER"] == "gemini"
    assert cfg["LLM_API_KEY"] == "g-key"


def test_build_client_uses_gemini_endpoint_and_key(monkeypatch):
    cfg = {"LLM_PROVIDER": "gemini", "LLM_API_KEY": "g-key",
           "LLM_BASE_URL": "https://generativelanguage.googleapis.com/v1beta/openai/",
           "LLM_TIMEOUT": 10.0}
    client = llm.build_client(cfg)
    assert client.provider == "gemini"
    assert client.provider_label == "Google Gemini"
    assert client.base_url == "https://generativelanguage.googleapis.com/v1beta/openai"

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, body=json)
        return _Resp(200, {"id": "x", "model": "gemini-3.5-flash",
                           "choices": [{"message": {"content": '{"ok": true}'}}],
                           "usage": {"total_tokens": 3}})

    monkeypatch.setattr(llm.requests, "post", fake_post)
    resp = client.chat([{"role": "user", "content": "hi"}], model="gemini-3.5-flash", json_mode=True)
    assert captured["url"] == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer g-key"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert resp.model_label == "gemini-3.5-flash"      # no router header: falls back to body model
    assert resp.cached is False


def test_build_client_returns_none_without_key_and_names_the_variable():
    assert llm.build_client({"LLM_PROVIDER": "gemini", "LLM_API_KEY": None}) is None
    assert llm.missing_key_message({"LLM_PROVIDER": "gemini"}) == "GEMINI_API_KEY is not set in .env"
    assert llm.missing_key_message({"LLM_PROVIDER": "orcarouter"}) == "ORCA_API_KEY is not set in .env"
    # Legacy configs without LLM_PROVIDER are detected from the keys present.
    assert llm.provider_for({"GEMINI_API_KEY": "g"}) == "gemini"
    assert llm.provider_for({"ORCA_API_KEY": "o", "GEMINI_API_KEY": "g"}) == "orcarouter"


def test_gemini_error_status_string_is_fatal(monkeypatch):
    body = {"error": {"code": 404, "message": "models/gemini-old is not found",
                      "status": "NOT_FOUND"}}
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: _Resp(404, body))
    client = llm.LLMClient("g", provider="gemini")
    with pytest.raises(llm.LLMError) as ei:
        client.chat([{"role": "user", "content": "hi"}], model="gemini-old")
    assert ei.value.status == 404
    assert ei.value.error_type == "NOT_FOUND"
    assert ei.value.fatal
    assert llm.LLMError("x", error_type="PERMISSION_DENIED").fatal


# ---------------------------------------------------------------------------
# Screening model pinning helpers
# ---------------------------------------------------------------------------

def test_is_meta_model():
    assert llm.is_meta_model("orcarouter/auto")
    assert llm.is_meta_model("OrcaRouter/Free")
    assert llm.is_meta_model("orcarouter/fusion-flash")
    assert not llm.is_meta_model("anthropic/claude-sonnet-5")
    assert not llm.is_meta_model("gemini-3.5-flash")
    assert not llm.is_meta_model("")


def test_screening_model_requires_concrete_id_on_orcarouter():
    with pytest.raises(llm.ScreeningModelError) as ei:
        llm.screening_model({"LLM_PROVIDER": "orcarouter", "MODEL_NAME": "orcarouter/auto"})
    assert "MODEL_SCREENING" in str(ei.value)
    with pytest.raises(llm.ScreeningModelError):
        llm.screening_model({"LLM_PROVIDER": "orcarouter", "MODEL_NAME": "anthropic/claude-sonnet-5",
                             "MODEL_SCREENING": "orcarouter/fusion-flash"})
    assert llm.screening_model({"LLM_PROVIDER": "orcarouter", "MODEL_NAME": "orcarouter/auto",
                                "MODEL_SCREENING": "anthropic/claude-sonnet-5"}) == "anthropic/claude-sonnet-5"
    assert llm.screening_model({"LLM_PROVIDER": "orcarouter",
                                "MODEL_NAME": "anthropic/claude-sonnet-5"}) == "anthropic/claude-sonnet-5"
    # Gemini's default is concrete and passes as-is.
    assert llm.screening_model({"LLM_PROVIDER": "gemini"}) == "gemini-3.5-flash"


def test_check_resolved_model_tolerates_snapshots_but_not_other_models():
    assert llm.check_resolved_model("anthropic/claude-sonnet-5", "anthropic/claude-sonnet-5")
    assert llm.check_resolved_model("anthropic/claude-sonnet-5", "Anthropic/Claude-Sonnet-5-20260301")
    assert llm.check_resolved_model("gemini-3.5-flash", "models/gemini-3.5-flash")
    assert llm.check_resolved_model("anthropic/claude-sonnet-5", "")   # nothing reported
    # OrcaRouter reports the upstream's own name for free-tier models.
    assert llm.check_resolved_model("deepseek/deepseek-v4-flash-free", "deepseek-v4-flash")
    assert llm.check_resolved_model("qwen/qwen3.8-27b-free", "Qwen/Qwen3.8-27B")
    assert llm.check_resolved_model("tencent/hy3-free", "hy3")
    assert llm.check_resolved_model("meta/llama-5:free", "llama-5")
    assert not llm.check_resolved_model("anthropic/claude-sonnet-5", "qwen/qwen-4")
    assert not llm.check_resolved_model("deepseek/deepseek-v4-flash-free", "hy3")
    assert not llm.check_resolved_model("tencent/hy3-free", "deepseek-v4-flash")
    assert llm.LLMError("x", code="model_substituted").fatal
