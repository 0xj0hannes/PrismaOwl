import os
import json
from dotenv import load_dotenv

# Project root is one level up from src/
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(PROJECT_ROOT, '.env')
CRITERIA_PATH = os.path.join(PROJECT_ROOT, 'criteria.json')
SEARCH_STRATEGY_PATH = os.path.join(PROJECT_ROOT, 'search_strategy.json')

# ---------------------------------------------------------------------------
# LLM providers. Both speak the OpenAI chat-completions dialect, so one HTTP
# client (src/llm.py) serves them; only key, base URL and default model differ.
#   orcarouter - gateway routing to 100+ upstream models (default)
#   gemini     - Google Gemini Developer API directly, via its OpenAI-compatible
#                endpoint (free tier available; no vendor SDK required)
# ---------------------------------------------------------------------------
PROVIDERS = {
    "orcarouter": {
        "label": "OrcaRouter",
        "key_env": "ORCA_API_KEY",
        "base_url_env": "ORCA_BASE_URL",
        "base_url": "https://api.orcarouter.ai/v1",
        "default_model": "orcarouter/auto",
        "console": "https://www.orcarouter.ai/console",
    },
    "gemini": {
        "label": "Google Gemini",
        "key_env": "GEMINI_API_KEY",
        "base_url_env": "GEMINI_BASE_URL",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_model": "gemini-3.5-flash",
        "console": "https://aistudio.google.com/",
    },
}
DEFAULT_PROVIDER = "orcarouter"
DEFAULT_MODEL = PROVIDERS[DEFAULT_PROVIDER]["default_model"]
DEFAULT_BASE_URL = PROVIDERS[DEFAULT_PROVIDER]["base_url"]


def resolve_provider(explicit: str = "", orca_key: str = "", gemini_key: str = "") -> str:
    """Pick the LLM provider: ``LLM_PROVIDER`` if set, else whichever API key
    is present (OrcaRouter wins when both are), else the default."""
    explicit = (explicit or "").strip().lower()
    if explicit:
        if explicit not in PROVIDERS:
            raise ValueError(
                f"Unknown LLM_PROVIDER '{explicit}'. Use one of: {', '.join(PROVIDERS)}")
        return explicit
    if orca_key:
        return "orcarouter"
    if gemini_key:
        return "gemini"
    return DEFAULT_PROVIDER


def load_criteria(path: str = CRITERIA_PATH) -> dict:
    if os.path.exists(path):
        with open(path, 'r') as f:
            return json.load(f)
    return {}


def save_criteria(criteria: dict, path: str = CRITERIA_PATH) -> None:
    """Persist criteria.json. Keys are kept in insertion order so the prompt
    and CSV column order follow what the user sees in the editor."""
    with open(path, 'w') as f:
        json.dump(criteria, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_search_strategy(path: str = SEARCH_STRATEGY_PATH) -> dict:
    if os.path.exists(path):
        with open(path, 'r') as f:
            return json.load(f)
    return {}


def save_search_strategy(strategy: dict, path: str = SEARCH_STRATEGY_PATH) -> None:
    with open(path, 'w') as f:
        json.dump(strategy, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_config():
    # Load from specific path
    load_dotenv(dotenv_path=ENV_PATH)

    orca_key = os.getenv("ORCA_API_KEY") or ""
    gemini_key = os.getenv("GEMINI_API_KEY") or ""
    provider = resolve_provider(os.getenv("LLM_PROVIDER", ""), orca_key, gemini_key)
    spec = PROVIDERS[provider]

    return {
        # --- LLM provider (OpenAI-compatible chat completions) ---
        "LLM_PROVIDER": provider,
        "ORCA_API_KEY": orca_key or None,
        "ORCA_BASE_URL": os.getenv("ORCA_BASE_URL", PROVIDERS["orcarouter"]["base_url"]),
        "GEMINI_API_KEY": gemini_key or None,
        "GEMINI_BASE_URL": os.getenv("GEMINI_BASE_URL", PROVIDERS["gemini"]["base_url"]),
        # Resolved for the active provider (what src/llm.py actually uses).
        "LLM_API_KEY": os.getenv(spec["key_env"]) or None,
        "LLM_BASE_URL": os.getenv(spec["base_url_env"], spec["base_url"]),
        # Global default model; empty MODEL_NAME = the provider's default
        # ("orcarouter/auto" lets the router pick; "gemini-3.5-flash" for Gemini).
        "MODEL_NAME": os.getenv("MODEL_NAME") or spec["default_model"],
        # Optional per-task overrides (empty = fall back to MODEL_NAME).
        "MODEL_SCREENING": os.getenv("MODEL_SCREENING", ""),
        "MODEL_QUERY": os.getenv("MODEL_QUERY", ""),
        "MODEL_CRITERIA": os.getenv("MODEL_CRITERIA", ""),
        "MODEL_CHAT": os.getenv("MODEL_CHAT", ""),
        "MAX_RETRIES": int(os.getenv("MAX_RETRIES", "3")),
        "LLM_TIMEOUT": float(os.getenv("LLM_TIMEOUT", "300")),
        "CRITERIA": load_criteria(),
        # --- Optional credentials for the literature-database harvesters
        # (src/harvest.py). OpenAlex, arXiv and Crossref work without any key.
        "OPENALEX_EMAIL": os.getenv("OPENALEX_EMAIL", ""),
        "SEMANTIC_SCHOLAR_API_KEY": os.getenv("SEMANTIC_SCHOLAR_API_KEY", ""),
        "SCOPUS_API_KEY": os.getenv("SCOPUS_API_KEY", ""),
        "SCOPUS_INST_TOKEN": os.getenv("SCOPUS_INST_TOKEN", ""),
        "IEEE_API_KEY": os.getenv("IEEE_API_KEY", ""),
    }
