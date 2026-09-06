"""Tests for the .env writer and the web Settings endpoints (no network)."""
import asyncio
import os

import pytest

from src import config as cfgmod


def test_update_env_preserves_comments_order_and_appends(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# LLM\nORCA_API_KEY=old\n\nMODEL_NAME=orcarouter/auto\n# tail comment\n")
    for k in ("ORCA_API_KEY", "MODEL_NAME", "MODEL_SCREENING", "OPENALEX_EMAIL"):
        monkeypatch.delenv(k, raising=False)

    written = cfgmod.update_env({"MODEL_NAME": "", "MODEL_SCREENING": "anthropic/claude-sonnet-5",
                                 "ORCA_API_KEY": "sk-orca-new"}, path=str(env))

    assert sorted(written) == ["MODEL_NAME", "MODEL_SCREENING", "ORCA_API_KEY"]
    assert env.read_text() == (
        "# LLM\nORCA_API_KEY=sk-orca-new\n\nMODEL_NAME=\n# tail comment\n"
        "MODEL_SCREENING=anthropic/claude-sonnet-5\n")
    # os.environ mirrors the file, because load_dotenv never overrides set vars.
    assert os.environ["ORCA_API_KEY"] == "sk-orca-new"
    assert os.environ["MODEL_SCREENING"] == "anthropic/claude-sonnet-5"
    assert "MODEL_NAME" not in os.environ


def test_update_env_quotes_values_dotenv_would_misread(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.delenv("OPENALEX_EMAIL", raising=False)
    cfgmod.update_env({"OPENALEX_EMAIL": "me@example.org", "MODEL_CHAT": 'a model # with "quotes"'},
                      path=str(env))
    text = env.read_text()
    assert "OPENALEX_EMAIL=me@example.org\n" in text
    assert 'MODEL_CHAT="a model # with \\"quotes\\""' in text
    # Round-trips through python-dotenv.
    from dotenv import dotenv_values
    assert dotenv_values(str(env))["MODEL_CHAT"] == 'a model # with "quotes"'


def test_update_env_creates_missing_file(tmp_path, monkeypatch):
    env = tmp_path / "sub" / ".env"
    env.parent.mkdir()
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    cfgmod.update_env({"LLM_PROVIDER": "gemini"}, path=str(env))
    assert env.read_text() == "LLM_PROVIDER=gemini\n"


def test_load_config_tolerates_blank_numeric_settings(monkeypatch):
    monkeypatch.setenv("MAX_RETRIES", "")
    monkeypatch.setenv("LLM_TIMEOUT", "")
    monkeypatch.setattr(cfgmod, "load_dotenv", lambda *a, **k: None)
    cfg = cfgmod.load_config()
    assert cfg["MAX_RETRIES"] == 3 and cfg["LLM_TIMEOUT"] == 300.0


# ---------------------------------------------------------------------------
# Endpoint handlers, called directly with a minimal Request stand-in.
# ---------------------------------------------------------------------------

class _Req:
    def __init__(self, payload):
        self._payload = payload
        self.headers = {"content-length": "1"}

    async def json(self):
        return self._payload


@pytest.fixture
def web(monkeypatch):
    import app as web_app
    written = {}
    monkeypatch.setattr(web_app, "update_env", lambda values: written.update(values) or list(values))
    monkeypatch.setattr(web_app.screening_module, "reload_config", lambda: None)
    monkeypatch.setattr(web_app.llm_module, "reset_client", lambda: None)
    monkeypatch.setattr(web_app, "is_screening_running", False)
    return web_app, written


def test_get_settings_never_returns_secret_values(monkeypatch):
    import app as web_app
    monkeypatch.setattr(cfgmod, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("ORCA_API_KEY", "sk-orca-1234567890abcd")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    data = asyncio.run(web_app.get_settings())
    assert data["active_provider"] == "orcarouter"
    assert data["secrets"]["ORCA_API_KEY"] == {"set": True, "hint": "…abcd"}
    assert data["secrets"]["GEMINI_API_KEY"] == {"set": False, "hint": ""}
    assert "sk-orca" not in str(data)


def test_put_settings_writes_fields_and_secret_semantics(web):
    web_app, written = web
    res = asyncio.run(web_app.put_settings(_Req({
        "LLM_PROVIDER": "gemini", "MODEL_SCREENING": " gemini-3.5-flash ",
        "GEMINI_API_KEY": "AIza-new", "ORCA_API_KEY": None, "IEEE_API_KEY": "",
        "NOT_A_SETTING": "ignored",
    })))
    assert res["status"] == "success"
    assert written == {"LLM_PROVIDER": "gemini", "MODEL_SCREENING": "gemini-3.5-flash",
                       "GEMINI_API_KEY": "AIza-new", "IEEE_API_KEY": ""}
    assert "ORCA_API_KEY" not in written          # null = keep
    assert "NOT_A_SETTING" not in written


@pytest.mark.parametrize("payload, fragment", [
    ({"LLM_PROVIDER": "openai"}, "LLM_PROVIDER"),
    ({"MAX_RETRIES": "0"}, "MAX_RETRIES"),
    ({"LLM_TIMEOUT": "-5"}, "LLM_TIMEOUT"),
    ({"ORCA_BASE_URL": "api.orcarouter.ai/v1"}, "http"),
    ({"MODEL_NAME": "a\nb"}, "single line"),
    ({}, "Nothing"),
])
def test_put_settings_validation(web, payload, fragment):
    web_app, written = web
    res = asyncio.run(web_app.put_settings(_Req(payload)))
    assert res.status_code == 400
    assert fragment in res.body.decode()
    assert written == {}


def test_put_settings_refused_while_screening(web, monkeypatch):
    web_app, written = web
    monkeypatch.setattr(web_app, "is_screening_running", True)
    res = asyncio.run(web_app.put_settings(_Req({"MODEL_NAME": "x"})))
    assert res.status_code == 409 and written == {}


def test_client_for_override_uses_that_providers_own_key(monkeypatch):
    import app as web_app
    monkeypatch.setattr(cfgmod, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("ORCA_API_KEY", "sk-orca-active")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("GEMINI_BASE_URL", raising=False)

    ps, client = web_app._client_for("")
    assert ps["provider"] == "orcarouter" and client.api_key == "sk-orca-active"

    # Gemini has no key: must not silently fall back to the OrcaRouter key.
    from src.llm import LLMError
    with pytest.raises(LLMError) as ei:
        web_app._client_for("gemini")
    assert "GEMINI_API_KEY" in str(ei.value)

    monkeypatch.setenv("GEMINI_API_KEY", "AIza-g")
    ps, client = web_app._client_for("gemini")
    assert client.api_key == "AIza-g"
    assert client.base_url.startswith("https://generativelanguage.googleapis.com/")
